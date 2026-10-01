#!/bin/bash
# quantpad, thequantbuilder, tradingdata2: select then validate, 5/15/30/60/240m
cd /c/Users/JawirGaming66/quant
for g in thequantbuilder quantpad tradingdata2; do
  echo "=== $g select $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families select --groups $g --bar-minutes 5,15,30,60,240 > sandbox/results/tt_${g}_select.out 2>&1
  echo "=== $g validate $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families validate --groups $g --bar-minutes 5,15,30,60,240 > sandbox/results/tt_${g}_validate.out 2>&1
  echo "=== $g done $(date)"
done
