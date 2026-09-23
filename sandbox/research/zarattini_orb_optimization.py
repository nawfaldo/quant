"""Staged optimisation of the Zarattini ORB rule on BTCUSD.

`zarattini_orb` measured the paper's own parameters: +164% on RTH but a gross
edge of +0.83 bps at t=0.54, which is not distinguishable from zero. This module
asks whether any setting of the rule does better, under a protocol that can
actually answer the question rather than one that is guaranteed to find
something.

PROTOCOL
  * The grid is scored on 2020-01-01 .. 2024-12-31 and ranked by Sharpe there.
  * The top cells are then scored on 2025-01-01 .. 2026-08-03 and printed
    beside their in-sample figures. No cell is selected, promoted or tuned on
    that window; ranking reads the in-sample block only.
  * Sessions before 2020 still feed the regime lookbacks, so a 2020 trade sees
    the same warmed state it would have seen live.
  * The cell count is charged to `trials.json` under `--record-trials`. Reading
    a top-of-grid table is still picking the best of N draws, so the count is
    what any later significance claim has to be deflated against.

AXES
  Path (change which bar the trade exits on):
    session          rth | full            -- where the opening range is anchored
    range_minutes    5 | 15 | 30 | 60      -- the paper uses 5
    stop_mult        0.5 | 1.0 | 1.5       -- multiple of the range extreme
    target_r         2 | 3 | 5 | 10 | None -- None means hold to the close
    trail_r          None | 1.5 | 3.0      -- trailing stop, in R
  Filter (drop trades without changing the survivors):
    trend            None | sma50 | sma200 -- only trade with the daily trend
    vol_band         None | low | high     -- 20-session realised vol vs its median
    weekday          None | mon_fri
  Sizing (all equity-based, as required -- every mode scales with the account):
    risk_leverage    the paper's min(risk/R, cap) at risk 0.5|1|2%, cap 1|2|4
    vol_target       leverage set so position vol hits 30|60|100% annual
    fixed_fraction   a constant 0.25|0.5|1|2x of equity, ignoring R

WHY IT IS TRACTABLE. Which bar a trade exits on depends only on the price
levels, never on the sizing or the filters. So the five path axes are resolved
once over the whole sample (~1,000 combinations of a few thousand sessions), and
the filter and sizing axes are then swept over those pre-resolved trade lists.
That factorisation is what makes ~100k cells cost minutes instead of days.

CAUSALITY. Every regime feature reads strictly prior sessions: the SMA and the
realised-volatility median both exclude the session being judged. The trailing
stop is resolved bar by bar; the fixed stop and target are resolved by binary
search over the running high/low, which is the same answer as a scan and is why
the path stage is fast.
"""
from __future__ import annotations

import argparse
import bisect
import itertools
import json
import math
import os
import statistics
from array import array
from datetime import datetime, timezone

from sandbox import trials
from sandbox.research import zarattini_orb as base
from sandbox.walkforward import bootstrap_edge


OUTPUT = os.path.join(os.path.dirname(__file__), "zarattini_orb_optimization_result.json")

STRATEGY = "Zarattini ORB"

IS_START, IS_END = "2020-01-01", "2025-01-01"
OOS_START, OOS_END = "2025-01-01", "2027-01-01"

INITIAL = 1_000.0
QUANTITY_STEP = base.QUANTITY_STEP
SESSIONS_PER_YEAR = base.SESSIONS_PER_YEAR

#: A cell with fewer in-sample trades than this is not rankable -- its Sharpe is
#: a statement about a handful of sessions -- so it is never scored at all.
MIN_TRADES = 150

PATH_GRID = {
    "session": ["rth", "full"],
    "range_minutes": [5, 15, 30, 60],
    "stop_mult": [0.5, 1.0, 1.5],
    "target_r": [2.0, 3.0, 5.0, 10.0, None],
    "trail_r": [None, 1.5, 3.0],
}

FILTER_GRID = {
    "trend": [None, "sma50", "sma200"],
    "vol_band": [None, "low", "high"],
    "weekday": [None, "mon_fri"],
}

