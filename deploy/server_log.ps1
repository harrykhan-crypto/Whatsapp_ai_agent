# Show HARRY's live log from the server (Ctrl+C to stop).
. (Join-Path $PSScriptRoot "common.ps1")
& ssh @SshArgs $Server "journalctl -u harry -n 60 -f"
