#!/bin/bash
# keep the PC<->Mac done files in step for the IS 2024-2025 / OOS 2026 queue and
# pull the Mac's sealed results and job logs back. Exits once both supervisors
# have logged ALL DONE (or given up), after one last pull.
cd "$(dirname "$0")"
M=mac:/Users/nawfaldo/quant/sandbox/results
pull() {
  ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results && find . -maxdepth 2 \( -name "fundednext_*families_is2024-2025_*" -o -name "fns_*.log" \) | tar -czf - -T -' 2>/dev/null \
    | tar -xzf - -C .. --keep-newer-files 2>/dev/null
}
while true; do
  [ -f fns_done.txt ] && scp -q -o ConnectTimeout=10 fns_done.txt $M/logs/fns_other.txt
  scp -q -o ConnectTimeout=10 $M/logs/fns_done.txt fns_other.txt.tmp 2>/dev/null && mv fns_other.txt.tmp fns_other.txt
  pull
  if grep -qE "ALL DONE|gave up" super_fns.log 2>/dev/null \
     && ssh -o ConnectTimeout=10 mac 'grep -qE "ALL DONE|gave up" ~/quant/sandbox/results/logs/super_fns.log' 2>/dev/null; then
    pull; echo "$(date) both machines finished" >> fns_sync.log
    # PASS and PASS* survivors -> results/fundednext/
    (cd ../../.. && py -m sandbox.research._fundednext_export) >> fns_sync.log 2>&1
    exit 0
  fi
  sleep 60
done
