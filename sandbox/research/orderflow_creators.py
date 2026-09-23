"""Baseline comparison of the implemented orderflow creators' setups.

Defaults only. No search runs here and none should: the Fabervaale campaign
already showed what an eleven-month in-sample window does to a staged
optimizer, and the point of this module is to see which of these setups is
worth spending a search on at all. Both windows are reported for every
strategy, but neither is used to choose anything, so 2026 stays a holdout for
whichever candidate is taken forward.

    py -B -m sandbox.research.orderflow_creators
"""
import argparse
import json
import os

from sandbox import data, execution, metrics
from sandbox.strategies.fabervaale_orderflow import FabervaaleOrderflow
from sandbox.strategies.stacked_imbalance import StackedImbalance
from sandbox.strategies.unfinished_auction import UnfinishedAuction
from sandbox.strategies.volume_accumulation import VolumeAccumulation

IS_FROM, IS_TO = "2025-02-12", "2026-01-01"
OOS_FROM = "2026-01-01"

CANDIDATES = (
    ("Fabervaale IVB2 (breakout + orderflow confirmation)", FabervaaleOrderflow, {}),
    ("Footprint stacked imbalance (continuation)", StackedImbalance, {}),
    ("Orderflows unfinished auction (Valtos, magnet)", UnfinishedAuction, {}),
    ("Volume accumulation pullback (Trader Dale)", VolumeAccumulation, {}),
)


def window(fills, ex, lo, hi):
    inside = [fill for fill in fills if lo <= fill.entry_ts < hi]
    stat = metrics.stats(execution.size(inside, ex), initial=ex.initial, span=(lo, hi))
    stat["return_pct"] = round(100.0 * stat["pnl"] / ex.initial, 2)
    return stat


def run(use_cached=True):
    bars = (data.load_cached_level_two_bars() if use_cached
            else data.load_level_two_bars())
    features = (data.load_cached_l2_features() if use_cached
                else data.load_l2_features())
    shared = {
        "features": features,
        "vix": data.vix_series(bars, data.load_hourly_vix()),
        "atr": data.atr_by_day(bars, 20),
        "closes": data.load_session_closes(),
        "footprint": data.load_footprint_features(),
    }
    lo, hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    oos_lo, oos_hi = metrics.split_ts(OOS_FROM), bars[-1][0] + 86_400

    rows = []
    for label, factory, overrides in CANDIDATES:
        strategy = factory()
        params = strategy.all_params(overrides)
        signals = strategy.signals(bars, shared, "all", params)
        fills = execution.resolve(bars, signals, strategy.execution)
        rows.append({
            "label": label,
            "strategy": strategy.name,
            "signals": len(signals),
            "in_sample": window(fills, strategy.execution, lo, hi),
            "out_of_sample": window(fills, strategy.execution, oos_lo, oos_hi),
        })
    return {"in_sample": [IS_FROM, IS_TO], "out_of_sample": [OOS_FROM, "end"],
            "note": "defaults only; no parameters were searched",
            "candidates": rows}


def print_report(report):
    header = (f"{'strategy':<46} {'win':>4} {'pnl':>9} {'ret%':>7} {'tr':>5} "
              f"{'pf':>6} {'dd':>7} {'mSh':>6} {'pos':>6} {'strk':>5}")
    print(f"in sample {report['in_sample']}   "
          f"out of sample {report['out_of_sample']}")
    print(f"\n{header}")
    print("-" * len(header))
    for row in report["candidates"]:
        for name, stat in (("IS", row["in_sample"]), ("OOS", row["out_of_sample"])):
            label = row["label"] if name == "IS" else ""
            print(f"{label:<46} {name:>4} {stat['pnl']:>9.2f} "
                  f"{stat['return_pct']:>7.2f} {stat['trades']:>5} "
                  f"{stat['pf']:>6.2f} {stat['max_dd']:>7.2f} "
                  f"{stat['msharpe']:>6.2f} "
                  f"{stat['pos_months']:>3}/{stat['n_months']:<2} "
                  f"{stat['max_loss_streak']:>5}")
        print()

    for row in report["candidates"]:
        print(f"\n{row['label']} — monthly")
        for name, stat in (("IS ", row["in_sample"]), ("OOS", row["out_of_sample"])):
            for month, value in stat["months"].items():
                mark = "+" if value > 0 else ("-" if value < 0 else ".")
                print(f"  {name} {month} {value:>9.2f}  "
                      f"{mark * min(28, int(abs(value)))}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--out",
                        default="sandbox/results/orderflow_creators.json")
    args = parser.parse_args()
    report = run(use_cached=not args.fresh)
    print_report(report)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
