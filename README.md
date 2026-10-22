# SHEIN Cart (Flask + PostgreSQL)

Arabic Flask application for parsing SHEIN shared-cart links, showing products, and saving selections to a PostgreSQL-backed cart.

## Project layout

- `app.py` — Flask routes, authentication, results, and cart
- `scraper.py` — resolves supported SHEIN shared links and captures the share API response with Playwright
- `schema.sql` — database tables
- `templates/` — Jinja HTML templates
- `requirements.txt` — Python dependencies
- `install.sh` — initial Ubuntu/Debian server setup (run as root)
- `deploy.sh` — update/restart an existing installation

## Deploy on a fresh Ubuntu EC2 instance

1. Clone this repository to `/opt/shein-site` (or clone it elsewhere and update `APP_DIR` in the scripts).
2. Run `sudo bash install.sh`.
3. Allow inbound TCP 80 in the EC2 Security Group only if you intend to expose the site. SSH should be restricted to your IP.
4. Check service status and logs using the commands below.

```bash
sudo systemctl status shein-site --no-pager
sudo journalctl -u shein-site -n 100 --no-pager
```

## Update after pushing changes to GitHub

```bash
cd /opt/shein-site
sudo bash deploy.sh
```

## Important

- The scraper depends on SHEIN's current website/API behavior and may stop working if SHEIN changes it or requires a verification challenge.
- This is a starter project, not production-hardened. Before accepting public users, configure a domain and HTTPS, add CSRF protection and rate limiting, review cookie/session settings, and test account recovery and privacy requirements.
- Do not commit `.env` files, database passwords, session secrets, or any real customer data.
- Prices and currency are currently normalized as SAR in `scraper.py`, matching the provided source. Verify the returned price/currency semantics before using it for customer billing.
