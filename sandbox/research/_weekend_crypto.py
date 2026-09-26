"""The sealed crypto survivors, re-run with entries on Saturday and Sunday only.

No search. Every row is a strategy already in `sandbox/results/exness/` -- it
won its in-sample search, cleared the holdout and beat its coin-flip null --
replayed with its sealed parameters twice over the same window: entering on
every day, and entering only on the New York weekend (`ef.ENTRY_DAYS`). Both
runs use the live fill model, so the difference is the day filter and nothing
else.

    py -m sandbox.research._weekend_crypto              # holdout
    py -m sandbox.research._weekend_crypto --window is  # in-sample years
"""
from __future__ import annotations

import argparse
import glob
import json
import os

from sandbox.research import cfd_families as ef

HERE = os.path.dirname(os.path.abspath(__file__))
SURVIVORS = os.path.join(HERE, "..", "results", "exness")
WEEKEND = frozenset({5, 6})


def survivors():
    rows = []
    for path in sorted(glob.glob(os.path.join(SURVIVORS, "*.json"))):
        name = os.path.basename(path)
        if name.startswith("SURVIVORS"):
            continue
        with open(path, encoding="utf-8") as handle:
            row = json.load(handle)
        if row.get("asset_class") == "crypto" and row.get("symbol") in ("ethusd", "btc"):
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", choices=("oos", "is"), default="oos")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    rows = survivors()
    by_symbol = {}
    for row in rows:
        by_symbol.setdefault((row["symbol"], row["bar_minutes"]), []).append(row)

    out = []
    for (symbol, bar), members in sorted(by_symbol.items()):
        ef.resolve(symbol, allow_stale=True)
        ef.install_fills(symbol, bar, quiet=True)
        only = sorted({row["family"] for row in members})
        bars, ctx = ef.context(symbol, "validate", bar, only)
        lo, hi = ((ef.IS_END, ef.OOS_END) if args.window == "oos"
                  else (None, ef.IS_END))
        for row in members:
            params = ef.rehydrate(row["params"])
            result = {}
            for label, days in (("all", None), ("weekend", WEEKEND)):
                ef.ENTRY_DAYS = days
                stat = ef.backtest(row["family"], bars, ctx, params, lo=lo, hi=hi)
                result[label] = {k: stat.get(k) for k in
                                 ("return_pct", "max_dd_pct", "trades", "pf")}
            ef.ENTRY_DAYS = None
            out.append({"symbol": symbol, "family": row["family"],
                        "timeframe": row.get("timeframe"), **result})
            a, w = result["all"], result["weekend"]
            print(f"{symbol:7}{row['family']:22}"
                  f"all {a['return_pct']:+7.1f}% n={a['trades']:4d} pf={a['pf']:.2f}   "
                  f"wknd {w['return_pct']:+7.1f}% n={w['trades']:4d} "
                  f"pf={w['pf']:.2f} dd={w['max_dd_pct']:.1f}", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=1)


if __name__ == "__main__":
    main()
