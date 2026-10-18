#!/bin/bash
# supervisor: rerun the off-hours cfd + tt runners until every job in the lists is DONE (max 8 passes)
# usage: super_off.sh <cfd_list> <tt_list>
cd "$(dirname "$0")"
L=$(pwd)
clean() {  # drop own START claims that never finished, and stale tt locks
  awk '$1=="DONE"{d[$2" "$3" "$4]=1} {l[NR]=$0} END{for(i=1;i<=NR;i++){split(l[i],a," "); if(a[1]=="START" && !((a[2]" "a[3]" "a[4]) in d)) continue; print l[i]}}' offall_done.txt > offall_done.tmp && mv offall_done.tmp offall_done.txt
  awk '$1=="DONE"{d[$2" "$3]=1} {l[NR]=$0} END{for(i=1;i<=NR;i++){split(l[i],a," "); if(a[1]=="START" && !((a[2]" "a[3]) in d)) continue; print l[i]}}' ttoff_done.txt > ttoff_done.tmp && mv ttoff_done.tmp ttoff_done.txt
  rm -rf ttoff_locks; mkdir -p ttoff_locks
}
left() {
  n=0
  while read -r s b i; do [ -z "$s" ] && continue; grep -qE "^DONE $s $b $i( |$)" offall_done.txt offall_other.txt 2>/dev/null || n=$((n+1)); done < "$1"
  while read -r s b; do [ -z "$s" ] && continue; grep -qE "^DONE $s $b( |$)" ttoff_done.txt ttoff_other.txt 2>/dev/null || n=$((n+1)); done < "$2"
  echo $n
}
for pass in 1 2 3 4 5 6 7 8; do
  clean
  echo "$(date) pass $pass, jobs left $(left "$1" "$2")" >> super_off.log
  bash run_offall.sh "$L/$1" & a=$!
  bash run_ttoff.sh "$L/$2" & b=$!
  wait $a $b
  [ "$(left "$1" "$2")" = 0 ] && { echo "$(date) ALL DONE" >> super_off.log; exit 0; }
  sleep 60
done
