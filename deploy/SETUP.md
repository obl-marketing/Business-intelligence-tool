# Deploying STARS on Ubuntu

STARS is a Streamlit (Python) app. This guide self-hosts it on Ubuntu 22.04 / 24.04 with:
- a **shared-password** login,
- runs as a **systemd service** (auto-starts on boot, restarts on crash),
- **HTTPS** via nginx + Let's Encrypt,
- **auto-deploy**: push to GitHub → the server pulls and restarts automatically.

Run the commands as a sudo-capable user. Replace `stars.orientbell.com` with your real domain.

---

## 1. Install system packages
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-dev build-essential git nginx
```
> `build-essential` + `python3-dev` let pip compile any dependency that doesn't yet
> ship a pre-built package for your Python version — relevant on the newest Ubuntu
> releases (e.g. 26.04 LTS), which bundle a very recent Python.

## 2. Create an app user and clone the repo
`useradd -m` creates `/opt/stars` with skeleton files, so clone via a temp dir
(a direct clone into a non-empty directory fails):
```bash
sudo useradd -m -d /opt/stars -s /bin/bash stars
TMP=$(mktemp -d)
sudo git clone https://github.com/obl-marketing/business-intelligence-tool.git "$TMP"
sudo cp -a "$TMP/." /opt/stars/
sudo rm -rf "$TMP"
sudo git -C /opt/stars checkout claude/relaxed-volta-hvi2d
sudo chown -R stars:stars /opt/stars
```

## 3. Create the Python environment
```bash
sudo -u stars python3 -m venv /opt/stars/.venv
sudo -u stars /opt/stars/.venv/bin/pip install -r /opt/stars/requirements.txt
```

## 4. Add secrets
Create `/opt/stars/stars.env` (systemd reads it). `chmod 600` keeps it private.
```bash
sudo -u stars tee /opt/stars/stars.env >/dev/null <<'EOF'
APP_PASSWORD=choose-a-strong-shared-password
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.5-flash
DATA_SOURCE=ga4
GA4_PROPERTY_ID=123456789
GA4_SERVICE_ACCOUNT_JSON={"type":"service_account", ... whole JSON on ONE line ...}
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=obl-marketing/business-intelligence-tool
GITHUB_BRANCH=claude/relaxed-volta-hvi2d
SITE_BASE_URL=https://www.orientbell.com
QUICKLOOK_API_TOKEN=your_getusagestats_bearer_token
EOF
sudo chmod 600 /opt/stars/stars.env
sudo chown stars:stars /opt/stars/stars.env
```
> `GA4_SERVICE_ACCOUNT_JSON` must be the JSON **on a single line** (no line breaks) in this env-file format.

> **QuickLook dealer usage** (designs / catalogues / quotations / sessions / voice prompts by
> dealer): set `QUICKLOOK_API_TOKEN` to the GetUsageStats bearer token to go live. It is
> independent of `DATA_SOURCE` — leave the token out to keep demo data. The API host
> (`quicklook.orientbell.com`) must be reachable from the STARS **server** — it serves HTTPS on
> 443, which is the default. If your network blocks outbound port 80, keep the HTTPS default.
> Optional: `QUICKLOOK_BASE_URL` (defaults to `https://quicklook.orientbell.com`), and
> `DEALER_DIRECTORY_CSV` if you relocate the
> dealer list. The bundled `data/dealer_hierarchy.csv` maps each dealer code → branch/zone and is
> the spine for the counts; replace it to refresh or extend dealer coverage.

## 5. Install the service
```bash
sudo cp /opt/stars/deploy/stars.service /etc/systemd/system/stars.service
sudo systemctl daemon-reload
sudo systemctl enable --now stars
sudo systemctl status stars    # should say "active (running)"
```
The app now runs privately on `127.0.0.1:8501`. nginx exposes it next.

## 6. nginx + HTTPS
```bash
sudo cp /opt/stars/deploy/nginx.conf /etc/nginx/sites-available/stars
sudo sed -i 's/stars.orientbell.com/YOUR-REAL-DOMAIN/' /etc/nginx/sites-available/stars
sudo ln -s /etc/nginx/sites-available/stars /etc/nginx/sites-enabled/stars
sudo nginx -t && sudo systemctl reload nginx

# free HTTPS cert (point the domain's DNS A-record at this server first)
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR-REAL-DOMAIN
```
Visit `https://YOUR-REAL-DOMAIN` → you'll get the password screen.

> **Internal-only alternative:** if you don't want a public domain, skip nginx/certbot and change
> `--server.address 127.0.0.1` to `0.0.0.0` in `deploy/stars.service`, open port 8501 in the
> firewall, and reach it at `http://SERVER-IP:8501`.

## 7. Auto-deploy on push (GitHub Actions)
1. Generate a deploy SSH key **on your laptop** (not the server):
   ```bash
   ssh-keygen -t ed25519 -f stars_deploy -N ""
   ```
2. Add the **public** key to the server's `stars` user:
   ```bash
   sudo -u stars mkdir -p /opt/stars/.ssh
   sudo -u stars tee -a /opt/stars/.ssh/authorized_keys < stars_deploy.pub
   sudo chmod 700 /opt/stars/.ssh && sudo chmod 600 /opt/stars/.ssh/authorized_keys
   ```
3. Let the `stars` user restart the service without a password prompt:
   ```bash
   echo 'stars ALL=(ALL) NOPASSWD: /bin/systemctl restart stars' | sudo tee /etc/sudoers.d/stars
   ```
4. In GitHub → repo → **Settings → Secrets and variables → Actions**, add:
   | Secret | Value |
   |---|---|
   | `DEPLOY_HOST` | server IP or hostname |
   | `DEPLOY_USER` | `stars` |
   | `DEPLOY_PORT` | `22` |
   | `DEPLOY_PATH` | `/opt/stars` |
   | `DEPLOY_SSH_KEY` | contents of the **private** `stars_deploy` file |

Done. Every push to the branch runs `.github/workflows/deploy.yml`, which SSHes in, pulls, installs
deps, and restarts the service. ~30 seconds, no manual step.

---

## Everyday operations
| Task | Command |
|---|---|
| Restart | `sudo systemctl restart stars` |
| Stop / start | `sudo systemctl stop stars` / `sudo systemctl start stars` |
| Status | `systemctl status stars` |
| Live logs | `journalctl -u stars -f` |
| Change password | edit `/opt/stars/stars.env` → `sudo systemctl restart stars` |
| Manual update | `cd /opt/stars && sudo -u stars git pull && sudo systemctl restart stars` |

## Login on/off
- **On:** set `APP_PASSWORD` in `stars.env`. Everyone uses that one password.
- **Off (open):** remove `APP_PASSWORD` and restart.

## What lives where
- **Data (knowledge base + chats):** persists to your GitHub repo via the `GITHUB_*` secrets — survives restarts and redeploys.
- **AI compute:** on Gemini's servers; your server never runs a model, so it stays light.
- **Secrets:** only in `/opt/stars/stars.env` (chmod 600) + GitHub Actions secrets. Never committed.

## Disk footprint
STARS + Python + dependencies is roughly **8 GB** on top of Ubuntu. Data files are tiny (JSON).
No database server required.
