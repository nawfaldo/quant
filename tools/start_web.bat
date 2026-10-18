@echo off
REM Start the web dashboard and make it reachable from anywhere.
REM
REM     tools\start_web.bat
REM
REM Opens three windows:
REM   chart stream        tools\chart_live_candle.py: forming Dukascopy / Binance
REM                       candles for whatever symbols the dashboard's charts show
REM   web                 the SvelteKit dev server on http://127.0.0.1:5173 (pinned
REM                       to IPv4: cloudflared reads "localhost" as IPv6 ::1)
REM   cloudflare tunnel   a Cloudflare quick tunnel to it; its window prints the
REM                       public https://<random>.trycloudflare.com address
REM
REM The tunnel has NO password: anyone with the address can see the accounts and
REM positions. The address changes every time the tunnel restarts.
REM
REM The live trading stack (tools\start_live_trade.bat) is separate; the
REM dashboard only reads what it writes.

setlocal
set "ROOT=%~dp0.."
set "CLOUDFLARED=C:\Program Files (x86)\cloudflared\cloudflared.exe"

start "chart stream" /D "%ROOT%" cmd /k py tools\chart_live_candle.py
start "web" /D "%ROOT%\web" cmd /k bun run dev --host 127.0.0.1 --port 5173 --strictPort

REM The tunnel needs the dev server answering first. Wait up to a minute.
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
start "cloudflare tunnel" cmd /k ""%CLOUDFLARED%" tunnel --url http://127.0.0.1:5173"
endlocal
