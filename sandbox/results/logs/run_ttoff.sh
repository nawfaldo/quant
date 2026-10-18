#!/bin/bash
# cfd_tt_families, off-hours window: select -> validate -> coin flip on holdout winners.
# Several copies may run per machine (mkdir lock); the other machine's progress is in ttoff_other.txt.
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs; D=$L/ttoff_done.txt; O=$L/ttoff_other.txt; mkdir -p $L/ttoff_locks
T="$PY -m sandbox.research.cfd_tt_families"
while read -r s b; do
  [ -z "$s" ] && continue
  grep -qE "^(DONE|START) $s $b( |$)" $D $O 2>/dev/null && continue
  mkdir $L/ttoff_locks/${s}_$b 2>/dev/null || continue
  echo "START $s $b" >> $D
  G="--symbols $s --bar-minutes $b --window off --workers 1"
  $T select $G >> $L/ttoff_$s$b.log 2>&1 && $T validate $G >> $L/ttoff_$s$b.log 2>&1 || continue
  WIN=$($PY - "$s" "$b" <<'P'
import json,sys
s,b=sys.argv[1:]
d=json.load(open(f"sandbox/results/exness_tt_families_{s}_{b}m_win-off.json")); v=d.get("validation") or {}
print(",".join(f for f,w in d["families"].items() if w and (v.get(f) or {}).get("oos") and v[f]["oos"]["return_pct"]>0
      and (v[f]["oos"].get("pf") or 0)>=1.05 and v[f]["oos"]["trades"]>=30))
P
)
  [ -n "$WIN" ] && { $T why $G --families "$WIN" >> $L/ttoff_$s$b.log 2>&1 || continue; }
  echo "DONE $s $b $WIN" >> $D
done < "$1"
