# UTF-8 with BOM for Windows PowerShell 5.1.
param(
  [ValidateSet('start', 'stop')][string]$Action = 'start',
  [switch]$NoBrowser,
  [switch]$NoNotification
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$mutex = New-Object System.Threading.Mutex($false, 'Local\WrongBookDesktopControl')
$locked = $false
$code = 0
try {
  try { $locked = $mutex.WaitOne(0) }
  catch [System.Threading.AbandonedMutexException] { $locked = $true }
  if (-not $locked) {
    if (-not $NoNotification) {
      $shell = New-Object -ComObject WScript.Shell
      $shell.Popup('正在启动或关闭，请稍等片刻再试。', 5, '错题本', 64) | Out-Null
    }
    exit 0
  }
  Set-Location $root
  $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $logDir = Join-Path $root 'logs'
  New-Item -ItemType Directory -Force $logDir | Out-Null
  $scriptPath = Join-Path $PSScriptRoot ($Action + '.ps1')
  # Wait for the launcher only. Capturing a pipeline (or Start-Process -Wait)
  # can wait forever for the background server that inherits process handles.
  $launcher = Start-Process -FilePath $powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'desktop-control.out.log') -RedirectStandardError (Join-Path $logDir 'desktop-control.err.log')
  try {
    $processHandle = $launcher.Handle
    if (-not $launcher.WaitForExit(120000)) {
      $launcher.Kill()
      throw 'Control script timed out. See logs/desktop-control.err.log.'
    }
    if ($launcher.ExitCode -ne 0) { throw "Control script failed ($($launcher.ExitCode)). See logs/desktop-control.err.log." }
  } finally { $launcher.Dispose() }
  if ($Action -eq 'start') {
    $health = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 10
    if ($health.status -ne 'ok') { throw 'Health check failed.' }
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/' }
    $message = '错题本已启动，网页已打开。电脑运行期间，手机也可通过原来的地址访问。'
  } else {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw 'Port 8000 is still listening.' }
    $message = '错题本已关闭，数据库、图片和备份均已保留。现在可以关机。'
  }
  Write-Output "WrongBook desktop $Action succeeded."
  if (-not $NoNotification) {
    $shell = New-Object -ComObject WScript.Shell
    $shell.Popup($message, 6, '错题本', 64) | Out-Null
  }
} catch {
  $code = 1
  New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
  Add-Content -LiteralPath (Join-Path $root 'logs\desktop.log') -Encoding UTF8 -Value ("$(Get-Date -Format s) $Action failed: $($_.Exception.Message)")
  Write-Output "WrongBook desktop $Action failed. See logs\desktop.log."
  if (-not $NoNotification) {
    $shell = New-Object -ComObject WScript.Shell
    $shell.Popup(('操作未成功，请查看 {0}。请勿删除数据文件。' -f (Join-Path $root 'logs\desktop.log')), 0, '错题本', 16) | Out-Null
  }
} finally {
  if ($locked) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}
exit $code
