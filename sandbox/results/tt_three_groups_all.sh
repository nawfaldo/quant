#!/bin/bash
# resume 2026-09-28: per group select -> validate -> why --winners -> export, 5/15/30/60/240m
cd /c/Users/JawirGaming66/quant
B=5,15,30,60,240
for g in thequantbuilder quantpad tradingdata2; do
  if true; then
    echo "=== $g select $(date)"
    PYTHONPATH=. py -m sandbox.research.cfd_tt_families select --groups $g --bar-minutes $B --missing > sandbox/results/tt_${g}_select.out 2>&1
  fi
  echo "=== $g validate $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families validate --groups $g --bar-minutes $B > sandbox/results/tt_${g}_validate.out 2>&1
  echo "=== $g why $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families why --winners --groups $g --bar-minutes $B > sandbox/results/tt_${g}_why.out 2>&1
  echo "=== $g export $(date)"
  PYTHONPATH=. py -m sandbox.research._tiktok_export --group $g > sandbox/results/tt_${g}_export.out 2>&1
  echo "=== $g done $(date)"
done
echo "=== ALL DONE $(date)"
