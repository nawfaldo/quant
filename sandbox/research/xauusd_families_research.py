"""Nine intraday strategy families on XAUUSD, with a sealed 2025-2026 holdout.

The USOIL protocol (`usoil_families_research`) re-pointed at gold, reusing its
signal functions and summary code unchanged so the two instruments are directly
comparable. Same nine price-only families -- three breakout, two trend
following, two momentum, two mean reversion -- the same 11 exit modes, 5 stop
widths, 3 trend filters and 3 volatility regimes, the same selection gates, the
same coin-flip null control, and the same "select at 10,000, report at 1,000"
discipline. The oil-specific EIA family is dropped; nothing replaces it.

WHY GOLD IS A BETTER PRIOR THAN OIL. USOIL failed first and foremost on cost: a
0.20 spread on a ~70 dollar barrel is ~28bp, one to two orders of magnitude
above any edge in the grid. Gold trades at 1,270-5,600 over this sample, so the
same 0.20 is 1.6bp at 2018 prices and 0.44bp at 2026 prices. The cost wall that
made oil hopeless is 17-64x smaller here, so a real edge has room to survive.

TWO HAZARDS THIS INSTRUMENT HAS AND OIL DID NOT, both handled below.

1. **The holdout is a bull market.** Gold ran 2,614 -> 5,597 across 2025-2026,
   +114%. Any long-biased rule will post a wonderful out-of-sample number for
   reasons that have nothing to do with skill. `benchmarks()` therefore reports
   buy-and-hold and a mechanical always-long-intraday control for every window,
   and no family result should be read without them --
   [[zero-is-the-wrong-backtest-baseline]].

2. **Volatility exploded.** The mean 30-minute session bar range went 2.09
   (2018) -> 5.55 (2024) -> 19.84 (2026), a 9.5x rise in absolute terms and
   ~2.6x relative to price. Every stop, target and trail here is ATR-scaled and
   sizing is volatility-targeted, so the rules adapt; but the regime table in
   `why` prints the shift explicitly rather than letting it hide.

CLOCK. `xauusd_1m` is New York wall-clock, established the same way as USOIL and
not assumed: the daily maintenance break is the empty 17:00 hour, and hourly
volume peaks across 08:00-11:00, the COMEX open. Nothing converts timestamps.

SESSION. 08:00-16:00 New York -- the COMEX open through the New York close.
Hourly volume runs 5010/6095/6043/4461 across 08:00-11:00 and only collapses at
16:00 (741). This is wider than the oil pit session because gold's liquidity is
wider; 17 thirty-minute buckets a session.

CONTRACT AND COSTS. Exness XAUUSD is 100 troy ounces a lot with a 0.01 lot
minimum, so quantity is carried in **ounces** and the size step is 1 ounce. A
one dollar move is one dollar an ounce. The cost model is the 0.20 spread
charged wholly at entry; Zero-account commission is about 0.05 an ounce, which
that figure already covers.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import es_strategy_research as es
from sandbox.research import usoil_families_research as oil
# Instrument-agnostic pieces, reused verbatim so gold and oil cannot drift:
# these read only `params` and `ctx`, never a session or contract constant.
from sandbox.research.usoil_families_research import (  # noqa: F401
    annual_detail,
    donchian_signal,
    exit_plan,
    ma_cross_signal,
    momentum_signal,
    overnight_signal,
    pdr_signal,
    random_side,
    rolling_mean_sigma,
    summarize,
    valid,
    vwap_signal,
    zscore_signal,
)


OUTPUT = os.path.join(os.path.dirname(__file__), "xauusd_families_selection.json")

SELECT_BALANCE = 10_000.0
REPORT_BALANCES = (10_000.0, 1_000.0)

SPREAD = 0.2            # dollars an ounce, charged wholly at entry
MARGIN = 0.25           # self-imposed notional cap; Exness itself allows far more
STEP = 1.0              # ounces; 0.01 lot of a 100 ounce contract

WARMUP_START = "2016-01-01"
#: `xauusd_1m` starts in 2004, and the main study used only 2016 onward. That
#: leaves 2006-2017 as a second untouched window -- twelve years containing the
#: 2011 blow-off and the 2013 crash -- which the `pretest` phase scores the
#: sealed cells against. It is independent of the 2025-2026 holdout, so the two
#: together are far stronger evidence than either alone.
PRETEST_WARMUP = "2004-01-01"
PRE_START = int(datetime(2006, 1, 1, tzinfo=timezone.utc).timestamp())
IS_START = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())
FULL_IS_YEARS = tuple(range(2018, 2025))

#: New York minutes: COMEX open through the New York close.
SESSION_OPEN_MINUTE = 8 * 60            # 08:00
SESSION_CLOSE_MINUTE = 16 * 60          # 16:00, the flatten bucket

BARS_PER_SESSION = 17                   # 30-minute buckets, 08:00 through 16:00
ATR_BARS = 2 * BARS_PER_SESSION
VOLATILITY_BARS = 20 * BARS_PER_SESSION
LONG_VOLATILITY_BARS = 100 * BARS_PER_SESSION
ANNUAL_PERIODS = 252.0 * BARS_PER_SESSION

#: Gold's realised volatility has run 12-25% for most of this sample and higher
#: since 2025, so this throttles the recent surge rather than the whole history.
VOL_TARGET = 0.20

MIN_POSITIVE_YEARS = 6
MIN_TRADES = 300
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 8.0
SELECTION_DD_LIMIT = 20.0
ANNUAL_DD_LIMIT = 22.0
NEIGHBOUR_DD_LIMIT = 22.0

TS, O, H, L, C, V = range(6)

TREND_PERIODS = {"ema_20d": 20 * BARS_PER_SESSION, "ema_50d": 50 * BARS_PER_SESSION}
ZSCORE_PERIODS = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 5 * BARS_PER_SESSION)
MA_FAST = (BARS_PER_SESSION, 2 * BARS_PER_SESSION)
MA_SLOW = (5 * BARS_PER_SESSION, 10 * BARS_PER_SESSION, 20 * BARS_PER_SESSION)

#: 13:00 and 14:30 New York; both leave at least two buckets before the flatten.
LAST_ENTRY = (13 * 60, 14 * 60 + 30)


# --------------------------------------------------------------------------- #
# bars and context
# --------------------------------------------------------------------------- #


def all_bars_30m(phase):
    selecting = phase.startswith("select")
    start = PRETEST_WARMUP if phase == "pretest" else WARMUP_START
    # The selector's SQL cannot return a holdout row; the pretest window ends
    # where selection begins, so it cannot see one either.
    upper = "AND timestamp < '2025-01-01'" if selecting else ""
    if phase == "pretest":
        upper = f"AND timestamp < '{WARMUP_START}'"
    sql = (
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        "last(close),sum(volume) FROM xauusd_1m "
        f"WHERE timestamp >= '{start}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    key = f"{sql}:{data._table_fingerprint(['xauusd_1m'])}"

    def build():
        return [[int(row[0]) // 1_000_000, *(float(value) for value in row[1:])]
                for row in data.query(sql)]

    scope = "pre" if phase == "pretest" else ("is" if selecting else "full")
    return [tuple(row) for row in data._cached(f"xauusd_30m_{scope}", key, build)]


def in_session(ts):
    return SESSION_OPEN_MINUTE <= ts % 86_400 // 60 <= SESSION_CLOSE_MINUTE


def trailing_annual_volatility(bars, periods):
    returns = [0.0]
    for previous, current in zip(bars, bars[1:]):
        returns.append(math.log(current[C] / previous[C]))
    out = [None] * len(bars)
    total = total_sq = 0.0
    for index, value in enumerate(returns):
        total += value
        total_sq += value * value
        if index >= periods:
            old = returns[index - periods]
            total -= old
            total_sq -= old * old
        if index >= periods - 1:
            mean = total / periods
            out[index] = math.sqrt(
                max(0.0, total_sq / periods - mean * mean) * ANNUAL_PERIODS)
    return out


def session_anchors(full):
    outside = {}
    pit_close = {}
    for bar in full:
        minute = bar[TS] % 86_400 // 60
        day = bar[TS] // 86_400
        if minute >= SESSION_CLOSE_MINUTE:
            day += 1
        if minute < SESSION_OPEN_MINUTE or minute >= SESSION_CLOSE_MINUTE:
            entry = outside.get(day)
            if entry is None:
                outside[day] = [bar[H], bar[L]]
            else:
                entry[0] = max(entry[0], bar[H])
                entry[1] = min(entry[1], bar[L])
        else:
            pit_close[bar[TS] // 86_400] = bar[C]
    ordered = sorted(pit_close)
    prior = {day: pit_close[ordered[index - 1]]
             for index, day in enumerate(ordered) if index}
    return {day: tuple(value) for day, value in outside.items()}, prior


def prior_session_range(bars):
    per_day = {}
    for bar in bars:
        day = bar[TS] // 86_400
        entry = per_day.get(day)
        if entry is None:
            per_day[day] = [bar[H], bar[L]]
        else:
            entry[0] = max(entry[0], bar[H])
            entry[1] = min(entry[1], bar[L])
    ordered = sorted(per_day)
    return {day: tuple(per_day[ordered[index - 1]])
            for index, day in enumerate(ordered) if index}


def session_vwap(bars):
    out = [None] * len(bars)
    day = None
    notional = volume = 0.0
    for index, bar in enumerate(bars):
        current = bar[TS] // 86_400
        if current != day:
            day, notional, volume = current, 0.0, 0.0
        notional += (bar[H] + bar[L] + bar[C]) / 3.0 * bar[V]
        volume += bar[V]
        out[index] = notional / volume if volume > 0 else None
    return out


def context(phase):
    full = all_bars_30m(phase)
    bars = [bar for bar in full if in_session(bar[TS])]
    closes = [bar[C] for bar in bars]
    outside, prior = session_anchors(full)
    channels = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION)
    means, sigmas = {}, {}
    for period in ZSCORE_PERIODS:
        means[period], sigmas[period] = rolling_mean_sigma(closes, period)
    short = trailing_annual_volatility(bars, VOLATILITY_BARS)
    long = trailing_annual_volatility(bars, LONG_VOLATILITY_BARS)
    return bars, {
        "atr": es.average_true_range(bars, periods=ATR_BARS),
        "ema": {period: es.ema(closes, period)
                for period in set(TREND_PERIODS.values())},
        "fast": {p: es.ema(closes, p) for p in MA_FAST},
        "slow": {p: es.ema(closes, p) for p in MA_SLOW},
        "high": {p: es.rolling_extreme([b[H] for b in bars], p, True) for p in channels},
        "low": {p: es.rolling_extreme([b[L] for b in bars], p, False) for p in channels},
        "mean": means,
        "sigma": sigmas,
        "vwap": session_vwap(bars),
        "overnight": outside,
        "prior_close": prior,
        "prior_range": prior_session_range(bars),
        "volatility": short,
        "calm": [None if s is None or l is None or l <= 0.0 else s < l
                 for s, l in zip(short, long)],
    }


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #

EXIT_MODES = oil.EXIT_MODES
COMMON = {
    "exit_mode": EXIT_MODES,
    "stop_atr": (1.0, 1.5, 2.5, 3.5, 5.0),
    "trend": ("none", "ema_20d", "ema_50d"),
    "vol_mode": ("none", "calm", "active"),
}

AXES = {
    "orb": {"direction": ("breakout", "fade"), "range_bars": (1, 2),
            "breakout_atr": (0.0, 0.25), "last_entry_minute": LAST_ENTRY, **COMMON},
    "overnight": {"direction": ("breakout", "fade"), "buffer_atr": (0.0, 0.25),
                  "last_entry_minute": LAST_ENTRY, **COMMON},
    "pdr": {"direction": ("breakout", "fade"), "buffer_atr": (0.0, 0.25),
            "last_entry_minute": LAST_ENTRY, **COMMON},
    "donchian": {"channel": (BARS_PER_SESSION, 2 * BARS_PER_SESSION,
                             4 * BARS_PER_SESSION),
                 "last_entry_minute": LAST_ENTRY, **COMMON},
    "ma_cross": {"fast": MA_FAST, "slow": MA_SLOW,
                 "last_entry_minute": LAST_ENTRY, **COMMON},
    "momentum": {"direction": ("breakout", "fade"),
                 "signal_minute": (9 * 60, 10 * 60, 13 * 60),
                 "lookback": (4, 17, 34), "threshold_atr": (0.5, 1.0, 1.5), **COMMON},
    "gap": {"direction": ("fade", "follow"), "threshold_atr": (0.25, 0.5, 1.0),
            **COMMON},
    "vwap": {"direction": ("fade", "follow"), "threshold_atr": (0.5, 1.0, 1.5),
             "last_entry_minute": LAST_ENTRY, **COMMON},
    "zscore": {"direction": ("fade", "follow"), "period": ZSCORE_PERIODS,
               "threshold_z": (1.5, 2.0, 2.5), "last_entry_minute": LAST_ENTRY,
               **COMMON},
}

CATEGORICAL = oil.CATEGORICAL


def candidates(axes):
    cells = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    return [cell for cell in cells if valid(cell)]


def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    reference = ctx["ema"][TREND_PERIODS[mode]][index]
    return price > reference if side == 1 else price < reference


def accepts_vol(ctx, index, mode):
    if mode == "none":
        return True
    if mode == "calm":
        return ctx["calm"][index] is True
    return ctx["calm"][index] is False


# --------------------------------------------------------------------------- #
# signals
#
# Seven of the nine are imported unchanged from the oil module because they read
# only `params` and `ctx`. Only the two that reference the session open are
# redefined here, against gold's 08:00 rather than oil's 09:00.
# --------------------------------------------------------------------------- #


def orb_signal(index, bars, ctx, params, state):
    bar = bars[index]
    day = bar[TS] // 86_400
    minute = bar[TS] % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update({"day": day, "high": None, "low": None})
    end = SESSION_OPEN_MINUTE + 30 * params["range_bars"]
    if minute < end:
        state["high"] = bar[H] if state["high"] is None else max(state["high"], bar[H])
        state["low"] = bar[L] if state["low"] is None else min(state["low"], bar[L])
        return None
    if minute > params["last_entry_minute"] or state["high"] is None:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    upper = state["high"] + params["breakout_atr"] * atr
    lower = state["low"] - params["breakout_atr"] * atr
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def gap_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 != SESSION_OPEN_MINUTE:
        return None
    previous = ctx["prior_close"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if previous is None or atr is None or atr <= 0.0:
        return None
    drift = (bar[O] - previous) / atr
    threshold = params["threshold_atr"]
    side = -1 if drift > threshold else 1 if drift < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


SIGNALS = {
    "orb": orb_signal, "overnight": overnight_signal, "pdr": pdr_signal,
    "donchian": donchian_signal, "ma_cross": ma_cross_signal,
    "momentum": momentum_signal, "gap": gap_signal, "vwap": vwap_signal,
    "zscore": zscore_signal,
}

SIZE_MODES = oil.SIZE_MODES
DEFAULT_SIZE = oil.DEFAULT_SIZE


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def quantity(mode, equity, price, stop_distance, realized, margin=MARGIN):
    """Ounces to trade under `mode`, floored to the 1-ounce lot step.

    `margin` is the notional cap as a fraction. The repo default of 0.25 is a
    *self-imposed* 4x cap inherited from the `idk` environment, not a broker
    limit: Exness offers up to 1:2000 on XAUUSD (margin 0.0005). Which of the
    two legs actually binds is an empirical question, so it is a parameter.
    """
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    ceiling = equity / margin / price
    if mode == "flat":
        return STEP if ceiling >= STEP else 0.0
    family, level = mode.split("_")
    if family == "notional":
        raw = equity * float(level.rstrip("x")) / price
    else:
        fraction = float(level.rstrip("pct")) / 100.0
        if family == "riskvol" and realized and realized > 0.0:
            fraction *= min(1.0, VOL_TARGET / realized)
        raw = equity * fraction / stop_distance
    return max(0.0, math.floor(min(raw, ceiling) / STEP) * STEP)


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
             initial=SELECT_BALANCE, spread=SPREAD, size_mode=DEFAULT_SIZE,
             null_seed=None, margin=MARGIN):
    """Signals to sized trades over `[lo, hi)`. Mirrors the oil engine exactly."""
    equity = peak = initial
    maximum_drawdown = 0.0
    trades = []
    position = pending = None
    traded_day = None
    state = {}
    signal_fn = SIGNALS[family]

    for index, bar in enumerate(bars):
        ts = bar[TS]
        if ts < lo:
            continue
        if ts >= hi:
            break
        day = ts // 86_400
        minute = ts % 86_400 // 60

        if position is not None:
            side = position["side"]
            price = reason = None
            if minute >= SESSION_CLOSE_MINUTE:
                price, reason = bar[O], "session"
            else:
                stop = position["stop"]
                if (side == 1 and bar[L] <= stop) or (side == -1 and bar[H] >= stop):
                    price = min(bar[O], stop) if side == 1 else max(bar[O], stop)
                    reason = "stop"
                elif position["target"] is not None:
                    target = position["target"]
                    if (side == 1 and bar[H] >= target) or (side == -1 and bar[L] <= target):
                        price = max(bar[O], target) if side == 1 else min(bar[O], target)
                        reason = "target"
                if (price is None and position["max_bars"] is not None
                        and index - position["index"] >= position["max_bars"]):
                    price, reason = bar[O], "time"
            if price is None:
                if position["trail"] is not None and ctx["atr"][index]:
                    atr = ctx["atr"][index]
                    if side == 1:
                        position["best"] = max(position["best"], bar[C])
                        position["stop"] = max(
                            position["stop"], position["best"] - position["trail"] * atr)
                    else:
                        position["best"] = min(position["best"], bar[C])
                        position["stop"] = min(
                            position["stop"], position["best"] + position["trail"] * atr)
            else:
                gross = side * (price - position["entry"])
                points = gross - spread
                pnl = points * position["quantity"]
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": position["ts"], "exit_ts": ts, "side": side,
                               "points": points, "gross": gross, "pnl": pnl,
                               "atr": position["atr"], "entry": position["entry"],
                               "quantity": position["quantity"], "reason": reason})
                position = None

        if position is None and pending is not None:
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                amount = quantity(size_mode, equity, bar[O], pending["distance"],
                                  pending["realized"], margin)
                if amount >= STEP:
                    side, entry = pending["side"], bar[O]
                    target, max_bars, trail = exit_plan(params["exit_mode"],
                                                        pending["distance"])
                    position = {
                        "side": side, "entry": entry, "ts": ts, "index": index,
                        "quantity": amount, "atr": pending["atr"],
                        "stop": entry - side * pending["distance"],
                        "target": None if target is None else entry + side * target,
                        "max_bars": max_bars, "trail": trail, "best": entry,
                    }
                    traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE
                and accepts_vol(ctx, index, params["vol_mode"])):
            side = signal_fn(index, bars, ctx, params, state)
            if side is not None and null_seed is not None:
                side = random_side(bars[index][TS], null_seed)
            atr, realized = ctx["atr"][index], ctx["volatility"][index]
            if (side is not None and atr is not None and realized is not None
                    and accepts_trend(bar[C], ctx, index, side, params["trend"])):
                pending = {"side": side, "day": day, "atr": atr,
                           "distance": params["stop_atr"] * atr,
                           "realized": realized}

    result = summarize(trades, maximum_drawdown, equity, initial)
    result["annual"] = annual_detail(trades, initial)
    gross = [trade["gross"] for trade in trades]
    net = [trade["points"] for trade in trades]
    if len(net) > 1:
        deviation = statistics.stdev(gross)
        result["gross_points_per_trade"] = round(statistics.fmean(gross), 4)
        result["net_points_per_trade"] = round(statistics.fmean(net), 4)
        result["breakeven_spread"] = round(statistics.fmean(gross), 4)
        result["gross_t_stat"] = (
            round(statistics.fmean(gross) / (deviation / math.sqrt(len(gross))), 2)
            if deviation else 0.0)

        # Dollars an ounce are NOT comparable across this sample. The mean
        # 30-minute session bar ran 2.09 in 2018 and 19.84 in 2026, so a raw
        # gross figure is mostly a statement about how big the bars were. Both
        # normalisations below are volatility- and price-neutral, and they are
        # what any in-sample versus holdout comparison must use.
        #
        #   *_atr  gross in units of the ATR the trade was sized against
        #   *_bp   gross in basis points of the entry price
        #
        # The spread has to be normalised the same way to stay comparable,
        # which `spread_atr` and `spread_bp` do.
        in_atr = [t["gross"] / t["atr"] for t in trades if t["atr"]]
        in_bp = [1e4 * t["gross"] / t["entry"] for t in trades if t["entry"]]
        if len(in_atr) > 1:
            atr_sd = statistics.stdev(in_atr)
            result["gross_atr_per_trade"] = round(statistics.fmean(in_atr), 4)
            result["gross_atr_t_stat"] = (
                round(statistics.fmean(in_atr) / (atr_sd / math.sqrt(len(in_atr))), 2)
                if atr_sd else 0.0)
            result["spread_atr"] = round(
                statistics.fmean([spread / t["atr"] for t in trades if t["atr"]]), 4)
        if len(in_bp) > 1:
            result["gross_bp_per_trade"] = round(statistics.fmean(in_bp), 3)
            result["spread_bp"] = round(statistics.fmean(
                [1e4 * spread / t["entry"] for t in trades if t["entry"]]), 3)
    return result


# --------------------------------------------------------------------------- #
# benchmarks -- mandatory reading for this instrument
# --------------------------------------------------------------------------- #


def benchmarks(bars, lo, hi, initial=SELECT_BALANCE, spread=SPREAD):
    """What you get from gold without any strategy at all.

    Gold rose +114% across the holdout, so an intraday rule that ends the window
    up is not thereby good -- it has to beat *these*. Two controls:

      buy_and_hold    the price change over the window, no trading
      always_long     buy every session open, flatten every session close, sized
                      by the same rule and paying the same spread. This is the
                      one that matters: it isolates how much of a long-biased
                      intraday result is simply gold's drift showing up inside
                      the session.
    """
    window = [bar for bar in bars if lo <= bar[TS] < hi]
    if len(window) < 2:
        return {}
    hold = 100.0 * (window[-1][C] - window[0][O]) / window[0][O]

    equity = peak = initial
    drawdown = 0.0
    trades = 0
    gross_points = []
    day = None
    entry = None
    for index, bar in enumerate(window):
        current = bar[TS] // 86_400
        minute = bar[TS] % 86_400 // 60
        if entry is not None and (current != day or minute >= SESSION_CLOSE_MINUTE):
            gross = bar[O] - entry[0]
            equity += (gross - spread) * entry[1]
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 1.0)
            gross_points.append(gross)
            trades += 1
            entry = None
        if entry is None and minute < SESSION_CLOSE_MINUTE and (
                index + 1 < len(window)):
            # One ounce a session: an unlevered, un-compounded drift control.
            if equity / MARGIN / bar[O] >= STEP:
                entry, day = (bar[O], STEP), current
    return {
        "buy_and_hold_pct": round(hold, 2),
        "always_long_return_pct": round(100.0 * (equity - initial) / initial, 2),
        "always_long_max_dd_pct": round(100.0 * drawdown, 2),
        "always_long_trades": trades,
        "always_long_gross_points_per_trade": (
            round(statistics.fmean(gross_points), 4) if gross_points else 0.0),
    }


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def passes(family, stat, limit=SELECTION_DD_LIMIT, annual_limit=ANNUAL_DD_LIMIT):
    annual = stat["annual"]
    positive = sum(1 for year in FULL_IS_YEARS
                   if annual.get(str(year), {}).get("pnl", 0.0) > 0.0)
    worst = max((annual.get(str(year), {}).get("max_dd_pct", 100.0)
                 for year in FULL_IS_YEARS), default=100.0)
    return (stat["trades"] >= MIN_TRADES
            and stat["pf"] >= MIN_PROFIT_FACTOR
            and stat["max_dd_pct"] <= limit
            and positive >= MIN_POSITIVE_YEARS
            and worst <= annual_limit)


def quality(family, stat):
    if not passes(family, stat):
        return -math.inf
    returns = [stat["annual"].get(str(year), {}).get("return_pct", 0.0)
               for year in FULL_IS_YEARS]
    return (100.0 * math.log(stat["final"] / SELECT_BALANCE)
            + min(returns) + 0.25 * statistics.median(returns)
            - 0.5 * statistics.pstdev(returns))


def neighbours(params, axes):
    out = []
    for axis, values in axes.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                variant = {**params, axis: values[other]}
                if valid(variant):
                    out.append(variant)
    return out


def select_family(family, results, quiet=False, micro=False):
    axes = AXES[family]
    universe = candidates(axes)
    ranked = []
    for params in universe:
        stat = results[es.frozen(params)]
        own = quality(family, stat)
        if not math.isfinite(own):
            continue
        if not SELECTION_DD_FLOOR <= stat["max_dd_pct"] <= SELECTION_DD_LIMIT:
            continue
        # Ranking still uses the smooth SELECT_BALANCE run; the small account
        # only decides *eligibility*. Ranking on the 1,000 run itself would rank
        # lot-rounding artefacts -- [[lot-granularity-fakes-low-drawdown]].
        if micro and stat.get("fill_rate_micro", 0.0) < MIN_FILL_RATE:
            continue
        bare = results.get(es.frozen({**params, "vol_mode": "none", "trend": "none"}))
        if bare is None or bare["pnl"] <= 0.0 or bare["pf"] < 1.0:
            continue
        nearby = [results[es.frozen(item)] for item in neighbours(params, axes)]
        robust = [item for item in nearby
                  if passes(family, item, limit=NEIGHBOUR_DD_LIMIT)]
        if not nearby or len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(family, item) for item in robust if passes(family, item)]
        if not strict:
            continue
        ranked.append((own, params, stat, statistics.median(strict),
                       len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    if quiet:
        if not ranked:
            return None
        score, params, stat, plateau, robust, total = ranked[0]
        return {"params": params, "in_sample": stat, "score": round(score, 6),
                "plateau_score": round(plateau, 6),
                "robust_neighbours": f"{robust}/{total}"}

    print(f"\nTop {family} cells (2018-2024 only, {SELECT_BALANCE:,.0f} account):")
    for score, params, stat, plateau, robust, total in ranked[:5]:
        print(json.dumps({"score": round(score, 3), "plateau": round(plateau, 3),
                          "robust_neighbours": f"{robust}/{total}",
                          "params": params,
                          "stats": {k: v for k, v in stat.items() if k != "annual"}},
                         sort_keys=True))
    if not ranked:
        print(f"  no {family} cell cleared the gates; closest by profitable years:")
        closest = sorted(
            ((sum(stat["annual"].get(str(y), {}).get("pnl", 0.0) > 0.0
                  for y in FULL_IS_YEARS), stat["pf"], stat["trades"], params, stat)
             for params in universe for stat in (results[es.frozen(params)],)),
            key=lambda item: item[:3], reverse=True)
        for positive, _pf, _n, params, stat in closest[:3]:
            print(json.dumps({"positive_years": positive, "params": params,
                              "stats": {k: v for k, v in stat.items()
                                        if k != "annual"}}, sort_keys=True))
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "plateau_score": round(plateau, 6),
            "robust_neighbours": f"{robust}/{total}"}


# --------------------------------------------------------------------------- #
# parallel evaluation
# --------------------------------------------------------------------------- #

_WORKER = {}


def _init_worker(phase):
    _WORKER["bars"], _WORKER["ctx"] = context(phase)


#: A cell must fill at least this share of its own signals on MICRO_BALANCE,
#: measured **in sample**, to be eligible when `--micro` is set. Deciding
#: fillability on 2018-2024 keeps the account-size constraint out of the
#: holdout; reading a workable stop width off 2025-2026 would be selection on
#: the test set, which is the error this whole study exists to detect.
MICRO_BALANCE = 1_000.0
MIN_FILL_RATE = 80.0


def _evaluate_cell(job):
    family, params, spread, null_seed, micro = job
    stat = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                    spread=spread, null_seed=null_seed)
    if micro:
        # Exness leverage, so the margin leg cannot bind and the measurement is
        # purely about the risk budget against the 1-ounce step.
        small = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                         initial=MICRO_BALANCE, spread=spread,
                         null_seed=null_seed, margin=0.0005)
        stat["fill_rate_micro"] = (
            round(100.0 * small["trades"] / stat["trades"], 1)
            if stat["trades"] else 0.0)
        stat["micro_return_pct"] = small["return_pct"]
        stat["micro_max_dd_pct"] = small["max_dd_pct"]
        stat["micro_trades"] = small["trades"]
    return es.frozen(params), stat


def evaluate_all(family, phase, pool, spread, null_seed=None, micro=False):
    universe = candidates(AXES[family])
    jobs = [(family, params, spread, null_seed, micro) for params in universe]
    results = {}
    for number, (key, stat) in enumerate(
            pool.imap_unordered(_evaluate_cell, jobs, chunksize=16), 1):
        results[key] = stat
        if number % 5000 == 0:
            print(f"  {family}: evaluated {number}/{len(universe)}", flush=True)
    return results


def _edge_cell(job):
    family, params = job
    inside = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                      lo=IS_START, hi=IS_END, spread=0.0)
    outside = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                       lo=IS_END, hi=OOS_END, spread=0.0)
    return family, params, inside, outside


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #


def output_path(spread, micro=False):
    path = OUTPUT
    if micro:
        path = path.replace(".json", "_micro.json")
    if spread != SPREAD:
        path = path.replace(".json", f"_spread{spread:.2f}.json")
    return path


def seal(payload, path):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def edge_scan(phase, workers):
    print(f"\nCOST-FREE EDGE SCAN (spread 0.00, {SELECT_BALANCE:,.0f} account)")
    print(f"gross = mean dollars an ounce a trade before the spread; the real "
          f"spread is {SPREAD}.\n")
    print("Ranked on gross/ATR, NOT on dollars: gold's bar size grew ~9x across")
    print("this sample, so a dollar figure mostly measures the era, not the edge.")
    header = (f"{'family':<10} {'cells':>6} {'IS gross/ATR':>13} {'t':>6} {'n':>6} "
              f"{'median':>8} {'>spread':>8} {'OOS gross/ATR':>14} {'IS $':>7} "
              f"{'OOS $':>8}")
    print(header)
    print("-" * len(header))
    summary = {}
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        for family in AXES:
            jobs = [(family, params) for params in candidates(AXES[family])]
            rows = []
            for _f, params, inside, outside in pool.imap_unordered(
                    _edge_cell, jobs, chunksize=16):
                if inside["trades"] < MIN_TRADES:
                    continue
                rows.append((inside.get("gross_atr_per_trade", 0.0),
                             inside.get("gross_atr_t_stat", 0.0), inside["trades"],
                             outside.get("gross_atr_per_trade", 0.0),
                             inside.get("spread_atr", 0.0),
                             inside.get("gross_points_per_trade", 0.0),
                             outside.get("gross_points_per_trade", 0.0), params))
            if not rows:
                print(f"{family:<10} {len(jobs):>6}   (no cell reached the trade floor)")
                summary[family] = None
                continue
            rows.sort(key=lambda row: row[0], reverse=True)
            best = rows[0]
            median = statistics.median(row[0] for row in rows)
            above = sum(1 for row in rows if row[0] > row[4])
            print(f"{family:<10} {len(rows):>6} {best[0]:>13.4f} {best[1]:>6.2f} "
                  f"{best[2]:>6} {median:>8.4f} {above:>8} {best[3]:>14.4f} "
                  f"{best[5]:>7.3f} {best[6]:>8.3f}")
            summary[family] = {
                "cells_scored": len(rows),
                "best_gross_atr_per_trade": best[0],
                "best_gross_atr_t_stat": best[1], "best_trades": best[2],
                "best_oos_gross_atr_per_trade": best[3],
                "spread_in_atr_units": best[4],
                "best_gross_points_per_trade": best[5],
                "best_oos_gross_points_per_trade": best[6],
                "median_gross_atr_per_trade": round(median, 4),
                "cells_above_spread": above, "best_params": best[7],
            }
    bars, _ctx = context(phase)
    control = {"in_sample": benchmarks(bars, IS_START, IS_END),
               "out_of_sample": benchmarks(bars, IS_END, OOS_END)}
    print("\nBENCHMARKS (what gold gives you with no strategy):")
    print(json.dumps(control, indent=2, sort_keys=True))
    path = os.path.join(os.path.dirname(__file__), "xauusd_edge_scan.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"spread_for_comparison": SPREAD, "benchmarks": control,
                   "families": summary}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {path}")


def select(phase, workers, spread=SPREAD, micro=False):
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        families = {family: select_family(
            family, evaluate_all(family, phase, pool, spread, micro=micro),
            micro=micro) for family in AXES}
    bars, _ctx = context(phase)
    payload = {
        "sealed": True,
        "protocol": {
            "bars": "xauusd_1m causally aggregated to 30m; out-of-session buckets "
                    "used only for the overnight and gap anchors",
            "clock": "xauusd_1m is New York wall-clock, established from the empty "
                     "17:00 maintenance hour and the 08:00-11:00 COMEX volume peak",
            "session": "08:00-16:00 New York; entries inside it only, forced "
                       "flatten at the close",
            "in_sample": "2018-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-08-06, untouched by selection",
            "candidate_counts": {f: len(candidates(AXES[f])) for f in AXES},
            "selection_gate": (
                f">={MIN_POSITIVE_YEARS} of {len(FULL_IS_YEARS)} in-sample years "
                f"profitable; selected DD {SELECTION_DD_FLOOR}-{SELECTION_DD_LIMIT}%; "
                f"annual DD <={ANNUAL_DD_LIMIT}%; >={MIN_TRADES} trades; PF "
                f">={MIN_PROFIT_FACTOR}; regime filters must refine an already "
                "profitable unfiltered cell; >=60% of numeric neighbours robust"),
            "sizing": f"{DEFAULT_SIZE}, floored to the {STEP:.0f}-ounce lot step "
                      f"inside {MARGIN:.0%} margin; swept separately, never ranked on",
            "select_balance": SELECT_BALANCE,
            "report_balances": list(REPORT_BALANCES),
            "contract": "Exness XAUUSD: 100 troy ounces a lot, 0.01 lot minimum, "
                        "so quantity is in ounces and the step is 1 ounce",
            "entry_spread": spread,
            "benchmark_warning": (
                "gold rose +114% across the holdout; read every family result "
                "against the buy_and_hold and always_long controls below"),
            "benchmarks": {"in_sample": benchmarks(bars, IS_START, IS_END),
                           "out_of_sample": benchmarks(bars, IS_END, OOS_END)},
        },
        "families": families,
    }
    if micro:
        payload["protocol"]["micro_constraint"] = (
            f"cells must fill >={MIN_FILL_RATE}% of their own signals on a "
            f"${MICRO_BALANCE:,.0f} account at Exness 1:2000 leverage, measured "
            "on 2018-2024 ONLY; ranking still uses the smooth "
            f"${SELECT_BALANCE:,.0f} run")
    path = output_path(spread, micro)
    seal(payload, path)
    print(f"\nSEALED XAUUSD family selections (spread {spread}"
          f"{', micro' if micro else ''}) to {path}")


def validate(phase, workers, spread=SPREAD, micro=False):
    del workers
    bars, ctx = context(phase)
    path = output_path(spread, micro)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("XAUUSD selection seal mismatch; rerun select first")

    control = benchmarks(bars, IS_END, OOS_END)
    print("\nHOLDOUT BENCHMARKS -- every family below must beat these:")
    print(json.dumps(control, indent=2, sort_keys=True))

    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        entry = {}
        balances = ((MICRO_BALANCE,) + REPORT_BALANCES) if micro else REPORT_BALANCES
        for balance in balances:
            # Micro runs use Exness leverage; the margin leg is not the
            # constraint on this instrument (see `microaccount`).
            stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                            initial=balance, spread=spread,
                            margin=0.0005 if micro else MARGIN)
            entry[f"oos_{balance:.0f}"] = stat
            print(f"\n{family} 2025-2026 OUT OF SAMPLE @ {balance:,.0f}:")
            print(json.dumps({k: v for k, v in stat.items() if k != "annual"},
                             sort_keys=True))
            for year, detail in sorted(stat["annual"].items()):
                print(f"  {year}: {detail['return_pct']:+7.2f}%  "
                      f"dd {detail['max_dd_pct']:5.2f}%  n={detail['trades']}")
        curve = {}
        for level in (0.0, 0.1, 0.2, 0.5, 1.0):
            stat = backtest(family, bars, ctx, params, lo=IS_START, hi=OOS_END,
                            initial=SELECT_BALANCE, spread=level)
            curve[f"{level:.2f}"] = {"pnl": stat["pnl"], "pf": stat["pf"]}
        entry["spread_curve_full_sample"] = curve
        print("  spread curve: " + ", ".join(f"{k}->{v['pf']}"
                                             for k, v in curve.items()))
        validation[family] = entry

    payload["seal_sha256"] = expected
    payload["validation"] = validation
    payload["holdout_benchmarks"] = control
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


BALANCE_LADDER = (1_000.0, 2_500.0, 5_000.0, 10_000.0, 25_000.0, 100_000.0)


def why(phase, workers, spread=SPREAD):
    """The two tests that decide whether a surviving gold family is real.

    1. **Coin-flip null.** Re-run the identical selection with each trade's
       direction randomised. On USOIL this produced +622% in sample at t=4.19,
       proving the search manufactures winners from noise. A family is only
       interesting if it beats what randomness scores through the same machine.
    2. **Balance ladder.** XAUUSD is 100 ounces a lot, so the step is 1 ounce --
       and at gold's 2025-2026 prices one ounce is a 4,000-5,600 dollar
       position. A 1,000 dollar account cannot round up to it for most of these
       stops, so the ladder reports where each family stops being fillable.
    """
    bars, ctx = context(phase)
    with open(output_path(spread), encoding="utf-8") as handle:
        payload = json.load(handle)
    live = [f for f, w in payload["families"].items() if w is not None]

    print("\nA. COIN-FLIP NULL — same selection, randomised trade direction")
    print(f"{'family':<10} {'run':>6} {'IS ret%':>9} {'IS g/ATR':>9} {'IS t':>6} "
          f"{'OOS ret%':>9} {'OOS g/ATR':>10}")
    null_rows = {}
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        for family in live:
            real = payload["families"][family]
            oos = payload.get("validation", {}).get(family, {}).get("oos_10000", {})
            print(f"{family:<10} {'real':>6} "
                  f"{real['in_sample']['return_pct']:>9.2f} "
                  f"{real['in_sample'].get('gross_atr_per_trade', 0):>9.4f} "
                  f"{real['in_sample'].get('gross_atr_t_stat', 0):>6.2f} "
                  f"{oos.get('return_pct', float('nan')):>9.2f} "
                  f"{oos.get('gross_atr_per_trade', 0):>10.4f}")
            for seed in (1, 2, 3):
                results = evaluate_all(family, phase, pool, spread, null_seed=seed)
                chosen = select_family(family, results, quiet=True)
                if chosen is None:
                    print(f"{family:<10} {'flip ' + str(seed):>6} "
                          f"{'no cell cleared the gates':>40}")
                    null_rows.setdefault(family, []).append(None)
                    continue
                stat = chosen["in_sample"]
                outside = backtest(family, bars, ctx, dict(chosen["params"]),
                                   lo=IS_END, hi=OOS_END, spread=spread,
                                   null_seed=seed)
                print(f"{family:<10} {'flip ' + str(seed):>6} "
                      f"{stat['return_pct']:>9.2f} "
                      f"{stat.get('gross_atr_per_trade', 0):>9.4f} "
                      f"{stat.get('gross_atr_t_stat', 0):>6.2f} "
                      f"{outside['return_pct']:>9.2f} "
                      f"{outside.get('gross_atr_per_trade', 0):>10.4f}")
                null_rows.setdefault(family, []).append(
                    {"params": chosen["params"], "in_sample": stat,
                     "out_of_sample": outside})

    print("\nB. BALANCE LADDER — can the account actually fill the signal?")
    print("   'fill rate' is trades taken against trades the rule wanted, so")
    print("   anything under 100% means the 1-ounce step is dropping signals.")
    ladder = {}
    for family in live:
        params = dict(payload["families"][family]["params"])
        wanted = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                          initial=1e9, spread=spread)["trades"]
        print(f"\n  {family}: the rule wants {wanted} trades in 2025-2026")
        print(f"  {'balance':>10} {'trades':>7} {'fill rate':>10} {'return %':>9} "
              f"{'max dd %':>9}")
        rows = {}
        for balance in BALANCE_LADDER:
            stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                            initial=balance, spread=spread)
            rate = 100.0 * stat["trades"] / wanted if wanted else 0.0
            print(f"  {balance:>10,.0f} {stat['trades']:>7} {rate:>9.0f}% "
                  f"{stat['return_pct']:>9.2f} {stat['max_dd_pct']:>9.2f}")
            rows[f"{balance:.0f}"] = {"trades": stat["trades"],
                                      "fill_rate_pct": round(rate, 1),
                                      "return_pct": stat["return_pct"],
                                      "max_dd_pct": stat["max_dd_pct"]}
        ladder[family] = {"wanted_trades": wanted, "ladder": rows}

    destination = os.path.join(os.path.dirname(__file__), "xauusd_why.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"spread": spread, "null_control": null_rows,
                   "balance_ladder": ladder}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


#: Broker leverage settings, as margin fractions.
MARGINS = {"repo cap 4x": 0.25, "1:200": 0.005, "1:2000 (Exness)": 0.0005}


def microaccount(phase, spread=SPREAD, balance=1_000.0, family="momentum"):
    """Can a $1,000 forex account actually trade the gold momentum cell?

    Two separate constraints can zero out a position, and conflating them gives
    the wrong answer:

      margin leg   equity / margin / price  -- how much notional the broker
                   lets you carry. The repo's 0.25 is a self-imposed 4x cap,
                   NOT an Exness limit; at 1:2000 this leg is irrelevant.
      risk leg     equity * risk / stop     -- how much the strategy's own risk
                   budget allows given the stop distance.

    Whichever is smaller is floored to the 1-ounce step. This reports both legs
    directly, then sweeps leverage, risk fraction and stop width to find what,
    if anything, makes the cell fillable at `balance`.
    """
    bars, ctx = context(phase)
    with open(output_path(spread), encoding="utf-8") as handle:
        payload = json.load(handle)
    base = dict(payload["families"][family]["params"])

    # What the two legs are worth at a representative holdout bar.
    window = [b for b in bars if IS_END <= b[TS] < OOS_END]
    price = statistics.fmean([b[C] for b in window])
    atrs = [ctx["atr"][i] for i, b in enumerate(bars)
            if IS_END <= b[TS] < OOS_END and ctx["atr"][i]]
    atr = statistics.fmean(atrs)
    stop = base["stop_atr"] * atr
    print(f"\nAt the mean 2025-2026 holdout bar: gold {price:,.0f}, "
          f"ATR {atr:.1f}, stop {base['stop_atr']}xATR = {stop:.1f}")
    print(f"One ounce is therefore a ${price:,.0f} position risking ${stop:.0f}.\n")
    print(f"{'leverage':<18} {'margin leg (oz)':>16} {'risk leg @1.5% (oz)':>20} "
          f"{'fills?':>8}")
    for label, margin in MARGINS.items():
        margin_leg = balance / margin / price
        risk_leg = balance * 0.015 / stop
        fills = min(margin_leg, risk_leg) >= STEP
        print(f"{label:<18} {margin_leg:>16.2f} {risk_leg:>20.2f} "
              f"{'YES' if fills else 'no':>8}")
    print("\n-> the binding leg is whichever column is smaller.")

    # Sweep the two things the trader actually controls.
    print(f"\nSWEEP at ${balance:,.0f}, Exness 1:2000 leverage "
          f"(margin cap removed), {family} cell")
    print("Each row re-runs the full 2025-2026 holdout. 'want' is what the rule")
    print("would trade on an unconstrained account.")
    header = (f"{'risk/trade':>11} {'stop_atr':>9} {'trades':>7} {'want':>6} "
              f"{'fill':>6} {'return %':>9} {'max dd %':>9} {'PF':>6}")
    print(header)
    print("-" * len(header))
    rows = []
    for stop_atr in (1.0, 1.5, 2.5, 3.5):
        params = {**base, "stop_atr": stop_atr}
        wanted = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                          initial=1e9, spread=spread,
                          margin=0.0005)["trades"]
        for risk in (1.5, 3.0, 5.0, 8.0, 12.0):
            stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                            initial=balance, spread=spread,
                            size_mode=f"riskvol_{risk}pct", margin=0.0005)
            rate = 100.0 * stat["trades"] / wanted if wanted else 0.0
            print(f"{risk:>10.1f}% {stop_atr:>9.1f} {stat['trades']:>7} "
                  f"{wanted:>6} {rate:>5.0f}% {stat['return_pct']:>9.2f} "
                  f"{stat['max_dd_pct']:>9.2f} {stat['pf']:>6.3f}")
            rows.append({"risk_pct": risk, "stop_atr": stop_atr,
                         "trades": stat["trades"], "wanted": wanted,
                         "fill_rate_pct": round(rate, 1),
                         "return_pct": stat["return_pct"],
                         "max_dd_pct": stat["max_dd_pct"], "pf": stat["pf"]})
    print("\nNOTE: stop_atr other than the sealed value is a DIFFERENT cell -- "
          "those rows\nare re-fits shown for feasibility, not holdout evidence.")

    destination = os.path.join(os.path.dirname(__file__), "xauusd_microaccount.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"balance": balance, "family": family, "sealed_params": base,
                   "mean_holdout_price": round(price, 2),
                   "mean_holdout_atr": round(atr, 3),
                   "margins": MARGINS, "sweep": rows}, handle, indent=2,
                  sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


def pretest(phase, spread=SPREAD, micro=False):
    """Score the sealed cells on 2006-2017 -- twelve years never touched.

    The main study warmed up in 2016 and selected on 2018-2024, so everything
    before 2018 is untouched by the search in exactly the way the 2025-2026
    holdout is. It is also a very different gold: the 2011 blow-off to 1,920
    and the 2013 crash both sit inside it, at prices of 500-1,800 and bar sizes
    a fraction of today's.

    This is the strongest single test available on this instrument, because it
    is independent of the holdout rather than an extension of it. A cell that
    works in 2006-2017 *and* 2025-2026, having been fitted only on 2018-2024,
    is very hard to explain as selection luck.
    """
    bars, ctx = context(phase)
    path = output_path(spread, micro)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    span = (datetime.fromtimestamp(bars[0][TS], tz=timezone.utc),
            datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc))
    print(f"\nPRETEST on {len(bars):,} bars, {span[0]:%Y-%m-%d} to "
          f"{span[1]:%Y-%m-%d} (selection never saw these)")
    print(f"benchmark: {json.dumps(benchmarks(bars, PRE_START, IS_START), sort_keys=True)}")

    out = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        stat = backtest(family, bars, ctx, params, lo=PRE_START, hi=IS_START,
                        initial=SELECT_BALANCE, spread=spread,
                        margin=0.0005 if micro else MARGIN)
        oos = payload.get("validation", {}).get(family, {}).get("oos_10000", {})
        print(f"\n{family}")
        print(f"  fitted 2018-2024 : {winner['in_sample']['return_pct']:+8.2f}%  "
              f"PF {winner['in_sample']['pf']:.3f}  "
              f"g/ATR {winner['in_sample'].get('gross_atr_per_trade', 0):.4f}")
        print(f"  holdout 2025-26  : {oos.get('return_pct', float('nan')):+8.2f}%  "
              f"PF {oos.get('pf', 0):.3f}  "
              f"g/ATR {oos.get('gross_atr_per_trade', 0):.4f}")
        print(f"  PRETEST 2006-17  : {stat['return_pct']:+8.2f}%  "
              f"PF {stat['pf']:.3f}  "
              f"g/ATR {stat.get('gross_atr_per_trade', 0):.4f}  "
              f"t {stat.get('gross_atr_t_stat', 0):.2f}  n={stat['trades']}  "
              f"dd {stat['max_dd_pct']:.1f}%")
        for year, detail in sorted(stat["annual"].items()):
            print(f"      {year}: {detail['return_pct']:+7.2f}%  "
                  f"dd {detail['max_dd_pct']:5.2f}%  n={detail['trades']}")
        out[family] = stat

    destination = os.path.join(os.path.dirname(__file__),
                               f"xauusd_pretest{'_micro' if micro else ''}.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"window": "2006-01-01 to 2018-01-01", "spread": spread,
                   "micro": micro, "families": out}, handle, indent=2,
                  sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate", "edge", "why",
                                          "micro", "pretest"))
    parser.add_argument("--micro", action="store_true",
                        help="require cells to be fillable on a $1,000 account, "
                             "decided on in-sample data only")
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--spread", type=float, default=SPREAD)
    args = parser.parse_args()
    bars, _ctx = context(args.phase)
    print(f"loaded {len(bars)} session thirty-minute XAUUSD bars "
          f"({datetime.fromtimestamp(bars[0][TS], tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc):%Y-%m-%d})")
    print(f"candidate cells: {sum(len(candidates(a)) for a in AXES.values()):,} "
          f"across {len(AXES)} families; {args.workers} workers")
    if args.phase == "select":
        select(args.phase, args.workers, args.spread, args.micro)
    elif args.phase == "edge":
        edge_scan(args.phase, args.workers)
    elif args.phase == "why":
        why(args.phase, args.workers, args.spread)
    elif args.phase == "micro":
        microaccount(args.phase, args.spread)
    elif args.phase == "pretest":
        pretest(args.phase, args.spread, args.micro)
    else:
        validate(args.phase, args.workers, args.spread, args.micro)


if __name__ == "__main__":
    main()