SIZING_GRID = (
    [{"mode": "risk_leverage", "risk": r, "cap": c}
     for r in (0.005, 0.01, 0.02) for c in (1.0, 2.0, 4.0)]
    + [{"mode": "vol_target", "vol_target_annual": v, "cap": 4.0}
       for v in (0.3, 0.6, 1.0)]
    + [{"mode": "fixed_fraction", "fraction": f} for f in (0.25, 0.5, 1.0, 2.0)]
)

#: The null every winner must beat: the paper's own settings, untouched.
PAPER_CELL = {
    "session": "rth", "range_minutes": 5, "stop_mult": 1.0, "target_r": 10.0,
    "trail_r": None, "trend": None, "vol_band": None, "weekday": None,
    "sizing": {"mode": "risk_leverage", "risk": 0.01, "cap": 4.0},
}


def _epoch(iso):
    return int(datetime.strptime(iso, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# path stage: resolve which bar each trade exits on
# --------------------------------------------------------------------------- #


def build_paths(sessions, range_minutes):
    """Per session, everything the exit resolver needs, precomputed once.

    `cummax` / `cummin` are the running high and low from the entry bar onward,
    so they are monotone and a fixed stop or target can be located by binary
    search instead of a scan.
    """
    paths = []
    for session in sessions:
        bars = session["bars"]
        opening = base.opening_range(bars, range_minutes)
        entry_bar = next((i for i in range(range_minutes, session["span"])
                          if bars[i] is not None), None)
        if opening is None or entry_bar is None:
            continue
        range_open, range_high, range_low, range_close = opening
        if range_close == range_open:      # doji: the paper skips the day
            continue

        live = [b for b in bars[entry_bar:] if b is not None]
        if not live:
            continue

        cummax, cummin = array("d"), array("d")
        highs, lows, opens, closes = array("d"), array("d"), array("d"), array("d")
        running_high, running_low = -math.inf, math.inf
        for bar in live:
            running_high = max(running_high, bar[2])
            running_low = min(running_low, bar[3])
            cummax.append(running_high)
            cummin.append(running_low)
            opens.append(bar[1])
            highs.append(bar[2])
            lows.append(bar[3])
            closes.append(bar[4])

        paths.append({
            "day": session["day"], "ts": live[0][0],
            "side": "long" if range_close > range_open else "short",
            "entry": live[0][1],
            "range_high": range_high, "range_low": range_low,
            "cummax": cummax, "neg_cummin": array("d", [-v for v in cummin]),
            "opens": opens, "highs": highs, "lows": lows, "closes": closes,
        })
    return paths


def _first_at_or_above(ascending, value):
    """First index whose running extreme has reached `value`, or len if never."""
    return bisect.bisect_left(ascending, value)


def resolve(paths, stop_mult, target_r, trail_r):
    """One path cell over every session. Returns a list of resolved trades.

    Sizing and filters are deliberately absent: neither can change which bar the
    trade leaves on, which is the whole reason this stage is shared.
    """
    trades = []
    for path in paths:
        side, entry = path["side"], path["entry"]
        extreme = path["range_low"] if side == "long" else path["range_high"]
        risk = abs(entry - extreme) * stop_mult
        if risk <= 0:
            continue
        stop = entry - risk if side == "long" else entry + risk
        target = (None if target_r is None else
                  entry + target_r * risk if side == "long"
                  else entry - target_r * risk)

        if trail_r is not None:
            index, price, reason = _scan_exit(path, side, entry, risk, stop,
                                              target, trail_r)
        else:
            index, price, reason = _search_exit(path, side, stop, target)

        points = (price - entry) if side == "long" else (entry - price)
        trades.append({
            "day": path["day"], "ts": path["ts"], "side": side, "entry": entry,
            "exit": price, "points": points, "risk": risk,
            "r_multiple": points / risk, "reason": reason, "index": index,
        })
    return trades


def _search_exit(path, side, stop, target):
    """Fixed stop and target, located by binary search on the running extremes."""
    cummax, neg_cummin = path["cummax"], path["neg_cummin"]
    if side == "long":
        stop_at = _first_at_or_above(neg_cummin, -stop)
        target_at = (_first_at_or_above(cummax, target)
                     if target is not None else len(cummax))
    else:
        stop_at = _first_at_or_above(cummax, stop)
        target_at = (_first_at_or_above(neg_cummin, -target)
                     if target is not None else len(cummax))

    # Stop first on a tie: one minute's OHLC cannot order the two, and awarding
    # the target would manufacture the edge.
    if stop_at <= target_at and stop_at < len(cummax):
        opening = path["opens"][stop_at]
        return stop_at, (min(opening, stop) if side == "long"
                         else max(opening, stop)), "stop"
    if target_at < len(cummax):
        opening = path["opens"][target_at]
        return target_at, (max(opening, target) if side == "long"
                           else min(opening, target)), "target"
    return len(cummax) - 1, path["closes"][-1], "session_close"


def _scan_exit(path, side, entry, risk, stop, target, trail_r):
    """Trailing stop: path-dependent, so this one has to walk the bars.

    The stop only ever ratchets toward price, and it is updated from bars that
    have already closed, so the level applied to bar i was knowable at bar i-1.
    """
    opens, highs, lows, closes = (path["opens"], path["highs"],
                                  path["lows"], path["closes"])
    best = entry
    level = stop
    for index in range(len(opens)):
        if side == "long":
            if lows[index] <= level:
                return index, min(opens[index], level), "stop"
            if target is not None and highs[index] >= target:
                return index, max(opens[index], target), "target"
            best = max(best, highs[index])
            level = max(level, best - trail_r * risk)
        else:
            if highs[index] >= level:
                return index, max(opens[index], level), "stop"
            if target is not None and lows[index] <= target:
                return index, min(opens[index], target), "target"
            best = min(best, lows[index])
            level = min(level, best + trail_r * risk)
    return len(opens) - 1, closes[-1], "session_close"


# --------------------------------------------------------------------------- #
# regime features -- all read strictly prior sessions
# --------------------------------------------------------------------------- #


def regime_features(sessions):
    """Per session day: SMA state and realised-volatility state, as of the open.

    Everything is shifted by one session. A filter that reads the close of the
    day it is filtering is not a filter, it is the answer.
    """
    closes = [s["close"] for s in sessions]
    days = [s["day"] for s in sessions]
    timestamps = [s["ts"] for s in sessions]

    returns = [0.0] + [closes[i] / closes[i - 1] - 1.0 if closes[i - 1] else 0.0
                       for i in range(1, len(closes))]

    features = {}
    vol_history = []
    for index, day in enumerate(days):
        prior_closes = closes[:index]          # strictly before this session
        feature = {"weekday": datetime.fromtimestamp(timestamps[index],
                                                     timezone.utc).weekday()}
        for window in (50, 200):
            if len(prior_closes) >= window:
                sma = statistics.fmean(prior_closes[-window:])
                feature[f"sma{window}"] = 1 if prior_closes[-1] > sma else -1
            else:
                feature[f"sma{window}"] = None

        feature["vol"] = 0.0
        if len(returns[:index]) >= 20:
            vol = statistics.pstdev(returns[max(0, index - 20):index])
            feature["vol"] = vol
            # Compare against this feature's own trailing median, so "high vol"
            # means high for BTC at the time, not high versus 2017.
            if len(vol_history) >= 250:
                median = statistics.median(vol_history[-250:])
                feature["vol_band"] = "high" if vol > median else "low"
            else:
                feature["vol_band"] = None
            vol_history.append(vol)
        else:
            feature["vol_band"] = None
        features[day] = feature
    return features


def apply_filters(trades, features, trend, vol_band, weekday):
    """Drop the trades a filter forbids. Survivors are untouched."""
    if trend is None and vol_band is None and weekday is None:
        return trades
    out = []
    for trade in trades:
        feature = features.get(trade["day"])
        if feature is None:
            continue
        if trend is not None:
            state = feature.get(trend)
            # An absent feature blocks the trade rather than waving it through;
            # a gate that silently stops gating is how a filter looks free.
            if state is None:
                continue
            if (state > 0) != (trade["side"] == "long"):
                continue
        if vol_band is not None and feature.get("vol_band") != vol_band:
            continue
        if weekday == "mon_fri" and feature["weekday"] >= 5:
            continue
        out.append(trade)
    return out


# --------------------------------------------------------------------------- #
# sizing stage -- every mode is equity-based
# --------------------------------------------------------------------------- #


def _quantity(equity, price, risk, sizing, symbol_vol):
    if price <= 0 or equity <= 0:
        return 0.0
    mode = sizing["mode"]
    if mode == "risk_leverage":
        if risk <= 0:
            return 0.0
        notional = min(equity * sizing["risk"] / risk * price,
                       equity * sizing["cap"])
    elif mode == "vol_target":
        annual = symbol_vol * math.sqrt(SESSIONS_PER_YEAR)
        leverage = (sizing["vol_target_annual"] / annual) if annual > 0 else sizing["cap"]
        notional = equity * min(leverage, sizing["cap"])
    elif mode == "fixed_fraction":
        notional = equity * sizing["fraction"]
    else:
        raise ValueError(f"unknown sizing mode {mode!r}")
    return math.floor(notional / price / QUANTITY_STEP) * QUANTITY_STEP


def equity_walk(trades, sizing, vols, initial=INITIAL):
    """Compound `trades` through `sizing`. Returns the per-session equity curve.

    Every mode here scales with the account, so the curve is a genuine return
    series and Sharpe means what it usually means.
    """
    equity = initial
    curve = []
    taken = 0
    for trade in trades:
        quantity = _quantity(equity, trade["entry"], trade["risk"], sizing,
                             vols.get(trade["day"], 0.0))
        if quantity >= QUANTITY_STEP:
            equity += base._pnl(trade["side"], trade["entry"], trade["exit"],
                                quantity)
            taken += 1
        curve.append((trade["day"], trade["ts"], equity))
        if equity <= 0:
            break
    return curve, taken


def score(curve, initial=INITIAL):
    """Sharpe, t and drawdown from a per-trade equity curve."""
    if len(curve) < 3:
        return None
    equities = [row[2] for row in curve]
    returns = [b / a - 1.0 if a > 0 else -1.0
               for a, b in zip([initial] + equities[:-1], equities)]
    deviation = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns)
    if deviation <= 0:
        return None
    t_stat = mean / deviation * math.sqrt(len(returns))
    peak, drawdown = initial, 0.0
    for value in equities:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak > 0 else 0.0)

    by_year = {}
    for (_day, ts, _equity), ret in zip(curve, returns):
        by_year.setdefault(datetime.fromtimestamp(ts, timezone.utc).year,
                           []).append(ret)
    positive = sum(1 for values in by_year.values()
                   if sum(math.log1p(max(v, -0.999)) for v in values) > 0)

    return {
        "final": equities[-1],
        "total_return_pct": 100 * (equities[-1] / initial - 1.0),
        "sharpe": mean / deviation * math.sqrt(len(returns) / _years(curve))
        if _years(curve) > 0 else 0.0,
        "t": t_stat,
        "max_drawdown": drawdown,
        "trades": len(curve),
        "positive_years": positive,
        "years": len(by_year),
    }


