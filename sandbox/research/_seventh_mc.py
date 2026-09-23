"""Monte Carlo band for one membership across a risk ladder, both windows.

A guarded, reusable version of the one-off scripts: `fr._run` opens a
multiprocessing Pool and on Windows every spawned child re-imports the main
module, so an unguarded call fork-bombs the machine
([[multiprocessing-needs-a-main-guard]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_mc \
        --add a:b,c:d --drop e:f --risks 0.11,0.12,0.13
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_mc.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", default="")
    parser.add_argument("--drop", default="")
    parser.add_argument("--risks", default="0.11,0.12,0.13")
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    live.arm()
    split = lambda s: tuple(x for x in s.replace(" ", "").split(",") if x)
    add, drop = split(args.add), split(args.drop)
    risks = [float(v) for v in split(args.risks)]
    n = len(ecs.BOOK) - len(drop) + len(add)

    print(f"{n} sleeves   add: {', '.join(add) or '-'}   "
          f"drop: {', '.join(drop) or '-'}   {args.paths} paths")
    out = {}
    for era in ("full", "oos"):
        jobs = [(drop, add, risk, seed)
                for risk in risks for seed in range(1, args.paths + 1)]
        print(f"\n=== era {era}: {len(jobs):,} replays ===", flush=True)
        rows = fr._run(jobs, args.workers, args.block_days, era, era)
        grouped = {}
        for row in rows:
            grouped.setdefault(row["risk"], []).append(row)
        for risk, group in grouped.items():
            out.setdefault(risk, {})[era] = fr._band(group)

    print(f"\n{'risk':>6}{'era':>6}{'p10':>8}{'p25':>8}{'p50':>8}{'p75':>8}"
          f"{'p90':>8}{'p95':>8}{'p99':>8}{'sprd':>7}"
          f"{'ret p10':>11}{'ret p50':>11}")
    table = []
    for risk in sorted(out):
        for era in ("full", "oos"):
            b = out[risk][era]
            print(f"{risk:>6.2f}{era:>6}{b['p10_dd']:>8.2f}{b['p25_dd']:>8.2f}"
                  f"{b['p50_dd']:>8.2f}{b['p75_dd']:>8.2f}{b['p90_dd']:>8.2f}"
                  f"{b['p95_dd']:>8.2f}{b['p99_dd']:>8.2f}"
                  f"{b['spread_dd']:>7.2f}{b['p10_ret']:>10,.0f}%"
                  f"{b['p50_ret']:>10,.0f}%")
        table.append({"risk": risk, "n": n,
                      "worst_p95": max(out[risk][e]["p95_dd"]
                                       for e in ("full", "oos")),
                      **{f"{e}_{k}": out[risk][e][k]
                         for e in ("full", "oos")
                         for k in ("p10_dd", "p50_dd", "p95_dd",
                                   "p10_ret", "p50_ret")}})

    path = OUT_PATH.replace(".json", f"_{args.tag}.json") if args.tag else OUT_PATH
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"add": list(add), "drop": list(drop), "paths": args.paths,
                   "book": list(ecs.BOOK), "table": table}, handle, indent=2)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
