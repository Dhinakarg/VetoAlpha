# Start Autonomous Paper Trading Agent as a Persistent Detached Background Process on Windows
# Will continue running even if VSCode or PowerShell window is closed.

$WorkDir = "d:\Algo Trading"
$LogFile = "$WorkDir\logs\background_daemon.log"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "⚡ LAUNCHING PERSISTENT BACKGROUND TRADING DAEMON (DETACHED)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Work Directory:  $WorkDir"
Write-Host "Log Output File: $LogFile"
Write-Host "Mode:            ALPACA_PAPER_SUBMISSION"
Write-Host "==================================================================" -ForegroundColor Cyan

# Launch pythonw / py detached in hidden window style
$ArgList = "-3 scripts/run_live_crypto_paper.py --symbols BTC/USD ETH/USD SOL/USD --interval 30 --cycles 100000 --submit-alpaca-crypto"

Start-Process -FilePath "py" -ArgumentList $ArgList -WorkingDirectory $WorkDir -WindowStyle Hidden

Write-Host "`n✅ Daemon launched successfully in hidden background process!" -ForegroundColor Green
Write-Host "It will continue running in the background even if VSCode or this terminal is closed.`n"
Write-Host "To view real-time log activity:  Get-Content logs\crypto_run.jsonl -Tail 10 -Wait"
Write-Host "To stop the background daemon:   Stop-Process -Name python -Force`n"
