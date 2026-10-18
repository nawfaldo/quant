#!/bin/bash
# FundedNext search, any bar: cfd_families (59 session families, barlb) + cfd_tt_families,
# select -> validate per job. Shared queue with the other machine:
# fn_done.txt (mine) / fn_other.txt (theirs).
# usage: run_fn.sh <job list>   lines: "cfd|tt <symbol> rth|off <bar minutes>"
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs; D=$L/fn_done.txt; O=$L/fn_other.txt
W=${WORKERS:-8}; R=${RESERVE:-3}
export CFD_BROKER=fundednext EXNESS_HOLD_SKIP=1 EXNESS_MEMORY_RESERVE_GB=$R
F=$(tr -d '\n ' < $L/fn30_families.txt)
mkdir -p $L/fn_locks
touch $D
while read -r kind s win bar; do
  [ -z "$kind" ] && continue
  grep -qE "^(DONE|START) $kind $s $win $bar( |$)" $D $O 2>/dev/null && continue
  mkdir $L/fn_locks/${kind}_${s}_${win}_$bar 2>/dev/null || continue
  echo "START $kind $s $win $bar $(hostname)" >> $D
  G="--symbols $s --bar-minutes $bar --window $win"
  LOG=$L/fn_${kind}_${s}_${win}_$bar.log
  if [ "$kind" = cfd ]; then
    M="env EXNESS_BAR_LOOKBACKS=1 $PY -m sandbox.research.cfd_families"
    $M select $G --workers $W --families "$F" >> $LOG 2>&1 \
      && $M validate $G --families "$F" >> $LOG 2>&1 \
      || { echo "FAIL $kind $s $win $bar" >> $D; continue; }
  else
    M="$PY -m sandbox.research.cfd_tt_families"
    $M select $G --workers $W >> $LOG 2>&1 \
      && $M validate $G >> $LOG 2>&1 \
      || { echo "FAIL $kind $s $win $bar" >> $D; continue; }
  fi
  echo "DONE $kind $s $win $bar" >> $D
done < "$1"
