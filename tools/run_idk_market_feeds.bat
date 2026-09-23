@echo off
REM Market data feeds for the `idk` live environment.
REM
REM ONE SCRIPT, ONE WINDOW. `idk_market_live_data_feeds.py` feeds every canon
REM table and supervises both legs itself -- it polls Dukascopy on an interval
REM and keeps the Binance websocket alive, restarting it if it dies.
REM
REM     dukascopy   audusd eurjpy gbpjpy jp225 ukoil usdjpy
REM     binance     ethusd (ETHUSDT)
REM
REM THAT LIST IS NOT AUTHORITATIVE AND IS NOT CHECKED AGAINST ANYTHING HERE.
REM The script cross-checks what it feeds against `exness_live_book` -- the same
REM registry the live activation copies its rows from -- and REFUSES to start if
REM a canon market has no feed. Trust its startup log over this comment.
REM
REM DO NOT ALSO START `binance_stream_1m.py` BY HAND. The script owns that
REM process now. Two streamers on ETHUSDT write the same table over two
REM websockets; the Parquet writer replaces a row at a timestamp it already
REM holds so nothing corrupts, but it is a wasted connection and two things to
REM debug instead of one.
REM
REM CLOUDFLARE WARP MUST BE ON. dukascopy.com is DNS-blocked on this network and
REM the script refuses to start rather than let it be diagnosed from timeouts.
REM
REM ------------------------------------------------------------------------
REM WHAT IS NOT FED, AND WHY, so a reader looking for one of these finds the
REM reason rather than an omission. The script logs the same list at startup.
REM
REM   nq       barred as a symbol 2026-09-03. Its level-two feed and `nq_1m`
REM            always came over Bookmap and their own importers, never here.
REM   xalusd   `xalusd:gated_fade` left the book 2026-08-29. It was the ONE
REM            sleeve whose signal came from the broker, because Dukascopy
REM            carries no aluminium at all (its metals list is COPPER, XPD,
REM            XPT) and Binance is a crypto exchange. With it gone there is no
REM            Exness window to start, and the operator's rule -- no sleeve
REM            decides on broker prices -- holds without an exception.
REM   btc      fed until 2026-09-03 because `ethusd:idio_break` read it as a
REM            benchmark. That cell left with the decay screen and nothing in
REM            the live runtime reads `btc_1m` now. If a benchmarked sleeve is
REM            ever seated again the registry reports its benchmark market too,
REM            so the script's preflight fails rather than the sleeve silently
REM            never firing.
REM   hk50     dropped 2026-09-07 on data quality: 6% of weekdays carried no
REM            30-minute bar at all and only 70% of its entries were
REM            repriceable, so a third of its trades were scored at a constant
REM            spread while the rest of the book paid measured costs.
REM   gbpusd   `gbpusd:obv_break` dropped 2026-09-03 by the decay screen.
REM   uk100    `uk100:gated_fade` dropped 2026-09-03 by the decay screen.
REM
REM Re-deciding the canon on broker bars instead of the vendor's was measured at
REM -150pp of return (+630.8% -> +506.0%) and -1.3pp of drawdown, with
REM `ukoil:xma_cross` alone about half of it. That is why the vendor tables are
REM the ones that have to be current.
REM
REM ------------------------------------------------------------------------
REM THIS FILE IS A SUPERVISOR, NOT A LAUNCHER, and that is the whole point of
REM it. It used to `start` the script once in a `-NoExit` PowerShell window, so
REM the process exiting left a window that still looked alive with a prompt in
REM it. On 2026-09-08 the feed took a stray console interrupt at 12:48:18Z, and
REM because nothing was watching the window nothing restarted it and nobody
REM knew until that night.
REM
REM EXIT CODES ARE THE INTERFACE. They are declared in the script beside the
REM reasons:
REM
REM   0  the operator pressed Ctrl+C twice -- stay down, that was deliberate
REM   3  the canon needs a market nothing here feeds -- a book change, and
REM      restarting cannot fix it; stay down and make it read
REM   1  preflight failed on something that recovers (Warp/DNS), or the script
REM      crashed -- wait a minute and try again
REM   4  the stall watchdog fired -- restart at once; the restart's catch-up is
REM      what gets bars flowing before the server's 180s watchdog flattens
REM
REM Anything unrecognised restarts, because a feed that is down is the one
REM state this must never settle into quietly.
REM ------------------------------------------------------------------------
setlocal

set "WORKSPACE=%~dp0.."
set "PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=py"

if /i "%~1"=="--supervise" goto supervise

start "IDK Vendor Feeds (Dukascopy + Binance)" cmd /k call "%~f0" --supervise
endlocal
exit /b 0

:supervise
cd /d "%WORKSPACE%"
if not exist "%WORKSPACE%\logs" mkdir "%WORKSPACE%\logs"
set "LOGFILE=%WORKSPACE%\logs\idk_market_feeds_supervisor.log"

:loop
call :say "starting tools\idk_market_live_data_feeds.py"
"%PYTHON%" "tools\idk_market_live_data_feeds.py"
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto down
if "%CODE%"=="3" goto down
set "WAIT=10"
if "%CODE%"=="1" set "WAIT=60"
call :say "exited with %CODE%; restarting in %WAIT%s"
REM NOT `timeout`, WHICH FAILS TWO WAYS AND BOTH END IN A RESTART STORM. It is
REM also a Git-for-Windows coreutil, so with Git's usr\bin ahead on PATH the
REM wait becomes "invalid time interval"; and the real one refuses to run at
REM all -- "Input redirection is not supported" -- whenever stdin is not a
REM console, which is every scheduled or piped invocation. Either way the loop
REM respawns with no delay against whatever the feed was already failing on.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Start-Sleep -Seconds %WAIT%"
goto loop

:down
call :say "exited with %CODE%; staying down -- read the lines above before restarting"
pause
REM ONE LINE, BECAUSE `endlocal` DISCARDS %CODE%. Split across two, the exit
REM reads an undefined variable and reports success over a feed that is down.
endlocal & exit /b %CODE%

REM Echoed AND appended, because the console is what the operator watches and
REM the file is what is still there the next morning. The script keeps its own
REM `logs\idk_market_feeds-<day>.log` for the same reason.
:say
echo [supervisor] %~1
>>"%LOGFILE%" echo %DATE% %TIME% [supervisor] %~1
exit /b 0
