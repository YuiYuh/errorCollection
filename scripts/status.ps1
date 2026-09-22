# ASCII-only on purpose; see the note in start.ps1.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pidFile = Join-Path $root 'run\wrongbook.pid'
$running = $false
if (Test-Path $pidFile) {
  $serverPid = [int](Get-Content $pidFile)
  if (Get-Process -Id $serverPid -ErrorAction SilentlyContinue) {
    $running = $true
    Write-Host "Process: running (PID $serverPid)"
  }
}
if (-not $running) { Write-Host 'Process: not running' }
try {
  $status = Invoke-RestMethod http://127.0.0.1:8000/api/system/status -TimeoutSec 10
  Write-Host 'Backend: responding on http://127.0.0.1:8000'
  Write-Host ("AI model: {0} (key configured: {1})" -f $status.ai_model, $status.api_key_configured)
  Write-Host ("Disk ok: {0}  Tailscale: {1}  Pending: {2}  Failed: {3}  Due reviews: {4}" -f $status.disk_ok, $status.tailscale_running, $status.pending_tasks, $status.failed_tasks, $status.today_reviews)
  # Keep launcher output ASCII-only: Windows PowerShell 5.1 can decode the
  # UTF-8 API message as the active legacy code page and print mojibake.
  if (-not $status.disk_ok) {
    $freeMb = [math]::Round(($status.disk_free_bytes / 1MB), 0)
    Write-Host ("WARNING: low disk space ({0} MB free; need at least 500 MB)" -f $freeMb)
  }
  if (-not $status.tailscale_running) { Write-Host 'WARNING: Tailscale is not connected' }
} catch {
  Write-Host 'Backend: not responding - run scripts\start.ps1'
}
