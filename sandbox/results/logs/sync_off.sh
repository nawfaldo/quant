#!/bin/bash
# keep the PC<->Mac done files in step for the off-hours queues
cd "$(dirname "$0")"
M=nawfaldo@100.95.232.126:/Users/nawfaldo/quant/sandbox/results/logs
while true; do
  scp -q -o ConnectTimeout=10 offall_done.txt $M/offall_other.txt
  scp -q -o ConnectTimeout=10 ttoff_done.txt $M/ttoff_other.txt
  scp -q -o ConnectTimeout=10 $M/offall_done.txt offall_other.txt.tmp && mv offall_other.txt.tmp offall_other.txt
  scp -q -o ConnectTimeout=10 $M/ttoff_done.txt ttoff_other.txt.tmp && mv ttoff_other.txt.tmp ttoff_other.txt
  sleep 30
done
