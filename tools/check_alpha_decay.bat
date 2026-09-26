@echo off
rem Alpha-decay check for the canon book: is each sleeve still working?
rem
rem   check_alpha_decay.bat                  shows everything: canon and the jp225
rem                                          case on both splits, every number,
rem                                          then the self-test (about 1-2 minutes)
rem   check_alpha_decay.bat --case jp225     any sandbox/alpha_decay.py flags run directly

setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Alpha decay check
cd /d "%~dp0.."

if "%~1"=="" (
    py -m sandbox.alpha_decay --full
) else (
    py -m sandbox.alpha_decay %*
)
echo.
pause
endlocal
