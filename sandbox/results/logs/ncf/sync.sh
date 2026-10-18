#!/bin/bash
# PC <-> Mac for the ncf queue: swap done files, push ready symbols' data and
# specs, pull the Mac's results. Ends after both ALL_DONE_<host>, then writes
# the summary and touches ALL_DONE (which stops keepawake.ps1).
cd /c/Users/JawirGaming66/quant
N=sandbox/results/logs/ncf; R=quant/sandbox/results
pull() {
  ssh -o ConnectTimeout=10 mac "cd ~/quant && tar czf - sandbox/results/new_cfd_families/fundednext sandbox/results/logs/ncf/logs" 2>/dev/null \
    | tar xzf - --keep-newer-files 2>/dev/null
}
i=0
while true; do
  scp -q -o ConnectTimeout=10 $N/done_pc.txt mac:$R/logs/ncf/other.txt
  scp -q -o ConnectTimeout=10 mac:$R/logs/ncf/done_mac.txt $N/other.tmp 2>/dev/null && mv $N/other.tmp $N/other.txt
  new=$(comm -23 <(sort $N/ready.txt) <(sort $N/pushed.txt))
  if [ -n "$new" ]; then
    T=""; for s in $new; do t=$(CFD_BROKER=fundednext py -c "from sandbox.research import cfd_families as c; print(c.broker_minute_table('$s'))"); T="$T data/parquet/$t.parquet"; done
    tar czf - $T sandbox/results/fundednext_specs.json sandbox/research/cfd_families.py | ssh mac 'cd ~/quant && tar xzf -' \
      && scp -q $N/ready.txt mac:$R/logs/ncf/ready.txt && echo "$new" >> $N/pushed.txt \
      && echo "$(date) pushed $(echo $new)" >> $N/sync.log
  fi
  i=$((i+1)); [ $((i % 2)) = 0 ] && pull
  [ -f $N/ALL_DONE ] && { pull; exit 0; }
  sleep 120
done
