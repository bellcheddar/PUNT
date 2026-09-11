#!/usr/bin/env bash
# First-time setup for PUNT on the droplet, and safe to re-run.
#
#   scp -r deploy root@<droplet>:/tmp/punt-deploy
#   ssh root@<droplet> bash /tmp/punt-deploy/provision.sh
#
# Creates the service user, the venv, the systemd unit and the nginx vhost, then
# obtains or renews the certificate. Run deploy/deploy.sh afterwards to put the
# code there.
set -euo pipefail

APP=punt
APP_DIR=/opt/$APP
SERVER_NAME=${SERVER_NAME:-punt.mdeller.com}
PORT=8011
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }

echo "==> Checking nothing else already owns port $PORT"
if ss -ltnp 2>/dev/null | grep -q "127.0.0.1:$PORT"; then
  echo "    Port $PORT is already in use:"
  ss -ltnp | grep "127.0.0.1:$PORT"
  echo "    Pick another and change it in deploy/gunicorn.conf.py AND deploy/nginx-punt.conf."
  exit 1
fi

echo "==> Service user and directory"
id -u $APP >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin $APP
mkdir -p "$APP_DIR"
chown $APP:$APP "$APP_DIR"

echo "==> Virtualenv"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
  chown -R $APP:$APP "$APP_DIR/.venv"
fi

echo "==> systemd unit"
install -m 0644 "$HERE/punt-web.service" /etc/systemd/system/punt-web.service
systemctl daemon-reload
systemctl enable punt-web.service

echo "==> nginx vhost"
# Written only if absent. certbot edits this file in place to add the TLS block,
# so overwriting it on a re-run would delete HTTPS -- and would fail silently,
# because the port 80 block left behind is valid on its own and `nginx -t`
# still passes.
if [[ ! -f /etc/nginx/sites-available/$APP ]]; then
  install -m 0644 "$HERE/nginx-punt.conf" /etc/nginx/sites-available/$APP
  ln -sf /etc/nginx/sites-available/$APP /etc/nginx/sites-enabled/$APP
else
  echo "    /etc/nginx/sites-available/$APP exists; leaving it alone."
  echo "    Diff it against deploy/nginx-punt.conf if the template has moved on."
fi
nginx -t
systemctl reload nginx

echo "==> Certificate"
# Re-run on EVERY provision, not just the first. It is idempotent -- a valid
# certificate is reused rather than re-issued -- and skipping it "because a cert
# already exists" is exactly how HTTPS gets deleted by a later template copy.
certbot --nginx -d "$SERVER_NAME" --non-interactive --agree-tos \
        -m marc@marcdeller.com --redirect || {
  echo "    certbot failed. The site will serve on port 80 only until it is fixed."
}

echo "==> http2"
# nginx 1.24 on this box, so it is `listen 443 ssl http2;` -- the `http2 on;`
# form arrived in 1.25.1 and fails `nginx -t` here with "unknown directive".
# certbot writes `listen 443 ssl;`, so the directive is appended to that line.
if grep -q 'listen 443 ssl;' /etc/nginx/sites-available/$APP; then
  sed -i 's/listen 443 ssl;/listen 443 ssl http2;/' /etc/nginx/sites-available/$APP
  sed -i 's/listen \[::\]:443 ssl;/listen [::]:443 ssl http2;/' /etc/nginx/sites-available/$APP
  nginx -t && systemctl reload nginx
  echo "    http2 enabled."
else
  echo "    No plain 'listen 443 ssl;' found; http2 already on, or no TLS block yet."
fi

echo
echo "Provisioned. Now run deploy/deploy.sh from the repository to install the code."
