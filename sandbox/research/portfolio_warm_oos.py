"""The warm-start check: does volatility targeting still win when its 2026 run
is not handed a free full-size January?

WHY THIS EXISTS. `portfolio_sizing_mix` scored each candidate on 2026 by running
the engine over 2026 alone. That restarts the account at $1,000 on 1 January and,
more importantly, restarts the *rule* -- a 60-day volatility window has no
history, `rule_volatility` returns 1.0 until it fills, and the book therefore
trades at full size through the first sixty days of 2026. January and February
2026 are the two largest winning months in the whole sample (+$414 and +$311 on
the untouched book). A rule that is structurally guaranteed to be at maximum
exposure for exactly those months, and only starts throttling afterwards, will
look excellent for a reason that has nothing to do with regime detection.

So the 38-of-48 out-of-sample win rate is not trustworthy as reported, and the
honest test is the one below: run the engine *continuously* from 2025-01-01, let
the rule warm up on 2025 as it would in real deployment, and then measure return
and drawdown over the 2026 segment alone. The account is not reset, the rule is
not reset, and January 2026 is sized by whatever 2025 left in the volatility
window.

The control gets the identical treatment -- a constant sliced over the same
segment of its own continuous run -- so both sides are measured the same way.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from sandbox.research import portfolio_exposure as px
from sandbox.research import portfolio_regime_control as rc
from sandbox.research import portfolio_sizing_mix as mix


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_warm_oos.json")
SEGMENT = ("2026-01-01", "2026-08-05")


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def segment_stats(result, segment=SEGMENT, initial=px.INITIAL):
    """Return and max drawdown over a slice of a continuous run.

    The balance entering the slice is the run's own balance on that date, not
    $1,000, so the percentages are what an account already in flight would have
    experienced -- which is the whole point of warming the rule up.
    """
    trades = sorted(result["trades_log"], key=lambda t: t["xt"])
    equity = initial
    opening = None
    peak = None
    worst = 0.0
    final = None
    for trade in trades:
        day = day_of(trade["xt"])
        if day < segment[0]:
            equity += trade["pnl"]
            continue
        if day > segment[1]:
            break
        if opening is None:
            opening = equity
            peak = equity
        equity += trade["pnl"]
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 100.0 * (peak - equity) / peak)
        final = equity
    if opening is None or final is None:
        return None
    return {
        "opening": round(opening, 2),
        "final": round(final, 2),
        "return_pct": round(100.0 * (final - opening) / opening, 2),
        "max_dd_pct": round(worst, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    report = {"segment": SEGMENT}

    # Control: constants, sliced over the same segment of their continuous runs.
    print("=" * 96)
    print("CONTROL -- constants, 2026 segment of a continuous run from 2025-01-01")
    print("=" * 96)
    print(f"  {'candidate':<26} {'2026 return':>12} {'2026 maxDD':>12} {'full return':>12} {'full maxDD':>11}")
    control = {}
    for factor in [round(0.05 * i, 2) for i in range(1, 21)]:
        result = px.run(px.flat(factor), px.FULL)
        stats = segment_stats(result)
        if not stats:
            continue
        control[factor] = stats
        if factor in (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2):
            print(f"  flat k={factor:<19} {stats['return_pct']:11.2f}% {stats['max_dd_pct']:11.2f}% "
                  f"{result['return_pct']:11.2f}% {result['max_dd_pct']:10.2f}%")
    report["control"] = {str(k): v for k, v in control.items()}

    def matched(drawdown):
        """Best-returning constant whose 2026 segment drawdown is no worse."""
        best = None
        for factor, stats in control.items():
            if stats["max_dd_pct"] <= drawdown + 1e-9:
                if best is None or stats["return_pct"] > best[1]["return_pct"]:
                    best = (factor, stats)
        return best

    print()
    print("=" * 96)
    print("VOLATILITY TARGETING -- warmed on 2025, measured on the 2026 segment")
    print("=" * 96)
    print(f"  {'candidate':<26} {'2026 ret':>10} {'2026 DD':>9} {'ctrl k':>7} "
          f"{'ctrl ret':>9} {'edge':>8}   {'full ret/DD':>16}")
    rows, wins, total = [], 0, 0
    for window in (20, 40, 60, 90):
        for target in (8, 10, 12, 15, 20, 25):
            params = {"window": window, "target": target, "cap": 1.0}
            result = px.run(mix.vol_schedule(params, px.FULL), px.FULL)
            stats = segment_stats(result)
            if not stats:
                continue
            match = matched(stats["max_dd_pct"])
            if not match:
                continue
            factor, ctrl = match
            edge = stats["return_pct"] - ctrl["return_pct"]
            wins += edge > 0
            total += 1
            label = f"vol w={window},t={target}"
            print(f"  {label:<26} {stats['return_pct']:9.2f}% {stats['max_dd_pct']:8.2f}% "
                  f"{factor:7.2f} {ctrl['return_pct']:8.2f}% {edge:+7.2f}   "
                  f"{result['return_pct']:7.1f}% /{result['max_dd_pct']:6.2f}%")
            rows.append({"params": params, "label": label, "segment": stats,
                         "control_k": factor, "control": ctrl, "edge": round(edge, 2),
                         "full": {f: result[f] for f in px.FIELDS}})
    report["volatility"] = rows
    report["win_rate"] = {"wins": wins, "cells": total}
    print(f"\n  cells beating the matched constant on the warm 2026 segment: {wins}/{total}")

    # The same question on the full continuous window, which is what an account
    # actually running from 2025-01-01 would have lived through.
    print()
    print("=" * 96)
    print("FULL CONTINUOUS WINDOW -- 2025-01-01 to 2026-08-05, matched drawdown")
    print("=" * 96)
    full_control = {}
    for factor in [round(0.05 * i, 2) for i in range(1, 21)]:
        full_control[factor] = px.run(px.flat(factor), px.FULL)

    def matched_full(drawdown):
        best = None
        for factor, result in full_control.items():
            if result["max_dd_pct"] <= drawdown + 1e-9:
                if best is None or result["return_pct"] > best[1]["return_pct"]:
                    best = (factor, result)
        return best

    print(f"  {'candidate':<26} {'return':>9} {'maxDD':>8} {'ctrl k':>7} {'ctrl ret':>9} {'edge':>8}")
    full_wins, full_total = 0, 0
    for row in rows:
        result = px.run(mix.vol_schedule(row["params"], px.FULL), px.FULL)
        match = matched_full(result["max_dd_pct"])
        if not match:
            continue
        factor, ctrl = match
        edge = result["return_pct"] - ctrl["return_pct"]
        full_wins += edge > 0
        full_total += 1
        print(f"  {row['label']:<26} {result['return_pct']:8.2f}% {result['max_dd_pct']:7.2f}% "
              f"{factor:7.2f} {ctrl['return_pct']:8.2f}% {edge:+7.2f}")
        row["full_edge"] = round(edge, 2)
        row["full_control_k"] = factor
    print(f"\n  cells beating the matched constant over the full window: {full_wins}/{full_total}")
    report["full_win_rate"] = {"wins": full_wins, "cells": full_total}

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
