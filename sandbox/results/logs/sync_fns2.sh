#!/bin/bash
# sync_fns.sh, plus: if the Mac has been unreachable for STALE_MIN minutes, its
# claimed-but-unfinished jobs are released (START lines without DONE dropped
# from fns_other.txt) so the PC supervisor's next pass takes them over, and the
# run may finish without the Mac. Ends with the PASS / PASS* export.
cd "$(dirname "$0")"
M=mac:/Users/nawfaldo/quant/sandbox/results
STALE_MIN=${STALE_MIN:-20}
seen=$(date +%s)
pull() {
  ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results && find . -maxdepth 2 \( -name "fundednext_*families_is2024-2025_*" -o -name "fns_*.log" \) | tar -czf - -T -' 2>/dev/null \
    | tar -xzf - -C .. --keep-newer-files 2>/dev/null
}
release() {  # drop the Mac's START lines that never reached DONE
  awk '$1=="DONE"{d[$2" "$3" "$4" "$5]=1} {l[NR]=$0}
       END{for(i=1;i<=NR;i++){split(l[i],a," "); k=a[2]" "a[3]" "a[4]" "a[5];
           if(a[1]=="START" && !(k in d)) continue; print l[i]}}' fns_other.txt > fns_other.rel && mv fns_other.rel fns_other.txt
}
while true; do
  if ssh -o ConnectTimeout=10 -o BatchMode=yes mac true 2>/dev/null; then
    seen=$(date +%s)
    [ -f fns_done.txt ] && scp -q -o ConnectTimeout=10 fns_done.txt $M/logs/fns_other.txt
    scp -q -o ConnectTimeout=10 $M/logs/fns_done.txt fns_other.txt.tmp 2>/dev/null && mv fns_other.txt.tmp fns_other.txt
    pull
    mac_done=$(ssh -o ConnectTimeout=10 mac 'grep -qE "ALL DONE|gave up" ~/quant/sandbox/results/logs/super_fns.log && echo y' 2>/dev/null)
  else
    away=$(( ($(date +%s) - seen) / 60 ))
    if [ $away -ge $STALE_MIN ]; then
      [ -f fns_other.txt ] && release
      mac_done=y   # unreachable: do not wait for it
      echo "$(date) mac unreachable ${away} min -- its unfinished jobs released to the PC" >> fns_sync.log
    fi
  fi
  if grep -qE "ALL DONE|gave up" super_fns.log 2>/dev/null && [ "$mac_done" = y ]; then
    pull; echo "$(date) finished" >> fns_sync.log
    # PASS and PASS* survivors -> results/fundednext/
    (cd ../../.. && py -m sandbox.research._fundednext_export) >> fns_sync.log 2>&1
    echo "$(date) export done" >> fns_sync.log
    exit 0
  fi
  sleep 60
done
