@echo off
REM Start the whole live trading stack, in order, waiting for each stage to be
REM READY before starting the next.
REM
REM     tools\start_live_trade.bat
REM
REM     0  the books          parity gate, release build, live rows on both canons
REM     1  MT5 terminals      Exness and FundedNext terminal64.exe, one login each
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
REM   book before everything   nothing may start on logic the parity gate has
REM                            not just proved, and the feed daemon checks what
REM                            it feeds against the BUILT `exness_live_book` --
REM                            a stale binary names the old book's markets.
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
REM diagnosed from timeouts. Binance (ETHUSD, BTC) needs it too.
REM
REM Each stage gets its own window and keeps running after this script exits.
REM Closing this window does not stop them; each is stopped in its own.
setlocal

set "WORKSPACE=%~dp0.."
set "PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=py"
set "TERMINAL=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
if not "%MT5_TERMINAL%"=="" set "TERMINAL=%MT5_TERMINAL%"
REM TWO ACCOUNTS, TWO TERMINALS since 2026-10-14. A terminal holds one login, so
REM the FundedNext account runs in its own installation.
set "FN_TERMINAL=C:\Program Files\FundedNext MT5 Terminal\terminal64.exe"
if not "%MT5_FN_TERMINAL%"=="" set "FN_TERMINAL=%MT5_FN_TERMINAL%"
if "%PORT%"=="" set "PORT=4000"

cd /d "%WORKSPACE%"
if not exist "%WORKSPACE%\logs" mkdir "%WORKSPACE%\logs"
set "LOGFILE=%WORKSPACE%\logs\start_live_trade.log"
call :say "starting the live stack"

REM -------------------------------------------------------------------- 0. book
REM THE PARITY GATE FIRST. Live must trade exactly what the sandbox backtested:
REM every sleeve's Rust port is replayed against its Python trade log
REM (`parity.rs`, fixtures from `_export_parity.py` and `tt_book.py parity`) and
REM the book's own membership, markets and cost tables are checked. A single
REM trade that differs -- entry bar, side, fill, stop or exit -- stops the launch
REM here, before anything can place an order on logic the sandbox never ran.
call :say "0/4 parity gate: live strategies against their sandbox trade logs"
pushd "%WORKSPACE%\live_trade"
cargo test --lib -- oct_12_2026 oct_13_2026 strategies::cfd::
set "PARITY=%ERRORLEVEL%"
popd
if not "%PARITY%"=="0" (
  call :say "FAILED: the live book no longer reproduces the sandbox (cargo test exited %PARITY%)"
  goto fail
)
REM BUILT, NOT JUST RUN, AND EVERY BINARY. `cargo build` is a no-op when nothing
REM changed and takes seconds; skipping it is how a stale `live_trade.exe` ends
REM up trading yesterday's logic after an afternoon of edits. `--bins` because
REM the feed daemon (stage 2) and the switch below both ask the built
REM `exness_live_book` which markets and rows the book has.
call :say "0/4 building the server and the book registry"
pushd "%WORKSPACE%\live_trade"
cargo build --release --bins
set "BUILD=%ERRORLEVEL%"
popd
if not "%BUILD%"=="0" (
  call :say "FAILED: cargo build exited %BUILD%"
  goto fail
)
REM THE ACCOUNT'S LIVE ROWS ON THE CANON. A no-op when they already are, so a
REM restart keeps every row's `live_started_at`. Otherwise it backs up app.db,
REM rewrites the rows from the registry and clears the cost rules -- and it
REM REFUSES while any position is open or any command unsettled, because
REM dropping a sleeve that still holds a position orphans it.
call :say "0/4 putting the live account on the canon book"
"%PYTHON%" "tools\go_live_canon_25.py"
if errorlevel 1 (
  call :say "FAILED: the live account could not be put on the canon book"
  goto fail
)
REM THE FUNDEDNEXT ACCOUNT ON ITS OWN BOOK: created on first run, then the same
REM idempotent switch, refusing while that account holds a position.
call :say "0/4 putting the FundedNext account on the FundedNext book"
"%PYTHON%" "tools\go_live_fundednext.py"
if errorlevel 1 (
  call :say "FAILED: the FundedNext account could not be put on its book"
  goto fail
)
REM ACCOUNT ID -> TERMINAL, read by bridge.py (stage 1's check and stage 4) from
REM MT5_TERMINALS, so both resolve each account to its own installation.
set "MT5_TERMINALS="
REM Through a FILE, not `for /f`: a `for /f` command whose first and last
REM tokens are both quoted loses its outer quotes in cmd ("The filename,
REM directory name, or volume label syntax is incorrect").
set "TERMINAL_MAP=%WORKSPACE%\logs\mt5_terminals.txt"
if exist "%TERMINAL_MAP%" del "%TERMINAL_MAP%"
"%PYTHON%" "tools\live_terminals.py" "%TERMINAL%" "%FN_TERMINAL%" > "%TERMINAL_MAP%"
if errorlevel 1 (
  call :say "FAILED: tools\live_terminals.py could not map the accounts"
  goto fail
)
set /p MT5_TERMINALS=<"%TERMINAL_MAP%"
if "%MT5_TERMINALS%"=="" (
  call :say "FAILED: could not map the accounts to their terminals"
  goto fail
)
call :say "0/4 terminals: %MT5_TERMINALS%"

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
call :terminal "%TERMINAL%"
if errorlevel 1 goto fail
call :terminal "%FN_TERMINAL%"
if errorlevel 1 goto fail
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
REM Gated and built in stage 0, so this starts exactly the binary that passed.
call :say "3/4 starting the server on port %PORT%"
REM `start /D` for the working directory, NOT a nested `cd /d "..."`:
REM quotes inside an already-quoted `cmd /k` string do not nest, and the
REM window silently opens in the wrong place or not at all. PORT is
REM inherited from this shell, so it does not need passing either.
start "Live Server (port %PORT%)" /D "%WORKSPACE%\live_trade" cmd /k target\release\live_trade.exe
REM A cold start replays every active sleeve's warm-up before it answers, and
REM that is months of bars across eleven markets (the 2026-09-30 book trades
REM DE40, UK100, USOIL and BTC as well, and 5- and 15-minute sleeves replay many
REM more candles than 30-minute ones).
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

call :say "LIVE. both terminals, feeds, server and bridge are all up."
call :say "Each runs in its own window; closing this one changes nothing."
endlocal
exit /b 0

:fail
call :say "LAUNCH STOPPED. The stage above did not come up; the stages after it"
call :say "were not started. Read that window before retrying."
pause
endlocal & exit /b 1

REM Starts the terminal at %~1 unless a terminal64.exe FROM THAT PATH is
REM already running. Matched by path, not by image name: both installations run
REM a terminal64.exe, and a second copy of the same one fights the first for
REM its account session.
:terminal
if not exist "%~1" (
  call :say "FAILED: no terminal at %~1 -- set MT5_TERMINAL / MT5_FN_TERMINAL"
  exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "if (Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '%~1' }) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  call :say "1/4 launching %~1"
  start "" "%~1"
) else (
  call :say "1/4 already running: %~1"
)
exit /b 0

REM Echoed AND appended, because the console is what the operator watches and the
REM file is what is still there tomorrow.
:say
echo [start_live_trade] %~1
>>"%LOGFILE%" echo %DATE% %TIME% %~1
exit /b 0
