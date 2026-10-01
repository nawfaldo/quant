#!/bin/bash
# ethusd only: IS 2021-2024 (skip the 172bp 2020 broker spread), stop widen x1/x2/x4
cd /c/Users/JawirGaming66/quant
export EXNESS_TT_STOP_WIDEN=1,2,4 EXNESS_IS_FIRST_YEAR=ethusd:2021 PYTHONPATH=.
for g in luxalgo quantpad thequantbuilder tradingdata2; do
  for c in select validate "why --winners"; do
    echo "=== $g $c $(date)"
    py -m sandbox.research.cfd_tt_families $c --groups $g --symbols ethusd --bar-minutes 5,15,30,60,240 > "sandbox/results/tt_eth_${g}_${c%% *}.out" 2>&1
  done
done
echo "=== ALL DONE $(date)"
