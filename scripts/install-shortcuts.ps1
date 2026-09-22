# UTF-8 with BOM for Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
foreach ($item in @(
  @{ Name = '启动错题本'; Action = 'start'; Icon = 'shell32.dll,24'; Description = '启动错题本并打开网页' },
  @{ Name = '关闭错题本'; Action = 'stop'; Icon = 'shell32.dll,27'; Description = '关闭错题本，保留所有数据、图片和备份' }
)) {
  $path = Join-Path $desktop ($item.Name + '.lnk')
  $shortcut = $shell.CreateShortcut($path)
  $shortcut.TargetPath = $powershell
  $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\desktop.ps1`" -Action $($item.Action)"
  $shortcut.WorkingDirectory = $root
  $shortcut.IconLocation = Join-Path "$env:SystemRoot\System32" $item.Icon
  $shortcut.Description = $item.Description
  $shortcut.WindowStyle = 7
  $shortcut.Save()
  Write-Output $path
}
