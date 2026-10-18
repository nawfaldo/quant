#!/bin/bash
# After the FundedNext import: specs + tick spreads for the new symbols, then
# mark every symbol with a table and a spread ready.
cd /c/Users/JawirGaming66/quant
N=sandbox/results/logs/ncf
while ps -ef | grep -v grep | grep -q exness_import_1m; do sleep 30; done
NEW="audcad audchf audnzd audsgd cadchf cadjpy chfjpy euraud eurcad eurchf eurgbp eurhkd eurhuf eurnok eurnzd eursgd gbpaud gbpcad gbpchf gbpnzd gbpsgd mxnjpy nokjpy nzdcad nzdchf nzdjpy nzdsgd nzdusd sgdjpy usdchf usdcnh usddkk usdhkd usdhuf usdmxn usdnok usdsek usdsgd usdzar zarjpy nth25 swi20 us2000 us30 vixcfd xauusd"
S=$(echo $NEW | tr ' ' ',')
cp sandbox/results/fundednext_specs.json $N/fundednext_specs.backup.json
py -m sandbox.research.cfd_families specs --broker fundednext --symbols $S >> $N/finish.log 2>&1
py -m sandbox.research.cfd_families ticks --broker fundednext --symbols $S >> $N/finish.log 2>&1
CFD_BROKER=fundednext py - >> $N/finish.log 2>&1 <<'PY'
import json
from sandbox import parquet_store as ps
from sandbox.research import cfd_families as cf
d = json.load(open("sandbox/results/fundednext_specs.json"))["symbols"]
ready = set(open("sandbox/results/logs/ncf/ready.txt").read().split())
jobs = {l.split()[1] for l in open("sandbox/results/logs/ncf/jobs_pc.txt") if l.strip()} | {l.split()[1] for l in open("sandbox/results/logs/ncf/jobs_mac.txt") if l.strip()}
ready = {s for s in ready if s in jobs}
for s in [x for x in """audcad audchf audnzd audsgd cadchf cadjpy chfjpy euraud eurcad eurchf eurgbp eurhkd eurhuf eurnok eurnzd eursgd gbpaud gbpcad gbpchf gbpnzd gbpsgd mxnjpy nokjpy nzdcad nzdchf nzdjpy nzdsgd nzdusd sgdjpy usdchf usdcnh usddkk usdhkd usdhuf usdmxn usdnok usdsek usdsgd usdzar zarjpy nth25 swi20 us2000 us30 vixcfd xauusd""".split() if x in jobs]:
    t = cf.broker_minute_table(s)
    if ps.has_table(t) and (d.get(s) or {}).get("spread_bp"):
        ready.add(s)
    else:
        print("NOT READY", s, ps.has_table(t), d.get(s))
open("sandbox/results/logs/ncf/ready.txt", "w", newline="\n").write("\n".join(sorted(ready)) + "\n")
print("ready", len(ready))
PY
echo "finish_data done $(date)" >> $N/finish.log
