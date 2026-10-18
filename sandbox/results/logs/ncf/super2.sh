#!/bin/bash
# Supervisor: rerun the queues until every job is DONE here or on the other
# machine (FAILs and dead STARTs retry), max 6 passes. Then touch ALL_DONE_<host>.
# usage: super.sh <runners> <job list>...
cd "$(dirname "$0")"; H=${HOST_TAG:-pc}; R=$1; shift
left() { awk 'FILENAME ~ /done_|other/ {if($1=="DONE") d[$2" "$3" "$4" "$5]=1; next}
  NF && !(($1" "$2" "$3" "$4) in d) {n++} END{print n+0}' done2_$H.txt other2.txt "$@" 2>/dev/null; }
for pass in $(seq 1 40); do
  # forget START/FAIL lines without DONE so they retry
  [ -f done2_$H.txt ] && grep "^DONE" done2_$H.txt > d2.tmp; mv d2.tmp done2_$H.txt 2>/dev/null
  rm -rf locks2_$H; echo "$(date) pass $pass, jobs left $(left "$@")" >> super2_$H.log
  A=(); for f in "$@"; do A+=("$PWD/$f"); done
  for i in $(seq 1 $R); do bash run2.sh "${A[@]}" & sleep 20; done; wait
  [ "$(left "$@")" = 0 ] && break; sleep 600
done
echo "$(date) finished, jobs left $(left "$@")" >> super2_$H.log; touch ALL_DONE2_$H
