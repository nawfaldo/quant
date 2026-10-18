#!/bin/bash
# new_cfd_families on FundedNext, IS 2024-2025 / OOS 2026 (to 2026-10-15), one
# job = (cfd|tt, symbol, window, bar) with every family of that module:
# select (IS) -> validate (OOS) -> coinflip (OOS winners). Same env, families,
# windows and commission as sandbox/research/new_cfd_families.py.
# usage: run.sh <job list> [<job list> ...]   lines: "cfd|tt <symbol> <window> <bar>"
# Done file: done_<host>.txt; the other machine's is other.txt (synced).
cd "$(dirname "$0")/../../../.."
N=sandbox/results/logs/ncf
PY=${PY:-py}; W=${WORKERS:-6}; H=${HOST_TAG:-pc}
D=$N/done2_$H.txt; O=$N/other2.txt
export CFD_BALANCE=6000 CFD_BROKER=fundednext CFD_SPLIT=2024-2025 CFD_OOS_END=2026-10-15
export CFD_COMMISSION="$(cat $N/commission.json)"
export CFD_BROKER_HOURS=1 EXNESS_FILLS_ONLY=1 EXNESS_HOLD_SKIP=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-3}
CF=$(cat $N/cfd_families.txt); TT=$(cat $N/tt_families.txt)
mkdir -p $N/locks2_$H $N/logs; touch $D
for list in "$@"; do
while read -r kind s win bar; do
  [ -z "$kind" ] && continue
  grep -qx $s $N/ready2.txt || continue
  grep -qE "^DONE $kind $s $win $bar( |$)" $D $O 2>/dev/null && continue
  mkdir $N/locks2_$H/${kind}_${s}_${win}_$bar 2>/dev/null || continue
  echo "START $kind $s $win $bar $(date +%F_%T)" >> $D
  spec=$(awk -v w=$win '$1==w{print $2}' $N/windows.txt)
  tf=$([ $bar = 60 ] && echo 1h || echo ${bar}m)
  LOG=$N/logs/${kind}_${s}_${win}_$bar.log
  k=${kind#w}
  OUT=sandbox/results/new_cfd_families/fundednext/$k/$s-$tf-$win
  mkdir -p $OUT
  if [ $k = cfd ]; then M="$PY -m sandbox.research.cfd_families"; B=(--broker fundednext); F=$CF
  else M="$PY -m sandbox.research.cfd_tt_families"; B=(); F=$TT; fi
  ok=1
  if [ ${kind:0:1} != w ]; then
    for stage in select validate; do
      [ $stage = select ] && ls $OUT/*_is2024-2025_*.json >/dev/null 2>&1 && continue
      CFD_RESULTS_DIR=$OUT timeout 21600 $M $stage "${B[@]}" --symbols $s --families "$F"         --bar-minutes $bar --window "$spec" --workers $W >> $LOG 2>&1 || { ok=0; break; }
    done
  fi
  # the user's coin flip: `why` re-runs the search 3x with random sides on the OOS winners
  if [ $ok = 1 ]; then
    WIN=$($PY $N/winners.py $OUT)
    if [ -n "$WIN" ]; then
      CFD_RESULTS_DIR=$OUT timeout 21600 $M why "${B[@]}" --symbols $s --families "$WIN"         --bar-minutes $bar --window "$spec" --workers $W >> $LOG 2>&1 || ok=0
    fi
  fi
  if [ $ok = 1 ]; then echo "DONE $kind $s $win $bar $(date +%F_%T)" >> $D
  else echo "FAIL $kind $s $win $bar $(date +%F_%T)" >> $D; fi
done < "$list"
done
