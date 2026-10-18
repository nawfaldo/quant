#!/bin/bash
# Every 30 min: pull the Mac's results, rebuild the summary, export why PASS/PASS*.
# After the Mac touches ALL_DONE_mac: one last pull + export, then exit.
cd "$(dirname "$0")/../../../.."
R=sandbox/results/new_cfd_families
L=sandbox/results/logs/ncf/export_watch.log
while true; do
  fin=0; ssh -o ConnectTimeout=10 mac 'test -f ~/quant/sandbox/results/logs/ncf/ALL_DONE_mac' && fin=1
  if ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results/new_cfd_families && tar cf - fundednext/cfd fundednext/tt' | (cd $R && tar xf -); then
    py sandbox/results/logs/ncf/summary.py > /dev/null 2>&1
    echo "$(date '+%F %T') $(py -m sandbox.research._ncf_fundednext_export 2>&1 | head -1)" >> $L
  else
    echo "$(date '+%F %T') mac unreachable, retry" >> $L
  fi
  [ $fin = 1 ] && { echo "$(date '+%F %T') FINAL export done" >> $L; exit 0; }
  sleep 1800
done
