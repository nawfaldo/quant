"""Does the Robbins Cup order-flow setup survive on NQ level-two minutes?

Scores `strategies/creamer_orderflow.py` -- the port of Chris Creamer's
Context/Location/Confirmation process (youtube PL7LKUsCgIQ) -- on a $1,000
Exness Pro account, and reports four things in this order:

    funnel      which of the talk's four steps is actually binding, counted on
                the strategy's own code path rather than a re-statement of it
    ladder      the literal cell and a series of progressively relaxed ones,
                each split in sample / out of sample
    nulls       every cell with enough trades against a coin-flip direction
                control AND a random-entry control
    power       the per-trade edge this sample could detect at all

READ THE POWER SECTION FIRST. The published defaults are a discretionary
trader's setup and they fire a handful of times in eighteen months, which is
below the number of trades at which any of the other three sections mean
anything. `zero-is-the-wrong-backtest-baseline` and
`minimum-detectable-edge-is-0.85-points` are the reason the nulls and the MDE
are here rather than a return figure.

COSTS ARE `combined_book`'s, imported rather than copied. That module's
`apply_cost_model("pro")` is the account this book is actually priced on --
spread 0.300 bp of a 29,731 index (0.89 points), no commission, plus the 0.2
point slippage allowance -- and getting it wrong does not shade a result, it
inverts one. `nq_execution` then reprices the strategy's own `Execution`.

WINDOWS. The level-two table starts 2025-02-12, so the in-sample window is
short: 2025-02-12..2026-01-01 in sample, 2026-01-01 onward out of sample,
matching `combined_book`'s split. Neither is a sealed holdout -- this file's
author has now read both -- so treat the OOS column as a consistency check, not
as evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import replace

from sandbox import data, execution, metrics, paths
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research import combined_book
from sandbox.strategies.creamer_orderflow import STAGES, CreamerOrderflow

INITIAL = 1_000.0
SPLIT = "2026-01-01"
TRIALS = 500
SEED = 20260815

#: The ladder. Each cell adds its overrides to the one above it, so a column
#: reads as "what does dropping this rule cost?" rather than as an unrelated
#: parameter set. NOTHING HERE IS FITTED: every relaxation turns a rule OFF or
#: widens a window, and no cell was chosen by reading its P&L.
LADDER = [
    ("literal", {}),
    ("no regime/structure", {"structure": "none"}),
    ("no location gate", {"structure": "none", "location": "none"}),
    ("delta flip only", {"structure": "none", "location": "none",
                         "flip": "delta"}),
    ("no participation", {"structure": "none", "location": "none",
                          "flip": "delta", "participation_mult": 0}),
    ("z >= 1.0", {"structure": "none", "location": "none", "flip": "delta",
                  "participation_mult": 0, "aggression_z": 1.0}),
    ("whole session", {"structure": "none", "location": "none", "flip": "delta",
                       "participation_mult": 0, "aggression_z": 1.0,
                       "entry_to": 915, "max_entries": 4}),
]

#: A cell with fewer trades than this cannot be scored against anything, so the
#: null controls are skipped rather than printed as noise.
MIN_TRADES_FOR_NULL = 25


def account():
    """The strategy's `Execution`, priced on the live Exness Pro account."""
    combined_book.apply_cost_model("pro")
    ex = combined_book.nq_execution(CreamerOrderflow().execution)
    return replace(ex, initial=INITIAL)


def fills_for(strategy, bars, context, overrides, ex):
    params = strategy.all_params(overrides)
    signals = strategy.signals(bars, context, "all", params)
    return signals, execution.resolve(bars, signals, ex)


def points_per_trade(fills):
    return sum(f.points for f in fills) / len(fills) if fills else 0.0


