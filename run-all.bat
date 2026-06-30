@echo off
REM One window, tmux-style: QuestDB + backend + frontend in split panes.
REM Uses Windows Terminal (wt.exe). Layout:
REM   +-----------+-----------+
REM   |           |  Backend  |   left  = QuestDB
REM   |  QuestDB  +-----------+   top-r  = backend  (:8080)
REM   |           |  Frontend |   bot-r  = frontend (:5173)
REM   +-----------+-----------+
setlocal
cd /d "%~dp0"

set "WT=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"
if not exist "%WT%" set "WT=wt.exe"

"%WT%" -w quant-stack ^
  new-tab     --title QuestDB  cmd /k "%~dp0run-questdb.bat" ^
  ; split-pane -V --title Backend  cmd /k "%~dp0run-backend.bat" ^
  ; split-pane -H --title Frontend cmd /k "%~dp0run-frontend.bat"

if errorlevel 1 (
    echo.
    echo Could not launch Windows Terminal ^(wt.exe^).
    echo Falling back to separate windows via run-web.bat ...
    call "%~dp0run-questdb.bat"
    call "%~dp0run-web.bat"
)
exit /b 0
