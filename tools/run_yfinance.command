#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ -f "../.venv/bin/python" ]; then
    ../.venv/bin/python run_yfinance.py
else
    python3 run_yfinance.py
fi
