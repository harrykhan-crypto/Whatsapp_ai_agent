# Starts HARRY and restarts it automatically if it crashes. Close the window or press Ctrl+C to stop.
Set-Location -Path $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "HARRY is not set up yet. Double-click SETUP_HARRY.bat first." -ForegroundColor Red
    Read-Host "Press ENTER to close"
    exit 1
}
$Host.UI.RawUI.WindowTitle = "HARRY - Abasyn LMS Assistant (keep this window open)"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

while ($true) {
    Write-Host "`n[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting HARRY..." -ForegroundColor Green
    Write-Host "Keep this window and the Moodle browser window open. If Moodle asks you to log in, sign in in that browser window."
    & $venvPython app.py
    $code = $LASTEXITCODE
    if ($code -eq 0) { Write-Host "HARRY stopped."; break }
    Write-Host "`nHARRY stopped unexpectedly (exit code $code). Restarting in 15 seconds... (close this window to stop)" -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}
Read-Host "Press ENTER to close"
