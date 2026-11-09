# Live checks against your real LMS and WhatsApp. Stop HARRY (START_HARRY window) before running this.
Set-Location -Path $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Write-Host "Run SETUP_HARRY.bat first." -ForegroundColor Red; Read-Host "Press ENTER to close"; exit 1 }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $venvPython diagnostics.py all
Write-Host "`nReport saved in storage\diagnostics_report.txt" -ForegroundColor Cyan
Read-Host "Press ENTER to close"
