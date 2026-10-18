#!/bin/bash
# FundedNext search, IS 2024-2025 / OOS 2026 (CFD_SPLIT): cfd_families (59 session
# families, barlb) + cfd_tt_families. Per job: select on IS -> validate the IS
# winners on OOS -> coin flip (`why`) on the OOS winners.
# Shared queue with the other machine: fns_done.txt (mine) / fns_other.txt (theirs).
# usage: run_fns.sh <job list>   lines: "cfd|tt <symbol> rth|off <bar minutes>"
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs; D=$L/fns_done.txt; O=$L/fns_other.txt
W=${WORKERS:-8}; R=${RESERVE:-3}
export CFD_BROKER=fundednext CFD_SPLIT=2024-2025 CFD_OOS_END=2026-10-13
export EXNESS_HOLD_SKIP=1 EXNESS_MEMORY_RESERVE_GB=$R
F=$(tr -d '\n ' < $L/fn30_families.txt)
mkdir -p $L/fns_locks
touch $D
while read -r kind s win bar; do
  [ -z "$kind" ] && continue
  grep -qE "^(DONE|START) $kind $s $win $bar( |$)" $D $O 2>/dev/null && continue
  mkdir $L/fns_locks/${kind}_${s}_${win}_$bar 2>/dev/null || continue
  echo "START $kind $s $win $bar $(hostname)" >> $D
  G="--symbols $s --bar-minutes $bar --window $win"
  LOG=$L/fns_${kind}_${s}_${win}_$bar.log
  if [ "$kind" = cfd ]; then
    M="env EXNESS_BAR_LOOKBACKS=1 $PY -m sandbox.research.cfd_families"
    P=fundednext_families_is2024-2025; LB=${bar}m-barlb; FF=(--families "$F")
  else
    M="$PY -m sandbox.research.cfd_tt_families"
    P=fundednext_tt_families_is2024-2025; LB=${bar}m; FF=()
  fi
  $M select $G --workers $W "${FF[@]}" >> $LOG 2>&1 \
    && $M validate $G "${FF[@]}" >> $LOG 2>&1 \
    || { echo "FAIL $kind $s $win $bar" >> $D; continue; }
  WIN=$($PY $L/fns_winners.py $P $s $LB $win)
  if [ -n "$WIN" ]; then
    $M why $G --workers $W --families "$WIN" >> $LOG 2>&1 \
      || { echo "FAIL $kind $s $win $bar" >> $D; continue; }
  fi
  echo "DONE $kind $s $win $bar $WIN" >> $D
done < "$1"
