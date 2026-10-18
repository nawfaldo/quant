#!/bin/bash
# 2026-10-03 wave: the 18 groups extracted 2026-10-03, per group
# select (IS) -> validate (OOS on IS winners) -> why --winners (coin flip on
# OOS winners) -> export PASS / PASS* into sandbox/results/exness/.
# Symbols: cfd_tt_families DEFAULT_SYMBOLS (us500, ustec, non-US indices,
# FX pairs, btc, ethusd). Bars as the earlier TikTok waves. All cores.
cd /c/Users/JawirGaming66/quant
B=5,15,30,60,240
for g in tradingrush codetrading quantifiedstrategies tradingmythbusters algovibes fluxchart \
         neurotrader quantitativo parttimelarry artybryjatma tst_life setup4alpha tomtrades \
         bionicnq jasonjets quantcrawler deltatrendtrading okala8020; do
  echo "=== $g select $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families select --groups $g --bar-minutes $B --missing > sandbox/results/tt_${g}_select.out 2>&1
  echo "=== $g validate $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families validate --groups $g --bar-minutes $B > sandbox/results/tt_${g}_validate.out 2>&1
  echo "=== $g why $(date)"
  PYTHONPATH=. py -m sandbox.research.cfd_tt_families why --winners --groups $g --bar-minutes $B > sandbox/results/tt_${g}_why.out 2>&1
  echo "=== $g export $(date)"
  PYTHONPATH=. py -m sandbox.research._tiktok_export --group $g > sandbox/results/tt_${g}_export.out 2>&1
  grep -h "PASS\|wrote\|survivor" sandbox/results/tt_${g}_export.out | tail -3
  echo "=== $g done $(date)"
done
echo "=== ALL DONE $(date)"
