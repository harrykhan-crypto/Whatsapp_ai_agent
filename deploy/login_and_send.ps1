# Sign in to Moodle on this laptop and send the login to the HARRY server.
. (Join-Path $PSScriptRoot "common.ps1")

$python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Fail "Run SETUP_HARRY.bat first." }
$env:PYTHONUTF8 = "1"
Write-Host "==> Opening Moodle. Sign in in the browser window if it asks." -ForegroundColor Cyan
& $python login_helper.py "storage\moodle_session.json"
if ($LASTEXITCODE -ne 0) { Fail "Login was not completed." }

Write-Host "==> Sending the login to the server" -ForegroundColor Cyan
Send-File (Join-Path $Root "storage\moodle_session.json") "/tmp/moodle_session.json"
Invoke-Remote "mkdir -p ~/harry/storage && mv -f /tmp/moodle_session.json ~/harry/storage/moodle_session.json && chmod 600 ~/harry/storage/moodle_session.json && sudo systemctl restart harry && sleep 3 && systemctl is-active harry"
Write-Host "`nDone. HARRY on the server is signed in again. Check WhatsApp with /status." -ForegroundColor Green
Read-Host "Press ENTER to close"
