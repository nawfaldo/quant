@echo off
:: Change directory to the folder containing this batch script
cd /d "%~dp0"

echo ==================================================
echo Starting InsiderFinance GEX Scraper (Continuous)
echo Symbols: SPY, QQQ
echo ==================================================
echo.

:: Path to the virtual environment python interpreter
set VENV_PYTHON=.\.venv\Scripts\python.exe

if exist "%VENV_PYTHON%" (
    echo Using virtual environment python...
    "%VENV_PYTHON%" -u gex_scraper.py --symbol SPY,QQQ
) else (
    echo [Warning] Virtual environment not found at %VENV_PYTHON%.
    echo Attempting to run with system python...
    python -u gex_scraper.py --symbol SPY,QQQ
)

echo.
echo ==================================================
echo Scraper has stopped.
echo ==================================================
pause
