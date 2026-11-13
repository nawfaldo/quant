@echo off
REM Start the web dashboard and make it reachable from anywhere.
REM
REM     tools\start_web.bat         production build: fast to load over the tunnel
REM     tools\start_web.bat dev     dev server instead: code edits show up live
REM
REM Opens three windows:
REM   chart stream        tools\chart_live_candle.py: forming Dukascopy / Binance
REM                       candles for whatever symbols the dashboard's charts show
REM   web                 the dashboard on http://127.0.0.1:5173 (pinned to IPv4:
REM                       cloudflared reads "localhost" as IPv6 ::1). By default it
REM                       is rebuilt (bun run build, a few seconds) and served by
REM                       node: ~37 files per page load instead of the dev server's
REM                       ~150, which is what made /chart take 6 s over the tunnel.
REM                       Code changes need this script (or the web window) rerun.
REM   cloudflare tunnel   the named tunnel "ariyanto" (~/.cloudflared/config.yml):
REM                       serves it at https://ariyanto.cloud
REM
REM The tunnel has NO password: anyone with the address can see the accounts and
REM positions. The address is fixed.
REM
REM The live trading stack (tools\start_live_trade.bat) is separate; the
REM dashboard only reads what it writes.

setlocal
set "ROOT=%~dp0.."
set "CLOUDFLARED=C:\Program Files (x86)\cloudflared\cloudflared.exe"

REM Restarted 5 s after it exits: without it the charts only move once a minute.
start "chart stream" /D "%ROOT%" cmd /k "for /l %%i in (0,0,1) do @(py tools\chart_live_candle.py & echo chart stream stopped, restarting in 5 s & timeout /t 5 /nobreak >nul)"
if /i "%~1"=="dev" (
	start "web" /D "%ROOT%\web" cmd /k bun run dev --host 127.0.0.1 --port 5173 --strictPort
) else (
	start "web" /D "%ROOT%\web" cmd /k "bun run build && set HOST=127.0.0.1&& set PORT=5173&& node build"
)

REM The tunnel needs the web server answering first. Wait up to a minute.
echo Waiting for the web server on http://localhost:5173 ...
set /a tries=0
:wait
curl -s -o nul http://127.0.0.1:5173/ && goto up
set /a tries+=1
if %tries% geq 60 (
	echo The web server did not start within a minute; see its window. Not starting the tunnel.
	exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait

:up
echo Web server is up. Starting the Cloudflare tunnel; its window shows the public address.
start "cloudflare tunnel" cmd /k ""%CLOUDFLARED%" tunnel run ariyanto"
endlocal
