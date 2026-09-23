"""The return/drawdown efficient frontier: most return per p95 drawdown bucket.

THE QUESTION. "What is the highest return available at each p95 MTM drawdown
level?" -- reported in 0.5-point buckets, with the membership and risk that
reach it.

WHY THIS IS A MEMBERSHIP SEARCH AND NOT A RISK SWEEP. On this book `risk_scale`
is nearly inert against p95: 0.080 -> 0.150 moves it 23.63 -> 24.02 while return
goes 3,646% -> 28,402%, because at $500 the broker's minimum lot is a large
share of equity and a smaller risk request still places the minimum
([[oos-drawdown-ignores-the-risk-dial]], [[min-lot-pinned-sleeves-do-not-compound]]).
So each MEMBERSHIP has a p95 floor it cannot be sized under, and the frontier is
built by walking membership down through those floors and then taking the
highest risk each one can carry.

HOW THE CANDIDATES ARE ORDERED. Drops are applied cheapest-first by RETURN LOST
PER DRAWDOWN POINT BOUGHT, measured by `_mc_live_frontier screen` on the 40
worst block orders -- not by standalone return, which values a sleeve by what it
makes alone rather than by what removing it costs
([[drop-test-not-standalone-return-values-a-sleeve]]). Adds are the three cells
that cut tail drawdown while ADDING return.

WHAT IT CANNOT ESTABLISH. Every cell is scored on the window its members were
selected on, so the frontier is fitted by construction and the LEVEL of return
on it is not a forecast ([[selection-gate-manufactures-drawdown-and-consistency]]).
The SHAPE -- what a drawdown point costs -- is the usable part.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_frontier --paths 300
    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_frontier \
        --paths 1000 --only 12,17,23
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_frontier.json")

#: Cheapest drawdown first: return surrendered per p95 point bought, from the
#: tail-40 screen of 2026-09-03. A sleeve absent here RAISED drawdown when
#: dropped -- it is a damper and is never offered for removal.
DROP_ORDER = (
    "ukoil:xma_cross",            # 5,546 return per dd point
    "jp225:volume_thrust",        # 8,437
    "ethusd:idio_break",          # 10,339
    "jp225:volatility_breakout",  # 14,203
    "usdjpy:pullback",            # 15,878
    "ustec:volatility_breakout",  # 17,127
    "ustec:kendall",              # 19,586
    "eurjpy:two_stage",           # 21,118
    "audusd:zscore",              # 21,417
    "usdjpy:volume_thrust",       # 24,945
    "jp225:cusum",                # 29,966
    "jp225:break_retest",         # 32,257
)

#: Cells that cut tail drawdown AND add return. BTC cells qualify too and are
#: excluded on the operator's standing bar for that symbol.
ADD_SETS = {
    "": (),
    "+3dmp": ("eurjpy:gated_orb", "usoil:momentum", "usoil:aroon"),
}

RISKS = (0.10, 0.13, 0.16, 0.19, 0.22)


def cells():
    out = []
    for add_tag, add in ADD_SETS.items():
        for n in range(len(DROP_ORDER) + 1):
            drop = DROP_ORDER[:n]
            out.append({"drop": drop, "add": add, "add_tag": add_tag,
                        "n": len(ecs.BOOK) - len(drop) + len(add)})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=300)
    parser.add_argument("--workers", type=int, default=(os.cpu_count() or 8))
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--era", default="full", choices=("is", "oos", "full"))
    parser.add_argument("--only", default="",
                        help="comma-separated cell indices, for a re-run at "
                             "more paths")
    parser.add_argument("--risks", default="")
    args = parser.parse_args()
    live.arm()

    risks = ([float(v) for v in args.risks.split(",") if v] if args.risks
             else list(RISKS))
    grid = cells()
    if args.only:
        keep = {int(v) for v in args.only.split(",") if v}
        grid = [c for i, c in enumerate(grid) if i in keep]

    jobs = []
    for index, cell in enumerate(grid):
        for risk in risks:
            for seed in range(1, args.paths + 1):
                jobs.append((tuple(cell["drop"]), tuple(cell["add"]), risk,
                             seed))
    print(f"{len(grid)} memberships x {len(risks)} risks x {args.paths} paths "
          f"= {len(jobs):,} replays, era {args.era}", flush=True)

    rows = fr._run(jobs, args.workers, args.block_days, "frontier", args.era)

    grouped = {}
    for row in rows:
        key = (tuple(row["drop"]), tuple(row["add"]), row["risk"])
        grouped.setdefault(key, []).append(row)

    points = []
    for (drop, add, risk), group in grouped.items():
        band = fr._band(group)
        points.append({"drop": list(drop), "add": list(add), "risk": risk,
                       "n": len(ecs.BOOK) - len(drop) + len(add),
                       "p95_dd": band["p95_dd"], "p90_dd": band["p90_dd"],
                       "p50_dd": band["p50_dd"], "p10_dd": band["p10_dd"],
                       "ret_p50": band["p50_ret"], "ret_p10": band["p10_ret"]})
    points.sort(key=lambda r: r["p95_dd"])

    print(f"\n{'p95 bucket':13}{'best p95':>10}{'n':>4}{'risk':>7}"
          f"{'ret p50':>12}{'ret p10':>12}{'p50 dd':>9}  membership")
    buckets = {}
    for row in points:
        key = int(row["p95_dd"] * 2) / 2.0
        if key not in buckets or row["ret_p50"] > buckets[key]["ret_p50"]:
            buckets[key] = row
    best = None
    for key in sorted(buckets):
        row = buckets[key]
        # The frontier is the RUNNING maximum: a bucket whose best cell earns
        # less than a tighter bucket's is dominated and is not on it.
        if best is not None and row["ret_p50"] <= best:
            continue
        best = row["ret_p50"]
        label = []
        if row["drop"]:
            label.append(f"-{len(row['drop'])}: " + ", ".join(
                d.split(":")[0] + ":" + d.split(":")[1][:9]
                for d in row["drop"][-2:]))
        if row["add"]:
            label.append("+3dmp")
        print(f"{key:>5.1f}-{key + 0.5:<7.1f}{row['p95_dd']:>10.2f}"
              f"{row['n']:>4}{row['risk']:>7.2f}{row['ret_p50']:>11,.0f}%"
              f"{row['ret_p10']:>11,.0f}%{row['p50_dd']:>9.2f}  "
              f"{' | '.join(label) or 'canon'}")

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"era": args.era, "paths": args.paths, "risks": risks,
                   "book": list(ecs.BOOK), "points": points}, handle, indent=2)
    print(f"\n{len(points)} cells -> {OUT_PATH}")


if __name__ == "__main__":
    main()
