# Upload HARRY to the server and (re)install it. Used for the first install and for every update.
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path ".env")) { Fail ".env not found. Run SETUP_HARRY.bat first." }
Write-Host "==> Packing the current code" -ForegroundColor Cyan
$zip = Join-Path $env:TEMP "harry.zip"
if (Test-Path $zip) { Remove-Item $zip }
git archive --format=zip -o $zip HEAD
if ($LASTEXITCODE -ne 0) { Fail "git archive failed. Run this from the project folder (git pull origin main first)." }

Write-Host "==> Uploading to $Server" -ForegroundColor Cyan
Send-File $zip "/tmp/harry.zip"
Send-File (Join-Path $Root ".env") "/tmp/harry.env"
$session = Join-Path $Root "storage\moodle_session.json"
$haveSession = Test-Path $session
if ($haveSession) { Send-File $session "/tmp/moodle_session.json" }

Write-Host "==> Installing on the server (first time takes 5-10 minutes)" -ForegroundColor Cyan
$cmd = "sudo apt-get install -y -qq unzip >/dev/null 2>&1 || true; mkdir -p ~/harry/storage && cd ~/harry && unzip -oq /tmp/harry.zip && mv -f /tmp/harry.env .env"
if ($haveSession) { $cmd += " && mv -f /tmp/moodle_session.json storage/moodle_session.json" }
$cmd += " && rm -f /tmp/harry.zip && bash deploy/server_setup.sh"
Invoke-Remote $cmd

Write-Host "`nDone. HARRY now runs on the server 24/7." -ForegroundColor Green
if (-not $haveSession) { Write-Host "Next: double-click LOGIN_AND_SEND.bat to give the server your Moodle login." -ForegroundColor Yellow }
Write-Host "Do NOT run START_HARRY.bat on the laptop any more (two HARRYs would fight over WhatsApp)."
Read-Host "Press ENTER to close"
