# STARS auto-update script (Windows).
# Called by the GitHub Actions deploy workflow over SSH, and can be run
# by hand:  powershell -ExecutionPolicy Bypass -File C:\stars\deploy\update.ps1
#
# It pulls the latest code, installs any new dependencies, and restarts
# the STARS Windows service (managed by NSSM).

$ErrorActionPreference = "Stop"
Set-Location C:\stars

git fetch --all
git reset --hard origin/claude/relaxed-volta-hvi2d
& C:\stars\.venv\Scripts\pip.exe install -q -r requirements.txt
nssm restart STARS

Write-Host "Deployed $(git rev-parse --short HEAD)"
