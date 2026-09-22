# WrongBook verification script. Keep this file ASCII-only for PowerShell 5.1.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$command = Get-Command python -ErrorAction SilentlyContinue
if (-not $command) { $command = Get-Command py -ErrorAction SilentlyContinue }
if (-not $command) { throw 'Python was not found.' }
$py = $command.Source
Write-Host 'Running backend tests...'
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
Write-Host 'Checking Python compilation...'
& $py -m compileall -q app
if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
Write-Host 'Building frontend...'
Push-Location (Join-Path $root 'frontend')
try {
  npm.cmd test
  if ($LASTEXITCODE -ne 0) { throw 'Frontend content tests failed.' }
  npm.cmd run build
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Host 'Checking running service...'
try {
  $health = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 10
  if ($health.status -ne 'ok') { throw 'Health endpoint did not return ok.' }
} catch { throw 'Service is not responding. Run scripts\start.ps1 first.' }
Write-Host 'WrongBook verification passed.'
