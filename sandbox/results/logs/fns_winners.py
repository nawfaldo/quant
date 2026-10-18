"""Print the holdout winners of one sealed search file, comma-separated.

usage: fns_winners.py <prefix> <symbol> <bar label> <rth|off>
A winner: a family whose `select` picked a cell AND whose validation (OOS)
returned > 0 with profit factor >= 1.05 on >= MIN_TRADES trades.
"""
import glob
import json
import os
import sys

MIN_TRADES = 20

prefix, symbol, label, window = sys.argv[1:5]
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
paths = [p for p in glob.glob(os.path.join(here, f"{prefix}_{symbol}_{label}*.json"))
         if ("_win-off" in p) == (window == "off") and "_null_" not in p]
if not paths:
    sys.exit(0)
with open(max(paths, key=os.path.getmtime), encoding="utf-8") as handle:
    data = json.load(handle)
validation = data.get("validation") or {}
print(",".join(
    family for family, cell in (data.get("families") or {}).items()
    if cell and (validation.get(family) or {}).get("oos")
    and validation[family]["oos"]["return_pct"] > 0
    and (validation[family]["oos"].get("pf") or 0) >= 1.05
    and validation[family]["oos"]["trades"] >= MIN_TRADES))
