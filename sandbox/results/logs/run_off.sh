#!/bin/bash
# off-hours window: select -> validate -> coin flip on holdout winners, one job
cd "$(dirname "$0")/../../.."
PY=${PY:-py}; L=sandbox/results/logs
export EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-3}
s=$1; b=$2; F=$3; W=${WORKERS:-5}; G="--symbols $s --bar-minutes $b --window off --families $F"
$PY -m sandbox.research.cfd_families select $G --workers $W >> $L/off_$s$b.log 2>&1 || exit 1
$PY -m sandbox.research.cfd_families validate $G >> $L/off_$s$b.log 2>&1 || exit 1
WIN=$($PY - "$s" "$b" "$F" <<'P'
import json,sys
s,b,F=sys.argv[1:]; fams=sorted(F.split(','))
d=json.load(open(f"sandbox/results/exness_families_{s}_{b}m-barlb_{'+'.join(fams)[:60]}_win-off.json")); v=d.get("validation") or {}
print(",".join(f for f in fams if d["families"].get(f) and (v.get(f) or {}).get("oos") and v[f]["oos"]["return_pct"]>0
      and (v[f]["oos"].get("pf") or 0)>=1.05 and v[f]["oos"]["trades"]>=30))
P
)
[ -n "$WIN" ] && $PY -m sandbox.research.cfd_families why --symbols $s --bar-minutes $b --window off --families $WIN --workers $W >> $L/off_$s$b.log 2>&1
echo "DONE $s $b ${WIN:-none}"
