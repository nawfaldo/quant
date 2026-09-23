"""Anchored walk-forward for the BTC trend / vwap / orb families.

`btc_families_research` selects one cell per family on 2018-2024 and scores it
once on 2025-2026. That answers "does the chosen cell work", not "would this
selection *rule* have chosen a working cell at any point in time". A single
sealed split cannot distinguish a durable edge from one lucky cell, and the
BTC out-of-sample totals lean heavily on single months (trend earns +236 of its
+489 in 2025-11), which is exactly the failure mode a walk-forward exposes.

Discipline, mirroring `sandbox/walkforward.py`:

  * folds are **anchored** -- every train window starts at the first bar, so a
    winner must survive every regime so far rather than forgetting the bad ones;
  * one session of **embargo** separates train from test;
  * selection runs inside the train window alone, the chosen cell is scored once
    on that fold's test window, and is never revisited;
  * headline numbers come from the **stitched** test windows only;
  * **plateau width** -- the winning cell and all its immediate numeric
    neighbours must be profitable in train, so a spike ringed by losers cannot
    win;
  * **deflated Sharpe** -- with N cells the best in-sample Sharpe is inflated by
    about sqrt(2 ln N) standard errors; the deflated figure is what says whether
    the result is edge or search.

Sizing is path dependent, so the expensive half runs once per cell as *per-unit*
trades over the whole span, and each fold re-sizes its own slice from a fresh
$1,000. That is the same split `execution.py` makes, and it makes 5,712 cells
across 14 folds tractable.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import btc_families_research as btc
from sandbox.research import es_strategy_research as es


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_walkforward_result.json")

FAMILIES = ("trend", "vwap", "orb")
SPAN_START = btc.IS_START
SPAN_END = btc.OOS_END
EMBARGO = 86_400                     # one session
INITIAL = btc.INITIAL

#: Six-month test windows from 2020-01, so the first train window holds two full
#: years. Quarterly steps gave ~40 trades a fold, too few to rank on.
def semiannual_folds(first_year=2020):
    out = []
    for year in range(first_year, 2027):
        for month in (1, 7):
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            end = (datetime(year, 7, 1, tzinfo=timezone.utc) if month == 1
                   else datetime(year + 1, 1, 1, tzinfo=timezone.utc))
            lo, hi = int(start.timestamp()), int(end.timestamp())
            if lo >= SPAN_END:
                continue
            out.append((lo, min(hi, SPAN_END)))
    return out


FOLDS = semiannual_folds()

MIN_TRADES_PER_MONTH = 4
MIN_PROFIT_FACTOR = 1.05
MAX_TRAIN_DD = 0.17


# --------------------------------------------------------------------------- #
# per-unit resolution (equity independent)
# --------------------------------------------------------------------------- #


def resolve(family, bars, ctx, params):
    """Per-unit trades over the whole span: (entry_ts, points, stop, price).

    Carries no sizing at all, so a fold can re-size its own slice. Mirrors the
    exit precedence in `btc_families_research.backtest` exactly: session
    flatten, stop, target, elapsed time, with the trail updated when nothing
    fired.
    """
    signal_fn = btc.SIGNALS[family]
    out = []
    position = pending = None
    traded_day = None
    state = {}

    for index, bar in enumerate(bars):
        ts = bar[btc.TS]
        if ts < SPAN_START:
            continue
        if ts >= SPAN_END:
            break
        day, minute = ts // 86_400, ts % 86_400 // 60

        if position is not None:
            side = position["side"]
            price = None
            if minute >= btc.SESSION_CLOSE_MINUTE:
                price = bar[btc.O]
            else:
                stop = position["stop"]
                if (side == 1 and bar[btc.L] <= stop) or (side == -1 and bar[btc.H] >= stop):
                    price = min(bar[btc.O], stop) if side == 1 else max(bar[btc.O], stop)
                elif position["target"] is not None:
                    target = position["target"]
                    if ((side == 1 and bar[btc.H] >= target)
                            or (side == -1 and bar[btc.L] <= target)):
                        price = (max(bar[btc.O], target) if side == 1
                                 else min(bar[btc.O], target))
                if price is None and position["max_bars"] is not None \
                        and index - position["index"] >= position["max_bars"]:
                    price = bar[btc.O]
            if price is None:
                if position["trail"] is not None and ctx["atr"][index]:
                    atr = ctx["atr"][index]
                    if side == 1:
                        position["best"] = max(position["best"], bar[btc.C])
                        position["stop"] = max(
                            position["stop"], position["best"] - position["trail"] * atr)
                    else:
                        position["best"] = min(position["best"], bar[btc.C])
                        position["stop"] = min(
                            position["stop"], position["best"] + position["trail"] * atr)
            else:
                out.append((position["ts"], side * (price - position["entry"]) - btc.SPREAD,
                            position["distance"], position["entry"]))
                position = None

        if position is None and pending is not None:
            if day == pending["day"] and minute < btc.SESSION_CLOSE_MINUTE:
                side, entry = pending["side"], bar[btc.O]
                target, max_bars, trail = btc.exit_plan(params["exit_mode"],
                                                        pending["distance"])
                position = {"side": side, "entry": entry, "ts": ts, "index": index,
                            "distance": pending["distance"],
                            "stop": entry - side * pending["distance"],
                            "target": None if target is None else entry + side * target,
                            "max_bars": max_bars, "trail": trail, "best": entry}
                traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and btc.SESSION_OPEN_MINUTE <= minute < btc.SESSION_CLOSE_MINUTE
                and btc.accepts_day(ts, params["day_mode"])
                and es.accepts_vix(ctx["vix"][index], params["vix_mode"])):
            side = signal_fn(index, bars, ctx, params, state)
            atr, realized = ctx["atr"][index], ctx["volatility"][index]
            if (side is not None and atr is not None and realized is not None
                    and btc.accepts_trend(bar[btc.C], ctx, index, side, params["trend"])):
                pending = {"side": side, "day": day,
                           "distance": params["stop_atr"] * atr}
    return out


def size_segment(trades, lo, hi):
    """Compound a timestamp slice from a fresh $1,000. Returns a stat bundle."""
    equity = peak = INITIAL
    max_dd = 0.0
    pnls = []
    points = []
    months = {}
    for entry_ts, pts, stop, price in trades:
        if entry_ts < lo or entry_ts >= hi:
            continue
        risk = btc.RISK_FRACTION
        quantity = es.quantity(equity, price, stop, risk)
        if quantity < btc.STEP:
            continue
        pnl = pts * quantity
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 1.0)
        pnls.append(pnl)
        points.append(pts)
        dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc)
        key = f"{dt.year}-{dt.month:02d}"
        months[key] = months.get(key, 0.0) + pnl
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    monthly = list(months.values())
    return {
        "n": len(pnls),
        "return_pct": 100.0 * (equity - INITIAL) / INITIAL,
        "pf": (wins / losses) if losses else (999.0 if wins else 0.0),
        "max_dd_pct": 100.0 * max_dd,
        "points": points,
        "months": months,
        "monthly_sharpe": (statistics.fmean(monthly) / statistics.pstdev(monthly)
                           if len(monthly) > 1 and statistics.pstdev(monthly) else 0.0),
    }


def t_stat(points):
    if len(points) < 3:
        return 0.0
    sd = statistics.stdev(points)
    return statistics.fmean(points) / (sd / math.sqrt(len(points))) if sd else 0.0


# --------------------------------------------------------------------------- #
# fold selection
# --------------------------------------------------------------------------- #


def select_in_train(family, resolved, universe, lo, hi):
    """Best cell inside `[lo, hi)`, by t-stat, subject to floors and plateau."""
    months = max(1.0, (hi - lo) / (30.44 * 86_400))
    scored = {}
    for key, params in universe:
        stat = size_segment(resolved[key], lo, hi)
        scored[key] = stat
    best = None
    for key, params in universe:
        stat = scored[key]
        if stat["n"] < MIN_TRADES_PER_MONTH * months:
            continue
        if stat["pf"] < MIN_PROFIT_FACTOR or stat["max_dd_pct"] > 100 * MAX_TRAIN_DD:
            continue
        # Plateau width: every immediate numeric neighbour must also profit.
        neighbours = btc.neighbours(params, btc.AXES[family])
        keys = [es.frozen(v) for v in neighbours]
        if any(k not in scored or scored[k]["return_pct"] <= 0.0 for k in keys):
            continue
        score = t_stat(stat["points"])
        if best is None or score > best[0]:
            best = (score, key, params, stat)
    return best


def run_family(family, bars, ctx):
    universe = [(es.frozen(p), p) for p in btc.candidates(btc.AXES[family])]
    print(f"\n{family}: resolving {len(universe):,} cells over the full span...",
          flush=True)
    resolved = {}
    for number, (key, params) in enumerate(universe, 1):
        resolved[key] = resolve(family, bars, ctx, params)
        if number % 500 == 0:
            print(f"  {number}/{len(universe)}", flush=True)

    stitched_points = []
    stitched_months = {}
    rows = []
    chosen = []
    for lo, hi in FOLDS:
        train_hi = lo - EMBARGO
        best = select_in_train(family, resolved, universe, SPAN_START, train_hi)
        if best is None:
            rows.append((lo, hi, None, None, None))
            continue
        score, key, params, train_stat = best
        test = size_segment(resolved[key], lo, hi)
        stitched_points.extend(test["points"])
        for month, value in test["months"].items():
            stitched_months[month] = stitched_months.get(month, 0.0) + value
        rows.append((lo, hi, params, train_stat, test))
        chosen.append(params)

    print(f"\n=== {family}: {len(FOLDS)} anchored folds ===")
    print(f"{'test window':>18} | {'train t':>7} {'train pf':>8} | "
          f"{'test n':>6} {'test ret':>9} {'test pf':>7}")
    print("-" * 74)
    wins = 0
    for lo, hi, params, train_stat, test in rows:
        label = (f"{datetime.fromtimestamp(lo, tz=timezone.utc):%Y-%m}"
                 f"..{datetime.fromtimestamp(hi - 1, tz=timezone.utc):%Y-%m}")
        if params is None:
            print(f"{label:>18} | {'selected nothing':>44}")
            continue
        wins += test["return_pct"] > 0
        print(f"{label:>18} | {t_stat(train_stat['points']):>7.2f} "
              f"{train_stat['pf']:>8.3f} | {test['n']:>6} "
              f"{test['return_pct']:>+8.2f}% {test['pf']:>7.3f}")

    selected = [r for r in rows if r[2] is not None]
    stitched_t = t_stat(stitched_points)
    deflation = math.sqrt(2.0 * math.log(len(universe)))
    monthly = list(stitched_months.values())
    summary = {
        "folds": len(FOLDS),
        "folds_selected": len(selected),
        "folds_profitable": wins,
        "stitched_trades": len(stitched_points),
        "stitched_mean_points": round(statistics.fmean(stitched_points), 3)
                                if stitched_points else 0.0,
        "stitched_t": round(stitched_t, 2),
        "cells": len(universe),
        "deflation_threshold_t": round(deflation, 2),
        "deflated_t": round(stitched_t - deflation, 2),
        "stitched_positive_months": sum(1 for v in monthly if v > 0),
        "stitched_months": len(monthly),
        "stitched_monthly_sharpe": round(
            statistics.fmean(monthly) / statistics.pstdev(monthly), 3)
            if len(monthly) > 1 and statistics.pstdev(monthly) else 0.0,
    }
    print(f"\n  folds profitable      {wins}/{len(selected)} selected "
          f"({len(FOLDS)} total)")
    print(f"  stitched trades       {summary['stitched_trades']}")
    print(f"  stitched t-stat       {summary['stitched_t']:+.2f}")
    print(f"  deflation threshold   {summary['deflation_threshold_t']:.2f}  "
          f"({len(universe):,} cells)")
    print(f"  DEFLATED t            {summary['deflated_t']:+.2f}   "
          f"{'PASS' if summary['deflated_t'] > 0 else 'FAIL'}")
    print(f"  positive months       {summary['stitched_positive_months']}"
          f"/{summary['stitched_months']}")

    # Parameter stability: how often each axis kept its modal value.
    stability = {}
    for axis in btc.AXES[family]:
        values = [p[axis] for p in chosen]
        if values:
            modal = max(set(values), key=values.count)
            stability[axis] = f"{values.count(modal)}/{len(values)} {modal}"
    print("  parameter stability:")
    for axis, value in sorted(stability.items()):
        print(f"    {axis:<20} {value}")
    summary["stability"] = stability
    return summary


def main():
    bars, ctx = btc.context("validate")
    print(f"loaded {len(bars):,} session bars; {len(FOLDS)} folds "
          f"from {datetime.fromtimestamp(FOLDS[0][0], tz=timezone.utc):%Y-%m}")
    out = {}
    for family in FAMILIES:
        out[family] = run_family(family, bars, ctx)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {OUTPUT}")


if __name__ == "__main__":
    main()
