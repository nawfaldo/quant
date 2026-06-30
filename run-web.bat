@echo off
REM Start backend (:8080) and frontend dev server in two separate windows.
setlocal
cd /d "%~dp0"

start "quant-backend"  cmd /k "cd /d "%~dp0web\backend" && zig build run"
start "quant-frontend" cmd /k "cd /d "%~dp0web\frontend" && bun dev"

echo Backend and frontend launched in separate windows.
echo   Backend : http://localhost:8080/health
echo   Frontend: the URL bun/vite prints (usually http://localhost:5173)
