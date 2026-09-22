# WrongBook launcher. Keep this file ASCII-only: Windows PowerShell 5.1 reads
# UTF-8 files as ANSI/GBK on Chinese systems, and an odd number of bytes in a
# comment can swallow the following newline and comment out real code.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot 'process.ps1')
$pidFile = Join-Path $root 'run\wrongbook.pid'
if (Test-Path $pidFile) {
  $oldPid = 0
  [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$oldPid) | Out-Null
  $oldProcess = if ($oldPid -gt 0) { Get-Process -Id $oldPid -ErrorAction SilentlyContinue } else { $null }
  if ($oldProcess -and (Test-WrongBookProcess $oldPid)) {
    try {
      Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 2 | Out-Null
      Write-Host "WrongBook already running (PID $oldPid)"
      exit 0
    } catch {
      throw 'WrongBook process exists but is not ready. Wait or close it before restarting.'
    }
  }
  # A launcher can exit before an older version wrote its child PID. Never let
  # a stale PID file prevent a fresh start.
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force (Join-Path $root 'run') | Out-Null
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  try {
    Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 2 | Out-Null
    throw 'WrongBook is already responding on port 8000, but its PID file was missing.'
  } catch {
    if ($_.Exception.Message -like 'WrongBook is already responding*') { throw }
    throw 'Port 8000 is already in use.'
  }
}

$pyArgs = @()
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and (Test-Path $pythonCmd.Source)) { $py = $pythonCmd.Source }
else { $pyCmd = Get-Command py -ErrorAction SilentlyContinue; if (-not $pyCmd) { throw 'Python was not found.' }; $py = $pyCmd.Source; $pyArgs = @('-3') }
& $py @pyArgs -c "import fastapi,uvicorn,pydantic_settings,httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Installing backend dependencies...'
  & $py @pyArgs -m pip install -e . --no-build-isolation
  if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies could not be installed.' }
}

$serverArgs = '-m uvicorn app.main:app --host 127.0.0.1 --port 8000'
if ($pyArgs.Count -gt 0) { $serverArgs = ($pyArgs -join ' ') + ' ' + $serverArgs }

# pythonw.exe has no console, so a stray Ctrl+C or a closing console cannot kill
# the service. Logs still go to files thanks to the redirection below.
$runner = $py
$pythonw = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
if (Test-Path $pythonw) { $runner = $pythonw }

# Log to disk so an unexpected exit leaves evidence behind.
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$outLog = Join-Path $logDir 'uvicorn.out.log'
$errLog = Join-Path $logDir 'uvicorn.err.log'
foreach ($logFile in @($outLog, $errLog)) {
  if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 5MB)) { Remove-Item $logFile -Force }
}
Add-Content -Path $errLog -Value ("===== " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " starting =====")

# Start the actual Python process and record its PID. Recording an intermediate
# cmd/WMI launcher made the PID file stale after the launcher exited.
$argumentList = @()
if ($pyArgs.Count -gt 0) { $argumentList += $pyArgs }
$argumentList += @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000')
$process = Start-Process -FilePath $runner -ArgumentList $argumentList -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
$pidToRecord = $process.Id
$pidToRecord | Set-Content $pidFile

# Keep the phone URL working: if Tailscale is installed, publish the local port
# over HTTPS in the background so the mapping survives closing this window.
if ((Get-Command tailscale -ErrorAction SilentlyContinue) -and ($env:TAILSCALE_SERVE -ne 'false')) {
  $hostname = ''
  $envFile = Join-Path $root '.env'
  if (Test-Path $envFile) {
    $hostnameLine = Select-String -Path $envFile -Pattern '^TAILSCALE_HOSTNAME=' | Select-Object -First 1
    if ($hostnameLine) { $hostname = $hostnameLine.Line.Substring('TAILSCALE_HOSTNAME='.Length).Trim() }
  }
  try {
    $serveState = (tailscale serve status 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale CLI status requires elevated access.' }
    if ($serveState -notmatch 'proxy http://127\.0\.0\.1:8000') {
      tailscale serve --bg --https=443 http://127.0.0.1:8000 | Out-Null
      Write-Host 'Tailscale HTTPS mapping configured (background).'
    } else {
      Write-Host 'Tailscale HTTPS mapping already active.'
    }
  } catch {
    $service = (sc.exe query Tailscale 2>&1 | Out-String)
    if ($service -match 'RUNNING' -and $hostname) {
      Write-Host ("Tailscale service is running. Phone HTTPS address: https://{0}" -f $hostname)
    } else {
      Write-Host 'Tailscale HTTPS could not be verified. Start Tailscale, then run this script again.'
    }
  }
}
Start-Sleep 3
try {
  Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 10 | Out-Null
  Write-Host 'WrongBook started: http://127.0.0.1:8000'
} catch {
  Write-Host 'WrongBook failed to answer on http://127.0.0.1:8000 - see logs\uvicorn.err.log'
  exit 1
}
