#!/bin/bash
# Run one Python module on the PC and the Mac together, sharing its units
# through `twohost` (claims on the Mac, so no unit runs twice). Run from the PC.
#
#   TWOHOST_PUSH="paths to copy to the Mac first" \
#   bash sandbox/research/twohost_run.sh <run id> <pc workers> <mac workers> <module> [args...]
#
# Every CFD_* / EXNESS_* / FN_* / TWOHOST_* variable set here is passed to the
# Mac. Code (sandbox/research) is always pushed; data and pool files only if
# listed in TWOHOST_PUSH (uncompressed tar, so the PC stays light). The Mac log
# is ~/quant/.twohost/<run>/mac.log; the PC waits for the Mac's units at the end.
# Reusing a run id resumes it (done units are skipped); use a new id for new work.
set -e
cd "$(dirname "$0")/../.."
RUN=$1 PW=$2 MW=$3 MOD=$4; shift 4
[ -n "$MOD" ] || { sed -n 2,14p "$0"; exit 1; }
export TWOHOST_RUN=$RUN
MAC=${TWOHOST_MAC:-mac}

tar cf - --exclude=__pycache__ sandbox/__init__.py sandbox/research/*.py sandbox/research/fill_models \
    $TWOHOST_PUSH | ssh "$MAC" 'cd ~/quant && tar xf -'

ENV=""
while IFS='=' read -r k v; do
  ENV="$ENV $k=$(printf %q "$v")"
done < <(env | grep -E '^(CFD_|EXNESS_|FN_|TWOHOST_)')
ARGS=""; [ $# -gt 0 ] && ARGS=$(printf ' %q' "$@")

ssh "$MAC" "cd ~/quant && mkdir -p .twohost/$RUN && (env $ENV PYTHONIOENCODING=utf-8 nohup caffeinate -is \
  .venv/bin/python -m $MOD $ARGS --workers $MW > .twohost/$RUN/mac.log 2>&1) > /dev/null 2>&1 < /dev/null &"
echo "twohost $RUN: Mac started ($MW workers), PC running ($PW workers)"

PYTHONIOENCODING=utf-8 ${PY:-py} -m "$MOD" "$@" --workers "$PW"
