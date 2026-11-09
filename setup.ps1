# HARRY one-time setup. Run by double-clicking SETUP_HARRY.bat (or: powershell -ExecutionPolicy Bypass -File setup.ps1)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nERROR: $text" -ForegroundColor Red; Read-Host "Press ENTER to close"; exit 1 }

Step "Checking Python"
$python = $null
$candidates = @(
    @{ Exe = "py"; Args = @("-3.13") },
    @{ Exe = "py"; Args = @("-3") },
    @{ Exe = "python"; Args = @() }
)
foreach ($candidate in $candidates) {
    try {
        $version = & $candidate.Exe @($candidate.Args + "--version") 2>&1
        if ($LASTEXITCODE -eq 0 -and "$version" -match "Python 3\.(1[1-9]|[2-9][0-9])") { $python = $candidate; break }
    } catch { }
}
if (-not $python) { Fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run setup again." }
Write-Host "Using $version"

Step "Creating virtual environment (venv)"
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    & $python.Exe @($python.Args + @("-m", "venv", "venv"))
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
} else { Write-Host "venv already exists - keeping it" }
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

Step "Installing Python packages"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed (check your internet connection)." }

Step "Installing the Chromium browser for Playwright"
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Fail "Chromium installation failed." }

Step "Checking .env settings"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Fill in WHATSAPP_AGENT_API_KEY, save and close Notepad." -ForegroundColor Yellow
    Start-Process notepad.exe ".env" -Wait
} else {
    Write-Host ".env already exists - keeping your settings"
}
$envText = Get-Content ".env" -Raw
if ($envText -notmatch "(?m)^\s*WHATSAPP_AGENT_API_KEY\s*=\s*\S+" -and $envText -notmatch "(?m)^\s*WHATSAPP_PROVIDER\s*=\s*(cloud|console)") {
    Write-Host "WHATSAPP_AGENT_API_KEY is empty in .env. Opening it so you can add it..." -ForegroundColor Yellow
    Start-Process notepad.exe ".env" -Wait
}

Step "Running the automated tests"
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Some tests failed - send the output above to get help." -ForegroundColor Yellow }

Write-Host "`nSetup finished." -ForegroundColor Green
Write-Host "Next: send 'menu' to your HARRY agent on WhatsApp once, then double-click START_HARRY.bat"
Read-Host "Press ENTER to close"
