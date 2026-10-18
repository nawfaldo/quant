#!/bin/bash
# Rerun of the 2026-10-13 FundedNext rth/off search (fns_jobs.txt, minus es/nq/jp225)
# under the ncf rules: $6,000, measured $3/lot commission, broker hours, fills only.
# usage: run.sh <job list>   lines: "cfd|tt <symbol> rth|off <bar minutes>"
cd "$(dirname "$0")/../../../.."
L=sandbox/results/logs; N=$L/ncf; R=$L/rerun
PY=${PY:-py}; W=${WORKERS:-8}; D=$R/done.txt
export CFD_BALANCE=6000 CFD_BROKER=fundednext CFD_SPLIT=2024-2025 CFD_OOS_END=2026-10-15
export CFD_COMMISSION="$(cat $N/commission.json)"
export CFD_BROKER_HOURS=1 EXNESS_FILLS_ONLY=1 EXNESS_HOLD_SKIP=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-3}
F=$(tr -d '\n ' < $L/fn30_families.txt)
mkdir -p $R/locks $R/logs; touch $D
while read -r kind s win bar; do
  [ -z "$kind" ] && continue
  grep -qE "^DONE $kind $s $win $bar( |$)" $D && continue
  mkdir $R/locks/${kind}_${s}_${win}_$bar 2>/dev/null || continue
  echo "START $kind $s $win $bar $(date +%F_%T)" >> $D
  G="--symbols $s --bar-minutes $bar --window $win"
  LOG=$R/logs/${kind}_${s}_${win}_$bar.log
  OUT=sandbox/results/fundednext_rerun/$kind/$s-$bar-$win; mkdir -p $OUT
  if [ $kind = cfd ]; then M="env EXNESS_BAR_LOOKBACKS=1 $PY -m sandbox.research.cfd_families"; FF=(--families "$F")
  else M="$PY -m sandbox.research.cfd_tt_families"; FF=(); fi
  ok=1
  for stage in select validate; do
    CFD_RESULTS_DIR=$OUT $M $stage $G --workers $W "${FF[@]}" >> $LOG 2>&1 || { ok=0; break; }
  done
  if [ $ok = 1 ]; then
    WIN=$($PY $N/winners.py $OUT)
    [ -n "$WIN" ] && { CFD_RESULTS_DIR=$OUT $M why $G --workers $W --families "$WIN" >> $LOG 2>&1 || ok=0; }
  fi
  [ $ok = 1 ] && echo "DONE $kind $s $win $bar $WIN" >> $D || echo "FAIL $kind $s $win $bar" >> $D
done < "$1"
