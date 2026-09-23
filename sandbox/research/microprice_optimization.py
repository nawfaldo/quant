"""Staged optimisation of NQ Microprice Divergence on a $1,000 account.

The compiled strategy loses: the Rust engine returns -9.9% over 2025-02..2026-08
on $100k, and the entry's edge before the 0.2-point spread is +0.196 points per
trade at t=0.55 -- a number that is not distinguishable from zero. This module
asks whether *any* setting of the rule does better, under a protocol that can
answer "no".

PROTOCOL
  * Every cell is ranked on 2025 only. 2026 is sliced exactly once, at the end,
    to score cells that were already chosen.
  * Bars before the ranking window still feed the EWMA warm-up, the ATR and the
    regime lookbacks, so a 2025 trade sees the state it would have seen live.
  * Selection gates are the ones asked for: in-sample max drawdown under 15% of
    peak equity and monthly consistency (share of profitable months, longest
    losing run, and the share of gross profit from the single best month).
  * The cell count is charged to `trials.json`. Reading a top-of-grid table is
    picking the best of N draws, and N is what any significance claim has to be
    deflated against.

AXES -- every one the request named, swept in three stages rather than as one
cross-product. The full product is ~10^8 cells; a coordinate-staged search
covers the same axes at ~4*10^4.

  Stage 1  signal x path (17,280 cells)
    halflife      1 | 3 | 5 | 10        EWMA halflife of the tilt, minutes
    tilt          0.05 .. 0.25          entry cut on |tilt_e|
    direction     continuation | reversion
    max_spread    0.75 | 1.25 | 3.0     book-dislocation refusal
    k             0.02 .. 0.08          stop as a fraction of the 20-session ATR
    rr            1.0 | 1.5 | 2.0 | 3.0 target, in units of the stop
    time_stop     10 | 20 | 40          minutes
    trail_r       none | 0.75 | 1.5     trailing stop, in units of the stop

  Stage 2  regime / time / day filters (top 40 stage-1 cells x 540 combos)
    window        all | open | midday | afternoon | no_open
    weekday       all | mon_thu | no_mon | no_fri
    vol_band      all | low | high      ATR vs its own trailing median
    vix_band      all | low | high      VIX vs its trailing median
    trend         all | with | against  session close vs a 20-session SMA

  Stage 3  exposure and sizing (top 25 stage-2 cells x 14 modes)
    risk          0.25% .. 2% of equity per trade, the compiled shape
    dd_throttle   halve size while the account is more than X% off its peak
    vol_target    leverage set so position volatility hits an annual target
    fixed_frac    a constant multiple of equity, ignoring the stop distance

WHY IT IS TRACTABLE. Which bar a position leaves on depends only on price
levels and the bracket -- never on the signal cut, the filters or the sizing. So
each path cell is resolved once for every candidate bar and both sides, and the
signal, filter and sizing axes then sweep those pre-resolved outcomes. The
single-position occupancy rule is re-applied per cell by greedy interval
selection over the resolved exits, which is what the Rust strategy does.

CAUSALITY. The tilt EWMA is read off a closed bar and filled at the next bar's
open. The ATR uses completed sessions only. The regime medians and the SMA read
strictly prior sessions. A filter that reads the session it is filtering is not
a filter, it is the answer.

DRAWDOWN. Equity is marked conservatively: the curve carries, at every trade,
the loss that trade would post if it went straight to its stop. Closed-trade
replay understated this engine's drawdown by nearly half once before, and the
15% gate is only meaningful against the pessimistic series.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
from datetime import datetime, timezone

import numpy as np

from sandbox import data, trials
from sandbox.data import C, H, L, O, TS
from sandbox.paths import result_path

STRATEGY = "Microprice Divergence"
OUTPUT = "microprice_optimization_result.json"

INITIAL = 1_000.0
SPREAD = 0.2            # environment_rules for `idk`; slippage/commission are 0
MARGIN = 0.25           # LONG_MARGIN_REQUIREMENT
STEP = 0.01             # QUANTITY_STEP (Forex)
POINT_VALUE = 1.0       # Instrument::Forex

OPEN_MIN, EXIT_MIN = 570, 945       # 09:30 open, 15:45 forced flatten
ATR_SESSIONS = 20
SESSIONS_PER_YEAR = 252

IS_START, IS_END = "2025-01-01", "2026-01-01"
OOS_START, OOS_END = "2026-01-01", "2027-01-01"

#: A cell with fewer in-sample trades than this is not rankable -- its monthly
#: Sharpe is a statement about a handful of sessions.
MIN_TRADES = 150

LONG, SHORT = 1, -1

SIGNAL_GRID = {
    "halflife": [1, 3, 5, 10],
    "tilt": [0.05, 0.10, 0.15, 0.20, 0.25],
    "direction": ["continuation", "reversion"],
    "max_spread": [0.75, 1.25, 3.0],
}

PATH_GRID = {
    "k": [0.02, 0.03, 0.05, 0.08],
    "rr": [1.0, 1.5, 2.0, 3.0],
    "time_stop": [10, 20, 40],
    "trail_r": [None, 0.75, 1.5],
}

FILTER_GRID = {
    # Minute-of-day entry windows, inclusive of both ends.
    "window": [None, "open", "midday", "afternoon", "no_open"],
    "weekday": [None, "mon_thu", "no_mon", "no_fri"],
    "vol_band": [None, "low", "high"],
    "vix_band": [None, "low", "high"],
    "trend": [None, "with", "against"],
}

WINDOWS = {
    "open": (570, 660),         # 09:30 .. 11:00
    "midday": (660, 810),       # 11:00 .. 13:30
    "afternoon": (810, 900),    # 13:30 .. 15:00
    "no_open": (600, 900),      # 10:00 .. 15:00
}

SIZING_GRID = (
    [{"mode": "risk", "risk": r} for r in (0.0025, 0.005, 0.01, 0.02)]
    + [{"mode": "risk", "risk": r, "dd_throttle": d}
       for r in (0.005, 0.01) for d in (0.05, 0.08)]
    + [{"mode": "vol_target", "vol_target_annual": v} for v in (0.10, 0.20, 0.30)]
    + [{"mode": "fixed_frac", "fraction": f} for f in (0.5, 1.0, 2.0)]
)

#: The compiled Rust parameters, untouched. Every winner is measured against
#: this and against the same cell with each stage's additions stripped.
COMPILED = {
    "halflife": 1, "tilt": 0.15, "direction": "continuation", "max_spread": 1.25,
    "k": 0.05, "rr": 2.0, "time_stop": 20, "trail_r": None,
    "window": None, "weekday": None, "vol_band": None, "vix_band": None,
    "trend": None, "sizing": {"mode": "risk", "risk": 0.01},
}

# Selection gates. The request is drawdown under 15% and month-to-month
# consistency, so those are hard filters rather than tie-breaks.
MAX_DRAWDOWN = 0.15
MIN_POS_RATE = 0.60
MAX_LOSS_STREAK = 3
MAX_TOP_MONTH_SHARE = 0.50
#: Months a cell must actually trade in to be rankable. Without this the search
#: is won by cells whose regime filter only warms up late: three profitable
#: months in a row score a monthly Sharpe near 60 and beat every cell that
#: traded the whole year. That is a statement about the length of the series,
#: not about the strategy. 2025 offers ten and a half months of level-two data,
#: so eight is a real majority of the window.
MIN_MONTHS = 8


def _epoch(iso):
    return int(datetime.strptime(iso, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# market state
# --------------------------------------------------------------------------- #


def load_state(symbol="nq"):
    """Every per-bar and per-session array the search reads, built once.

    Timestamps are New York wall-clock stored as fake UTC, so `datetime` is used
    with `timezone.utc` throughout and no conversion is applied.
    """
    bars = data.load_bars("level_two", symbol)
    features = data.load_l2_features(symbol)
    n = len(bars)

    ts = np.fromiter((b[TS] for b in bars), dtype=np.int64, count=n)
    opens = np.fromiter((b[O] for b in bars), dtype=np.float64, count=n)
    highs = np.fromiter((b[H] for b in bars), dtype=np.float64, count=n)
    lows = np.fromiter((b[L] for b in bars), dtype=np.float64, count=n)
    closes = np.fromiter((b[C] for b in bars), dtype=np.float64, count=n)
    day = ts // 86_400
    minute = (ts % 86_400) // 60

    spread = np.full(n, np.nan)
    for i, stamp in enumerate(ts):
        row = features.get(int(stamp))
        if row is not None and row["book_valid"] and row["spread"] > 0.0:
            spread[i] = row["spread"]

    tilt = {h: _tilt_ewma(bars, features, h) for h in SIGNAL_GRID["halflife"]}

    # The bar each position is flattened on: the last bar of its session with a
    # minute before the 15:45 forced flatten.
    flat = np.zeros(n, dtype=np.int64)
    start = 0
    for i in range(1, n + 1):
        if i == n or day[i] != day[start]:
            eligible = np.nonzero(minute[start:i] < EXIT_MIN)[0]
            last = start + int(eligible[-1]) if len(eligible) else i - 1
            flat[start:i] = last
            start = i

    atr_by_day = data.atr_by_day(bars, ATR_SESSIONS)
    atr = np.array([atr_by_day.get(int(d), np.nan) for d in day])

    vix = np.array(data.vix_series(bars))
    regimes = _session_regimes(bars, atr_by_day, symbol)

    return {
        "ts": ts, "open": opens, "high": highs, "low": lows, "close": closes,
        "day": day, "minute": minute, "spread": spread, "tilt": tilt,
        "flat": flat, "atr": atr, "vix": vix, "n": n, "regimes": regimes,
    }


def _tilt_ewma(bars, features, halflife):
    """`tilt_e` per bar, NaN wherever the average is not trustworthy.

    Reproduces `strategies/microprice_divergence.py` exactly: the average resets
    on a new session, on a gap in the minute sequence and on a bar whose book is
    invalid, and it is withheld until four halflives of uninterrupted minutes
    have been folded in. A bar with no usable book contributes nothing rather
    than a fabricated zero.
    """
    alpha = 1.0 - 0.5 ** (1.0 / halflife)
    warmup = 4 * halflife
    out = np.full(len(bars), np.nan)
    value = None
    warmed = 0
    previous_minute = None
    day = None

    for i, bar in enumerate(bars):
        stamp = bar[TS]
        minute = (stamp % 86_400) // 60
        if stamp // 86_400 != day:
            day, value, warmed, previous_minute = stamp // 86_400, None, 0, None
        if not (OPEN_MIN <= minute < EXIT_MIN):
            continue
        if previous_minute is not None and previous_minute + 1 != minute:
            value, warmed = None, 0
        previous_minute = minute
        row = features.get(stamp)
        if row is None or not row["book_valid"] or row["spread"] <= 0.0:
            value, warmed = None, 0
            continue
        tilt = (row["microprice"] - row["midprice"]) / row["spread"]
        value = tilt if value is None else value + alpha * (tilt - value)
        warmed += 1
        if warmed >= warmup:
            out[i] = value
    return out


def _session_regimes(bars, atr_by_day, symbol):
    """Per session day: volatility band, VIX band, SMA trend state.

    Each reading is a comparison against a trailing median or average of
    sessions that closed strictly earlier, so the session being judged never
    contributes to its own threshold.
    """
    sessions = data.session_ranges(bars)
    days = [d for d, *_ in sessions]

    out = {}
    atr_history, vix_history = [], []
    closes = [c for *_, c in sessions]
    hourly_vix = data.load_hourly_vix()

    # One VIX reading per session: the last close stamped before the session's
    # own open, so it is knowable at the open.
    vix_by_day = {}
    index = 0
    for day in days:
        session_open = day * 86_400 + OPEN_MIN * 60
        while index + 1 < len(hourly_vix) and hourly_vix[index + 1][0] <= session_open:
            index += 1
        if index < len(hourly_vix) and hourly_vix[index][0] <= session_open:
            vix_by_day[day] = hourly_vix[index][1]

    for position, day in enumerate(days):
        entry = {"weekday": datetime.fromtimestamp(day * 86_400,
                                                   timezone.utc).weekday()}

        atr = atr_by_day.get(day)
        entry["vol_band"] = None
        if atr is not None and atr > 0:
            if len(atr_history) >= 60:
                entry["vol_band"] = ("high" if atr > statistics.median(atr_history[-250:])
                                     else "low")
            atr_history.append(atr)

        level = vix_by_day.get(day)
        entry["vix_band"] = None
        if level is not None and level > 0:
            if len(vix_history) >= 60:
                entry["vix_band"] = ("high" if level > statistics.median(vix_history[-250:])
                                     else "low")
            vix_history.append(level)

        # Trend: the previous session's close against the mean of the twenty
        # sessions that closed before it.
        entry["trend"] = None
        prior = closes[:position]
        if len(prior) >= 21:
            entry["trend"] = 1 if prior[-1] > statistics.fmean(prior[-21:-1]) else -1
        out[day] = entry
    return out


# --------------------------------------------------------------------------- #
# candidates: every bar any signal cell could fire on
# --------------------------------------------------------------------------- #


def build_candidates(state):
    """Signal bars shared by every cell, with the per-cell inputs attached.

    A bar qualifies when the book is usable, some halflife has a warmed average
    past the loosest cut, the next minute is contiguous (the fill bar), an ATR
    exists, and the entry falls in the widest window any filter uses. Narrower
    cells are then masks over this array rather than a fresh pass.
    """
    n = state["n"]
    loosest = min(SIGNAL_GRID["tilt"])
    index = np.arange(n - 1)

    contiguous = state["ts"][1:] == state["ts"][:-1] + 60
    usable = np.isfinite(state["spread"][:-1]) & np.isfinite(state["atr"][:-1])
    in_window = ((state["minute"][:-1] >= OPEN_MIN)
                 & (state["minute"][:-1] <= max(hi for _, hi in WINDOWS.values())))
    warm = np.zeros(n - 1, dtype=bool)
    for series in state["tilt"].values():
        warm |= np.abs(series[:-1]) >= loosest

    keep = contiguous & usable & in_window & warm & (state["atr"][:-1] > 0)
    signal_index = index[keep]

    return {
        "signal": signal_index,
        "fill": signal_index + 1,
        "entry": state["open"][signal_index + 1],
        "atr": state["atr"][signal_index],
        "spread": state["spread"][signal_index],
        "day": state["day"][signal_index],
        "minute": state["minute"][signal_index],
        "ts": state["ts"][signal_index + 1],
        "flat": state["flat"][signal_index + 1],
        "tilt": {h: series[signal_index] for h, series in state["tilt"].items()},
    }


# --------------------------------------------------------------------------- #
# path stage: which bar each candidate leaves on
# --------------------------------------------------------------------------- #


def resolve_path(state, candidates, side, k, rr, time_stop, trail_r,
                 stop_points=None, bracket=True):
    """Per-unit outcome of entering every candidate on `side` under one bracket.

    Three exit families share this resolver:

      * `k`/`rr`            -- the compiled shape, a bracket scaled by the
                               20-session ATR, so the distance tracks the
                               regime;
      * `stop_points`       -- a fixed bracket in points, the same distance in
                               every regime;
      * `bracket=False`     -- no stop and no target at all. The position is
                               held exactly `time_stop` minutes and then leaves
                               at the open, which measures the signal's
                               directional information at that horizon without
                               any path dependence to launder it.

    Mirrors `execution.resolve` for a single position, vectorised over
    candidates with one pass per minute of the hold: the stop is checked before
    the target on an ambiguous bar, the time stop is consulted only once the
    bracket has not fired, and the session flatten runs last. The trailing stop
    ratchets off bars that have already closed, so the level applied to bar j
    was knowable at bar j-1.

    Returns `(exit_index, points)`; `points` is per unit and already carries the
    whole spread at entry, as the Rust engine charges it.
    """
    fill = candidates["fill"]
    entry = candidates["entry"]
    stop = (np.full(len(fill), float(stop_points)) if stop_points is not None
            else k * candidates["atr"])
    target = rr * stop
    flat = candidates["flat"]
    highs, lows, opens, closes = (state["high"], state["low"],
                                  state["open"], state["close"])
    minute = state["minute"]
    entry_minute = minute[fill]

    count = len(fill)
    exit_index = np.full(count, -1, dtype=np.int64)
    exit_price = np.full(count, np.nan)
    extreme = entry.copy()
    active = np.ones(count, dtype=bool)

    fixed_stop = entry - side * stop
    take = entry + side * target

    # A position opened on the flatten bar itself is flattened at that bar's
    # close without ever being offered its bracket.
    opens_on_flat = fill >= flat
    if opens_on_flat.any():
        exit_index[opens_on_flat] = flat[opens_on_flat]
        exit_price[opens_on_flat] = closes[flat[opens_on_flat]]
        active &= ~opens_on_flat

    for offset in range(1, time_stop + 2):
        if not active.any():
            break
        where = np.nonzero(active)[0]
        j = fill[where] + offset

        # Past the flatten bar means the flatten already happened.
        past = j > flat[where]
        if past.any():
            done = where[past]
            exit_index[done] = flat[done]
            exit_price[done] = closes[flat[done]]
            active[done] = False
            where = where[~past]
            if not len(where):
                continue
            j = fill[where] + offset

        level = fixed_stop[where]
        if trail_r is not None:
            trail = trail_r * stop[where]
            level = (np.maximum(level, extreme[where] - trail) if side == LONG
                     else np.minimum(level, extreme[where] + trail))

        if not bracket:
            # No stop and no target: only the clock and the session flatten can
            # end the position.
            stop_hit = target_hit = np.zeros(len(where), dtype=bool)
            stop_fill = target_fill = opens[j]
        elif side == LONG:
            stop_hit = lows[j] <= level
            target_hit = highs[j] >= take[where]
            stop_fill = np.minimum(opens[j], level)
            target_fill = np.maximum(opens[j], take[where])
        else:
            stop_hit = highs[j] >= level
            target_hit = lows[j] <= take[where]
            stop_fill = np.maximum(opens[j], level)
            target_fill = np.minimum(opens[j], take[where])

        timed = minute[j] >= entry_minute[where] + time_stop
        price = np.where(stop_hit, stop_fill,
                         np.where(target_hit, target_fill, opens[j]))
        left = stop_hit | target_hit | timed

        # Still open on the flatten bar: the flatten runs after the bracket.
        flatten = ~left & (j >= flat[where])
        price = np.where(flatten, closes[j], price)
        left = left | flatten

        done = where[left]
        exit_index[done] = j[left]
        exit_price[done] = price[left]
        active[done] = False

        carry = where[~left]
        if len(carry):
            live = j[~left]
            extreme[carry] = (np.maximum(extreme[carry], highs[live]) if side == LONG
                              else np.minimum(extreme[carry], lows[live]))

    # A candidate whose hold ran past the loop without resolving is flattened;
    # the loop covers `time_stop` minutes, so this only catches feed gaps.
    if active.any():
        rest = np.nonzero(active)[0]
        exit_index[rest] = flat[rest]
        exit_price[rest] = closes[flat[rest]]

    if side == LONG:
        points = (exit_price - (entry + SPREAD)) * POINT_VALUE
    else:
        points = ((entry - SPREAD) - exit_price) * POINT_VALUE
    return exit_index, points


# --------------------------------------------------------------------------- #
# occupancy: the Rust strategy holds at most one position
# --------------------------------------------------------------------------- #


def occupy(signal_index, exit_index):
    """Greedy single-position selection over an eligible, time-ordered set.

    The Rust strategy refuses a signal while a position is open and evaluates
    its exits before the signal check, so the exit bar itself is free again:
    the next signal taken is the first whose *signal* index is at or after the
    previous position's exit index. Equivalent to the replica's `free_from`
    walk, done by jumping rather than scanning.
    """
    taken = []
    position = 0
    total = len(signal_index)
    while position < total:
        taken.append(position)
        position = int(np.searchsorted(signal_index, exit_index[position], "left"))
        if position <= taken[-1]:
            position = taken[-1] + 1
    return np.array(taken, dtype=np.int64)


# --------------------------------------------------------------------------- #
# sizing and scoring
# --------------------------------------------------------------------------- #


def equity_walk(entry, stop, points, atr, sizing, initial=INITIAL):
    """Compound one trade list through one sizing mode.

    Every mode is equity-based, so the curve is a genuine return series. The
    quantity is always capped by buying power (`equity / margin / price`), which
    on a $1,000 account is the binding constraint for most risk fractions --
    that ceiling is why the risk axis flattens out here rather than a bug.

    Returns `(pnl per trade, closed equity curve, marked equity curve)`. The
    marked curve carries the open risk of each trade -- what the account is
    worth if that position goes straight to its stop -- because a closed-trade
    replay has understated this engine's drawdown before.
    """
    mode = sizing["mode"]
    throttle = sizing.get("dd_throttle")
    # Buying power, as a margin requirement. On a $1,000 account the default
    # 0.25 (4x notional) binds before any risk fraction does -- NQ near 21,000
    # allows about 0.19 lots -- so this is the knob that actually moves
    # exposure, and it is a statement about the broker, not about the strategy.
    margin = sizing.get("margin", MARGIN)
    equity = initial
    peak = initial
    pnls = np.zeros(len(points))
    closed = np.zeros(len(points))
    marked = np.zeros(len(points))

    for i in range(len(points)):
        price = entry[i]
        risk_points = stop[i]
        if equity <= 0 or price <= 0 or risk_points <= 0:
            closed[i] = marked[i] = equity
            continue

        ceiling = equity / margin / price
        if mode == "risk":
            raw = min(equity * sizing["risk"] / risk_points, ceiling)
        elif mode == "vol_target":
            daily = atr[i] / price if price > 0 else 0.0
            annual = daily * math.sqrt(SESSIONS_PER_YEAR)
            leverage = (sizing["vol_target_annual"] / annual) if annual > 0 else 0.0
            raw = min(equity * leverage / price, ceiling)
        elif mode == "fixed_frac":
            raw = min(equity * sizing["fraction"] / price, ceiling)
        else:
            raise ValueError(f"unknown sizing mode {mode!r}")

        # `scale` is account leverage applied *after* the buying-power ceiling,
        # so a value above 1 is a statement that the broker allows more than
        # `margin` implies. It is reported as an implied notional multiple
        # rather than hidden, because it is the only knob here that moves
        # drawdown without changing a single trading decision.
        raw *= sizing.get("scale", 1.0)

        if throttle is not None and equity < peak * (1.0 - throttle):
            raw *= 0.5

        quantity = math.floor(raw / STEP) * STEP
        if quantity < STEP:
            closed[i] = marked[i] = equity
            continue

        # Worst case while the position is open: straight to the stop.
        marked[i] = equity - risk_points * quantity
        pnls[i] = points[i] * quantity
        equity += pnls[i]
        closed[i] = equity
        peak = max(peak, equity)
    return pnls, closed, marked


def _drawdown(closed, marked, initial=INITIAL):
    """Deepest peak-to-trough fall of the marked series, as a fraction."""
    peak = initial
    worst = 0.0
    for i in range(len(closed)):
        low = min(marked[i], closed[i])
        if peak > 0:
            worst = max(worst, (peak - low) / peak)
        peak = max(peak, closed[i])
    return worst


def score(ts, pnls, closed, marked, initial=INITIAL):
    """Monthly-consistency bundle plus drawdown, from one sized trade list."""
    if len(pnls) < 3:
        return None
    months = {}
    for stamp, pnl in zip(ts, pnls):
        stamp = datetime.fromtimestamp(int(stamp), timezone.utc)
        months.setdefault(f"{stamp.year}-{stamp.month:02d}", 0.0)
        months[f"{stamp.year}-{stamp.month:02d}"] += pnl
    series = [months[k] for k in sorted(months)]
    if len(series) < 3:
        return None

    mean = statistics.fmean(series)
    deviation = statistics.pstdev(series)
    streak = worst_streak = 0
    for value in series:
        streak = streak + 1 if value < 0 else 0
        worst_streak = max(worst_streak, streak)
    windows = [sum(series[i:i + 3]) for i in range(max(1, len(series) - 2))]
    gross = sum(v for v in series if v > 0)

    final = closed[-1] if len(closed) else initial
    returns = np.diff(np.concatenate(([initial], closed)))
    base = np.concatenate(([initial], closed[:-1]))
    trade_returns = np.divide(returns, base, out=np.zeros_like(returns),
                              where=base > 0)
    deviation_t = float(np.std(trade_returns)) if len(trade_returns) > 1 else 0.0

    return {
        "final": round(float(final), 2),
        "total_return_pct": round(100.0 * (final / initial - 1.0), 2),
        "trades": int(len(pnls)),
        "max_drawdown": round(_drawdown(closed, marked, initial), 4),
        "msharpe": round(mean / deviation, 3) if deviation > 0 else 0.0,
        "t": round(float(np.mean(trade_returns)) / deviation_t * math.sqrt(len(pnls)), 3)
        if deviation_t > 0 else 0.0,
        "n_months": len(series),
        "pos_rate": round(sum(1 for v in series if v > 0) / len(series), 3),
        "max_loss_streak": worst_streak,
        "worst_month": round(min(series), 2),
        "best_month": round(max(series), 2),
        "worst_quarter": round(min(windows), 2),
        "top_month_share": round(max(series) / gross, 3) if gross > 0 else 0.0,
        "months": {k: round(v, 2) for k, v in sorted(months.items())},
    }


def passes(stats):
    """The requested gates: drawdown under 15% and month-to-month consistency."""
    return (stats is not None
            and stats["trades"] >= MIN_TRADES
            and stats["n_months"] >= MIN_MONTHS
            and stats["max_drawdown"] <= MAX_DRAWDOWN
            and stats["pos_rate"] >= MIN_POS_RATE
            and stats["max_loss_streak"] <= MAX_LOSS_STREAK
            and stats["top_month_share"] <= MAX_TOP_MONTH_SHARE
            and stats["total_return_pct"] > 0)


# --------------------------------------------------------------------------- #
# cell evaluation
# --------------------------------------------------------------------------- #


def signal_mask(candidates, halflife, tilt, direction, max_spread):
    """Eligible candidates and the side each one trades, for one signal cell."""
    values = candidates["tilt"][halflife]
    ok = np.isfinite(values) & (candidates["spread"] <= max_spread)
    long_side = ok & (values >= tilt)
    short_side = ok & (values <= -tilt)
    if direction == "reversion":
        long_side, short_side = short_side, long_side
    side = np.zeros(len(values), dtype=np.int8)
    side[long_side] = LONG
    side[short_side] = SHORT
    return side


def filter_mask(candidates, regimes, window, weekday, vol_band, vix_band, trend,
                side):
    """Which eligible candidates a filter combination lets through.

    An absent regime reading blocks the trade rather than waving it through: a
    gate that silently stops gating is how a filter looks free.
    """
    keep = side != 0
    if window is not None:
        lo, hi = WINDOWS[window]
        keep &= (candidates["minute"] >= lo) & (candidates["minute"] <= hi)
    if weekday is None and vol_band is None and vix_band is None and trend is None:
        return keep

    for position in np.nonzero(keep)[0]:
        regime = regimes.get(int(candidates["day"][position]))
        if regime is None:
            keep[position] = False
            continue
        if weekday is not None:
            day = regime["weekday"]
            if ((weekday == "mon_thu" and day >= 4)
                    or (weekday == "no_mon" and day == 0)
                    or (weekday == "no_fri" and day == 4)):
                keep[position] = False
                continue
        if vol_band is not None and regime["vol_band"] != vol_band:
            keep[position] = False
            continue
        if vix_band is not None and regime["vix_band"] != vix_band:
            keep[position] = False
            continue
        if trend is not None:
            state = regime["trend"]
            if state is None:
                keep[position] = False
                continue
            aligned = (state > 0) == (side[position] == LONG)
            if (trend == "with") != aligned:
                keep[position] = False
    return keep


def build_trades(candidates, side, keep, resolved, lo, hi):
    """Filtered, occupancy-respecting trades inside `[lo, hi)`, in entry order.

    Filters are applied *before* occupancy on purpose: a signal the filter
    refuses leaves the account flat, so the next signal is free to take the
    slot. Filtering afterwards would silently keep the blocked trade's dead
    time.
    """
    eligible = np.nonzero(keep)[0]
    if not len(eligible):
        return None

    exits = np.empty(len(eligible), dtype=np.int64)
    points = np.empty(len(eligible))
    for direction in (LONG, SHORT):
        which = side[eligible] == direction
        if which.any():
            exit_index, per_unit = resolved[direction]
            exits[which] = exit_index[eligible[which]]
            points[which] = per_unit[eligible[which]]

    chosen = occupy(candidates["signal"][eligible], exits)
    taken = eligible[chosen]

    stamps = candidates["ts"][taken]
    inside = (stamps >= lo) & (stamps < hi)
    if inside.sum() < 3:
        return None
    return {
        "ts": stamps[inside],
        "entry": candidates["entry"][taken][inside],
        "atr": candidates["atr"][taken][inside],
        "points": points[chosen][inside],
        "stop": None,   # filled by the caller, which knows `k`
        "index": taken[inside],
    }


def evaluate(state, candidates, resolved_cache, cell, lo, hi, initial=INITIAL):
    """One complete cell over one window. Returns its stats bundle or None."""
    side = signal_mask(candidates, cell["halflife"], cell["tilt"],
                       cell["direction"], cell["max_spread"])
    keep = filter_mask(candidates, state["regimes"], cell["window"],
                       cell["weekday"], cell["vol_band"], cell["vix_band"],
                       cell["trend"], side)
    resolved = resolved_cache(cell["k"], cell["rr"], cell["time_stop"],
                              cell["trail_r"])
    trades = build_trades(candidates, side, keep, resolved, lo, hi)
    if trades is None:
        return None
    stop = cell["k"] * trades["atr"]
    if cell["trail_r"] is not None:
        # A trail tighter than the fixed stop is the real initial risk, so it is
        # what sizing divides the risk budget by.
        stop = np.minimum(stop, cell["trail_r"] * stop)
    pnls, closed, marked = equity_walk(trades["entry"], stop, trades["points"],
                                       trades["atr"], cell["sizing"], initial)
    stats = score(trades["ts"], pnls, closed, marked, initial)
    if stats is not None:
        stats["gross_points_per_trade"] = round(
            float(np.mean(trades["points"] + SPREAD)), 4)
        stats["net_points_per_trade"] = round(float(np.mean(trades["points"])), 4)
    return stats


class PathCache:
    """Resolved exits per path cell, shared by every signal, filter and sizing.

    The exit a position takes depends only on the bracket and the bars, so this
    is computed once per `(k, rr, time_stop, trail_r)` and reused across the
    whole search. It is the reason the grid costs minutes rather than days.
    """

    def __init__(self, state, candidates, limit=64):
        self.state = state
        self.candidates = candidates
        self.limit = limit
        self.store = {}

    def __call__(self, k, rr, time_stop, trail_r, stop_points=None,
                 bracket=True):
        key = (k, rr, time_stop, trail_r, stop_points, bracket)
        hit = self.store.get(key)
        if hit is None:
            hit = {side: resolve_path(self.state, self.candidates, side,
                                      k, rr, time_stop, trail_r,
                                      stop_points, bracket)
                   for side in (LONG, SHORT)}
            if len(self.store) >= self.limit:
                self.store.pop(next(iter(self.store)))
            self.store[key] = hit
        return hit


# --------------------------------------------------------------------------- #
# the three stages
# --------------------------------------------------------------------------- #


def stage_one(state, candidates, lo, hi, verbose=True):
    """Signal x path, no filters, compiled sizing. Ranked on in-sample only."""
    cache = PathCache(state, candidates)
    signal_cells = [dict(zip(SIGNAL_GRID, values))
                    for values in itertools.product(*SIGNAL_GRID.values())]
    results = []
    scanned = 0

    for k, rr, time_stop, trail_r in itertools.product(*PATH_GRID.values()):
        resolved = cache(k, rr, time_stop, trail_r)
        for signal in signal_cells:
            side = signal_mask(candidates, signal["halflife"], signal["tilt"],
                               signal["direction"], signal["max_spread"])
            trades = build_trades(candidates, side, side != 0, resolved, lo, hi)
            scanned += 1
            if trades is None:
                continue
            stop = k * trades["atr"]
            if trail_r is not None:
                stop = np.minimum(stop, trail_r * stop)
            pnls, closed, marked = equity_walk(trades["entry"], stop,
                                               trades["points"], trades["atr"],
                                               COMPILED["sizing"])
            stats = score(trades["ts"], pnls, closed, marked)
            if stats is None or stats["trades"] < MIN_TRADES:
                continue
            cell = {**signal, "k": k, "rr": rr, "time_stop": time_stop,
                    "trail_r": trail_r}
            stats["cell"] = cell
            results.append(stats)
        if verbose:
            print(f"  path k={k} rr={rr} ts={time_stop} trail={trail_r}: "
                  f"{len(results)} scored / {scanned} tried")
    return results, scanned


def stage_two(state, candidates, seeds, lo, hi, verbose=True):
    """Regime, time-of-day and weekday filters over the stage-one survivors."""
    cache = PathCache(state, candidates)
    combos = [dict(zip(FILTER_GRID, values))
              for values in itertools.product(*FILTER_GRID.values())]
    results = []
    scanned = 0
    for position, seed in enumerate(seeds, 1):
        base = seed["cell"]
        resolved = cache(base["k"], base["rr"], base["time_stop"], base["trail_r"])
        side = signal_mask(candidates, base["halflife"], base["tilt"],
                           base["direction"], base["max_spread"])
        for filters in combos:
            keep = filter_mask(candidates, state["regimes"], filters["window"],
                               filters["weekday"], filters["vol_band"],
                               filters["vix_band"], filters["trend"], side)
            trades = build_trades(candidates, side, keep, resolved, lo, hi)
            scanned += 1
            if trades is None:
                continue
            stop = base["k"] * trades["atr"]
            if base["trail_r"] is not None:
                stop = np.minimum(stop, base["trail_r"] * stop)
            pnls, closed, marked = equity_walk(trades["entry"], stop,
                                               trades["points"], trades["atr"],
                                               COMPILED["sizing"])
            stats = score(trades["ts"], pnls, closed, marked)
            if stats is None or stats["trades"] < MIN_TRADES:
                continue
            stats["cell"] = {**base, **filters}
            results.append(stats)
        if verbose:
            print(f"  seed {position}/{len(seeds)}: {len(results)} scored / "
                  f"{scanned} tried")
    return results, scanned


def stage_three(state, candidates, seeds, lo, hi, verbose=True):
    """Exposure and sizing over the stage-two survivors."""
    cache = PathCache(state, candidates)
    results = []
    scanned = 0
    for position, seed in enumerate(seeds, 1):
        for sizing in SIZING_GRID:
            cell = {**seed["cell"], "sizing": sizing}
            stats = evaluate(state, candidates, cache, cell, lo, hi)
            scanned += 1
            if stats is None or stats["trades"] < MIN_TRADES:
                continue
            stats["cell"] = cell
            results.append(stats)
        if verbose:
            print(f"  seed {position}/{len(seeds)}: {len(results)} scored / "
                  f"{scanned} tried")
    return results, scanned


def best_effort(results, top):
    """Best monthly Sharpes among rankable cells, ignoring the quality gates.

    Used only when nothing clears the gates, so that "the gates were binding"
    and "the surface has nothing in it" stay distinguishable. The month-count
    floor is *not* relaxed: a three-month series is not a weaker candidate, it
    is an unscoreable one.
    """
    rankable = [r for r in results
                if r["n_months"] >= MIN_MONTHS and r["trades"] >= MIN_TRADES]
    return sorted(rankable, key=lambda s: s["msharpe"], reverse=True)[:top]


def rank(results, top):
    """Gate-passing cells, best monthly Sharpe first, duplicates collapsed.

    Two cells that produce the same trade list under different labels are one
    draw, not two; showing both would fake a plateau.
    """
    seen, out = set(), []
    for stats in sorted((r for r in results if passes(r)),
                        key=lambda s: s["msharpe"], reverse=True):
        signature = (round(stats["total_return_pct"], 4), stats["trades"],
                     round(stats["max_drawdown"], 6))
        if signature in seen:
            continue
        seen.add(signature)
        out.append(stats)
        if len(out) >= top:
            break
    return out


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #


def cell_label(cell):
    sizing = cell.get("sizing", COMPILED["sizing"])
    if sizing["mode"] == "risk":
        size = f"risk{100 * sizing['risk']:g}%"
        if sizing.get("dd_throttle"):
            size += f"/dd{100 * sizing['dd_throttle']:g}"
    elif sizing["mode"] == "vol_target":
        size = f"vol{100 * sizing['vol_target_annual']:g}%"
    else:
        size = f"fixed{sizing['fraction']:g}x"
    return (f"h{cell['halflife']}/t{cell['tilt']:g}/"
            f"{'rev' if cell['direction'] == 'reversion' else 'con'}/"
            f"sp{cell['max_spread']:g} "
            f"k{cell['k']:g}/rr{cell['rr']:g}/ts{cell['time_stop']}/"
            f"tr{cell.get('trail_r') or '-'} "
            f"{cell.get('window') or '-'}/{cell.get('weekday') or '-'}/"
            f"{cell.get('vol_band') or '-'}/{cell.get('vix_band') or '-'}/"
            f"{cell.get('trend') or '-'} {size}")


def _row(stats):
    if not stats:
        return f"{'-':>9}{'-':>7}{'-':>7}{'-':>6}{'-':>6}{'-':>6}"
    return (f"{stats['total_return_pct']:>9.1f}{stats['msharpe']:>7.2f}"
            f"{100 * stats['max_drawdown']:>7.1f}{stats['trades']:>6}"
            f"{stats['pos_rate']:>6.2f}{stats['max_loss_streak']:>6}")


HEADER = (f"{'cell':70}" + f"{'IS ret%':>9}{'mSh':>7}{'MDD%':>7}{'n':>6}"
          f"{'pos':>6}{'str':>6}" + "  " +
          f"{'OOS ret%':>9}{'mSh':>7}{'MDD%':>7}{'n':>6}{'pos':>6}{'str':>6}")


def edge_scan(state, candidates, lo, hi):
    """Per-unit gross edge of every signal cell, before sizing and before costs.

    Sizing, filters and the bracket can all move a P&L curve around; none of
    them can create an edge the entry does not have. This is the measurement
    that decides whether the search had anything to find: the mean points a
    trade earns from the entry rule alone, with the spread added back, and the
    t-statistic of that mean.
    """
    cache = PathCache(state, candidates)
    resolved = cache(COMPILED["k"], COMPILED["rr"], COMPILED["time_stop"],
                     COMPILED["trail_r"])
    rows = []
    for values in itertools.product(*SIGNAL_GRID.values()):
        cell = dict(zip(SIGNAL_GRID, values))
        side = signal_mask(candidates, cell["halflife"], cell["tilt"],
                           cell["direction"], cell["max_spread"])
        trades = build_trades(candidates, side, side != 0, resolved, lo, hi)
        if trades is None or len(trades["ts"]) < MIN_TRADES:
            continue
        gross = trades["points"] + SPREAD
        deviation = float(np.std(gross, ddof=1))
        rows.append({
            **cell,
            "trades": int(len(gross)),
            "gross_points": round(float(np.mean(gross)), 4),
            "net_points": round(float(np.mean(trades["points"])), 4),
            "t": round(float(np.mean(gross)) / deviation * math.sqrt(len(gross)), 3)
            if deviation > 0 else 0.0,
        })
    return rows


def run_edge_scan(state, candidates):
    """Print the gross-edge scan for both windows, side by side."""
    is_rows = {(_key(r)): r for r in edge_scan(state, candidates,
                                               _epoch(IS_START), _epoch(IS_END))}
    oos_rows = {(_key(r)): r for r in edge_scan(state, candidates,
                                                _epoch(OOS_START), _epoch(OOS_END))}
    header = (f"{'signal cell':34}{'IS n':>7}{'IS gross':>10}{'IS t':>7}"
              f"{'OOS n':>8}{'OOS gross':>11}{'OOS t':>7}")
    print("\nGROSS PER-TRADE EDGE BY SIGNAL CELL (points, spread added back)")
    print("The entry rule alone. No sizing, no filters, compiled bracket.")
    print(header)
    print("-" * len(header))
    for key in sorted(is_rows, key=lambda k: is_rows[k]["gross_points"],
                      reverse=True):
        row = is_rows[key]
        out = oos_rows.get(key)
        label = (f"h{row['halflife']}/t{row['tilt']:g}/"
                 f"{'rev' if row['direction'] == 'reversion' else 'con'}/"
                 f"sp{row['max_spread']:g}")
        tail = (f"{out['trades']:>8}{out['gross_points']:>11.3f}{out['t']:>7.2f}"
                if out else f"{'-':>8}{'-':>11}{'-':>7}")
        print(f"{label:34}{row['trades']:>7}{row['gross_points']:>10.3f}"
              f"{row['t']:>7.2f}{tail}")

    significant = [r for r in is_rows.values() if abs(r["t"]) >= 2.0]
    print(f"\n{len(significant)}/{len(is_rows)} signal cells reach |t| >= 2 "
          f"in sample before costs")
    agree = sum(1 for k, r in is_rows.items()
                if k in oos_rows
                and (r["gross_points"] > 0) == (oos_rows[k]["gross_points"] > 0))
    print(f"{agree}/{len(oos_rows)} cells keep the sign of their gross edge "
          f"out of sample (coin-flip expectation is {len(oos_rows) / 2:.0f})")
    return {"in_sample": list(is_rows.values()),
            "out_of_sample": list(oos_rows.values())}


def _key(row):
    return (row["halflife"], row["tilt"], row["direction"], row["max_spread"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="nq")
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--stage-one-seeds", type=int, default=40)
    parser.add_argument("--stage-two-seeds", type=int, default=25)
    parser.add_argument("--balance", type=float, default=INITIAL)
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--edge-scan", action="store_true",
                        help="report the entry rule's gross per-trade edge and "
                             "stop, without running the staged search")
    args = parser.parse_args()

    is_lo, is_hi = _epoch(IS_START), _epoch(IS_END)
    oos_lo, oos_hi = _epoch(OOS_START), _epoch(OOS_END)

    print(f"loading {args.symbol} level-two bars and features ...")
    state = load_state(args.symbol)
    candidates = build_candidates(state)
    print(f"  {state['n']} bars, {len(candidates['signal'])} candidate signals")

    if args.edge_scan:
        scan = run_edge_scan(state, candidates)
        path = result_path("microprice_edge_scan.json")
        with open(path, "w") as handle:
            json.dump(scan, handle, indent=1)
        print(f"\nwrote {path}")
        return

    print(f"IN-SAMPLE {IS_START} .. {IS_END}   (the only window ranked on)")

    print("\nstage 1: signal x path")
    one, scanned_one = stage_one(state, candidates, is_lo, is_hi)
    seeds_one = rank(one, args.stage_one_seeds)
    print(f"  {len(one)} cells scored, {len(seeds_one)} pass the gates")

    if not seeds_one:
        # Nothing to filter or size. Fall back to the best cells by monthly
        # Sharpe so the later stages still get a chance to fix consistency,
        # rather than reporting "no result" when the gate was the binding
        # constraint rather than the surface.
        print("  no cell cleared the gates; seeding stage 2 with the best "
              "monthly Sharpes instead")
        seeds_one = best_effort(one, args.stage_one_seeds)

    print("\nstage 2: regime / time / day filters")
    two, scanned_two = stage_two(state, candidates, seeds_one, is_lo, is_hi)
    seeds_two = rank(two, args.stage_two_seeds)
    print(f"  {len(two)} cells scored, {len(seeds_two)} pass the gates")
    if not seeds_two:
        print("  no filtered cell cleared the gates; seeding stage 3 with the "
              "best monthly Sharpes instead")
        seeds_two = best_effort(two, args.stage_two_seeds)

    print("\nstage 3: exposure and sizing")
    three, scanned_three = stage_three(state, candidates, seeds_two, is_lo, is_hi)
    final = rank(three, args.top)
    print(f"  {len(three)} cells scored, {len(final)} pass the gates")
    if not final:
        final = best_effort(three, args.top)

    cells = scanned_one + scanned_two + scanned_three
    charged = trials.total(STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(STRATEGY, cells,
                                "microprice optimisation: signal x path, "
                                "filters, sizing")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {STRATEGY!r}: {charged}")

    # ---- the out-of-sample window is read here and nowhere earlier ---------
    cache = PathCache(state, candidates)
    print(f"\n{'=' * len(HEADER)}")
    print(f"TOP {len(final)} BY IN-SAMPLE MONTHLY SHARPE, SAME CELL SCORED "
          f"{OOS_START} .. {OOS_END}")
    print(f"{'=' * len(HEADER)}")
    print(HEADER)
    print("-" * len(HEADER))

    rows = []
    for stats in final:
        cell = stats["cell"]
        out = evaluate(state, candidates, cache, cell, oos_lo, oos_hi,
                       args.balance)
        print(f"{cell_label(cell)[:69]:70}{_row(stats)}  {_row(out)}")
        rows.append({"cell": _serialisable(cell), "in_sample": _trim(stats),
                     "out_of_sample": _trim(out)})

    print("-" * len(HEADER))
    control_is = evaluate(state, candidates, cache, COMPILED, is_lo, is_hi,
                          args.balance)
    control_oos = evaluate(state, candidates, cache, COMPILED, oos_lo, oos_hi,
                           args.balance)
    print(f"{'compiled defaults (reference, nothing tuned)':70}"
          f"{_row(control_is)}  {_row(control_oos)}")

    survivors = [r for r in rows if r["out_of_sample"]
                 and r["out_of_sample"].get("total_return_pct", -1) > 0]
    print(f"\n{len(survivors)}/{len(rows)} of the ranked cells are profitable "
          f"out of sample")
    if rows:
        oos_dd = [r["out_of_sample"].get("max_drawdown")
                  for r in rows if r["out_of_sample"]]
        oos_dd = [d for d in oos_dd if d is not None]
        if oos_dd:
            under = sum(1 for d in oos_dd if d <= MAX_DRAWDOWN)
            print(f"{under}/{len(oos_dd)} hold drawdown under "
                  f"{100 * MAX_DRAWDOWN:g}% out of sample "
                  f"(median {100 * statistics.median(oos_dd):.1f}%)")

    report = {
        "strategy": STRATEGY,
        "symbol": args.symbol,
        "initial_balance": args.balance,
        "spread_points": SPREAD,
        "in_sample": [IS_START, IS_END],
        "out_of_sample": [OOS_START, OOS_END],
        "ranked_by": "in-sample monthly Sharpe, after the drawdown and "
                     "consistency gates",
        "gates": {"max_drawdown": MAX_DRAWDOWN, "min_pos_rate": MIN_POS_RATE,
                  "max_loss_streak": MAX_LOSS_STREAK,
                  "max_top_month_share": MAX_TOP_MONTH_SHARE,
                  "min_trades": MIN_TRADES},
        "cells_evaluated": cells,
        "cumulative_trials": charged,
        "top": rows,
        "compiled_reference": {"cell": _serialisable(COMPILED),
                               "in_sample": _trim(control_is),
                               "out_of_sample": _trim(control_oos)},
    }
    path = result_path(args.out)
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {path}")


def _serialisable(cell):
    return {k: (v if not isinstance(v, dict) else dict(v)) for k, v in cell.items()}


def _trim(stats):
    if not stats:
        return None
    return {k: v for k, v in stats.items() if k != "cell"}


if __name__ == "__main__":
    main()
