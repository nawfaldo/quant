@echo off
REM Start QuestDB (the market-data database every component reads from).
REM
REM QuestDB is a separate download, NOT part of this repo. One-time setup:
REM   1) Download the Windows bundle (with JVM) from https://questdb.io/download/
REM   2) Extract it to  C:\questdb   (so C:\questdb\questdb.exe exists), OR set
REM      the QUESTDB_HOME environment variable to wherever you extracted it.
REM   3) Run this script. Web console: http://localhost:9000
REM
REM Data is stored in  <repo>\qdbroot  (created on first run). We cd to the repo
REM root so the relative "qdbroot" data directory is always the same one your
REM imported tables live in, no matter where this script is launched from.
setlocal
cd /d "%~dp0"

set "FOUND="
for %%D in ("%QUESTDB_HOME%" "C:\questdb" "%USERPROFILE%\questdb" "%~dp0questdb") do (
    if not "%%~D"=="" (
        if exist "%%~D\questdb.exe"      set "FOUND=%%~D\questdb.exe"
        if exist "%%~D\bin\questdb.exe"  set "FOUND=%%~D\bin\questdb.exe"
    )
)

if defined FOUND (
    echo Starting QuestDB ^(foreground^): "%FOUND%"
    echo QuestDB web console: http://localhost:9000   ^(Ctrl+C here to stop^)
    REM Run in the FOREGROUND with no subcommand. The "start" subcommand writes a
    REM PID/lock inside the install dir (C:\questdb, a protected root path) and so
    REM demands Administrator -> "ACCESS DENIED". Plain foreground mode needs no
    REM admin, uses ./qdbroot relative to this repo root (we cd'd here above), and
    REM keeps the window/pane alive showing server logs.
    "%FOUND%"
    exit /b %errorlevel%
)

echo QuestDB was not found in any of:
echo   %%QUESTDB_HOME%%, C:\questdb, %USERPROFILE%\questdb, %~dp0questdb
echo.
echo Download it (Windows bundle WITH JVM) from:
echo   https://questdb.io/download/
echo Extract so that  C:\questdb\questdb.exe  exists, then re-run this script.
exit /b 1
