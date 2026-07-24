@echo off
setlocal

set "SERVER_DIR=%~dp0server"
set "SERVER_EXE=%~dp0server\target\debug\backend_rust.exe"

echo Stopping any stale Quant backend...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$target = [IO.Path]::GetFullPath($env:SERVER_EXE); Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'backend_rust.exe' -and $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -eq $target } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Stopping any stale Quant web server on port 5173...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }"

start "Quant Server" cmd /k "cd /d ""%SERVER_DIR%"" && cargo run"
start "Quant Web" cmd /k "cd /d ""%~dp0web"" && bun run dev"

endlocal
