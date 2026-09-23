@echo off
REM Restart ONLY the Rust server, leaving the terminal, the feeds and the bridge
REM up.
REM
REM     tools\restart_live_server.bat
REM
REM WHY THIS EXISTS INSTEAD OF RE-RUNNING start_live_trade.bat. That launcher is
REM safe to re-run for stages 1 and 2 -- it skips an already-running terminal and
REM the feed script refuses a second Binance stream on the same table -- but
REM stage 4 starts `mt5\bridge.py` UNCONDITIONALLY and the bridge has no
REM single-instance guard. Re-running the launcher to pick up a rebuilt server
REM therefore leaves TWO bridges polling the same backend and executing the same
REM commands against the same account. Picking up a new binary is a routine
REM thing to want; duplicating the only component that can send an order is not.
REM
REM FLAT FIRST. The runtime holds no broker-side stops -- every exit is a market
REM order this process sends itself -- so an open position is unprotected for the
REM whole restart, which includes a warm-up replay of months of bars. This script
REM refuses to stop a server that has a position on, rather than leaving that to
REM be noticed afterwards.
setlocal

set "WORKSPACE=%~dp0.."
set "PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=py"
if "%PORT%"=="" set "PORT=4000"

cd /d "%WORKSPACE%"
if not exist "%WORKSPACE%\logs" mkdir "%WORKSPACE%\logs"
set "LOGFILE=%WORKSPACE%\logs\start_live_trade.log"
call :say "restarting the server only"

REM ------------------------------------------------------------------- flat?
REM Reads the same rows the bridge reconciles against. A non-zero exit means
REM either a position is open or the check itself could not run, and both stop
REM the restart: an unreadable database is not evidence of being flat.
REM `--timeout 0` checks ONCE. The other stages poll because they are waiting
REM for something to come up; a position that is open now is a reason to stop,
REM not something to stand around hoping resolves itself.
"%PYTHON%" "tools\live_stack_ready.py" flat --timeout 0
if errorlevel 1 (
  call :say "REFUSED: not flat, or the position check failed. Nothing was stopped."
  goto fail
)

REM ------------------------------------------------------------------- stop
REM The hosting `cmd /k` is killed with the process, not just the exe. Killing
REM `live_trade.exe` alone leaves its window sitting at a prompt, which looks
REM exactly like the blank window this whole thing is meant to stop producing.
call :say "stopping the running server"
REM POWERSHELL, NOT `wmic`. wmic.exe is deprecated and is no longer present on
REM current Windows 11 builds -- this machine included -- and a missing command
REM here fails quietly enough that the exe still dies and only the empty window
REM survives, which is the exact symptom this script exists to remove.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='live_trade.exe'\" ^| Select-Object -First 1).ParentProcessId"`) do (
  "%SystemRoot%\System32\taskkill.exe" /PID %%P /T /F >nul 2>&1
)
"%SystemRoot%\System32\taskkill.exe" /IM live_trade.exe /F >nul 2>&1
REM The linker cannot replace an exe that is still mapped, and the handle
REM outlives the process by a moment.
REM
REM `ping`, NOT `timeout`. timeout.exe reads the console directly and aborts with
REM "Input redirection is not supported" whenever this script is run with its
REM stdin redirected -- from a CI step, a wrapper, or an agent -- so the one
REM delay that protects the build would be skipped in exactly the automated runs
REM that have nobody watching.
"%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul

REM ------------------------------------------------------------------ build
REM Same reasoning as the launcher: a no-op when nothing changed, and the only
REM thing standing between an afternoon of edits and a stale binary trading
REM yesterday's logic.
call :say "building the server"
pushd "%WORKSPACE%\live_trade"
cargo build --release --bin live_trade
set "BUILD=%ERRORLEVEL%"
popd
if not "%BUILD%"=="0" (
  call :say "FAILED: cargo build exited %BUILD% -- the server is now STOPPED."
  goto fail
)

REM ------------------------------------------------------------------ start
call :say "starting the server on port %PORT%"
start "Live Server (port %PORT%)" /D "%WORKSPACE%\live_trade" cmd /k target\release\live_trade.exe
REM The bridge is still up and has been getting `WinError 10061` on every poll
REM since the stop; it recovers on its own once this answers.
"%PYTHON%" "tools\live_stack_ready.py" server --timeout 900 --port %PORT%
if errorlevel 1 goto fail

call :say "server is back up. terminal, feeds and bridge were left alone."
endlocal
exit /b 0

:fail
call :say "RESTART STOPPED. Read the window above before retrying."
pause
endlocal & exit /b 1

:say
echo [restart_live_server] %~1
>>"%LOGFILE%" echo %DATE% %TIME% %~1
exit /b 0
