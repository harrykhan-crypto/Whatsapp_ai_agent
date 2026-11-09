# Shared helpers for DEPLOY_TO_SERVER / LOGIN_AND_SEND. Stores the server address in deploy\server.local (not in git).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ConfigFile = Join-Path $PSScriptRoot "server.local"

function Fail($text) { Write-Host "`nERROR: $text" -ForegroundColor Red; Read-Host "Press ENTER to close"; exit 1 }

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Fail "OpenSSH is missing. Settings > System > Optional features > Add a feature > 'OpenSSH Client'."
}

if (-not (Test-Path $ConfigFile)) {
    Write-Host "First time: tell me about your free Oracle server." -ForegroundColor Cyan
    $ip = (Read-Host "Server public IP address (e.g. 152.67.10.20)").Trim()
    $key = (Read-Host "Full path of the private key file you downloaded (e.g. C:\Users\you\Downloads\ssh-key.key)").Trim('"', ' ')
    $user = (Read-Host "Server user name (press ENTER for 'ubuntu')").Trim()
    if (-not $user) { $user = "ubuntu" }
    "IP=$ip`nKEY=$key`nUSER=$user" | Set-Content -Path $ConfigFile -Encoding ASCII
}
$cfg = @{}
Get-Content $ConfigFile | ForEach-Object { if ($_ -match "^(\w+)=(.*)$") { $cfg[$Matches[1]] = $Matches[2] } }
$Server = "$($cfg.USER)@$($cfg.IP)"
$Key = $cfg.KEY
if (-not (Test-Path $Key)) { Fail "Key file not found: $Key  (delete deploy\server.local to enter it again)" }

# Windows OpenSSH refuses keys that other users can read.
try { icacls $Key /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null } catch { }

$SshArgs = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20")

function Invoke-Remote($command) {
    & ssh @SshArgs $Server $command
    if ($LASTEXITCODE -ne 0) { Fail "The server command failed (see messages above)." }
}

function Send-File($local, $remote) {
    & scp @SshArgs $local "${Server}:$remote"
    if ($LASTEXITCODE -ne 0) { Fail "Could not upload $local to the server." }
}
