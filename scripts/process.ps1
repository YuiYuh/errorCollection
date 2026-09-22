# Only manage the Python server owned by this application on its dedicated port.
function Test-WrongBookProcess([int]$ProcessId) {
  $entry = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
  return ($entry -and $entry.Name -match '^python(w)?(\d+(\.\d+)*)?\.exe$' -and
    $entry.CommandLine -match '(?i)\buvicorn\s+app\.main:app\s' -and
    $entry.CommandLine -match '--port\s+8000(?:\s|$)')
}
