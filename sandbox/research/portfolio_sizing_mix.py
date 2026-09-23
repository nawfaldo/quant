"""Per-sleeve weights, and volatility targeting on top of them.

`portfolio_regime_control` settled the first question: of the three regime
families tried, only volatility targeting beats a constant at matched drawdown,
and it does so across its whole parameter grid rather than in one cell. The
equity-curve rules -- stand down below a moving average, stand down past a
drawdown limit -- mostly turned a positive 2025 negative while leaving the
drawdown where it was, which is the signature of a rule reacting after the loss
has already happened.

This module asks the two questions that remain.

FIRST, ARE THE SLEEVES WORTH EQUAL RISK. Inside the book the three earn very
different amounts per unit of drawdown they contribute: Deep OFI Momentum made
$942 against a 20.3% own-drawdown, Hourly Delta Reversal $242 against 17.6%, and
Maroy Ladder $121 against 13.6%. On those ratios the book is paying nearly full
risk for two sleeves that return a fraction of what the first does. But that
comparison is exactly the one that overfits most easily -- it is a ranking on
realised PnL over a single sample -- so the weights are chosen on 2025 and read
once on 2026, and the flat-weight book is kept as the control throughout.

SECOND, DO THE TWO STACK. Volatility targeting decides *when* the book carries
risk; weights decide *which sleeve* carries it. They are different axes and
there is no reason in principle they should not compose, but composing two
things each selected on the same sample is how a fitted result gets dressed up
as two independent confirmations. The combination is therefore reported with
both of its parts, and against the constant, on the window neither was fitted on.

A NOTE ON THE SEARCH BUDGET. This grid is a few hundred cells on top of the
previous module's forty. Reading the top of a table that size is picking the
best of N draws, and the honest defence is not a p-value -- it is that the
volatility-targeting result holds monotonically across its entire family, which
a lucky cell cannot do. Single-cell winners below are reported as such and
should be treated as the weakest claims here.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os

from sandbox.research import portfolio_exposure as px
from sandbox.research import portfolio_regime_control as rc


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_sizing_mix.json")

OFI, HDR, LADDER = px.SLEEVES
LEVELS = (0.0, 0.5, 1.0)


def weighted(mix):
    return {name: {"default": float(value)} for name, value in mix.items()}


def vol_schedule(params, window, mix=None):
    """Volatility targeting, optionally on top of a per-sleeve weight vector.

    The two compose by multiplication: the rule sets the book's exposure for the
    day, the weight sets each sleeve's share of it.
    """
    days, values = rc.reference_curve(window)
    base = rc.schedule_from(days, values, rc.rule_volatility, params)
    if not mix:
        return base
    return {name: {day: round(factor * mix.get(name, 1.0), 4)
                   for day, factor in points.items()}
            for name, points in base.items()}


def frontier_lookup(frontier, tag, drawdown):
    return rc.matched_control(frontier, tag, drawdown)


def verdict(row, frontier, tag):
    """Return versus the constant that reaches the same drawdown."""
    match = frontier_lookup(frontier, tag, row[tag]["max_dd_pct"])
    if not match:
        return None, None
    factor, result = match
    return factor, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    report = {}
    frontier = rc.control_curve()

    print("=" * 92)
    print("PER-SLEEVE WEIGHTS -- constant, no timing. control is the flat book.")
    print("=" * 92)
    print(px.HEADER)
    rows = []
    for ofi, hdr, ladder in itertools.product(LEVELS, repeat=3):
        if ofi == hdr == ladder == 0.0:
            continue
        mix = {OFI: ofi, HDR: hdr, LADDER: ladder}
        label = f"w ofi={ofi} hdr={hdr} lad={ladder}"
        results = px.evaluate(weighted(mix))
        print(px.show(label, results, width=26))
        rows.append({"mix": mix, "label": label,
                     **{tag: {f: results[tag][f] for f in px.FIELDS}
                        for tag in ("full", "is", "oos")}})
    report["weights"] = rows

    # Chosen on 2025 alone: best return per unit of drawdown, so the choice is
    # not simply "whichever weight vector took the most risk".
    ranked = sorted(rows, key=lambda r: r["is"]["return_pct"] / max(r["is"]["max_dd_pct"], 1e-9),
                    reverse=True)
    best_mix = ranked[0]
    print(f"\n  best 2025 return-per-drawdown: {best_mix['label']}")
    print(f"    2025 IS  {best_mix['is']['return_pct']:7.2f}% / {best_mix['is']['max_dd_pct']:5.2f}%"
          f"    2026 OOS {best_mix['oos']['return_pct']:7.2f}% / {best_mix['oos']['max_dd_pct']:5.2f}%")
    for tag, name in (("is", "2025 IS"), ("oos", "2026 OOS")):
        factor, result = verdict(best_mix, frontier, tag)
        if result:
            won = best_mix[tag]["return_pct"] > result["return_pct"]
            print(f"    {name} control flat k={factor}: {result['return_pct']:7.2f}% / "
                  f"{result['max_dd_pct']:5.2f}%   [{'weights win' if won else 'CONTROL WINS'}]")
    report["best_weights"] = best_mix

    print()
    print("=" * 92)
    print("VOLATILITY TARGETING -- finer grid, and with the cap released above 1.0")
    print("=" * 92)
    print(px.HEADER)
    vol_rows = []
    for window in (20, 40, 60, 90):
        for target in (8, 10, 12, 15, 20, 25):
            for cap in (1.0, 2.0):
                params = {"window": window, "target": target, "cap": cap}
                label = f"vol w={window},t={target},cap={cap}"
                results = {tag: px.run(vol_schedule(params, span), span)
                           for tag, span in (("full", px.FULL), ("is", px.IS),
                                             ("oos", px.OOS))}
                print(px.show(label, results, width=26))
                vol_rows.append({"params": params, "label": label,
                                 **{tag: {f: results[tag][f] for f in px.FIELDS}
                                    for tag in ("full", "is", "oos")}})
    report["volatility"] = vol_rows

    print()
    print("=" * 92)
    print("MATCHED-DRAWDOWN TABLE -- 2026 out of sample, every vol cell vs the constant")
    print("=" * 92)
    print(f"  {'candidate':<26} {'OOS return':>11} {'OOS maxDD':>10} "
          f"{'control k':>10} {'control ret':>12} {'edge':>8}")
    wins = 0
    for row in vol_rows:
        factor, result = verdict(row, frontier, "oos")
        if not result:
            continue
        edge = row["oos"]["return_pct"] - result["return_pct"]
        wins += edge > 0
        print(f"  {row['label']:<26} {row['oos']['return_pct']:10.2f}% "
              f"{row['oos']['max_dd_pct']:9.2f}% {factor:10.2f} "
              f"{result['return_pct']:11.2f}% {edge:+7.2f}")
    print(f"\n  volatility cells beating the matched constant out of sample: "
          f"{wins}/{len(vol_rows)}")
    report["oos_vol_wins"] = {"wins": wins, "cells": len(vol_rows)}

    print()
    print("=" * 92)
    print("COMBINED -- volatility targeting on the 2025-chosen weight vector")
    print("=" * 92)
    print(px.HEADER)
    combined = []
    mix = best_mix["mix"]
    for window in (20, 40, 60):
        for target in (10, 15, 20):
            params = {"window": window, "target": target, "cap": 1.0}
            label = f"vol w={window},t={target} x weights"
            results = {tag: px.run(vol_schedule(params, span, mix), span)
                       for tag, span in (("full", px.FULL), ("is", px.IS), ("oos", px.OOS))}
            print(px.show(label, results, width=26))
            combined.append({"params": params, "mix": mix, "label": label,
                             **{tag: {f: results[tag][f] for f in px.FIELDS}
                                for tag in ("full", "is", "oos")}})
    report["combined"] = combined

    print()
    print("=" * 92)
    print("COMBINED vs CONTROL -- 2026 out of sample at matched drawdown")
    print("=" * 92)
    for row in combined:
        factor, result = verdict(row, frontier, "oos")
        if not result:
            continue
        edge = row["oos"]["return_pct"] - result["return_pct"]
        print(f"  {row['label']:<26} {row['oos']['return_pct']:8.2f}% / "
              f"{row['oos']['max_dd_pct']:5.2f}%   control k={factor}: "
              f"{result['return_pct']:7.2f}% / {result['max_dd_pct']:5.2f}%   edge {edge:+.2f}")

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
