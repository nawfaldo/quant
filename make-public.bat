@echo off
setlocal

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo cloudflared was not found in PATH.
    echo Install cloudflared, then run this file again.
    pause
    exit /b 1
)

echo Creating a public tunnel to the web app at http://localhost:5173...
echo Keep this window open and use the trycloudflare.com URL shown below.
echo.
cloudflared tunnel --url http://localhost:5173

if errorlevel 1 (
    echo.
    echo The tunnel stopped with an error. Make sure the web app is running.
    pause
)

endlocal
