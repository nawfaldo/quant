@echo off
REM Run the frontend dev server (Vite, hot reload). Talks to backend on :8080.
setlocal
cd /d "%~dp0web\frontend"

if not exist "node_modules" (
    echo Installing frontend dependencies...
    call bun install || exit /b 1
)

call bun dev
