#!/bin/bash
# coin-flip null (why) on each "symbol bar fam,fam,..." line of the given list file.
# A job is skipped if this machine (null_done.txt) or the other one (null_other.txt,
# synced every 2 min) has started or finished it, so two machines can share one queue.
cd "$(dirname "$0")/../../.."
PY=${PY:-py}
D=sandbox/results/logs/null_done.txt; O=sandbox/results/logs/null_other.txt
while read -r s b F; do
  [ -z "$s" ] && continue
  grep -qE "^(DONE|START) $s $b$" $D $O 2>/dev/null && continue
  echo "START $s $b" >> $D
  EXNESS_HOLD_SKIP=1 EXNESS_BAR_LOOKBACKS=1 EXNESS_MEMORY_RESERVE_GB=${RESERVE:-3} $PY -m sandbox.research.cfd_families why \
    --symbols $s --bar-minutes $b --workers ${WORKERS:-16} --families "$F" >> sandbox/results/logs/null_tf.log 2>&1 \
    && echo "DONE $s $b" >> $D
done < "$1"
