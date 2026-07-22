#!/usr/bin/env bash
#
# STARS one-command installer for Ubuntu (22.04 / 24.04 / 26.04 LTS).
#
# Usage (run on the server as a sudo-capable user):
#   curl -fsSL https://raw.githubusercontent.com/obl-marketing/business-intelligence-tool/claude/relaxed-volta-hvi2d/deploy/install.sh | sudo bash
#
# Optional: give it a domain to also set up nginx + HTTPS:
#   curl -fsSL <url>/deploy/install.sh | sudo bash -s -- stars.orientbell.com
#
# It is safe to re-run (idempotent): it updates code + deps and restarts.
#
set -euo pipefail

REPO="https://github.com/obl-marketing/business-intelligence-tool.git"
BRANCH="claude/relaxed-volta-hvi2d"
APP_DIR="/opt/stars"
APP_USER="stars"
DOMAIN="${1:-}"

say() { echo -e "\n\033[1;33m>>> $*\033[0m"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  curl ... | sudo bash"
  exit 1
fi

say "1/7  Installing system packages"
apt-get update -y
apt-get install -y python3-venv python3-pip python3-dev build-essential git nginx

say "2/7  Creating the '${APP_USER}' service user"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -d "$APP_DIR" -s /bin/bash "$APP_USER"

say "3/7  Getting the code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --all
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  # /opt/stars already exists as the service user's home dir (non-empty),
  # so a direct clone into it fails. Clone to a temp dir and copy the repo in.
  TMP="$(mktemp -d)"
  git clone "$REPO" "$TMP"
  cp -a "$TMP/." "$APP_DIR/"
  rm -rf "$TMP"
  git -C "$APP_DIR" checkout "$BRANCH"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "4/7  Building the Python environment (this can take a couple of minutes)"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

say "5/7  Creating the secrets file (if it doesn't exist yet)"
ENV_FILE="$APP_DIR/stars.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# ---- FILL THESE IN, then run:  sudo systemctl restart stars ----
APP_PASSWORD=change-me-team-password
LLM_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
DATA_SOURCE=ga4
GA4_PROPERTY_ID=
GA4_SERVICE_ACCOUNT_JSON=
GITHUB_TOKEN=
GITHUB_REPO=obl-marketing/business-intelligence-tool
GITHUB_BRANCH=claude/relaxed-volta-hvi2d
SITE_BASE_URL=
EOF
  chown "$APP_USER:$APP_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  NEEDS_SECRETS=1
else
  NEEDS_SECRETS=0
fi

say "6/7  Installing the background service"
cp "$APP_DIR/deploy/stars.service" /etc/systemd/system/stars.service
systemctl daemon-reload
systemctl enable stars
systemctl restart stars || true   # will fully run once secrets are filled

if [ -n "$DOMAIN" ]; then
  say "7/7  Setting up nginx + HTTPS for ${DOMAIN}"
  cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/stars
  sed -i "s/stars.orientbell.com/${DOMAIN}/" /etc/nginx/sites-available/stars
  ln -sf /etc/nginx/sites-available/stars /etc/nginx/sites-enabled/stars
  nginx -t && systemctl reload nginx
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email || \
    echo "certbot needs the domain's DNS pointed here first; re-run: sudo certbot --nginx -d ${DOMAIN}"
else
  say "7/7  Skipping nginx/HTTPS (no domain given). App will be internal-only."
fi

echo
echo "============================================================"
echo " STARS installed."
if [ "$NEEDS_SECRETS" = "1" ]; then
  echo " NEXT: put your API keys in the secrets file, then restart:"
  echo "   sudo nano ${ENV_FILE}"
  echo "   sudo systemctl restart stars"
fi
if [ -n "$DOMAIN" ]; then
  echo " URL:  https://${DOMAIN}"
else
  echo " URL:  http://SERVER-IP:8501  (set --server.address 0.0.0.0 in"
  echo "        deploy/stars.service and open port 8501 for LAN access)"
fi
echo " Status:  systemctl status stars"
echo " Logs:    journalctl -u stars -f"
echo "============================================================"
