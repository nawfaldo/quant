@echo off
REM FundedNext's own minute bars, live: `tools\fundednext_stream_1m.py`.
REM
REM The 2026-10-18 FundedNext book signals nine markets off these tables
REM (xauusd us2000 us30 cadjpy nzdusd euraud usdchf chfjpy gbpaud) and checks
REM every candle against them (CFD_BROKER_HOURS), so this must run beside the
REM vendor feeds whenever the FundedNext account trades. It reads market data
REM only. A supervisor: any exit restarts it after 10 seconds.
setlocal
set "WORKSPACE=%~dp0.."
set "PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=py"

if /i "%~1"=="--supervise" goto supervise
start "FundedNext Minute Stream" cmd /k call "%~f0" --supervise
endlocal
exit /b 0

:supervise
cd /d "%WORKSPACE%"
:loop
echo [supervisor] starting tools\fundednext_stream_1m.py
"%PYTHON%" "tools\fundednext_stream_1m.py"
echo [supervisor] exited with %ERRORLEVEL%; restarting in 10s
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Start-Sleep -Seconds 10"
goto loop
