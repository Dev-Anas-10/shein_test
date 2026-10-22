#!/usr/bin/env bash
# تثبيت كامل لموقع SHEIN Cart على سيرفر Ubuntu/Debian (بدون venv، بورت 80)
# الاستخدام:  sudo bash install.sh https://github.com/USER/REPO.git [branch]
set -euo pipefail

APP_DIR="/opt/shein-site"
ENV_FILE="/etc/shein-site.env"
SERVICE="shein-site"
DB_NAME="sheindb"
DB_USER="sheinuser"
REPO_URL="${1:-${REPO_URL:-}}"
BRANCH="${2:-${BRANCH:-main}}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

say() { echo -e "\n\033[1;32m==> $*\033[0m"; }
die() { echo -e "\033[1;31mخطأ: $*\033[0m" >&2; exit 1; }
pg()  { (cd /tmp && runuser -u postgres -- psql "$@"); }

[ "$(id -u)" -eq 0 ] || die "شغّل السكريبت بـ root:  sudo bash install.sh <repo-url>"

say "1/6  تثبيت الحزم"
apt-get update
apt-get install -y git curl openssl postgresql gunicorn \
  python3-flask python3-psycopg2 python3-requests python3-pip
pip3 install --break-system-packages playwright
python3 -m playwright install --with-deps chromium
systemctl enable --now postgresql

say "2/6  تحميل الكود إلى $APP_DIR"
mkdir -p "$APP_DIR"
if [ -n "$REPO_URL" ]; then
  [ -d "$APP_DIR/.git" ] || git -C "$APP_DIR" init -q
  git -C "$APP_DIR" remote add origin "$REPO_URL" 2>/dev/null \
    || git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout -f -B "$BRANCH" FETCH_HEAD
elif [ -f "$SRC_DIR/app.py" ]; then
  echo "مفيش رابط GitHub — هنستخدم الملفات المحلية من $SRC_DIR"
  [ "$SRC_DIR" = "$APP_DIR" ] || cp -a "$SRC_DIR"/. "$APP_DIR"/
  echo "تنبيه: التحديث من GitHub محتاج تشغّل install.sh مرة تانية بالرابط."
else
  die "اكتب رابط الريبو:  sudo bash install.sh https://github.com/USER/REPO.git"
fi
[ -f "$APP_DIR/app.py" ] || die "app.py مش موجود في $APP_DIR — تأكد من الريبو والـ branch"

say "3/6  ملف الإعدادات والداتابيس"
if [ ! -f "$ENV_FILE" ]; then
  DB_PASS="$(openssl rand -hex 12)"
  SECRET="$(openssl rand -hex 32)"
  cat > "$ENV_FILE" <<EOF
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@127.0.0.1/$DB_NAME
SECRET_KEY=$SECRET
EOF
  chmod 600 "$ENV_FILE"
  echo "اتنشأ $ENV_FILE"
else
  DB_PASS="$(grep -oP "(?<=$DB_USER:)[^@]+" "$ENV_FILE")"
  echo "$ENV_FILE موجود — هنستخدمه زي ما هو"
fi

if [ "$(pg -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")" = "1" ]; then
  pg -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
else
  pg -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
fi
if [ "$(pg -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != "1" ]; then
  pg -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
fi

say "4/6  إنشاء الجداول"
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f "$APP_DIR/schema.sql"

say "5/6  تثبيت الخدمة"
cp "$APP_DIR/deploy/shein-site.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

say "6/6  فحص التشغيل"
sleep 3
systemctl --no-pager --lines=0 status "$SERVICE" || true
if curl -fsSI http://127.0.0.1/ >/dev/null; then
  IP="$(curl -s --max-time 4 https://checkip.amazonaws.com || echo '<IP-السيرفر>')"
  echo -e "\n\033[1;32mتمام! الموقع شغال:  http://${IP}\033[0m"
  echo "متنساش تفتح بورت 80 (و22) في Security Group."
else
  die "الموقع مش بيرد — شوف:  journalctl -u $SERVICE -n 50 --no-pager"
fi
