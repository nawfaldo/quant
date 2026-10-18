#!/bin/bash
# Supervisor for the FundedNext IS 2024-2025 / OOS 2026 search on THIS machine:
# run the queue (run_fns.sh) over and over -- retrying FAILs and anything left
# STARTed by a dead run -- until every job is DONE on one machine or the other
# (max 10 passes). Log: super_fns.log.
cd "$(dirname "$0")"
L=$(pwd)
clean() {  # forget my own START/FAIL lines that never reached DONE, so they retry
  [ -f fns_done.txt ] || return
  awk '$1=="DONE"{d[$2" "$3" "$4" "$5]=1} {l[NR]=$0}
       END{for(i=1;i<=NR;i++){split(l[i],a," "); k=a[2]" "a[3]" "a[4]" "a[5];
           if((a[1]=="START"||a[1]=="FAIL") && !(k in d)) continue; print l[i]}}' fns_done.txt > fns_done.tmp && mv fns_done.tmp fns_done.txt
  rm -rf fns_locks; mkdir -p fns_locks
}
left() {
  n=0
  while read -r k s w b; do [ -z "$k" ] && continue
    grep -qE "^DONE $k $s $w $b( |$)" fns_done.txt fns_other.txt 2>/dev/null || n=$((n+1)); done < fns_jobs.txt
  echo $n
}
for pass in 1 2 3 4 5 6 7 8 9 10; do
  clean
  echo "$(date) pass $pass, jobs left $(left)" >> super_fns.log
  bash run_fns.sh "$L/fns_jobs.txt"
  [ "$(left)" = 0 ] && { echo "$(date) ALL DONE" >> super_fns.log; exit 0; }
  sleep 120
done
echo "$(date) gave up after 10 passes, jobs left $(left)" >> super_fns.log
