#!/bin/bash
# after tt_three_groups.sh: coin flip on every OOS winner, then PASS/PASS* export
cd /c/Users/JawirGaming66/quant
until grep -q "=== tradingdata2 done" sandbox/results/tt_three_groups.log; do sleep 60; done
for g in thequantbuilder quantpad tradingdata2; do
  echo "=== $g why $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families why --winners --groups $g --bar-minutes 5,15,30,60,240 > sandbox/results/tt_${g}_why.out 2>&1
  echo "=== $g export $(date)"
  PYTHONPATH=. py -m sandbox.research._tiktok_export --group $g > sandbox/results/tt_${g}_export.out 2>&1
  echo "=== $g done $(date)"
done
