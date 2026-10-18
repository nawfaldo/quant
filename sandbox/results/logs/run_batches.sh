#!/bin/bash
cd /c/Users/JawirGaming66/quant
L=sandbox/results/logs/cfd_families_barlb_5m_rest.log


for i in 0 1 2 3 4; do
  EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1 EXNESS_MEMORY_RESERVE_GB=2 py -m sandbox.research.cfd_families select \
    --symbols audusd,eurjpy,eurusd,gbpjpy,gbpusd,usdcad,usdjpy,btc,ethusd --bar-minutes 5 --workers 16 \
    --families $(cat sandbox/results/logs/barlb_batch$i.txt) > sandbox/results/logs/cfd_families_barlb_5m_batch$i.log 2>&1
done