def _years(curve):
    if len(curve) < 2:
        return 0.0
    return (curve[-1][1] - curve[0][1]) / (365.25 * 86_400)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def _sizing_key(sizing):
    parts = [sizing["mode"]] + [f"{k}={v}" for k, v in sorted(sizing.items())
                                if k != "mode"]
    return "/".join(parts)


def _split(trades, lo, hi):
    return [t for t in trades if lo <= t["ts"] < hi]


def search(symbol="btc", verbose=True):
    """Every cell, scored in-sample only. Returns the full result table.

    The out-of-sample window is never read here -- it is not even sliced -- so
    there is no path by which a selection decision can see it.
    """
    is_lo, is_hi = _epoch(IS_START), _epoch(IS_END)
    prepared = {}
    for name in PATH_GRID["session"]:
        sessions = base.load_sessions(name, symbol)
        features = regime_features(sessions)
        prepared[name] = {
            "sessions": sessions, "features": features,
            "vols": {day: f["vol"] for day, f in features.items()},
        }

    filter_combos = [dict(zip(FILTER_GRID, values))
                     for values in itertools.product(*FILTER_GRID.values())]

    results = {}
    for name in PATH_GRID["session"]:
        context = prepared[name]
        for range_minutes in PATH_GRID["range_minutes"]:
            paths = build_paths(context["sessions"], range_minutes)
            for stop_mult, target_r, trail_r in itertools.product(
                    PATH_GRID["stop_mult"], PATH_GRID["target_r"],
                    PATH_GRID["trail_r"]):
                resolved = resolve(paths, stop_mult, target_r, trail_r)
                in_sample = _split(resolved, is_lo, is_hi)
                if len(in_sample) < MIN_TRADES:
                    continue
                for filters in filter_combos:
                    kept = apply_filters(in_sample, context["features"], **filters)
                    if len(kept) < MIN_TRADES:
                        continue
                    for sizing in SIZING_GRID:
                        curve, _taken = equity_walk(kept, sizing, context["vols"])
                        stats = score(curve)
                        if stats is None:
                            continue
                        key = (name, range_minutes, stop_mult, target_r, trail_r,
                               filters["trend"], filters["vol_band"],
                               filters["weekday"], _sizing_key(sizing))
                        stats["cell"] = {
                            "session": name, "range_minutes": range_minutes,
                            "stop_mult": stop_mult, "target_r": target_r,
                            "trail_r": trail_r, **filters, "sizing": sizing,
                        }
                        results[key] = stats
            if verbose:
                print(f"  resolved session={name} range={range_minutes}m "
                      f"-> {len(results)} cells so far")
    return results, prepared


