@echo off
setlocal
set "ROOT=%~dp0"
echo Stopping Portable AI...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = [IO.Path]::GetFullPath('%~dp0'); $pidFile = Join-Path $root 'data\server.pid'; $killed = @();" ^
  "$scriptPath = Join-Path $root 'app\server.py';" ^
  "if (Test-Path -LiteralPath $pidFile) { $srv = [int](Get-Content -LiteralPath $pidFile -Raw);" ^
  "  $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $srv) -ErrorAction SilentlyContinue;" ^
  "  $cmd = if ($p) { [string]$p.CommandLine } else { '' };" ^
  "  if ($p -and $cmd.IndexOf($scriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) { taskkill /T /F /PID $srv *> $null; $killed += $srv } }" ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'llama-server.exe' -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } |" ^
  "  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed += $_.ProcessId };" ^
  "Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue;" ^
  "if ($killed.Count) { Write-Host ('Stopped ' + ($killed -join ', ')) } else { Write-Host 'Nothing was running.' }"
endlocal