def summarise(fills, ex, split_ts):
    """Per-unit and account figures for one cell."""
    sized = execution.size(fills, ex)
    stats = metrics.stats(sized, split=split_ts, initial=ex.initial)
    points = [f.points for f in fills]
    mean = points_per_trade(fills)
    sd = (sum((p - mean) ** 2 for p in points) / len(points)) ** 0.5 if points else 0.0
    return {
        "trades": len(fills),
        "points_per_trade": round(mean, 3),
        "points_sd": round(sd, 2),
        # Standard error of the mean is what says whether the number above is
        # readable at all; see `mde` for the same thing as a threshold.
        "t": round(mean / (sd / math.sqrt(len(points))), 2) if sd and points else 0.0,
        "mde_points": round(2.8 * sd / math.sqrt(len(points)), 3) if points else None,
        "win_rate": stats["win_rate"],
        "pf": stats["pf"],
        "pnl": stats["pnl"],
        "return_pct": round(100.0 * stats["pnl"] / ex.initial, 2),
        "max_dd_pct": round(100.0 * stats["max_dd"] / ex.initial, 2),
        "in_sample": stats.get("train"),
        "out_of_sample": stats.get("hold"),
        "is_trades": sum(1 for f in fills if f.entry_ts < split_ts),
        "oos_trades": sum(1 for f in fills if f.entry_ts >= split_ts),
    }


def coin_flip_null(bars, signals, ex, rng):
    """Same entries, same brackets, RANDOM direction.

    Isolates the entry's direction from its timing and its bracket. A signal
    whose edge is in the bracket meeting the regime scores the same here
    ([[coin-flip-control-beats-real-signals]]).
    """
    draws = []
    for _ in range(TRIALS):
        fake = [replace(s, side=LONG if rng.random() < 0.5 else SHORT)
                for s in signals]
        draws.append(points_per_trade(execution.resolve(bars, fake, ex)))
    return draws


