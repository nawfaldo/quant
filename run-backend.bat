@echo off
REM Run the web backend (web API + embedded march engine) on port 8080.
REM Override the port:  run-backend.bat 8090
setlocal
cd /d "%~dp0web\backend"

if not "%~1"=="" set "PORT=%~1"
if "%PORT%"=="" set "PORT=8080"

echo Starting backend on port %PORT% ...
zig build run
