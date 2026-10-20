#!/bin/bash
# super2's left() matches /done_/, never done2_, so it never reaches 0. This
# touches the ALL_DONE2 flags once every q_redo job is DONE on either host,
# which lets sync2.sh pull the Mac results and run the final export.
cd /c/Users/JawirGaming66/quant/sandbox/results/logs/ncf
left() { awk 'FILENAME ~ /done2_|other2/ {if($1=="DONE") d[$2" "$3" "$4" "$5]=1; next}
  NF && !(($1" "$2" "$3" "$4) in d) {n++} END{print n+0}' done2_pc.txt other2.txt q_redo.txt; }
until [ "$(left)" = 0 ]; do sleep 120; done
ssh -o ConnectTimeout=10 mac "touch ~/quant/sandbox/results/logs/ncf/ALL_DONE2_mac"
touch ALL_DONE2_pc; echo "$(date) all q_redo DONE" >> super2_pc.log
