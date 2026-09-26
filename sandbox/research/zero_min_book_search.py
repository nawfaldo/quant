"""Full-history uncapped subset/risk search with a hard broker-lot floor.

This deliberately searches the recorded canon's membership on one continuous
$1,000 account.  A point is feasible only when it has at least ``--min-sleeves``
members, at least ``--min-symbols`` markets, no sizing cap, no refused trade,
and marked-to-market drawdown no greater than ``--max-dd``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as cs
from sandbox.research import cfd_families as ef


CORE = ("nq:ofi", "nq:drift_vwap", "nq:volatility_breakout")


def load(start_year: int):
    with open(cs.OUT_PATH, encoding="utf-8") as handle:
        members = json.load(handle)["members"]
    by_key = {f"{m['symbol']}:{m['family']}": m for m in members}
    missing = [key for key in cs.BOOK if key not in by_key]
    if missing:
        raise SystemExit(f"sealed artifact is not canon; missing {missing}")

    lo = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
    window = (f"{start_year}-01-01", "2026-08-16")
    logs, bars_by, ctx_by = {}, {}, {}
    for key in cs.BOOK:
        member = by_key[key]
        if key in cs.EXTERNAL:
            logs[key] = cs.external_trades(key, window=window)
        else:
            ef.resolve(member["symbol"], allow_stale=True)
            _result, log, bars, ctx = cs.sleeve_trades(
                member, lo=lo, hi=ef.OOS_END)
            logs[key] = log
            bars_by[member["symbol"]] = bars
            ctx_by[member["symbol"]] = ctx
    return by_key, logs, bars_by, ctx_by, lo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--max-dd", type=float, default=13.0)
    parser.add_argument("--min-sleeves", type=int, default=8)
    parser.add_argument("--min-symbols", type=int, default=6)
    parser.add_argument("--beam", type=int, default=36)
    parser.add_argument("--drop", default="",
                        help="comma-separated canon sleeves removed from the "
                             "starting anchor")
    parser.add_argument("--risks", default="0.20,0.25,0.30,0.35,0.40,0.45,0.50,0.55,0.60,0.70")
    parser.add_argument("--force-minimum-lot", action="store_true")
    parser.add_argument("--objective", choices=("return", "consistency"),
                        default="consistency")
    parser.add_argument("--out", default=os.path.join(
        cs.RESULTS, "zero_min_book_search.json"))
    args = parser.parse_args()

    risks = tuple(float(v) for v in args.risks.split(",") if v)
    cs.UNCAPPED = True
    cs.FORCE_MINIMUM_LOT = args.force_minimum_lot
    by_key, logs, bars_by, ctx_by, lo = load(args.start_year)
    cache = {}

    def evaluate(keys):
        ident = tuple(sorted(keys))
        if ident in cache:
            return cache[ident]
        members = [by_key[k] for k in ident]
        tested = {}

        def at(risk):
            if risk in tested:
                return tested[risk]
            book = cs.replay(
                members, logs, bars_by, ctx_by, scale=cs.SLEEVE_SCALE,
                sizing_cap={}, risk_scale=risk, gross_cap=None,
                lo=lo, hi=ef.OOS_END, fair_cap=False)
            refused = (book.get("refused_by_gross_cap", 0)
                       + sum((book.get("below_broker_minimum") or {}).values())
                       + sum((book.get("refused_by_sleeve") or {}).values()))
            months, sharpe, positive, count = cs.monthly(
                book["settled"], ef.INITIAL_BALANCE)
            row = {
                "keys": ident, "risk": risk,
                "return_pct": book["return_pct"],
                "mtm_dd_pct": book["mtm_dd_pct"],
                "closed_dd_pct": book["max_dd_pct"],
                "refused": refused,
                "below": dict(book.get("below_broker_minimum") or {}),
                "monthly_sharpe": sharpe,
                "positive_months": positive, "month_count": count,
                "worst_month_pct": min(
                    (v["return_pct"] for v in months.values()), default=0.0),
            }
            feasible = refused == 0 and book["mtm_dd_pct"] <= args.max_dd
            row["feasible"] = feasible
            # Feasible books rank on consistency first, then return.  Outside
            # the boundary, walk toward zero skipped trades and <= max DD.
            feasible_rank = ((book["return_pct"],
                              positive / max(count, 1), sharpe)
                             if args.objective == "return" else
                             (positive / max(count, 1), sharpe,
                              book["return_pct"]))
            row["rank"] = ((1, *feasible_rank) if feasible else
                           (0, -refused,
                            -max(0.0, book["mtm_dd_pct"] - args.max_dd),
                            book["return_pct"]))
            tested[risk] = row
            return row

        # Refusals fall monotonically in the useful region as risk rises. Find
        # the first grid point with zero skips by bisection instead of replaying
        # every obviously under-sized point. The final winner is still replayed
        # by the official report path before promotion.
        high = at(risks[-1])
        if high["refused"]:
            best = high
        else:
            left, right = 0, len(risks) - 1
            while left < right:
                middle = (left + right) // 2
                if at(risks[middle])["refused"] == 0:
                    right = middle
                else:
                    left = middle + 1
            first = left
            best = at(risks[first])
            # Once drawdown crosses the ceiling at zero skips, higher risk is
            # not useful. If there is room, walk upward to the best feasible
            # return/consistency point near the boundary.
            for index in range(first + 1, len(risks)):
                row = at(risks[index])
                if row["rank"] > best["rank"]:
                    best = row
                if row["refused"] == 0 and row["mtm_dd_pct"] > args.max_dd:
                    break
        best["risk_curve"] = [
            {k: v for k, v in tested[risk].items()
             if k not in ("rank", "keys", "below", "risk_curve")}
            for risk in sorted(tested)
        ]
        cache[ident] = best
        return best

    pre_dropped = {v for v in args.drop.replace(" ", "").split(",") if v}
    anchor = tuple(key for key in cs.BOOK if key not in pre_dropped)
    if not set(CORE).issubset(anchor):
        raise SystemExit("canon does not contain the required NQ core")
    frontier = [anchor]
    all_rows = []
    for size in range(len(anchor), args.min_sleeves - 1, -1):
        candidates = set()
        for keys in frontier:
            if len(keys) == size:
                candidates.add(tuple(sorted(keys)))
            if len(keys) > args.min_sleeves:
                for drop in keys:
                    if drop not in CORE:
                        candidates.add(tuple(sorted(set(keys) - {drop})))
        level = []
        for keys in candidates:
            if len(keys) != size:
                continue
            symbols = {key.split(":", 1)[0] for key in keys}
            if len(symbols) < args.min_symbols:
                continue
            row = evaluate(keys)
            level.append(row)
            all_rows.append(row)
        level.sort(key=lambda r: r["rank"], reverse=True)
        frontier = [r["keys"] for r in level[:args.beam]]
        if level:
            top = level[0]
            print(f"n={size:2d} tested={len(level):4d}  risk={top['risk']:.3f}  "
                  f"return={top['return_pct']:+.1f}% dd={top['mtm_dd_pct']:.2f}% "
                  f"below={top['refused']} months={top['positive_months']}/"
                  f"{top['month_count']} {'FEASIBLE' if top['feasible'] else ''}",
                  flush=True)
            print("  " + ",".join(top["keys"]), flush=True)
            checkpoint = {
                "through_sleeves": size, "tested_memberships": len(cache),
                "best_at_level": top,
                "feasible_so_far": sum(1 for r in all_rows if r["feasible"]),
            }
            with open(args.out + ".checkpoint", "w", encoding="utf-8") as handle:
                json.dump(checkpoint, handle, indent=2, sort_keys=True,
                          default=str)
                handle.write("\n")

    feasible = [r for r in all_rows if r["feasible"]]
    feasible.sort(key=lambda r: r["rank"], reverse=True)
    priced = feasible if feasible else sorted(
        all_rows, key=lambda r: r["rank"], reverse=True)
    payload = {
        "constraints": {"uncapped": True, "max_mtm_dd_pct": args.max_dd,
                        "zero_refused": True,
                        "force_minimum_lot": args.force_minimum_lot,
                        "min_sleeves": args.min_sleeves,
                        "min_symbols": args.min_symbols,
                        "core": CORE},
        "risks": risks, "tested_memberships": len(cache),
        "feasible_count": len(feasible),
        "best": priced[0] if priced else None,
        "top": priced[:25],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"wrote {args.out}; feasible={len(feasible)}", flush=True)


if __name__ == "__main__":
    main()
