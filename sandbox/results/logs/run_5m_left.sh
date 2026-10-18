#!/bin/bash
# Remaining 5m barlb jobs, one (symbol, batch) at a time; skips any job whose
# seal already exists (including ones copied back from the Mac).
cd "$(dirname "$0")/../../.."
PY=${PY:-py}
SYMS=${SYMS:-"gbpusd usdcad usdjpy btc ethusd audusd eurjpy eurusd gbpjpy"}
for i in 0 1 2 3 4; do
  F=$(cat sandbox/results/logs/barlb_batch$i.txt)
  TAG=$($PY -c "import sys;print('+'.join(sorted(sys.argv[1].split(',')))[:60])" "$F")
  for s in $SYMS; do
    [ -f "sandbox/results/exness_families_${s}_5m-barlb_${TAG}.json" ] && continue
    EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-2} $PY -m sandbox.research.cfd_families select \
      --symbols $s --bar-minutes 5 --workers ${WORKERS:-16} --families "$F" >> sandbox/results/logs/cfd_families_barlb_5m_left.log 2>&1
  done
done
