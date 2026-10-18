#!/bin/bash
# keep the PC<->Mac done files in step for the FundedNext queues (30m + 15m/1h)
# and pull the Mac's sealed results and job logs back. Exits when the Mac's
# supervisor has logged ALL DONE (or given up) and one last pull is made.
cd "$(dirname "$0")"
M=mac:/Users/nawfaldo/quant/sandbox/results
pull() {
  ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results && find . -maxdepth 2 \( -name "fundednext_families_*" -o -name "fundednext_tt_families_*" -o -name "fn30_*.log" -o -name "fn_*.log" \) -newer logs/fn30_jobs.txt | tar -czf - -T -' 2>/dev/null \
    | tar -xzf - -C .. --keep-newer-files 2>/dev/null
}
while true; do
  for f in fn30 fn; do
    [ -f ${f}_done.txt ] && scp -q -o ConnectTimeout=10 ${f}_done.txt $M/logs/${f}_other.txt
    scp -q -o ConnectTimeout=10 $M/logs/${f}_done.txt ${f}_other.txt.tmp 2>/dev/null && mv ${f}_other.txt.tmp ${f}_other.txt
  done
  pull
  if grep -qE "ALL DONE|gave up" super_fn.log 2>/dev/null \
     && ssh -o ConnectTimeout=10 mac 'grep -qE "ALL DONE|gave up" ~/quant/sandbox/results/logs/super_fn.log' 2>/dev/null; then
    pull; echo "$(date) both machines finished" >> fn_sync.log; exit 0
  fi
  sleep 60
done
