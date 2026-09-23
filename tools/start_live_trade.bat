@echo off
REM Start the whole live trading stack, in order, waiting for each stage to be
REM READY before starting the next.
REM
REM     tools\start_live_trade.bat
REM
REM     1  Exness terminal    terminal64.exe, logged into every account
REM     2  market data feeds  run_idk_market_feeds.bat (Dukascopy + Binance)
REM     3  server             the Rust live runtime on 127.0.0.1:4000
REM     4  MT5 bridge         mt5\bridge.py, which executes the orders
REM
REM READY, NOT STARTED, and the difference is the whole reason this file exists.
REM Every one of these binds, launches or prints something long before it can
REM serve the next stage: the terminal is up well before a login completes, the
REM server binds its port before its strategies are warm, and the bridge runs for
REM a while before its first heartbeat lands. Starting stage N+1 against a stage
REM N that is merely RUNNING produces a screen of errors that point at the wrong
REM component -- the bridge reporting `WinError 10061` when the real answer is
REM that the server is still replaying warm-up.
REM
REM So each stage is followed by `live_stack_ready.py`, which blocks on the same
REM signal the NEXT stage will use, and this script STOPS if one does not come
REM up. A half-started stack is worse than no stack: the server takes signals it
REM cannot execute, and the feed watchdog starts counting against a book nobody
REM is feeding.
REM
REM THE ORDER IS A DEPENDENCY CHAIN, NOT A PREFERENCE.
REM
REM   terminal before bridge   `bridge.py` attaches to a running terminal; it is
REM                            also what stage 1 verifies, so a bad login is
REM                            found before anything else is started.
REM   feeds before server      the runtime refuses to warm until every active
REM                            market has a completed minute, and its feed
REM                            watchdog starts counting the moment it does warm.
REM                            Feeding first means it never starts stale.
REM   server before bridge     the bridge polls the backend and gets
REM                            `WinError 10061` on every poll until it is up.
REM
REM CLOUDFLARE WARP MUST BE ON for stage 2 -- dukascopy.com is DNS-blocked on
REM this network and the feed daemon refuses to start rather than let it be
REM diagnosed from timeouts.
REM
REM Each stage gets its own window and keeps running after this script exits.
REM Closing this window does not stop them; each is stopped in its own.
setlocal

set "WORKSPACE=%~dp0.."
set "PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=py"
set "TERMINAL=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
if not "%MT5_TERMINAL%"=="" set "TERMINAL=%MT5_TERMINAL%"
if "%PORT%"=="" set "PORT=4000"

cd /d "%WORKSPACE%"
if not exist "%WORKSPACE%\logs" mkdir "%WORKSPACE%\logs"
set "LOGFILE=%WORKSPACE%\logs\start_live_trade.log"
call :say "starting the live stack"

REM ---------------------------------------------------------------- 1. terminal
REM Started only if it is not already up: a second `terminal64.exe` on the same
REM installation fights the first one for the same account session.
REM BOTH BY FULL PATH. `find` is also a Git-for-Windows coreutil, and with
REM Git's usr\bin ahead on PATH this pipe becomes `find: '/I': No such file or
REM directory` -- which cmd reads as "not found", so the terminal is launched a
REM SECOND time and the two fight over the same account session. It only
REM misbehaves when the launcher is started from a shell with that PATH, which
REM is exactly the kind of bug that works when you test it by double-clicking
REM and fails when something else runs it.
"%SystemRoot%\System32\tasklist.exe" /FI "IMAGENAME eq terminal64.exe" 2>nul | "%SystemRoot%\System32\find.exe" /I "terminal64.exe" >nul
if errorlevel 1 (
  if not exist "%TERMINAL%" (
    call :say "FAILED: no terminal at %TERMINAL% -- set MT5_TERMINAL to its path"
    goto fail
  )
  call :say "1/4 launching %TERMINAL%"
  start "" "%TERMINAL%"
) else (
  call :say "1/4 terminal64.exe is already running"
)
REM Generous: a cold terminal start includes the login and, on the first run
REM after an update, a silent upgrade.
"%PYTHON%" "tools\live_stack_ready.py" terminal --timeout 300
if errorlevel 1 goto fail

REM ------------------------------------------------------------------- 2. feeds
REM Its own supervisor: the window it opens restarts the daemon if it exits, so
REM this only has to start it once. Already-running is fine -- the feed script
REM refuses to start a second copy of the Binance stream on the same table.
call :say "2/4 starting market data feeds"
call "%WORKSPACE%\tools\run_idk_market_feeds.bat"
REM The catch-up runs before the first health write, and it is sized by how long
REM the tables have been stale -- minutes after a weekend.
"%PYTHON%" "tools\live_stack_ready.py" feeds --timeout 600
if errorlevel 1 goto fail

REM ------------------------------------------------------------------ 3. server
REM BUILT, NOT JUST RUN. `cargo build` is a no-op when nothing changed and takes
REM seconds; skipping it is how a stale `live_trade.exe` ends up trading yesterday's
REM logic after an afternoon of edits. A build failure stops the launch here,
REM before anything is executing.
call :say "3/4 building the server"
pushd "%WORKSPACE%\live_trade"
cargo build --release --bin live_trade
set "BUILD=%ERRORLEVEL%"
popd
if not "%BUILD%"=="0" (
  call :say "FAILED: cargo build exited %BUILD%"
  goto fail
)
call :say "3/4 starting the server on port %PORT%"
REM `start /D` for the working directory, NOT a nested `cd /d "..."`:
REM quotes inside an already-quoted `cmd /k` string do not nest, and the
REM window silently opens in the wrong place or not at all. PORT is
REM inherited from this shell, so it does not need passing either.
start "Live Server (port %PORT%)" /D "%WORKSPACE%\live_trade" cmd /k target\release\live_trade.exe
REM A cold start replays every active sleeve's warm-up before it answers, and
REM that is months of bars across seven markets.
"%PYTHON%" "tools\live_stack_ready.py" server --timeout 900 --port %PORT%
if errorlevel 1 goto fail

REM ------------------------------------------------------------------ 4. bridge
REM THE ONLY STAGE THAT CAN SEND AN ORDER. Everything before it reads or
REM decides; this is what turns a decision into a fill, which is why it is last
REM and why every gate above has to have passed before it starts.
call :say "4/4 starting the MT5 execution bridge"
start "MT5 Execution Bridge" /D "%WORKSPACE%" cmd /k "%PYTHON%" mt5\bridge.py
"%PYTHON%" "tools\live_stack_ready.py" bridge --timeout 300
if errorlevel 1 goto fail

call :say "LIVE. terminal, feeds, server and bridge are all up."
call :say "Each runs in its own window; closing this one changes nothing."
endlocal
exit /b 0

:fail
call :say "LAUNCH STOPPED. The stage above did not come up; the stages after it"
call :say "were not started. Read that window before retrying."
pause
endlocal & exit /b 1

REM Echoed AND appended, because the console is what the operator watches and the
REM file is what is still there tomorrow.
:say
echo [start_live_trade] %~1
>>"%LOGFILE%" echo %DATE% %TIME% %~1
exit /b 0
