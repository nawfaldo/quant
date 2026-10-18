#!/bin/bash
cd /c/Users/JawirGaming66/quant/tools
until grep -q "DUKA6 DONE" ../sandbox/results/logs/ncf/duka6.log; do sleep 30; done
for pt in "XAU/USD xauusd" "E_D&J-Ind us30" "USSC2000.IDX/USD us2000" "E_SWMI swi20" "NLD.IDX/EUR nth25"; do
  set -- $pt; echo "== $1 $2_1m $(date +%T)"
  py "dukascopy_fetch&import.py" --instrument "$1" --table $2_1m --start 2023-01 --through 2026-10-15 --timezone America/New_York || echo "FAIL $1"
done
echo "DUKA5 DONE $(date +%T)"
