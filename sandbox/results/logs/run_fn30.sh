#!/bin/bash
# FundedNext 30m search: cfd_families (59 session families, barlb) + cfd_tt_families,
# RTH and off-hours windows. select -> validate per job (no coin flip unless asked).
# Shared queue with the other machine: fn30_done.txt (mine) / fn30_other.txt (theirs).
# usage: run_fn30.sh <job list>   lines: "cfd|tt <symbol> rth|off"
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs; D=$L/fn30_done.txt; O=$L/fn30_other.txt
W=${WORKERS:-8}; R=${RESERVE:-3}
export CFD_BROKER=fundednext EXNESS_HOLD_SKIP=1 EXNESS_MEMORY_RESERVE_GB=$R
F=$(tr -d '\n ' < $L/fn30_families.txt)
mkdir -p $L/fn30_locks
while read -r kind s win; do
  [ -z "$kind" ] && continue
  grep -qE "^(DONE|START) $kind $s $win( |$)" $D $O 2>/dev/null && continue
  mkdir $L/fn30_locks/${kind}_${s}_$win 2>/dev/null || continue
  echo "START $kind $s $win $(hostname)" >> $D
  G="--symbols $s --bar-minutes 30 --window $win --workers $W"
  if [ "$kind" = cfd ]; then
    M="env EXNESS_BAR_LOOKBACKS=1 $PY -m sandbox.research.cfd_families"
    $M select $G --families "$F" >> $L/fn30_${kind}_${s}_$win.log 2>&1 \
      && $M validate --symbols $s --bar-minutes 30 --window $win --families "$F" >> $L/fn30_${kind}_${s}_$win.log 2>&1 \
      || { echo "FAIL $kind $s $win" >> $D; continue; }
  else
    M="$PY -m sandbox.research.cfd_tt_families"
    $M select $G >> $L/fn30_${kind}_${s}_$win.log 2>&1 \
      && $M validate --symbols $s --bar-minutes 30 --window $win >> $L/fn30_${kind}_${s}_$win.log 2>&1 \
      || { echo "FAIL $kind $s $win" >> $D; continue; }
  fi
  echo "DONE $kind $s $win" >> $D
done < "$1"
