# Deploying STARS on your own server

STARS is a Streamlit (Python) app. This guide self-hosts it with:
- a **shared-password** login,
- **HTTPS**,
- **auto-deploy**: push to GitHub → the server pulls and restarts automatically.

---

## Step 0 — Find out if your server is Linux or Windows

Ask whoever manages it, or check yourself:

- **You SSH in and get a `$` prompt / run `uname -a` and see "Linux"** → Linux. Use **Path A** (recommended).
- **You Remote-Desktop (RDP) into a Windows desktop / it runs Windows Server** → Windows. Use **Path B**.

**Strong recommendation: use a Linux (Ubuntu 22.04+) server.** Everything below is simpler, cheaper, and standard on Linux. If the only server available is Windows, Path B works but is more fiddly. If you can request a small Ubuntu VM instead, do that.

---

# PATH A — Linux (Ubuntu 22.04 / 24.04)  ← recommended

Run these as a sudo-capable user. Replace `stars.yourcompany.com` with your domain.

### A1. Install system packages
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git nginx
```

### A2. Create an app user and clone the repo
```bash
sudo useradd -m -d /opt/stars -s /bin/bash stars
sudo -u stars git clone https://github.com/obl-marketing/business-intelligence-tool.git /opt/stars
cd /opt/stars
sudo -u stars git checkout claude/relaxed-volta-hvi2d
```

### A3. Create the Python environment
```bash
sudo -u stars python3 -m venv /opt/stars/.venv
sudo -u stars /opt/stars/.venv/bin/pip install -r /opt/stars/requirements.txt
```

### A4. Add secrets
Create `/opt/stars/stars.env` (systemd reads this). `chmod 600` keeps it private.
```bash
sudo -u stars tee /opt/stars/stars.env >/dev/null <<'EOF'
APP_PASSWORD=choose-a-strong-shared-password
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.5-flash
DATA_SOURCE=ga4
GA4_PROPERTY_ID=123456789
GA4_SERVICE_ACCOUNT_JSON={"type":"service_account", ... one line ...}
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=obl-marketing/business-intelligence-tool
GITHUB_BRANCH=claude/relaxed-volta-hvi2d
SITE_BASE_URL=https://www.yourcompany.com
EOF
sudo chmod 600 /opt/stars/stars.env
sudo chown stars:stars /opt/stars/stars.env
```
> Note: in this env-file format `GA4_SERVICE_ACCOUNT_JSON` must be the JSON **on a single line** (no line breaks). Paste the whole `{...}` compacted.

### A5. Install the service
```bash
sudo cp /opt/stars/deploy/stars.service /etc/systemd/system/stars.service
sudo systemctl daemon-reload
sudo systemctl enable --now stars
sudo systemctl status stars    # should say "active (running)"
```
The app is now on `127.0.0.1:8501` (local only). nginx exposes it next.

### A6. nginx + HTTPS
```bash
sudo cp /opt/stars/deploy/nginx.conf /etc/nginx/sites-available/stars
sudo sed -i 's/stars.yourcompany.com/YOUR-REAL-DOMAIN/' /etc/nginx/sites-available/stars
sudo ln -s /etc/nginx/sites-available/stars /etc/nginx/sites-enabled/stars
sudo nginx -t && sudo systemctl reload nginx

# free HTTPS certificate (point your domain's DNS A-record at this server first)
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR-REAL-DOMAIN
```
Visit `https://YOUR-REAL-DOMAIN` → you'll get the password screen.

### A7. Auto-deploy on push (GitHub Actions)
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

Done. Now every push to the branch triggers `.github/workflows/deploy.yml`, which SSHes in, pulls, installs deps, and restarts. ~30 seconds, no manual step.

---

# PATH B — Windows Server

Streamlit runs on Windows, but there's no systemd/nginx. Use NSSM to run it as a service and IIS (or just the port) for access.

### B1. Install
- Install **Python 3.11** from python.org (check "Add to PATH").
- Install **Git for Windows**.
- Download **NSSM** (nssm.cc) — runs the app as a Windows service.

### B2. Clone + environment (PowerShell)
```powershell
git clone https://github.com/obl-marketing/business-intelligence-tool.git C:\stars
cd C:\stars
git checkout claude/relaxed-volta-hvi2d
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### B3. Secrets
Create `C:\stars\.env` with the same KEY=VALUE lines as Path A step A4 (the app reads `.env` via python-dotenv).

### B4. Run as a service with NSSM
```powershell
nssm install STARS "C:\stars\.venv\Scripts\streamlit.exe" `
  "run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true"
nssm set STARS AppDirectory C:\stars
nssm start STARS
```
Open Windows Firewall for port 8501 (or put IIS/Application Request Routing in front for HTTPS on 443).

### B5. Auto-deploy on Windows
Enable **OpenSSH Server** (Windows optional feature), then the same `.github/workflows/deploy.yml` works if you adjust the `script:` block to Windows commands (`git pull`, `.\.venv\Scripts\pip install`, `nssm restart STARS`). Ask and I'll write a Windows-specific workflow.

> Honestly: if you have any choice, run this on Linux. Windows adds friction at every step here.

---

## Turning the login on/off
- **On:** set `APP_PASSWORD` in the env file. Everyone uses that one password.
- **Off (open):** remove `APP_PASSWORD`. The app skips the login screen.
- Change the password: edit the env file → `sudo systemctl restart stars` (Linux) / `nssm restart STARS` (Windows).

## What still lives where
- **Data (knowledge base + chats):** persists to your GitHub repo via `GITHUB_*` secrets — unchanged from the cloud setup, survives restarts.
- **AI compute:** on Gemini's servers; your server never runs a model.
- **Secrets:** only in the server's env file (chmod 600) + GitHub Actions secrets. Never commit them.
