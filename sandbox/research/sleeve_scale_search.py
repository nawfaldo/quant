"""Tune canon sleeve risk multipliers on 2020-2024, then validate 2025-2026.

Membership, the global risk scale, uncapped sizing and forced broker minimums
stay fixed.  Imported NQ sleeves are frozen because their pre-holdout histories
are not uniform enough to tune without leaking the holdout into the weights.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_families as ef
from sandbox.research.zero_min_book_search import load


def stamp(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


def load_named(path: str, start_year: int):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    keys = payload.get("keys")
    if not keys:
        raise SystemExit("--book result has no keys")
    with open(cs.OUT_PATH, encoding="utf-8") as handle:
        canon = json.load(handle)["members"]
    by_key = cs.candidate_rows_exact(keys)
    by_key.update({f"{m['symbol']}:{m['family']}": m for m in canon
                   if f"{m['symbol']}:{m['family']}" in cs.EXTERNAL})
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise SystemExit(f"--book keys absent from sealed pool: {missing}")
    lo = stamp(f"{start_year}-01-01")
    window = (f"{start_year}-01-01", cs.CANON_DATA_END)
    logs, bars_by, ctx_by = {}, {}, {}
    for key in keys:
        member = by_key[key]
        print(f"  loading {key}", flush=True)
        if key in cs.EXTERNAL:
            logs[key] = cs.external_trades(key, window=window)
        else:
            ef.resolve(member["symbol"], allow_stale=True)
            _result, log, bars, ctx = cs.sleeve_trades(
                member, lo=lo, hi=ef.OOS_END)
            logs[key] = log
            bars_by[member["symbol"]] = bars
            ctx_by[member["symbol"]] = ctx
    return tuple(keys), by_key, logs, bars_by, ctx_by, lo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--train-end", default="2025-01-01")
    parser.add_argument("--max-dd", type=float, default=15.0)
    parser.add_argument("--annual-max-dd", type=float, default=None,
                        help="hard MTM drawdown ceiling applied separately to "
                             "every calendar year")
    parser.add_argument("--risk", type=float, default=cs.CANON_RISK_SCALE)
    parser.add_argument("--initial", type=float, default=ef.INITIAL_BALANCE,
                        help="starting account equity for every replay window")
    parser.add_argument("--book", default=None,
                        help="search-result JSON whose exact keys replace canon")
    parser.add_argument("--scales", default=None,
                        help="JSON containing optimized_scales used as the "
                             "starting multiplier map")
    parser.add_argument("--objective", choices=("return", "consistency"),
                        default="return")
    parser.add_argument("--levels",
                        default="0.50,0.75,1.00,1.25,1.50,1.75,2.00")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--tune-external", action="store_true",
                        help="also tune imported NQ sleeves; this is an "
                             "explicit full-window fit when train-end includes "
                             "their 2025-2026 history")
    parser.add_argument("--tune", default=None,
                        help="comma-separated sleeves to tune; all other "
                             "weights remain fixed at their starting values")
    parser.add_argument("--out", default=os.path.join(
        cs.RESULTS, "sleeve_scale_search.json"))
    args = parser.parse_args()

    cs.UNCAPPED = True
    cs.FORCE_MINIMUM_LOT = True
    if args.book:
        book_keys, by_key, logs, bars_by, ctx_by, lo = load_named(
            args.book, args.start_year)
    else:
        by_key, logs, bars_by, ctx_by, lo = load(args.start_year)
        book_keys = tuple(cs.BOOK)
    members = [by_key[key] for key in book_keys]
    train_hi = stamp(args.train_end)
    levels = tuple(float(v) for v in args.levels.split(",") if v)
    initial = {key: float(cs.SLEEVE_SCALE.get(key, 1.0)) for key in book_keys}
    if args.scales:
        with open(args.scales, encoding="utf-8") as handle:
            saved_scales = json.load(handle).get("optimized_scales") or {}
        initial.update({key: float(value) for key, value in saved_scales.items()
                        if key in initial})
    if args.tune:
        requested = [key for key in args.tune.replace(" ", "").split(",")
                     if key]
        missing_tunable = [key for key in requested if key not in book_keys]
        if missing_tunable:
            raise SystemExit(f"--tune sleeves absent from book: {missing_tunable}")
        tunable = requested
    else:
        tunable = [key for key in book_keys
                   if args.tune_external or key not in cs.EXTERNAL]
    cache = {}

    def annual_metrics(marked, hi):
        yearly_values = {}
        for ts, value in marked:
            if ts >= hi:
                continue
            year = datetime.fromtimestamp(ts, tz=timezone.utc).year
            yearly_values.setdefault(year, []).append(value)
        annual_dd, annual_return = {}, {}
        for year, values in yearly_values.items():
            peak, worst = values[0], 0.0
            for value in values:
                peak = max(peak, value)
                if peak > 0:
                    worst = max(worst, (peak - value) / peak)
            annual_dd[str(year)] = 100.0 * worst
            annual_return[str(year)] = (
                100.0 * (values[-1] / values[0] - 1.0)
                if values and values[0] > 0 else 0.0)
        return annual_dd, annual_return

    def evaluate(scales, hi):
        ident = (hi, tuple((key, round(scales[key], 8)) for key in book_keys))
        if ident in cache:
            return cache[ident]
        book = cs.replay(
            members, logs, bars_by, ctx_by, scale=scales, sizing_cap={},
            risk_scale=args.risk, gross_cap=None, lo=lo, hi=hi,
            fair_cap=False, initial=args.initial)
        months, sharpe, positive, count = cs.monthly(
            book["settled"], args.initial)
        refused = (book.get("refused_by_gross_cap", 0)
                   + sum((book.get("below_broker_minimum") or {}).values())
                   + sum((book.get("refused_by_sleeve") or {}).values()))
        annual_dd, annual_return = annual_metrics(book["marked"], hi)
        row = {
            "return_pct": book["return_pct"],
            "mtm_dd_pct": book["mtm_dd_pct"],
            "closed_dd_pct": book["max_dd_pct"],
            "trades": book["trades"], "refused": refused,
            "monthly_sharpe": sharpe, "positive_months": positive,
            "month_count": count,
            "worst_month_pct": min(
                (v["return_pct"] for v in months.values()), default=0.0),
            "annual_mtm_dd_pct": annual_dd,
            "annual_return_pct": annual_return,
            "max_annual_mtm_dd_pct": max(annual_dd.values(), default=0.0),
        }
        cache[ident] = row
        return row

    baseline_train = evaluate(initial, train_hi)
    min_positive = baseline_train["positive_months"]
    min_sharpe = baseline_train["monthly_sharpe"]

    def feasible(row):
        return (row["refused"] == 0
                and row["mtm_dd_pct"] < args.max_dd
                and (args.annual_max_dd is None
                     or row["max_annual_mtm_dd_pct"] <= args.annual_max_dd)
                and row["positive_months"] >= min_positive
                and row["monthly_sharpe"] >= min_sharpe)

    def rank(row):
        if feasible(row):
            quality = ((row["positive_months"], row["monthly_sharpe"],
                        row["return_pct"])
                       if args.objective == "consistency" else
                       (row["return_pct"], row["monthly_sharpe"],
                        row["positive_months"]))
            return (1, *quality, -row["mtm_dd_pct"])
        violation = (row["refused"] * 1000.0
                     + max(0.0, row["mtm_dd_pct"] - args.max_dd) * 100.0
                     + (max(0.0, row["max_annual_mtm_dd_pct"]
                                   - args.annual_max_dd) * 100.0
                        if args.annual_max_dd is not None else 0.0)
                     + max(0, min_positive - row["positive_months"]) * 20.0
                     + max(0.0, min_sharpe - row["monthly_sharpe"]) * 10.0)
        return (0, -violation, row["return_pct"], -row["mtm_dd_pct"])

    print("TRAIN BASELINE  "
          f"return={baseline_train['return_pct']:+.1f}% "
          f"dd={baseline_train['mtm_dd_pct']:.2f}% "
          f"annual-max={baseline_train['max_annual_mtm_dd_pct']:.2f}% "
          f"months={min_positive}/{baseline_train['month_count']} "
          f"Sharpe={min_sharpe:.2f}", flush=True)

    scales = dict(initial)
    trail = []
    for pass_no in range(1, args.passes + 1):
        changed = False
        for key in tunable:
            before = scales[key]
            choices = []
            for value in sorted(set(levels + (before, initial[key]))):
                trial = dict(scales)
                trial[key] = value
                row = evaluate(trial, train_hi)
                choices.append((rank(row), value, row))
            _score, value, row = max(choices, key=lambda item: item[0])
            scales[key] = value
            changed |= value != before
            trail.append({"pass": pass_no, "sleeve": key, "before": before,
                          "chosen": value, "metrics": row})
            print(f"pass {pass_no} {key:28} {before:>4.2f}->{value:>4.2f}  "
                  f"return={row['return_pct']:+8.1f}% "
                  f"dd={row['mtm_dd_pct']:>5.2f}% "
                  f"annual-max={row['max_annual_mtm_dd_pct']:>5.2f}% "
                  f"months={row['positive_months']}/{row['month_count']} "
                  f"Sharpe={row['monthly_sharpe']:.2f}", flush=True)
        if not changed:
            break

    oos_lo = train_hi
    end = ef.OOS_END

    def window_metrics(scales, window_lo, window_hi):
        old_lo = lo
        book = cs.replay(
            members, logs, bars_by, ctx_by, scale=scales, sizing_cap={},
            risk_scale=args.risk, gross_cap=None, lo=window_lo, hi=window_hi,
            fair_cap=False, initial=args.initial)
        months, sharpe, positive, count = cs.monthly(
            book["settled"], args.initial)
        annual_dd, annual_return = annual_metrics(book["marked"], window_hi)
        return {
            "return_pct": book["return_pct"],
            "mtm_dd_pct": book["mtm_dd_pct"],
            "closed_dd_pct": book["max_dd_pct"], "trades": book["trades"],
            "below_broker_minimum": book.get("below_broker_minimum") or {},
            "monthly_sharpe": sharpe, "positive_months": positive,
            "month_count": count,
            "annual_mtm_dd_pct": annual_dd,
            "annual_return_pct": annual_return,
            "max_annual_mtm_dd_pct": max(annual_dd.values(), default=0.0),
            "worst_month_pct": min(
                (v["return_pct"] for v in months.values()), default=0.0),
        }

    result = {
        "method": "coordinate search on 2020-2024 only",
        "constraints": {"uncapped": True, "force_minimum_lot": True,
                        "global_risk_scale": args.risk,
                        "initial_balance": args.initial,
                        "train_max_mtm_dd_pct": args.max_dd,
                        "annual_max_mtm_dd_pct": args.annual_max_dd,
                        "minimum_train_positive_months": min_positive,
                        "minimum_train_monthly_sharpe": min_sharpe,
                        "objective": args.objective,
                        "frozen_sleeves": list(cs.EXTERNAL)},
        "baseline_scales": initial, "optimized_scales": scales,
        "baseline": {
            "train_2020_2024": baseline_train,
            "oos_2025_2026": window_metrics(initial, oos_lo, end),
            "full_2020_2026": window_metrics(initial, lo, end),
        },
        "optimized": {
            "train_2020_2024": evaluate(scales, train_hi),
            "oos_2025_2026": window_metrics(scales, oos_lo, end),
            "full_2020_2026": window_metrics(scales, lo, end),
        },
        "evaluations": len(cache), "trail": trail,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print("\nOPTIMIZED SCALES", flush=True)
    for key in book_keys:
        print(f"  {key:28} {initial[key]:.2f} -> {scales[key]:.2f}",
              flush=True)
    for name in ("train_2020_2024", "oos_2025_2026", "full_2020_2026"):
        old, new = result["baseline"][name], result["optimized"][name]
        print(f"{name}: return {old['return_pct']:+.1f}% -> "
              f"{new['return_pct']:+.1f}%, DD {old['mtm_dd_pct']:.2f}% -> "
              f"{new['mtm_dd_pct']:.2f}%, months "
              f"{old['positive_months']}/{old['month_count']} -> "
              f"{new['positive_months']}/{new['month_count']}", flush=True)
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
