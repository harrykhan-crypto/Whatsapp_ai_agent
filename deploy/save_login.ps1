# Save the Moodle username/password (tested first), then send it to the HARRY server if you use one.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "Run SETUP_HARRY.bat first." -ForegroundColor Red; Read-Host "Press ENTER to close"; exit 1 }
$env:PYTHONUTF8 = "1"
& $python save_login.py
if ($LASTEXITCODE -ne 0) { Read-Host "Press ENTER to close"; exit 1 }

if (Test-Path (Join-Path $PSScriptRoot "server.local")) {
    . (Join-Path $PSScriptRoot "common.ps1")
    Write-Host "==> Sending the login to your HARRY server" -ForegroundColor Cyan
    Send-File (Join-Path $Root ".env") "harry/.env.upload"
    $session = Join-Path $Root "storage\moodle_session.json"
    $extra = ""
    if (Test-Path $session) {
        Send-File $session "harry/storage/moodle_session.json"
        $extra = " && chmod 600 storage/moodle_session.json"
    }
    Invoke-Remote ("cd ~/harry && grep -E '^MOODLE_(USERNAME|PASSWORD)=' .env.upload > .env.cred; " +
        "grep -vE '^MOODLE_(USERNAME|PASSWORD)=' .env > .env.new; cat .env.cred >> .env.new && mv -f .env.new .env " +
        "&& chmod 600 .env && rm -f .env.upload .env.cred$extra && sudo systemctl restart harry && sleep 3 && systemctl is-active harry")
    Write-Host "`nDone. The server is signed in and will log in again by itself from now on." -ForegroundColor Green
} else {
    Write-Host "`nIf HARRY is running on this PC, close it and start it again (START_HARRY.bat)." -ForegroundColor Yellow
}
Read-Host "Press ENTER to close"
