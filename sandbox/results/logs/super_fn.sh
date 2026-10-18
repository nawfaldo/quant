#!/bin/bash
# Supervisor for the FundedNext search on THIS machine: wait for the running 30m
# runner (run_fn30.sh) to finish, then run the 30m queue again (retries its
# FAILs) and the 15m + 1h queue (run_fn.sh), over and over until every job is
# DONE on one machine or the other (max 10 passes). Log: super_fn.log.
cd "$(dirname "$0")"
L=$(pwd)
clean() {  # forget my own START/FAIL lines that never reached DONE, so they retry
  for f in fn30_done.txt fn_done.txt; do
    [ -f $f ] || continue
    awk '$1=="DONE"{k=$2" "$3" "$4" "$5; d[k]=1} {l[NR]=$0}
         END{for(i=1;i<=NR;i++){split(l[i],a," "); k=a[2]" "a[3]" "a[4]" "a[5];
             if((a[1]=="START"||a[1]=="FAIL") && !(k in d)) continue; print l[i]}}' $f > $f.tmp && mv $f.tmp $f
  done
  rm -rf fn30_locks fn_locks; mkdir -p fn30_locks fn_locks
}
left() {
  n=0
  while read -r k s w; do [ -z "$k" ] && continue
    grep -qE "^DONE $k $s $w( |$)" fn30_done.txt fn30_other.txt 2>/dev/null || n=$((n+1)); done < fn30_jobs.txt
  while read -r k s w b; do [ -z "$k" ] && continue
    grep -qE "^DONE $k $s $w $b( |$)" fn_done.txt fn_other.txt 2>/dev/null || n=$((n+1)); done < fn_jobs.txt
  echo $n
}
running30() {  # is the first 30m runner (started before this supervisor) alive?
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "run_fn30.sh" >/dev/null 2>&1
  else   # Git bash on Windows: msys `ps` hides arguments, ask Windows
    [ "$(powershell -NoProfile -c "@(Get-CimInstance Win32_Process | ? { \$_.CommandLine -like '*run_fn30.sh*' -and \$_.CommandLine -notlike '*Get-CimInstance*' }).Count")" != "0" ]
  fi
}
while running30; do sleep 60; done
for pass in 1 2 3 4 5 6 7 8 9 10; do
  clean
  echo "$(date) pass $pass, jobs left $(left)" >> super_fn.log
  bash run_fn30.sh "$L/fn30_jobs.txt"
  bash run_fn.sh "$L/fn_jobs.txt"
  [ "$(left)" = 0 ] && { echo "$(date) ALL DONE" >> super_fn.log; exit 0; }
  sleep 120
done
echo "$(date) gave up after 10 passes, jobs left $(left)" >> super_fn.log