def cell_label(cell):
    """Compact one-line description of a cell, for the ranking table."""
    sizing = cell["sizing"]
    if sizing["mode"] == "risk_leverage":
        size = f"risk{100 * sizing['risk']:g}%/cap{sizing['cap']:g}x"
    elif sizing["mode"] == "vol_target":
        size = f"vol{100 * sizing['vol_target_annual']:g}%/cap{sizing['cap']:g}x"
    else:
        size = f"fixed{sizing['fraction']:g}x"
    return (f"{cell['session']}/{cell['range_minutes']}m "
            f"s{cell['stop_mult']:g}/t{cell['target_r'] or '-'}/"
            f"tr{cell['trail_r'] or '-'} "
            f"{cell['trend'] or '-'}/{cell['vol_band'] or '-'}/"
            f"{'wk' if cell['weekday'] else '-'} {size}")


# --------------------------------------------------------------------------- #
# out-of-sample -- read exactly once, at the end
# --------------------------------------------------------------------------- #


def evaluate(cell, symbol, lo, hi, prepared=None):
    """One cell over one window. Used for the winner and its controls only."""
    name = cell["session"]
    if prepared is None:
        sessions = base.load_sessions(name, symbol)
        features = regime_features(sessions)
        prepared = {"sessions": sessions, "features": features,
                    "vols": {d: f["vol"] for d, f in features.items()}}

    paths = build_paths(prepared["sessions"], cell["range_minutes"])
    resolved = resolve(paths, cell["stop_mult"], cell["target_r"], cell["trail_r"])
    window = _split(resolved, lo, hi)
    kept = apply_filters(window, prepared["features"], cell["trend"],
                         cell["vol_band"], cell["weekday"])
    curve, _taken = equity_walk(kept, cell["sizing"], prepared["vols"])
    stats = score(curve) or {}
    if kept:
        stats["gross_bps_per_trade"] = round(10_000 * statistics.fmean(
            t["points"] / t["entry"] for t in kept), 3)
        stats["avg_r_multiple"] = round(
            statistics.fmean(t["r_multiple"] for t in kept), 3)
        lo_ci, hi_ci = bootstrap_edge([t["points"] / t["entry"] for t in kept])
        stats["edge_ci_bps"] = [round(10_000 * lo_ci, 3), round(10_000 * hi_ci, 3)]
    return stats, kept


