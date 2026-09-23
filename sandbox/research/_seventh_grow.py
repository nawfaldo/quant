"""Grow the book for consistency and return, under a Monte Carlo tail budget.

THE OBJECTIVE. Add decay-clean sleeves while p95 MTM drawdown stays at or under
`--ceiling` (23% by default) AND the p99 stays within `--spread` of the p95, so
the distribution does not simply grow a fat tail beyond the point being
measured. Among candidates that satisfy both, the one taken is the one that most
improves MEDIAN positive months, with median return breaking ties.

EVERY GUARD IS A DISTRIBUTION AND NOT A PATH. A greedy guarded on realised
drawdown ran 15.6% realised against a 35.1% Monte Carlo p95 and was seated on
that basis for an hour ([[refill-guarded-on-one-path-hides-its-tail]]). Here a
candidate is judged on `--paths` bootstrap paths at every step, and the winner is
re-measured at `--confirm` paths on both windows before anything is reported.

WHY p99 - p95 IS A SEPARATE GATE. A sleeve can leave p95 flat and still add
paths far beyond it; the band would pass and the book would be worse. The gate
asks that the tail past the reported point be thin, not just that the reported
point be low.

CANDIDATES are the decay-clean bench: positive R in BOTH 2025 and 2026, 2026 mean
at least 80% of the 2022-2025 mean, at least 25 trades in 2026, no BTC (barred),
no NQ (barred, and one sleeve cannot pay for its feed
[[one-sleeve-cannot-pay-for-a-data-feed]]), and no inert-gate clone of a sleeve
already held ([[inert-gate-clones-a-family]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_grow \
        --ceiling 23 --paths 300 --confirm 1000 --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_grow.json")
#: `jp225:regime_breakout` is the inert-gate clone of `jp225:volatility_breakout`
#: ([[inert-gate-clones-a-family]]). `audjpy:regime_breakout` is barred by the
#: operator (2026-09-04); it was seated by an earlier run of this search and
#: rejected -- it tied on median months and won only on the return tie-break,
#: while costing a p10 month and 0.3 of p95.
BAN = {"jp225:regime_breakout", "audjpy:regime_breakout"}


def bench(state, held):
    """Decay-clean candidates not already held."""
    def year_r(log):
        by = {}
        for t in log:
            d = t.get("distance") or 0
            if d <= 0:
                continue
            y = datetime.fromtimestamp(t["exit_ts"], tz=timezone.utc).year
            by.setdefault(y, []).append(t["gross"] / d)
        return by

    out = []
    for key, log in state["logs"].items():
        if key in held or key in BAN or key.split(":")[0] in ("btc", "nq"):
            continue
        by = year_r(log)
        r26, r25 = by.get(2026, []), by.get(2025, [])
        early = [r for y in (2022, 2023, 2024, 2025) for r in by.get(y, [])]
        if len(r26) < 25 or not r25 or not early:
            continue
        m26, me = sum(r26) / len(r26), sum(early) / len(early)
        if me <= 0 or sum(r26) <= 0 or sum(r25) <= 0 or m26 / me < 0.8:
            continue
        out.append(key)
    return sorted(out)


def band(rows):
    dds = sorted(r["mtm_dd_pct"] for r in rows)
    rets = sorted(r["return_pct"] for r in rows)
    months = sorted(r["positive_months"] for r in rows)
    uw = sorted(r["under_water_days"] for r in rows)
    worst = sorted(r["worst_month_pct"] for r in rows)
    q = mc.quantile
    return {"p50_dd": q(dds, 50), "p90_dd": q(dds, 90), "p95_dd": q(dds, 95),
            "p99_dd": q(dds, 99), "spread": q(dds, 99) - q(dds, 95),
            "p50_ret": q(rets, 50), "p10_ret": q(rets, 10),
            "p50_months": q(months, 50), "p10_months": q(months, 10),
            "p50_uw": q(uw, 50), "p50_worst_month": q(worst, 50),
            "paths": len(rows)}


def evaluate(cells, paths, workers, block_days, era="full"):
    """`{add_tuple: band}` for each membership, one pool for the batch."""
    jobs = [((), tuple(add), None, seed)
            for add in cells for seed in range(1, paths + 1)]
    rows = fr._run(jobs, workers, block_days, "grow", era)
    grouped = {}
    for row in rows:
        grouped.setdefault(tuple(row["add"]), []).append(row)
    return {key: band(rows) for key, rows in grouped.items()}


def oos_filter(state, chosen, pool, slack_uw, lo, hi):
    """Drop candidates that damage the HOLDOUT before any path is spent on them.

    THE SEARCH RANKS ON THE FULL WINDOW AND THAT WINDOW CANNOT SEE THIS. A
    4.7-year median averages over the twenty months the account is about to
    trade: the first run of this search seated a sleeve whose full-window median
    time-underwater was unchanged at 91 days while it took the REALISED holdout
    figure from 49 to 115. One replay per candidate costs nothing next to 300
    bootstrap paths, so the holdout is checked first and the pool reaches the
    Monte Carlo already free of that failure.

    A single ordering is weak evidence for a tail and strong evidence for a
    blow-up: this rejects only candidates that make the holdout materially
    WORSE, and never promotes one for looking good on it
    ([[refill-guarded-on-one-path-hides-its-tail]]).
    """
    def run(keys):
        book = fr._replay([state["members"][k] for k in keys], lo=lo, hi=hi,
                          risk_scale=ecs.CANON_RISK_SCALE)
        _series, _sharpe, positive, _total = ecs.monthly(book["settled"],
                                                         ecs.CANON_INITIAL)
        curve = [(ts - lo, v) for ts, v in book["marked"]]
        return mc.underwater(curve), positive

    base_uw, base_months = run(list(ecs.BOOK) + chosen)
    keep, cut = [], []
    for cand in pool:
        uw, months = run(list(ecs.BOOK) + chosen + [cand])
        if uw > base_uw + slack_uw or months < base_months:
            cut.append((cand, uw, months))
        else:
            keep.append(cand)
    print(f"  OOS pre-filter: holdout underwater {base_uw:.0f} days, "
          f"{base_months} positive months; {len(keep)} of {len(pool)} pass "
          f"(slack {slack_uw:.0f}d)")
    for cand, uw, months in sorted(cut, key=lambda c: -c[1])[:6]:
        print(f"    cut {cand:28} uw {uw:>6.0f}d  months {months}")
    return keep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ceiling", type=float, default=23.0)
    parser.add_argument("--spread", type=float, default=3.0,
                        help="max p99 - p95, in drawdown points")
    parser.add_argument("--paths", type=int, default=300)
    parser.add_argument("--confirm", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--slack-uw", type=float, default=15.0,
                        help="days of HOLDOUT time-underwater a candidate may "
                             "add before it is cut without being replayed")
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--report-only", action="store_true",
                        help="score every holdout-clean candidate and print the "
                             "ranked table without seating anything, so the "
                             "binding gate can be READ rather than inferred")
    args = parser.parse_args()
    live.arm()

    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    pool = bench(state, set(ecs.BOOK))
    print(f"canon {len(ecs.BOOK)} sleeves, {len(pool)} decay-clean candidates")
    print(f"gates: p95 <= {args.ceiling}, p99 - p95 <= {args.spread}, "
          f"{args.paths} paths per cell\n", flush=True)

    chosen, trail = [], []
    base = evaluate([tuple(chosen)], args.paths, args.workers,
                    args.block_days)[tuple(chosen)]
    print(f"{'step':5}{'sleeve':28}{'p50 mo':>8}{'p10 mo':>8}{'p95':>7}"
          f"{'p99':>7}{'sprd':>6}{'p50 ret':>11}{'p50 uw':>8}")
    print(f"{'base':5}{'':28}{base['p50_months']:>8.0f}"
          f"{base['p10_months']:>8.0f}{base['p95_dd']:>7.2f}"
          f"{base['p99_dd']:>7.2f}{base['spread']:>6.2f}"
          f"{base['p50_ret']:>10,.0f}%{base['p50_uw']:>8.0f}", flush=True)

    oos_lo, oos_hi = fr._bounds("oos")
    for step in range(1, args.rounds + 1):
        live_pool = oos_filter(state, chosen,
                               [c for c in pool if c not in chosen],
                               args.slack_uw, oos_lo, oos_hi)
        cells = [tuple(chosen + [c]) for c in live_pool]
        if not cells:
            print(f"\nstep {step}: nothing survives the holdout filter; "
                  f"stopping")
            break
        bands = evaluate(cells, args.paths, args.workers, args.block_days)
        if args.report_only:
            print(f"\n{'candidate':30}{'p10 mo':>8}{'p50 mo':>8}{'p95':>7}"
                  f"{'p99':>7}{'sprd':>6}{'p50 ret':>11}{'p50 uw':>8}  gate")
            for key in sorted(bands, key=lambda k: (-bands[k]["p10_months"],
                                                    bands[k]["p95_dd"])):
                b = bands[key]
                why = []
                if b["p95_dd"] > args.ceiling:
                    why.append(f"p95 {b['p95_dd'] - args.ceiling:+.2f}")
                if b["spread"] > args.spread:
                    why.append(f"sprd {b['spread'] - args.spread:+.2f}")
                print(f"{key[-1][:29]:30}{b['p10_months']:>8.0f}"
                      f"{b['p50_months']:>8.0f}{b['p95_dd']:>7.2f}"
                      f"{b['p99_dd']:>7.2f}{b['spread']:>6.2f}"
                      f"{b['p50_ret']:>10,.0f}%{b['p50_uw']:>8.0f}  "
                      f"{', '.join(why) or 'PASS'}")
            return
        ok = {k: b for k, b in bands.items()
              if b["p95_dd"] <= args.ceiling and b["spread"] <= args.spread}
        if not ok:
            print(f"\nstep {step}: no candidate keeps p95 <= {args.ceiling} "
                  f"with p99 - p95 <= {args.spread}; stopping")
            break
        # RANKED ON p10 MONTHS, NOT THE MEDIAN. Ranking on the median let a
        # candidate tie there and win on the return tie-break while giving back
        # a p10 month -- the median is what a good draw looks like and the p10
        # is what a bad one does, and consistency is a claim about bad draws.
        best = max(ok, key=lambda k: (ok[k]["p10_months"], ok[k]["p50_months"],
                                      ok[k]["p50_ret"]))
        b = ok[best]
        if (b["p10_months"], b["p50_months"]) <= (base["p10_months"],
                                                  base["p50_months"]):
            print(f"\nstep {step}: nothing improves median months or return; "
                  f"stopping")
            break
        chosen = list(best)
        base = b
        trail.append({"add": chosen[-1], "n": len(ecs.BOOK) + len(chosen),
                      **b})
        print(f"{step:<5}{'+' + chosen[-1]:28}{b['p50_months']:>8.0f}"
              f"{b['p10_months']:>8.0f}{b['p95_dd']:>7.2f}{b['p99_dd']:>7.2f}"
              f"{b['spread']:>6.2f}{b['p50_ret']:>10,.0f}%{b['p50_uw']:>8.0f}",
              flush=True)

    if not chosen:
        print("\nnothing seated")
        return

    print(f"\nCONFIRMING {len(chosen)} additions at {args.confirm} paths, "
          f"both windows", flush=True)
    final = {}
    for era in ("full", "oos"):
        got = evaluate([(), tuple(chosen)], args.confirm, args.workers,
                       args.block_days, era)
        final[era] = got
    print(f"\n{'book':16}{'era':>6}{'p50 mo':>8}{'p10 mo':>8}{'p95':>7}"
          f"{'p99':>7}{'sprd':>6}{'p50 ret':>11}{'p10 ret':>11}{'p50 uw':>8}")
    for label, key in (("canon", ()), (f"+{len(chosen)}", tuple(chosen))):
        for era in ("full", "oos"):
            b = final[era][key]
            print(f"{label:16}{era:>6}{b['p50_months']:>8.0f}"
                  f"{b['p10_months']:>8.0f}{b['p95_dd']:>7.2f}"
                  f"{b['p99_dd']:>7.2f}{b['spread']:>6.2f}"
                  f"{b['p50_ret']:>10,.0f}%{b['p10_ret']:>10,.0f}%"
                  f"{b['p50_uw']:>8.0f}")
    print(f"\nadded: {', '.join(chosen)}")

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"book": list(ecs.BOOK), "added": chosen, "trail": trail,
                   "ceiling": args.ceiling, "spread": args.spread,
                   "confirm": {e: {str(k): v for k, v in final[e].items()}
                               for e in final}}, handle, indent=2)
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
