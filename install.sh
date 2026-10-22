#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash install.sh" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/shein-site}"
if [[ ! -f "$APP_DIR/app.py" || ! -f "$APP_DIR/schema.sql" ]]; then
  echo "Expected app.py and schema.sql in $APP_DIR. Clone/copy the project there first." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib build-essential libpq-dev curl nginx
systemctl enable --now postgresql

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m playwright install --with-deps chromium

DB_PASS="$(openssl rand -hex 24)"
SECRET="$(openssl rand -hex 32)"
# Create/refresh the dedicated DB role and database safely through psql variables.
sudo -u postgres psql -v ON_ERROR_STOP=1 -v dbpass="$DB_PASS" <<'SQL'
SELECT format('CREATE ROLE sheinuser LOGIN PASSWORD %L', :'dbpass')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sheinuser')\gexec
SELECT format('ALTER ROLE sheinuser WITH LOGIN PASSWORD %L', :'dbpass')\gexec
SELECT format('CREATE DATABASE sheindb OWNER sheinuser')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sheindb')\gexec
SQL
sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'ALTER DATABASE sheindb OWNER TO sheinuser;'

install -m 600 /dev/null /etc/shein-site.env
cat > /etc/shein-site.env <<EOF
DATABASE_URL=postgresql://sheinuser:${DB_PASS}@127.0.0.1:5432/sheindb
SECRET_KEY=${SECRET}
EOF
chmod 600 /etc/shein-site.env
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U sheinuser -d sheindb -v ON_ERROR_STOP=1 -f "$APP_DIR/schema.sql"

# Run the web app as a dedicated unprivileged account.
if ! id -u sheinapp >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/sheinapp --shell /usr/sbin/nologin sheinapp
fi
install -d -o sheinapp -g sheinapp /home/sheinapp/.cache
runuser -u sheinapp -- env HOME=/home/sheinapp "$APP_DIR/.venv/bin/python" -m playwright install chromium

cat > /etc/systemd/system/shein-site.service <<EOF
[Unit]
Description=SHEIN Cart Flask Site
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=/etc/shein-site.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 --timeout 180 app:app
Restart=on-failure
RestartSec=3
User=sheinapp
Group=sheinapp
Environment=HOME=/home/sheinapp

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/sites-available/shein-site <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 190s;
    }
}
NGINX
ln -sfn /etc/nginx/sites-available/shein-site /etc/nginx/sites-enabled/shein-site
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl daemon-reload
systemctl enable --now shein-site
systemctl --no-pager --full status shein-site || true
echo
echo "Install finished. Check: curl -I http://127.0.0.1/"
echo "Logs: journalctl -u shein-site -n 100 --no-pager"
