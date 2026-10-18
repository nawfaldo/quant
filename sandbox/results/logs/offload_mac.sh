#!/bin/bash
# Copy code + the 4 symbols' data to the Mac and run their 5m batches there.
set -e
cd "$(dirname "$0")/../../.."
MAC=nawfaldo@100.95.232.126
LIST=$(mktemp)
{ find sandbox -name "*.py" -not -path "*/results/*" -not -path "*/.cache/*"
  ls sandbox/.cache/families_{btc,ethusd,usdjpy,usdcad}_5m_* sandbox/.cache/fills_exness_{btc,ethusd,usdjpy,usdcad}_5m-barlb.*
  echo sandbox/results/exness_pro_specs.json
  ls sandbox/results/exness_families_{usdcad,usdjpy,btc,ethusd}_5m-barlb_*.json
  ls sandbox/results/logs/barlb_batch*.txt sandbox/results/logs/run_5m_left.sh
  ls -d data/parquet/exness_{usdjpy,usdcad,btcusd,ethusd}_1m.parquet data/parquet/usdcad_1m.parquet data/parquet/btc_1m data/parquet/ethusd_1m data/parquet/usdjpy_1m* 2>/dev/null
} > "$LIST"
tar -czf - -T "$LIST" | ssh $MAC 'mkdir -p ~/quant && cd ~/quant && tar -xzf - && echo copied'
# batches 1-4 (batch 0 is already done here), 2 workers for 8 GB
ssh $MAC 'cd ~/quant && nohup env PY=.venv/bin/python SYMS="usdcad usdjpy btc ethusd" WORKERS=2 bash sandbox/results/logs/run_5m_left.sh >/dev/null 2>&1 & echo started on mac'
