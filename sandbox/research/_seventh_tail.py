"""Ruin, profitability and drawdown exceedance for the candidate books.

THREE QUESTIONS THE PERCENTILE LADDER DOES NOT ANSWER.

  P(ruin)          how often equity ever TOUCHES a fraction of the starting
                   balance. Not the same as drawdown: a 30% fall from a peak
                   reached after tripling leaves the account at 210% of start,
                   while a 30% fall in month one leaves it at 70%. Only the
                   low-water mark separates them, so `min_equity_ratio` is the
                   quantity and `mtm_dd_pct` is not.
  P(profit)        the share of paths finishing above the starting balance.
  exceedance       P(drawdown > X) at a ladder of levels. The p99 is by
                   CONSTRUCTION exceeded by 1% of paths -- that is what the
                   ninety-ninth percentile means -- so the useful form of the
                   question is what level each probability corresponds to.

A path that ever falls under the broker minimum stops being the book that was
tested: sleeves start refusing entries and the mix concentrates into the cheap
ones, pro-cyclically ([[minimum-lot-de-diversifies-pro-cyclically]]), so that
share is reported too.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_tail
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_tail.json")

BOOKS = (
    ("canon 20", ()),
    ("+climax", ("jp225:climax",)),
    ("+FD+ES", ("usdjpy:fracdiff", "es:regime_breakout")),
    ("+EP+FD+ES", ("ethusd:pullback", "usdjpy:fracdiff",
                   "es:regime_breakout")),
    ("+EP+FD+ES+cl", ("ethusd:pullback", "usdjpy:fracdiff",
                      "es:regime_breakout", "jp225:climax")),
)
DD_LEVELS = (15, 20, 25, 30, 40)
RUIN_LEVELS = (0.75, 0.5, 0.25)


def tail(rows):
    n = len(rows)
    dds = sorted(r["mtm_dd_pct"] for r in rows)
    lows = [r["min_equity_ratio"] for r in rows]
    rets = [r["return_pct"] for r in rows]
    cut = mc.quantile(dds, 95)
    out = {"paths": n,
           "p_profit": 100.0 * sum(1 for v in rets if v > 0) / n,
           "p_negative": 100.0 * sum(1 for v in rets if v <= 0) / n,
           "cvar95": statistics.fmean([v for v in dds if v >= cut]),
           "worst_dd": max(dds), "worst_ret": min(rets),
           "p95_dd": mc.quantile(dds, 95), "p99_dd": mc.quantile(dds, 99),
           "below_min": 100.0 * sum(1 for r in rows
                                    if r["below_broker_minimum"]) / n}
    for level in DD_LEVELS:
        out[f"dd_over_{level}"] = 100.0 * sum(1 for v in dds if v > level) / n
    for level in RUIN_LEVELS:
        out[f"touch_{level}"] = 100.0 * sum(1 for v in lows if v <= level) / n
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    args = parser.parse_args()
    live.arm()

    out = {}
    for era in ("full", "oos"):
        jobs = [((), add, args.risk, seed)
                for _label, add in BOOKS
                for seed in range(1, args.paths + 1)]
        print(f"\n=== era {era}: {len(jobs):,} replays ===", flush=True)
        rows = fr._run(jobs, args.workers, args.block_days, era, era)
        grouped = {}
        for row in rows:
            grouped.setdefault(tuple(row["add"]), []).append(row)
        for key, group in grouped.items():
            out.setdefault(key, {})[era] = tail(group)

    for era in ("full", "oos"):
        span = "2022-01 .. 2026-08" if era == "full" else "2025-01 .. 2026-08"
        print(f"\n{'=' * 100}\n{era.upper()}  {span}   risk {args.risk}   "
              f"${ecs.CANON_INITIAL:,.0f}   {args.paths} paths\n{'=' * 100}")
        print(f"{'book':15}{'P(profit)':>11}{'P(<75%)':>9}{'P(<50%)':>9}"
              f"{'P(<25%)':>9}{'dd>15':>8}{'dd>20':>8}{'dd>25':>8}"
              f"{'dd>30':>8}{'CVaR95':>9}{'worst':>8}")
        for label, add in BOOKS:
            t = out[tuple(add)][era]
            print(f"{label:15}{t['p_profit']:>10.1f}%{t['touch_0.75']:>8.1f}%"
                  f"{t['touch_0.5']:>8.1f}%{t['touch_0.25']:>8.1f}%"
                  f"{t['dd_over_15']:>7.1f}%{t['dd_over_20']:>7.1f}%"
                  f"{t['dd_over_25']:>7.1f}%{t['dd_over_30']:>7.1f}%"
                  f"{t['cvar95']:>8.2f}%{t['worst_dd']:>7.1f}%")
        print(f"\n  worst path return, and p95 / p99 for reference")
        for label, add in BOOKS:
            t = out[tuple(add)][era]
            print(f"  {label:15}worst return {t['worst_ret']:>+9,.0f}%   "
                  f"p95 {t['p95_dd']:>6.2f}   p99 {t['p99_dd']:>6.2f}   "
                  f"paths ever under broker min {t['below_min']:.1f}%")

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"paths": args.paths, "risk": args.risk,
                   "book": list(ecs.BOOK),
                   "results": {label: {e: out[tuple(add)][e]
                                       for e in ("full", "oos")}
                               for label, add in BOOKS}}, handle, indent=2)
    print(f"\n-> {OUT_PATH}")


if __name__ == "__main__":
    main()
