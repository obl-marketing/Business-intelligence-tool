# Deploying STARS on your Windows server

STARS is a Streamlit (Python) app. This guide self-hosts it on Windows with:
- a **shared-password** login,
- runs as a **Windows service** (auto-starts on boot, restarts on crash),
- **auto-deploy**: push to GitHub → the server pulls and restarts automatically.

Run all PowerShell commands **as Administrator**.

---

## W1. Install the prerequisites

Download and install:
1. **Python 3.11** — https://www.python.org/downloads/  → during install, **check "Add python.exe to PATH"**.
2. **Git for Windows** — https://git-scm.com/download/win  (accept defaults).
3. **NSSM** (runs STARS as a service) — https://nssm.cc/download  → unzip, copy `nssm.exe` (the 64-bit one) to `C:\Windows\System32\` so it's on PATH.

Verify in a new PowerShell window:
```powershell
python --version   # 3.11.x
git --version
nssm version
```

---

## W2. Get the code

```powershell
cd C:\
git clone https://github.com/obl-marketing/business-intelligence-tool.git C:\stars
cd C:\stars
git checkout claude/relaxed-volta-hvi2d
```

---

## W3. Create the Python environment

```powershell
cd C:\stars
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

---

## W4. Add secrets (.env file)

Create `C:\stars\.env` (the app reads it via python-dotenv). Fill in your real values:

```ini
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
SITE_BASE_URL=https://www.yourcompany.com
```

> `GA4_SERVICE_ACCOUNT_JSON` must be the entire JSON **on a single line** (no line breaks).
> Keep this file private — it holds your keys. Don't commit it (it's already git-ignored).

Quick test that it runs before making it a service:
```powershell
.\.venv\Scripts\streamlit run app.py --server.port 8501
```
Open `http://localhost:8501` in a browser on the server → you should get the password screen. Press `Ctrl+C` to stop, then continue.

---

## W5. Install STARS as a Windows service (NSSM)

```powershell
nssm install STARS "C:\stars\.venv\Scripts\streamlit.exe" "run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false"
nssm set STARS AppDirectory C:\stars
nssm set STARS Start SERVICE_AUTO_START
nssm start STARS
```
Check it: `nssm status STARS` → should say `SERVICE_RUNNING`.
The app is now live on port **8501** and will auto-start on every reboot.

**Open the firewall** so people on your network can reach it:
```powershell
New-NetFirewallRule -DisplayName "STARS 8501" -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow
```
Colleagues can now visit `http://SERVER-IP:8501`.

> **HTTPS / clean domain (optional but recommended):** to get `https://stars.yourcompany.com`
> instead of `http://SERVER-IP:8501`, put **IIS with Application Request Routing** in front as
> a reverse proxy to `127.0.0.1:8501`, and bind a certificate. Ask your IT team — on Windows
> this is the standard way, and they'll likely already have an internal cert process. If it's
> purely internal, the plain `http://SERVER-IP:8501` is fine to start.

---

## W6. Enable auto-deploy (push → server updates itself)

### W6a. Turn on OpenSSH Server
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -DisplayName "OpenSSH 22" -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
```

### W6b. Make PowerShell the default SSH shell (so the deploy script runs cleanly)
```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

### W6c. Add the deploy key
On **your laptop** (not the server), create a key:
```bash
ssh-keygen -t ed25519 -f stars_deploy -N ""
```
Copy the **public** key (`stars_deploy.pub`) contents into this file on the **server**
(create the folder if needed), for the admin user you'll deploy as:
```
C:\Users\<that-user>\.ssh\authorized_keys
```
> For a local admin user, Windows OpenSSH also reads `C:\ProgramData\ssh\administrators_authorized_keys`
> — if the deploy user is an Administrator, put the key there instead and set its permissions to
> Administrators + SYSTEM only. (Your IT team will know this; it's a standard Windows OpenSSH step.)

### W6d. Add the secrets in GitHub
Repo → **Settings → Secrets and variables → Actions** → add:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | server IP or hostname |
| `DEPLOY_USER` | the Windows admin username |
| `DEPLOY_PORT` | `22` |
| `DEPLOY_SSH_KEY` | contents of the **private** `stars_deploy` file |

Done. Now every push to the branch runs `.github/workflows/deploy.yml`, which SSHes in and
runs `C:\stars\deploy\update.ps1` (git pull → pip install → `nssm restart STARS`). ~30 seconds.

---

## Everyday operations

| Task | Command (PowerShell, as Admin) |
|---|---|
| Restart STARS | `nssm restart STARS` |
| Stop / start | `nssm stop STARS` / `nssm start STARS` |
| Check status | `nssm status STARS` |
| See logs | `nssm set STARS AppStdout C:\stars\logs\out.log` then restart (configures logging) |
| Change the password | edit `C:\stars\.env` → `nssm restart STARS` |
| Manual update | `powershell -ExecutionPolicy Bypass -File C:\stars\deploy\update.ps1` |

## Login on/off
- **On:** set `APP_PASSWORD` in `.env`. Everyone uses that one password.
- **Off (open):** remove `APP_PASSWORD` from `.env` and restart.

## What lives where
- **Data (knowledge base + chats):** persists to your GitHub repo via the `GITHUB_*` secrets — survives restarts and redeploys.
- **AI compute:** on Gemini's servers; your server never runs a model, so it stays light.
- **Secrets:** only in `C:\stars\.env` + GitHub Actions secrets. Never committed.

## Disk footprint
STARS adds roughly **8–10 GB** on top of Windows (Python + dependencies + app + logs).
Your data files are tiny (JSON). No database server required.
