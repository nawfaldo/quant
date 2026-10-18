#!/bin/bash
# coin-flip null (why) on each "symbol fam,fam,..." line of the given list file
cd "$(dirname "$0")/../../.."
PY=${PY:-py}
while read -r s F; do
  [ -z "$s" ] && continue
  grep -q "^DONE $s$" sandbox/results/logs/null_5m_done.txt 2>/dev/null && continue
  EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-3} $PY -m sandbox.research.cfd_families why \
    --symbols $s --bar-minutes 5 --workers ${WORKERS:-16} --families "$F" >> sandbox/results/logs/null_5m.log 2>&1 \
    && echo "DONE $s" >> sandbox/results/logs/null_5m_done.txt
done < "$1"
