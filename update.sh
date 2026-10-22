#!/usr/bin/env bash
# تحديث الكود من GitHub + ضبط الداتابيس والخدمة + إعادة التشغيل
# الاستخدام:  sudo bash /opt/shein-site/update.sh [branch] [--force] [--deps]
#   --force  يعيد التطبيق حتى لو مفيش تغييرات جديدة
#   --deps   يحدّث حزم النظام وPlaywright/Chromium كمان
set -euo pipefail

APP_DIR="/opt/shein-site"
ENV_FILE="/etc/shein-site.env"
SERVICE="shein-site"
DB_NAME="sheindb"
DB_USER="sheinuser"
export DEBIAN_FRONTEND=noninteractive

say() { echo -e "\n\033[1;32m==> $*\033[0m"; }
die() { echo -e "\033[1;31mخطأ: $*\033[0m" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "شغّل السكريبت بـ root:  sudo bash update.sh"
[ -d "$APP_DIR/.git" ] || die "$APP_DIR مش ريبو git — ثبّت الأول بـ install.sh <repo-url>"
[ -f "$ENV_FILE" ] || die "$ENV_FILE مش موجود — شغّل install.sh الأول"

FORCE=0; DEPS=0; BRANCH=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --deps)  DEPS=1 ;;
    *)       BRANCH="$a" ;;
  esac
done
cd "$APP_DIR"
[ -n "$BRANCH" ] || BRANCH="$(git rev-parse --abbrev-ref HEAD)"

say "1/5  تحميل آخر نسخة من GitHub (branch: $BRANCH)"
OLD="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
NEW="$(git rev-parse FETCH_HEAD)"
if [ "$OLD" = "$NEW" ] && [ "$FORCE" -eq 0 ]; then
  echo "الكود already آخر نسخة ($OLD). مفيش تحديث. (استخدم --force لو عايز تعيد الضبط والتشغيل)"
  exit 0
fi
git checkout -f -B "$BRANCH" FETCH_HEAD
[ "$OLD" = "$NEW" ] || git --no-pager diff --stat "$OLD" "$NEW" || true

say "2/5  فحص الكود"
if ! python3 -m py_compile app.py scraper.py; then
  echo "فيه خطأ في الكود الجديد — برجّع النسخة القديمة"
  git checkout -f -B "$BRANCH" "$OLD"
  die "التحديث اتلغى والخدمة لسه على النسخة القديمة"
fi
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if [ "$DEPS" -eq 1 ]; then
  say "حزم النظام وPlaywright"
  apt-get update
  apt-get install -y git curl openssl postgresql gunicorn \
    python3-flask python3-psycopg2 python3-requests python3-pip
  pip3 install --break-system-packages -U playwright
  python3 -m playwright install --with-deps chromium
fi

say "3/5  ضبط الداتابيس (schema.sql آمن للتكرار)"
DB_PASS="$(grep -oP "(?<=$DB_USER:)[^@]+" "$ENV_FILE")"
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f "$APP_DIR/schema.sql"

say "4/5  تحديث ملف الخدمة"
cp "$APP_DIR/deploy/shein-site.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1

say "5/5  إعادة تشغيل الخدمة"
systemctl restart "$SERVICE"
sleep 3
if systemctl is-active --quiet "$SERVICE" && curl -fsSI http://127.0.0.1/ >/dev/null; then
  echo -e "\033[1;32mتم التحديث ✔  ($(git rev-parse --short HEAD))\033[0m"
else
  echo "الخدمة مش شغالة بعد التحديث. اللوج:"
  journalctl -u "$SERVICE" -n 30 --no-pager || true
  echo -e "\nللرجوع للنسخة السابقة:  cd $APP_DIR && git checkout -f -B $BRANCH $OLD && systemctl restart $SERVICE"
  exit 1
fi
