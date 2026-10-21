#!/bin/bash
# PC at 50%: started under affinity 0xFF (8 of 16 threads, inherited by every
# child) with 2 runners x 4 workers, plus the done-swap sync and finish watcher.
cd /c/Users/JawirGaming66/quant/sandbox/results/logs/ncf
rm -rf locks2_pc
bash sync2.sh >> sync2.out 2>&1 &
bash watch2.sh >> watch2.out 2>&1 &
HOST_TAG=pc WORKERS=4 RESERVE=4 bash super2.sh 2 q_redo.txt >> pc_go2.out 2>&1 &
wait
