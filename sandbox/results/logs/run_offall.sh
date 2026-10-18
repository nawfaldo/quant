#!/bin/bash
# select -> validate -> coin flip per (symbol, bar, batch); shared queue with the other machine
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs; D=$L/offall_done.txt; O=$L/offall_other.txt
export EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1
while read -r s b i; do
  [ -z "$s" ] && continue
  grep -qE "^(DONE|START) $s $b $i( |$)" $D $O 2>/dev/null && continue
  echo "START $s $b $i" >> $D
  F=$(cat $L/off_batch$i.txt)
  R=${RESERVE:-3}; W=${WORKERS:-16}
  EXNESS_MEMORY_RESERVE_GB=$R $PY -m sandbox.research.cfd_families select --symbols $s --bar-minutes $b --window off --workers $W --families "$F" >> $L/offall.log 2>&1 || continue
  EXNESS_MEMORY_RESERVE_GB=$R $PY -m sandbox.research.cfd_families validate --symbols $s --bar-minutes $b --window off --families "$F" >> $L/offall.log 2>&1 || continue
  WIN=$($PY - "$s" "$b" "$F" <<'P'
import json,sys
s,b,F=sys.argv[1:]; fams=sorted(F.split(','))
p=f"sandbox/results/exness_families_{s}_{b}m-barlb_{'+'.join(fams)[:60]}_win-off.json"
d=json.load(open(p)); v=d.get("validation") or {}
w=[f for f in fams if (d["families"].get(f) and (v.get(f) or {}).get("oos")
   and v[f]["oos"]["return_pct"]>0 and (v[f]["oos"].get("pf") or 0)>=1.05 and v[f]["oos"]["trades"]>=30)]
print(",".join(w))
P
)
  [ -n "$WIN" ] && { EXNESS_MEMORY_RESERVE_GB=$R $PY -m sandbox.research.cfd_families why --symbols $s --bar-minutes $b --window off --workers $W --families "$WIN" >> $L/offall.log 2>&1 || continue; }
  echo "DONE $s $b $i $WIN" >> $D
done < "$1"
