# Keep this file ASCII-only (see the note in start.ps1 about PowerShell 5.1 and
# UTF-8 comments). Stopping the service never deletes data, images or backups.
$root = Split-Path -Parent $PSScriptRoot
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'process.ps1')
$pidFile = Join-Path $root 'run\wrongbook.pid'
$stopped = $false
if (Test-Path $pidFile) {
  $serverPid = 0
  [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$serverPid) | Out-Null
  if ($serverPid -gt 0 -and (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
    if (Test-WrongBookProcess $serverPid) {
      Stop-Process -Id $serverPid -Force -ErrorAction Stop
      $stopped = $true
    }
  }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
# If an older launcher left a stale PID, stop the process that actually owns
# WrongBook's dedicated port. This keeps one-click close reliable across upgrades.
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  if ($listener.OwningProcess -gt 0) {
    if (-not (Test-WrongBookProcess $listener.OwningProcess)) {
      throw 'Port 8000 belongs to another application; it was not stopped.'
    }
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
    $stopped = $true
  }
}
Start-Sleep -Milliseconds 300
Write-Host 'WrongBook stopped; database, images and backups preserved.'
