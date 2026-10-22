#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy.sh" >&2
  exit 1
fi
APP_DIR="${APP_DIR:-/opt/shein-site}"
if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "$APP_DIR is not a Git repository. Clone the repository there first." >&2
  exit 1
fi
cd "$APP_DIR"
git pull --ff-only
"$APP_DIR/.venv/bin/pip" install -r requirements.txt
runuser -u sheinapp -- env HOME=/home/sheinapp "$APP_DIR/.venv/bin/python" -m playwright install chromium
PGPASSWORD="$(sed -n 's#^DATABASE_URL=postgresql://sheinuser:\([^@]*\)@.*#\1#p' /etc/shein-site.env)" \
  psql -h 127.0.0.1 -U sheinuser -d sheindb -v ON_ERROR_STOP=1 -f "$APP_DIR/schema.sql"
systemctl restart shein-site nginx
systemctl --no-pager --full status shein-site
