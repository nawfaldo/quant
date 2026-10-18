#!/bin/bash
cd "$(dirname "$0")"
export WORKERS=6 HOST_TAG=pcw
bash run_why2.sh "$PWD/q_why2.txt" & sleep 20; bash run_why2.sh "$PWD/q_why2.txt" & wait
HOST_TAG=pc WORKERS=6 bash super.sh 2 q_five_rev.txt