def random_entry_null(bars, signals, ex, rng, params):
    """Same count, same side balance, same brackets, RANDOM minutes.

    Drawn from the same entry window, so the only thing that differs from the
    real signals is *when* they happen.
    """
    from sandbox.data import TS

    lo, hi = params["entry_from"], params["entry_to"]
    pool = [i for i, bar in enumerate(bars)
            if lo <= (bar[TS] % 86_400) // 60 <= hi and i + 1 < len(bars)]
    longs = sum(1 for s in signals if s.side == LONG)
    brackets = [(s.stop, s.target, s.max_minutes, s.trail) for s in signals]

    draws = []
    for _ in range(TRIALS):
        picks = rng.sample(pool, min(len(signals), len(pool)))
        fake = []
        for k, index in enumerate(sorted(picks)):
            stop, target, minutes, trail = brackets[k % len(brackets)]
            fake.append(Signal(index, LONG if k < longs else SHORT, stop, target,
                               minutes, trail))
        draws.append(points_per_trade(execution.resolve(bars, fake, ex)))
    return draws


def null_verdict(actual, draws):
    mean = sum(draws) / len(draws)
    sd = (sum((d - mean) ** 2 for d in draws) / len(draws)) ** 0.5
    return {
        "null_mean": round(mean, 3),
        "null_sd": round(sd, 3),
        "z": round((actual - mean) / sd, 2) if sd else 0.0,
        "beats_pct": round(100.0 * sum(1 for d in draws if d < actual) / len(draws), 1),
    }


def report(strategy, bars, context, ex):
    split_ts = metrics.split_ts(SPLIT)
    rng = random.Random(SEED)
    out = {"initial": INITIAL, "cost_model": combined_book.COST_MODEL,
           "entry_spread_points": round(ex.spread, 3),
           "slippage_points": ex.slippage,
           "commission_per_lot": ex.commission_per_lot,
           "entry_cost_points": round(ex.entry_cost, 3),
           "split": SPLIT, "trials": TRIALS, "seed": SEED,
           "sessions": len({bar[0] // 86_400 for bar in bars}),
           "cells": []}

    print(f"NQ level-two, {out['sessions']} sessions, ${INITIAL:,.0f} Forex, "
          f"Exness {combined_book.COST_MODEL}")
    print(f"  entry cost {ex.entry_cost:.3f} points "
          f"= {ex.spread:.3f} spread + {ex.slippage} slippage "
          f"+ {ex.commission_per_lot} commission/lot")

    print("\nFUNNEL, literal defaults -- where the setup dies")
    funnel = strategy.funnel(bars, context, strategy.all_params({}))
    previous = None
    for stage, count in funnel:
        share = f"{100.0 * count / previous:5.1f}% kept" if previous else ""
        print(f"    {stage:>14} {count:>8,}  {share}")
        previous = count or None
    out["funnel"] = dict(funnel)

    print(f"\nLADDER  (in sample < {SPLIT} <= out of sample)")
    header = (f"    {'cell':<20}{'n':>5}{'pts/tr':>9}{'t':>7}{'MDE':>8}"
              f"{'win':>6}{'pf':>7}{'ret%':>8}{'dd%':>7}{'IS$':>9}{'OOS$':>9}")
    print(header)
    for name, overrides in LADDER:
        signals, fills = fills_for(strategy, bars, context, overrides, ex)
        cell = {"cell": name, "overrides": overrides, **summarise(fills, ex, split_ts)}
        mde = cell["mde_points"]
        print(f"    {name:<20}{cell['trades']:>5}{cell['points_per_trade']:>9.3f}"
              f"{cell['t']:>7.2f}{(mde if mde is not None else 0):>8.2f}"
              f"{cell['win_rate']:>6.2f}{cell['pf']:>7.2f}"
              f"{cell['return_pct']:>8.2f}{cell['max_dd_pct']:>7.2f}"
              f"{cell['in_sample'] or 0:>9.2f}{cell['out_of_sample'] or 0:>9.2f}")
        if len(fills) >= MIN_TRADES_FOR_NULL:
            params = strategy.all_params(overrides)
            actual = points_per_trade(fills)
            cell["coin_flip"] = null_verdict(
                actual, coin_flip_null(bars, signals, ex, rng))
            cell["random_entry"] = null_verdict(
                actual, random_entry_null(bars, signals, ex, rng, params))
        out["cells"].append(cell)

    print(f"\nNULLS  ({TRIALS} draws, seed {SEED}; cells with "
          f">= {MIN_TRADES_FOR_NULL} trades)")
    print(f"    {'cell':<20}{'pts/tr':>9}{'flip mean':>11}{'flip z':>8}"
          f"{'rand mean':>11}{'rand z':>8}")
    scored = [c for c in out["cells"] if "coin_flip" in c]
    for cell in scored:
        print(f"    {cell['cell']:<20}{cell['points_per_trade']:>9.3f}"
              f"{cell['coin_flip']['null_mean']:>11.3f}"
              f"{cell['coin_flip']['z']:>8.2f}"
              f"{cell['random_entry']['null_mean']:>11.3f}"
              f"{cell['random_entry']['z']:>8.2f}")
    if not scored:
        print("    none -- no cell reached the minimum trade count")

    print("\nPOWER")
    print("    MDE is the per-trade edge this many trades could separate from")
    print("    zero at t=2.8. A cell whose |pts/tr| is under its own MDE has")
    print("    not been measured, whatever its return column says.")
    for cell in out["cells"]:
        if cell["mde_points"] is None:
            print(f"    {cell['cell']:<20} no trades")
            continue
        readable = abs(cell["points_per_trade"]) >= cell["mde_points"]
        print(f"    {cell['cell']:<20}{cell['trades']:>5} trades, "
              f"MDE {cell['mde_points']:>6.2f} pts, "
              f"measured {cell['points_per_trade']:>7.3f} -- "
              f"{'readable' if readable else 'NOT MEASURED'}")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="creamer_orderflow.json",
                        help="report filename under sandbox/results")
    args = parser.parse_args()

    strategy = CreamerOrderflow()
    ex = account()
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    out = report(strategy, bars, context, ex)

    path = paths.result_path(args.json)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
