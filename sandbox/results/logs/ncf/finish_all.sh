#!/bin/bash
# Wait until both machines finish the queue, then pull, summarize, export PASS/PASS*, touch ALL_DONE.
cd "$(dirname "$0")/../../../.."
N=sandbox/results/logs/ncf; R=sandbox/results/new_cfd_families
until [ -f $N/ALL_DONE_pc ] && ssh -o ConnectTimeout=10 mac 'test -f ~/quant/sandbox/results/logs/ncf/ALL_DONE_mac'; do sleep 600; done
until ssh -o ConnectTimeout=10 mac 'cd ~/quant/sandbox/results/new_cfd_families && tar cf - fundednext/cfd fundednext/tt' | (cd $R && tar xf -); do sleep 300; done
py $N/summary.py > /dev/null 2>&1
echo "$(date '+%F %T') FINAL $(py -m sandbox.research._ncf_fundednext_export 2>&1 | tr '\n' ' ')" >> $N/export_watch.log
touch $N/ALL_DONE
