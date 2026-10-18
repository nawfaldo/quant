#!/bin/bash
# Redo of the 11 broker-signalled symbols on Dukascopy signals. Feeds each
# symbol to both hosts once its vendor table lands, swaps done2 files, and at
# the end pulls the Mac's results, drops the stale exports and re-exports.
cd /c/Users/JawirGaming66/quant
N=sandbox/results/logs/ncf; R=sandbox/results/new_cfd_families; M=quant/sandbox/results/logs/ncf
SYMS="cadjpy chfjpy euraud gbpaud nzdusd usdchf xauusd us30 us2000 swi20 nth25"
while true; do
  for s in $SYMS; do
    grep -qx $s $N/ready2.txt && continue
    grep -qE "^${s}_1m: [0-9,]+ -> [0-9,]+ rows" $N/duka6.log $N/duka5.log 2>/dev/null || continue
    tar cf - data/parquet/${s}_1m data/parquet/${s}_1m.parquet 2>/dev/null | ssh -o ConnectTimeout=10 mac "cd ~/quant && tar xf -" || continue
    echo $s >> $N/ready2.txt && scp -q $N/ready2.txt mac:$M/ready2.txt && echo "$(date +%T) ready $s" >> $N/sync2.log
  done
  scp -q -o ConnectTimeout=10 $N/done2_pc.txt mac:$M/other2.txt 2>/dev/null
  scp -q -o ConnectTimeout=10 mac:$M/done2_mac.txt $N/other2.tmp 2>/dev/null && mv $N/other2.tmp $N/other2.txt
  [ -f $N/ALL_DONE2_pc ] && ssh -o ConnectTimeout=10 mac "test -f ~/$M/ALL_DONE2_mac" && break
  sleep 120
done
until ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results/new_cfd_families && tar cf - fundednext/cfd fundednext/tt' | (cd $R && tar xf -); do sleep 300; done
py - <<'PY'
import glob, json, os
syms = "cadjpy chfjpy euraud gbpaud nzdusd usdchf xauusd us30 us2000 swi20 nth25".split()
n = 0
for p in glob.glob("sandbox/results/fundednext/*.json"):
    b = os.path.basename(p)
    if b == "SURVIVORS.json" or b.split("_")[0] not in syms: continue
    if json.load(open(p, encoding="utf-8")).get("study_wave", "").startswith("ncf"):
        os.makedirs("sandbox/results/fundednext_brokersig", exist_ok=True)
        os.replace(p, os.path.join("sandbox/results/fundednext_brokersig", b)); n += 1
print("moved stale", n)
PY
py $N/summary.py > /dev/null 2>&1
echo "$(date '+%F %T') REDO FINAL $(py -m sandbox.research._ncf_fundednext_export 2>&1 | tr '\n' ' ')" >> $N/export_watch.log
touch $N/ALL_DONE2
