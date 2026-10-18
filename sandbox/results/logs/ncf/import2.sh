#!/bin/bash
cd /c/Users/JawirGaming66/quant; N=sandbox/results/logs/ncf
until grep -q "finish_data done" $N/finish.log 2>/dev/null; do sleep 20; done
py tools/exness_import_1m.py --broker fundednext --symbols usdchf nzdusd gbpaud nth25 swi20 us2000 us30 vixcfd xauusd --first-year 2023 --to-date 2026-10-15 >> $N/import.log 2>&1
bash $N/finish_data.sh
