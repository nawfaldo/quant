@echo off
cd /d "%~dp0"

if exist "..\.venv\Scripts\python.exe" (
    "..\.venv\Scripts\python.exe" run_yfinance.py
) else (
    python run_yfinance.py
)
