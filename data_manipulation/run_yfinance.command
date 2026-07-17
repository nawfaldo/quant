#!/usr/bin/env bash
# Change directory to the folder containing this script
cd "$(dirname "$0")"

# Path to python interpreter
PYTHON="python3"

echo "=================================================="
echo "Starting Yahoo Finance daily fetch & import..."
echo "=================================================="
echo ""

echo "--- 1/3 Fetching & Importing NQ (Intraday) ---"
$PYTHON 'yfinance_fetch&import.py' --symbol NQ=F --table-prefix nq --timezone America/New_York
if [ $? -ne 0 ]; then
    echo "Error: Fetching NQ failed."
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s
    exit 1
fi
echo ""

echo "--- 2/3 Fetching & Importing ES (Intraday) ---"
$PYTHON 'yfinance_fetch&import.py' --symbol ES=F --table-prefix es --timezone America/New_York
if [ $? -ne 0 ]; then
    echo "Error: Fetching ES failed."
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s
    exit 1
fi
echo ""

echo "--- 3/3 Fetching & Importing VIX (Daily) ---"
$PYTHON 'yfinance_fetch&import_daily.py' --symbol ^VIX --table-prefix vix --timezone America/New_York
if [ $? -ne 0 ]; then
    echo "Error: Fetching VIX failed."
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s
    exit 1
fi
echo ""

echo "=================================================="
echo "Daily data fetch & import completed successfully!"
echo "=================================================="
echo ""
echo "Press any key to exit..."
read -n 1 -s
