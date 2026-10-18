#!/bin/bash
cd /c/Users/JawirGaming66/quant/tools
for p in CAD/JPY CHF/JPY EUR/AUD GBP/AUD NZD/USD USD/CHF; do
  t=$(echo $p | tr -d / | tr A-Z a-z)_1m
  echo "== $p $t $(date +%T)"
  py "dukascopy_fetch&import.py" --instrument $p --table $t --start 2023-01 --through 2026-10-15 --timezone America/New_York || echo "FAIL $p"
done
echo "DUKA6 DONE $(date +%T)"
