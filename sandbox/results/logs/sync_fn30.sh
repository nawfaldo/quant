#!/bin/bash
# keep the PC<->Mac done files in step for the FundedNext 30m queue, and pull
# the Mac's sealed results back as they appear. Exits once both sides are idle
# and every job in the list is DONE or FAIL somewhere.
cd "$(dirname "$0")"
M=mac:/Users/nawfaldo/quant/sandbox/results
total=$(grep -c . fn30_jobs.txt)
while true; do
  scp -q -o ConnectTimeout=10 fn30_done.txt $M/logs/fn30_other.txt
  scp -q -o ConnectTimeout=10 $M/logs/fn30_done.txt fn30_other.txt.tmp && mv fn30_other.txt.tmp fn30_other.txt
  # the Mac's results (sealed json + per-job logs), newest only
  ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results && find . -maxdepth 2 \( -name "fundednext_families_*_30m*" -o -name "fundednext_tt_families_*_30m*" -o -name "fn30_*.log" \) | tar -czf - -T -' 2>/dev/null \
    | tar -xzf - -C .. --keep-newer-files 2>/dev/null
  finished=$(cat fn30_done.txt fn30_other.txt 2>/dev/null | awk '$1=="DONE"||$1=="FAIL"{print $2,$3,$4}' | sort -u | wc -l)
  [ "$finished" -ge "$total" ] && { echo "$(date) all $total finished" >> fn30_sync.log; exit 0; }
  sleep 60
done