def _cells(stats):
    """The `ret% Sharpe MDD% trades` block, or dashes when a window is empty."""
    if not stats or not stats.get("trades"):
        return f"{'-':>10}{'-':>8}{'-':>7}{'-':>7}"
    return (f"{stats['total_return_pct']:>10.1f}{stats['sharpe']:>8.2f}"
            f"{100 * stats['max_drawdown']:>7.1f}{stats['trades']:>7}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="btc")
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--record-trials", action="store_true",
                        help="charge this run's cells to trials.json")
    args = parser.parse_args()

    print(f"IN-SAMPLE {IS_START} .. {IS_END}  (ranking window)")
    results, prepared = search(args.symbol)
    print(f"\n{len(results)} cells scored in-sample")

    ranked = sorted(results.values(), key=lambda s: s["sharpe"], reverse=True)
    # A trailing stop looser than the target never binds, so many cells are the
    # same trade list under different labels. Showing them 25 times would fake a
    # plateau that is really one cell.
    seen, top = set(), []
    for stats in ranked:
        signature = (round(stats["total_return_pct"], 4), stats["trades"],
                     round(stats["max_drawdown"], 6))
        if signature in seen:
            continue
        seen.add(signature)
        top.append(stats)
        if len(top) >= args.top:
            break

    charged = trials.total(STRATEGY) + len(results)
    if args.record_trials:
        charged = trials.record(STRATEGY, len(results),
                                "ORB optimisation: path x filter x sizing grid")
    print(f"cumulative trials charged to {STRATEGY!r}: {charged}")

    oos_lo, oos_hi = _epoch(OOS_START), _epoch(OOS_END)
    print(f"\n{'=' * 118}")
    print(f"TOP {len(top)} BY IN-SAMPLE SHARPE, WITH THE SAME CELL SCORED "
          f"{OOS_START} .. {OOS_END}")
    print(f"{'=' * 118}")
    header = (f"{'cell':58}" + f"{'IS ret%':>10}{'Sh':>8}{'MDD%':>7}{'n':>7}"
              + "   " + f"{'OOS ret%':>10}{'Sh':>8}{'MDD%':>7}{'n':>7}"
              + f"{'OOS bps':>10}")
    print(header)
    print("-" * len(header))

    rows = []
    oos_sharpes = []
    for stats in top:
        cell = stats["cell"]
        oos_stats, _ = evaluate(cell, args.symbol, oos_lo, oos_hi,
                                prepared[cell["session"]])
        print(f"{cell_label(cell)[:57]:58}{_cells(stats)}   {_cells(oos_stats)}"
              + (f"{oos_stats.get('gross_bps_per_trade', 0):>10.2f}"
                 if oos_stats.get("trades") else f"{'-':>10}"))
        if oos_stats.get("trades"):
            oos_sharpes.append(oos_stats["sharpe"])
        rows.append({
            "cell": cell,
            "in_sample": {"total_return_pct": round(stats["total_return_pct"], 1),
                          "sharpe": round(stats["sharpe"], 3),
                          "t": round(stats["t"], 3),
                          "max_drawdown_pct": round(100 * stats["max_drawdown"], 1),
                          "trades": stats["trades"],
                          "positive_years": stats["positive_years"]},
            "out_of_sample": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in oos_stats.items()},
        })

    # Printed for reference, not used to pick anything: the paper's untouched
    # settings over the same two windows.
    print("-" * len(header))
    control_is, _ = evaluate(PAPER_CELL, args.symbol, _epoch(IS_START),
                             _epoch(IS_END), prepared[PAPER_CELL["session"]])
    control_oos, _ = evaluate(PAPER_CELL, args.symbol, oos_lo, oos_hi,
                              prepared[PAPER_CELL["session"]])
    print(f"{'paper defaults (reference)':58}{_cells(control_is)}   "
          f"{_cells(control_oos)}"
          + (f"{control_oos.get('gross_bps_per_trade', 0):>10.2f}"
             if control_oos.get("trades") else f"{'-':>10}"))

    if oos_sharpes:
        beat = sum(1 for value in oos_sharpes
                   if value > control_oos.get("sharpe", 0.0))
        print(f"\nIS Sharpe {top[0]['sharpe']:.2f} (best) -> OOS Sharpe: "
              f"median {statistics.median(oos_sharpes):.2f}, "
              f"range {min(oos_sharpes):.2f}..{max(oos_sharpes):.2f}")
        print(f"{beat}/{len(oos_sharpes)} of the ranked cells beat the "
              f"unoptimised paper defaults out of sample "
              f"(Sharpe {control_oos.get('sharpe', 0):.2f})")

    report = {
        "paper": "Zarattini & Aziz (2023), ORB (ssrn-4416622)",
        "symbol": f"{args.symbol}usd", "initial_balance": INITIAL,
        "in_sample": [IS_START, IS_END], "out_of_sample": [OOS_START, OOS_END],
        "ranked_by": "in-sample Sharpe",
        "cells_scored": len(results), "cumulative_trials": charged,
        "top": rows,
        "paper_defaults_reference": {
            "cell": PAPER_CELL,
            "in_sample": {k: (round(v, 4) if isinstance(v, float) else v)
                          for k, v in control_is.items()},
            "out_of_sample": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in control_oos.items()},
        },
    }
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

