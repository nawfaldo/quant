"""Drift VWAP Pullback and Maroy intraday momentum, optimised on every symbol
in QuestDB except BTC and NQ.

PROTOCOL
    in-sample       2020-01-01 .. 2024-12-31   the grid is searched here only
    out-of-sample   2025-01-01 .. 2026-08-10   each winner is scored once

Equity restarts at $1,000 on the first session of each window and sessions
before a window still warm every lookback, so neither window pays a cold-start
penalty ([[cold-start-oos-fakes-regime-edges]]).

ACCOUNT.  The repo's Forex conventions throughout: $1,000 initial balance, point
value 1.0, quantity step 0.01 lots, 25% margin (a 4x buying-power ceiling), and
0.2 price points of spread charged wholly at entry.

WHY 0.2 IS NOT ONE COST.  The request phrases the cost in pips, which is an
absolute number, and these symbols span three orders of magnitude of price:
0.2 points is 80 bp of a $25 silver trade and 0.008 bp of a 24,000 DE40 trade
([[maroy-momentum-dies-on-real-costs]]).  The literal 0.2 is honoured because it
is what was asked for, and it is the default.  `--cost-bp` re-runs the whole
search against a proportional cost instead, which is the only setting under
which the symbols are comparable to each other -- and re-searching rather than
merely re-scoring is deliberate, because a strategy should be fitted to the cost
it will actually pay.  Run both: on the cheap symbols 0.2 points is the
optimistic bound, on the low-priced ones it is ruinous.

WHAT IS SEARCHED, AND WHAT IS NOT.  Only the signal parameters are searched.
Sizing is frozen for every cell of every symbol -- 1% equity risk per stop for
Drift, a 2x notional cap for Maroy -- because a search that ranks sizing inside
a selection grid picks the cell that got lucky with leverage, not the cell with
an edge ([[usoil-intraday-fails-twice]]).

SYMBOL-RELATIVE PARAMETERS.  Drift as published is an NQ strategy with an
80-point stop and a 40-point target: absolute levels that mean nothing on
$3.36 natural gas.  Both are re-expressed as a fraction of the entry price, and
the published NQ pair (0.53% stop, 0.27% target, RR 0.5) sits inside the grid.
Maroy is already scale-free -- its noise area, ladder and volatility target are
all fractional -- so its axes transfer unchanged.

SESSIONS.  Tables hold New York wall-clock (AGENT.md).  Session bounds and the
shifted clock for the three Asian indices are taken from `index_families_research`
and `commodity_families_research`, where they were verified against each table's
own volume profile.  The Asian indices straddle NY midnight, so their bars are
loaded with a fixed hour shift that puts the session inside one shifted day;
only the IS/OOS boundaries subtract the shift back out.

THE NULL CONTROL IS THE BASELINE.  A random-direction search over these same
grids earns real money at these costs, on crypto decisively so
([[coin-flip-control-beats-real-signals]], [[crypto-null-baseline-is-strongly-positive]]).
Every symbol is therefore searched twice: once for real, once with each entry's
direction replaced by a deterministic coin flip and everything else -- timing,
fill price, exits, sizing -- held identical.  Only a real winner's margin over
its own null is evidence.  A result is reported as a candidate only if it beats
the null out of sample.

Run from the repository root:

    py -B -m sandbox.research.drift_maroy_symbol_study
    py -B -m sandbox.research.drift_maroy_symbol_study --symbols xagusd,de40
    py -B -m sandbox.research.drift_maroy_symbol_study --strategy drift
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
from datetime import date, datetime, timedelta, timezone

from sandbox import data, metrics
from sandbox.research import maroy_intraday_momentum as maroy


OUTPUT = os.path.join(os.path.dirname(__file__), "drift_maroy_symbol_study_result.json")

IS_FROM, IS_TO = "2020-01-01", "2025-01-01"
OOS_FROM, OOS_TO = "2025-01-01", "2026-08-11"

INITIAL_BALANCE = 1_000.0
ENTRY_SPREAD = 0.2
POINT_VALUE = 1.0
QUANTITY_STEP = 0.01
MARGIN = 0.25

#: Frozen for every cell.  See the module docstring: sizing is not searched.
DRIFT_RISK_FRACTION = 0.01
MAROY_LEVERAGE = 2.0

#: A cell with fewer trades than this in a five-year window is not measurable;
#: it is excluded from selection rather than allowed to win on three lucky days
#: ([[short-month-series-fakes-monthly-sharpe]]).
MIN_IS_TRADES = 60

NULL_SEED = "drift-maroy-2026"


# --------------------------------------------------------------------------- #
# universe
# --------------------------------------------------------------------------- #


def _symbol(table, session, first_full_year=2020, shift_hours=0,
            sessions_per_year=252, note=None):
    return {"table": table, "session": session, "first_full_year": first_full_year,
            "shift_hours": shift_hours, "sessions_per_year": sessions_per_year,
            "note": note}


#: Sessions are minutes in the *shifted* clock.  For everything with
#: ``shift_hours=0`` they read directly as New York time.  Add the shift back to
#: recover NY for the Asian three: AUS200 and JP225 19:00-02:00, HK50 21:00-04:00.
SYMBOLS = {
    # Index CFDs.  Europe trades local cash plus the US overlap; volume peaks at
    # the New York open on all four.
    "de40": _symbol("de40_1m", (3 * 60, 11 * 60 + 30)),
    "fr40": _symbol("fr40_1m", (3 * 60, 11 * 60 + 30)),
    "stoxx50": _symbol("stoxx50_1m", (3 * 60, 11 * 60 + 30)),
    "uk100": _symbol("uk100_1m", (3 * 60, 11 * 60 + 30)),
    "aus200": _symbol("aus200_1m", (60, 8 * 60), shift_hours=6),
    "jp225": _symbol("jp225_1m", (60, 8 * 60), shift_hours=6),
    "hk50": _symbol("hk50_1m", (3 * 60, 10 * 60), shift_hours=6),
    # Commodities and precious metals, on the pit sessions the commodity study
    # verified against these same tables.
    "xagusd": _symbol("xagusd_1m", (8 * 60, 16 * 60)),
    "ukoil": _symbol("ukoil_1m", (9 * 60, 14 * 60 + 30)),
    "xngusd": _symbol("xngusd_1m", (9 * 60, 14 * 60 + 30)),
    "xcuusd": _symbol("xcuusd_1m", (8 * 60, 14 * 60 + 30),
        note="QuestDB quote scale (2.8-6.6) differs from live MT5 (~14,000)"),
    "xpdusd": _symbol("xpdusd_1m", (8 * 60, 16 * 60), first_full_year=2022),
    "xptusd": _symbol("xptusd_1m", (8 * 60, 16 * 60), first_full_year=2022),
    # Ether: not BTC, so in scope.  Trades every calendar day, and is given the
    # same 09:30-16:00 frame the BTC studies in this directory use, because an
    # intraday session strategy needs one imposed.
    "ethusd": _symbol("ethusd_1m", (9 * 60 + 30, 16 * 60), first_full_year=2020,
        sessions_per_year=365),
}

#: Excluded on purpose.  The metal crosses live only in 30-minute tables, and
#: both strategies need a per-minute noise profile or a 5-minute execution
#: chart; the Dukascopy 1m cross archive that would supply them skips a decade
#: ([[dukascopy-metal-crosses-have-a-decade-hole]]).  XAUGBP 1m starts 2023-03,
#: too late for a 2020-2024 fit.
EXCLUDED = {
    "btc, btcjpy, btckrw, nq": "named out of scope by the request",
    "xauaud, xaueur, xaugbp, xagaud, xageur, xaggbp": "30m only, or 1m starts 2023",
    "ethbtc": "quoted in BTC, so the 0.2-point spread is 20% of price",
    "vix": "an index level, not a tradeable CFD in this account",
}


def shift_seconds(symbol):
    return SYMBOLS[symbol]["shift_hours"] * 3_600


def window_ts(symbol):
    """IS and OOS bounds in the symbol's shifted clock.

    Selection and scoring happen in shifted time because that is what the bar
    timestamps carry; the shift is added so a real-time boundary lands on the
    same real-time instant for a shifted symbol as for an unshifted one.
    """
    shift = shift_seconds(symbol)
    return (
        (metrics.split_ts(IS_FROM) + shift, metrics.split_ts(IS_TO) + shift),
        (metrics.split_ts(OOS_FROM) + shift, metrics.split_ts(OOS_TO) + shift),
    )


# --------------------------------------------------------------------------- #
# bars
# --------------------------------------------------------------------------- #


def load_minutes(symbol, warm_from="2019-01-01"):
    """One-minute OHLCV from `warm_from`, timestamps already shifted.

    A calendar year of warm-up precedes the in-sample window so every lookback,
    noise profile and volatility estimate is fully populated on the first traded
    session of 2020.
    """
    table = SYMBOLS[symbol]["table"]
    shift = shift_seconds(symbol)
    rows = data.query(
        "SELECT cast(timestamp as long) ts,open,high,low,close,volume "
        f"FROM {table} WHERE timestamp >= '{warm_from}' ORDER BY timestamp"
    )
    out = []
    for row in rows:
        ts = int(row[0]) // 1_000_000 + shift
        values = [float(v) for v in row[1:6]]
        # A zero or negative print is a feed artefact, not a price; it would
        # divide into every fractional stop below.
        if values[0] <= 0 or values[3] <= 0:
            continue
        out.append((ts, *values))
    return out


def aggregate(minutes, size, session=None):
    """Clock-aligned OHLCV bars of `size` minutes, optionally session-only."""
    seconds = size * 60
    out = []
    key = current = None
    for ts, open_, high, low, close, volume in minutes:
        if session is not None:
            minute = (ts % 86_400) // 60
            if not session[0] <= minute < session[1]:
                continue
        bucket = ts // seconds
        if bucket != key:
            if current is not None:
                out.append(tuple(current))
            key = bucket
            current = [bucket * seconds, open_, high, low, close, volume]
        else:
            current[2] = max(current[2], high)
            current[3] = min(current[3], low)
            current[4] = close
            current[5] += volume
    if current is not None:
        out.append(tuple(current))
    return out


# --------------------------------------------------------------------------- #
# strategy 1: Drift VWAP Pullback, symbol-relative
# --------------------------------------------------------------------------- #

TS, O, H, L, C, V = range(6)

#: Stop as a fraction of the entry price.  The published NQ stop of 80 points on
#: a ~15,000 index is 0.53%, so the grid brackets the paper's own answer.
DRIFT_STOP_PCT = (0.0015, 0.0025, 0.0040, 0.0055, 0.0080)
#: Target as a multiple of the stop.  NQ's 40-point target on an 80-point stop
#: is 0.5; the 40/50 long/short asymmetry is dropped as an NQ-specific artefact.
DRIFT_RR = (0.5, 0.75, 1.0, 1.5)
#: One-hour return required to call a drift.  The video specifies 0.1%.
DRIFT_MOMENTUM = (0.0005, 0.0010, 0.0020)


def drift_grid():
    return [{"stop_pct": stop, "rr": rr, "momentum": momentum}
            for stop, rr, momentum in itertools.product(
                DRIFT_STOP_PCT, DRIFT_RR, DRIFT_MOMENTUM)]


def drift_states(bars_15m, session, momentum):
    """`{decision_ts: long|short|None}` from completed 15-minute bars.

    VWAP is the cumulative 15m HLC3 by volume, anchored at the session open and
    reset daily, exactly as the video specifies.  The one-hour return compares
    with the bar four 15-minute intervals earlier and may read outside the
    session, which is what makes it a drift filter rather than a session filter.
    A state stamped T is only readable by a 5-minute bar closing at T, so no
    15-minute bar is consulted before it has closed.
    """
    opened, closed = session
    closes = {bar[TS]: bar[C] for bar in bars_15m}
    states = {}
    day = None
    price_volume = volume = 0.0
    previous_vwap = None

    for bar in bars_15m:
        minute = (bar[TS] % 86_400) // 60
        if not opened <= minute < closed:
            continue
        current_day = bar[TS] // 86_400
        if current_day != day:
            day = current_day
            price_volume = volume = 0.0
            previous_vwap = None

        typical = (bar[H] + bar[L] + bar[C]) / 3.0
        if bar[V] > 0:
            price_volume += typical * bar[V]
            volume += bar[V]
        vwap = price_volume / volume if volume > 0 else typical
        hour_ago = closes.get(bar[TS] - 3_600)
        side = None
        if previous_vwap is not None and hour_ago and hour_ago > 0:
            move = bar[C] / hour_ago - 1.0
            if bar[C] > vwap and vwap > previous_vwap and move >= momentum:
                side = "long"
            elif bar[C] < vwap and vwap < previous_vwap and move <= -momentum:
                side = "short"
        states[bar[TS] + 15 * 60] = side
        previous_vwap = vwap
    return states


def _drift_quantity(equity, entry, stop_distance):
    """Lots under a fixed 1% equity risk, capped by 4x buying power."""
    if equity <= 0 or entry <= 0 or stop_distance <= 0:
        return 0.0
    raw = min(equity * DRIFT_RISK_FRACTION / stop_distance, equity / MARGIN / entry)
    return math.floor((raw + 1e-12) / QUANTITY_STEP) * QUANTITY_STEP


def drift_backtest(bars_5m, states, session, params, window, cost_bp=0.0,
                   null_seed=None):
    """The full strategy over `window`, including the daily guardrails.

    Guardrails are the video's: at most four trades and two losses a day, no new
    entry inside the last half hour, flat five minutes before the close.
    """
    opened, closed = session
    first_entry = opened + 60
    last_entry = closed - 30
    flatten = closed - 5
    stop_pct, rr = params["stop_pct"], params["rr"]
    lo_ts, hi_ts = window

    equity = INITIAL_BALANCE
    trades = []
    by_day = {}
    for bar in bars_5m:
        by_day.setdefault(bar[TS] // 86_400, []).append(bar)

    for day in sorted(by_day):
        day_bars = by_day[day]
        if not lo_ts <= day * 86_400 < hi_ts:
            continue

        position = None
        pending = None
        current_state = previous_state = None
        pullback_active = False
        trade_count = loss_count = 0

        for bar in day_bars:
            minute = (bar[TS] % 86_400) // 60

            if minute >= flatten:
                pending = None
                if position is not None:
                    trade = _drift_exit(position, bar[O], bar[TS], "session_close",
                                        cost_bp)
                    trades.append(trade)
                    equity += trade["pnl"]
                    loss_count += trade["pnl"] < 0
                    position = None
                continue

            opened_now = False
            if (pending is not None and position is None
                    and minute <= last_entry
                    and trade_count < 4 and loss_count < 2):
                side = pending
                if null_seed is not None:
                    side = maroy.null_side(bar[TS], null_seed)
                entry = bar[O]
                stop_distance = entry * stop_pct
                quantity = _drift_quantity(equity, entry, stop_distance)
                if quantity >= QUANTITY_STEP:
                    position = {
                        "entry_ts": bar[TS], "side": side, "entry_price": entry,
                        "quantity": quantity, "stop": stop_distance,
                        "target": stop_distance * rr,
                    }
                    trade_count += 1
                    opened_now = True
                pending = None

            # An entry cannot hit its own bracket in its entry bar: the order of
            # the two touches inside that bar is unknowable from OHLC.
            if position is not None and not opened_now:
                resolved = _drift_bracket(position, bar)
                if resolved is not None:
                    price, reason = resolved
                    trade = _drift_exit(position, price, bar[TS], reason, cost_bp)
                    trades.append(trade)
                    equity += trade["pnl"]
                    loss_count += trade["pnl"] < 0
                    position = None

            decision_ts = bar[TS] + 5 * 60
            if decision_ts in states:
                current_state = states[decision_ts]
            if current_state != previous_state:
                pullback_active = False
                previous_state = current_state

            counter = ((current_state == "long" and bar[C] < bar[O])
                       or (current_state == "short" and bar[C] > bar[O]))
            first_pullback = counter and not pullback_active
            pullback_active = counter if current_state is not None else False

            if (first_pullback and first_entry <= minute + 5 <= last_entry
                    and position is None and pending is None
                    and trade_count < 4 and loss_count < 2):
                pending = current_state

        if position is not None:
            last = day_bars[-1]
            trade = _drift_exit(position, last[C], last[TS] + 300, "data_end", cost_bp)
            trades.append(trade)
            equity += trade["pnl"]

    return {"trades": trades, "final": equity}


def _drift_bracket(position, bar):
    """Conservative stop-before-target resolution, matching execution.py."""
    entry, stop, target = position["entry_price"], position["stop"], position["target"]
    if position["side"] == "long":
        stop_price, target_price = entry - stop, entry + target
        if bar[L] <= stop_price:
            return min(bar[O], stop_price), "stop"
        if bar[H] >= target_price:
            return max(bar[O], target_price), "target"
    else:
        stop_price, target_price = entry + stop, entry - target
        if bar[H] >= stop_price:
            return max(bar[O], stop_price), "stop"
        if bar[L] <= target_price:
            return min(bar[O], target_price), "target"
    return None


def _drift_exit(position, exit_price, exit_ts, reason, cost_bp):
    sign = 1.0 if position["side"] == "long" else -1.0
    entry = position["entry_price"]
    cost = entry * cost_bp / 10_000.0 if cost_bp else ENTRY_SPREAD
    net = sign * (exit_price - entry) - cost
    return {"entry_ts": position["entry_ts"], "exit_ts": exit_ts,
            "side": position["side"], "reason": reason,
            "quantity": position["quantity"], "entry_price": entry,
            "net_points": net, "return_bp": 10_000.0 * net / entry,
            "pnl": net * position["quantity"] * POINT_VALUE}


# --------------------------------------------------------------------------- #
# strategy 2: Maroy intraday momentum
# --------------------------------------------------------------------------- #

#: The paper's own axes.  Ladder exits are dropped: their step distances are a
#: second risk parameterisation whose scale is set by the account rather than by
#: the market ([[maroy-ladder-scale-is-not-leverage]]), which would smuggle the
#: sizing axis back into a search that is meant to hold sizing fixed.
MAROY_EXITS = (("time", None), ("vwap", None),
               ("boundary+vwap", "match"), ("boundary", 0.78))
MAROY_LOOKBACK = (4, 8, 14)
MAROY_K_ENTER = (0.85, 1.00, 1.30)
MAROY_FREQUENCY = (15, 30, 45)


def maroy_grid(span):
    """Parameter cells for a session `span` minutes long.

    `exit_before` is expressed as a fraction of the session so the two settings
    reproduce the paper's 12 and 30 minutes on its own 390-minute session.
    """
    out = []
    for (kind, k_exit_rule), lookback, k_enter, frequency in itertools.product(
            MAROY_EXITS, MAROY_LOOKBACK, MAROY_K_ENTER, MAROY_FREQUENCY):
        for exit_before in sorted({max(5, span // 32), max(10, span // 13)}):
            k_exit = k_enter if k_exit_rule == "match" else k_exit_rule
            out.append({
                "name": f"{kind}_lb{lookback}_k{k_enter}_f{frequency}_e{exit_before}",
                "exit_kind": kind, "family": kind,
                "lookback_days": lookback, "k_enter": k_enter, "k_exit": k_exit,
                # 1.0 makes the paper's volatility scale-down inert, so the fixed
                # leverage cap is the sole determinant of size and the search
                # cannot reach the sizing axis through this knob.
                "target_vol": 1.0,
                "start_after": 1, "frequency": frequency,
                "exit_before": exit_before,
            })
    return out


def maroy_policy(null_seed=None):
    policy = {"sizing": "leverage_cap", "leverage": MAROY_LEVERAGE}
    if null_seed is not None:
        policy["null_seed"] = null_seed
    return policy


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


def summarise(trades, window, sessions_per_year, session_days):
    """Metrics from a trade list and the daily equity it implies.

    Volatility and Sharpe are computed on the *daily* equity series rather than
    per trade, so a cell that trades twice a day is not credited with a higher
    frequency than it has.
    """
    if not trades:
        return {"trades": 0, "return_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
                "cagr_pct": 0.0, "pf": None, "win_rate_pct": 0.0,
                "edge_bp": 0.0, "msharpe": 0.0, "pos_months": 0, "n_months": 0}

    by_day = {}
    for trade in trades:
        by_day[trade["exit_ts"] // 86_400] = by_day.get(trade["exit_ts"] // 86_400, 0.0) \
            + trade["pnl"]
    equity = INITIAL_BALANCE
    curve = []
    for day in sorted(session_days):
        equity += by_day.get(day, 0.0)
        curve.append(equity)

    returns = [b / a - 1.0 if a > 0 else 0.0 for a, b in zip(curve, curve[1:])]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns) if returns else 0.0
    sharpe = mean / volatility * math.sqrt(sessions_per_year) if volatility else 0.0

    peak, drawdown = INITIAL_BALANCE, 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak > 0 else 0.0)

    years = len(returns) / sessions_per_year
    final_equity = curve[-1] if curve else INITIAL_BALANCE
    cagr = ((final_equity / INITIAL_BALANCE) ** (1 / years) - 1.0) \
        if years > 0 and final_equity > 0 else -1.0

    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    monthly = metrics.stats([(t["entry_ts"], t["pnl"]) for t in trades],
                            initial=INITIAL_BALANCE, span=window)

    return {
        "trades": len(trades),
        "final_equity": round(final_equity, 2),
        "return_pct": round(100 * (final_equity / INITIAL_BALANCE - 1.0), 2),
        "cagr_pct": round(100 * cagr, 2),
        "sharpe": round(sharpe, 3),
        "ann_vol_pct": round(100 * volatility * math.sqrt(sessions_per_year), 1),
        "max_dd_pct": round(100 * drawdown, 2),
        "win_rate_pct": round(100 * sum(1 for t in trades if t["pnl"] > 0)
                              / len(trades), 1),
        "pf": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        # Per-trade edge in basis points of the traded notional: the one number
        # that is comparable between a $3 gas trade and a 24,000 index trade.
        "edge_bp": round(statistics.fmean(t["return_bp"] for t in trades), 3),
        "msharpe": monthly["msharpe"],
        "pos_months": monthly["pos_months"],
        "n_months": monthly["n_months"],
    }


def rank_key(summary):
    """IS ranking: Sharpe, then return.

    Deliberately not a drawdown or consistency filter.  Selecting on drawdown
    guarantees the drawdown band in the reported winner, which makes the number
    evidence about the filter rather than about the strategy
    ([[selection-gate-manufactures-drawdown-and-consistency]]).
    """
    if summary["trades"] < MIN_IS_TRADES:
        return (-1.0, -1.0)
    return (summary["sharpe"], summary["return_pct"])


# --------------------------------------------------------------------------- #
# per-symbol drivers
# --------------------------------------------------------------------------- #


def _trade_return_bp(trade, entry_price):
    return 10_000.0 * trade["pnl"] / (trade["quantity"] * entry_price) \
        if trade["quantity"] and entry_price else 0.0


def run_drift(symbol, minutes, cost_bp=0.0):
    """Search the Drift grid in-sample, score the winner and its null out."""
    session = SYMBOLS[symbol]["session"]
    per_year = SYMBOLS[symbol]["sessions_per_year"]
    bars_5m = aggregate(minutes, 5, session)
    bars_15m = aggregate(minutes, 15)
    is_window, oos_window = window_ts(symbol)

    days = sorted({bar[TS] // 86_400 for bar in bars_5m})
    is_days = [d for d in days if is_window[0] <= d * 86_400 < is_window[1]]
    oos_days = [d for d in days if oos_window[0] <= d * 86_400 < oos_window[1]]
    if len(is_days) < 250 or len(oos_days) < 60:
        return {"skipped": f"{len(is_days)} IS and {len(oos_days)} OOS sessions"}

    # The trend state depends only on the momentum threshold, so it is built
    # once per distinct threshold instead of once per cell.
    states = {m: drift_states(bars_15m, session, m) for m in DRIFT_MOMENTUM}

    def evaluate(params, window, days_in_window, seed=None):
        result = drift_backtest(bars_5m, states[params["momentum"]], session,
                                params, window, cost_bp, seed)
        return summarise(result["trades"], window, per_year, days_in_window)

    out = {}
    for label, seed in (("real", None), ("null", NULL_SEED)):
        candidates = []
        for params in drift_grid():
            candidates.append({"params": params,
                               "is": evaluate(params, is_window, is_days, seed)})
        winner = max(candidates, key=lambda row: rank_key(row["is"]))
        out[label] = {
            "params": winner["params"],
            "is": winner["is"],
            "oos": evaluate(winner["params"], oos_window, oos_days, seed),
            "cells": len(candidates),
            "is_cells_positive": sum(1 for c in candidates
                                     if c["is"]["return_pct"] > 0),
        }
    out["sessions"] = {"is": len(is_days), "oos": len(oos_days)}
    return out


def run_maroy(symbol, cost_bp=0.0):
    """Search the Maroy grid in-sample, score the winner and its null out.

    Sessions are loaded through the base module so the entry rule, the noise
    profile and every exit are the audited implementation, not a copy.
    """
    session = SYMBOLS[symbol]["session"]
    per_year = SYMBOLS[symbol]["sessions_per_year"]
    span = session[1] - session[0]
    is_window, oos_window = window_ts(symbol)

    maroy.SESSIONS[symbol] = session
    maroy.SESSIONS_PER_YEAR = per_year
    maroy.COST_BPS = cost_bp
    maroy.SPREAD = ENTRY_SPREAD
    maroy.INITIAL = INITIAL_BALANCE
    maroy.MARGIN = MARGIN
    maroy.QUANTITY_STEP = QUANTITY_STEP
    sessions = _maroy_sessions(symbol)

    is_days = [s["day"] for s in sessions if is_window[0] <= s["ts"] < is_window[1]]
    oos_days = [s["day"] for s in sessions if oos_window[0] <= s["ts"] < oos_window[1]]
    if len(is_days) < 250 or len(oos_days) < 60:
        return {"skipped": f"{len(is_days)} IS and {len(oos_days)} OOS sessions"}

    def evaluate(config, window, days_in_window, seed=None):
        result = maroy.run_config(sessions, config, symbol, maroy_policy(seed), window)
        trades = [{**t, "entry_ts": t["ts"],
                   "return_bp": _maroy_return_bp(t)} for t in result["trades"]]
        return summarise(trades, window, per_year, days_in_window)

    grid = maroy_grid(span)
    out = {}
    for label, seed in (("real", None), ("null", NULL_SEED)):
        candidates = [{"config": config,
                       "is": evaluate(config, is_window, is_days, seed)}
                      for config in grid]
        winner = max(candidates, key=lambda row: rank_key(row["is"]))
        out[label] = {
            "params": _maroy_params(winner["config"]),
            "is": winner["is"],
            "oos": evaluate(winner["config"], oos_window, oos_days, seed),
            "cells": len(candidates),
            "is_cells_positive": sum(1 for c in candidates
                                     if c["is"]["return_pct"] > 0),
        }
    out["sessions"] = {"is": len(is_days), "oos": len(oos_days)}
    return out


def _maroy_return_bp(trade):
    """Per-trade return in bp of the notional actually traded.

    A ladder exit closes a position in parts, so each part is scored against its
    own notional; averaging these gives the mean return on capital committed,
    which is what compares across a $3 gas trade and a 24,000 index trade.
    """
    notional = trade["quantity"] * trade["entry"]
    return 10_000.0 * trade["pnl"] / notional if notional else 0.0


def _maroy_params(config):
    return {k: config[k] for k in
            ("exit_kind", "lookback_days", "k_enter", "k_exit", "frequency",
             "start_after", "exit_before")}


_SESSION_CACHE = {}


def _maroy_sessions(symbol):
    """`maroy.load_sessions`' output, rebuilt on this study's clock and window.

    The base loader reads a whole table and knows nothing about the hour shift
    the Asian indices need, so the fold is repeated here over the same warmed
    1m range every other part of this study uses.  The dict shape is the base
    loader's exactly, because `maroy.run_config` consumes it unchanged.
    """
    if symbol in _SESSION_CACHE:
        return _SESSION_CACHE[symbol]
    start_min, end_min = SYMBOLS[symbol]["session"]
    span = end_min - start_min
    minutes = load_minutes(symbol)
    by_day = {}
    for ts, open_, high, low, close, volume in minutes:
        minute = (ts % 86_400) // 60
        if not start_min <= minute < end_min:
            continue
        bucket = by_day.setdefault(ts // 86_400, [None] * span)
        bucket[minute - start_min] = (ts, open_, high, low, close, volume)

    sessions = []
    previous_close = None
    for day in sorted(by_day):
        bars = by_day[day]
        present = [bar for bar in bars if bar is not None]
        if len(present) < span // 2:
            continue
        sessions.append({"day": day, "ts": present[0][0], "bars": bars, "span": span,
                         "open": present[0][1], "close": present[-1][4],
                         "prev_close": previous_close})
        previous_close = present[-1][4]
    _SESSION_CACHE[symbol] = sessions
    return sessions


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def study_symbol(job):
    symbol, strategies, cost_bp = job
    report = {"symbol": symbol, "session": SYMBOLS[symbol]["session"],
              "shift_hours": SYMBOLS[symbol]["shift_hours"],
              "note": SYMBOLS[symbol]["note"]}
    minutes = None
    try:
        if "drift" in strategies:
            minutes = load_minutes(symbol)
            report["drift"] = run_drift(symbol, minutes, cost_bp)
        if "maroy" in strategies:
            report["maroy"] = run_maroy(symbol, cost_bp)
    except Exception as error:  # noqa: BLE001 - one bad table must not kill the sweep
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def _verdict(block):
    """Whether a symbol's winner beat its own coin-flip control out of sample."""
    if "skipped" in block or "real" not in block:
        return "skipped"
    if block["real"]["is"]["trades"] < MIN_IS_TRADES:
        # No cell in the grid was measurable, so `max` returned an arbitrary one.
        return "no measurable IS cell"
    real, null = block["real"]["oos"], block["null"]["oos"]
    if real["trades"] < 20:
        return "too few OOS trades"
    if real["return_pct"] <= 0:
        return "OOS negative"
    if real["return_pct"] <= null["return_pct"]:
        return "loses to null"
    return "candidate"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--strategy", default="drift,maroy")
    parser.add_argument("--cost-bp", type=float, default=0.0,
                        help="entry cost in bp of notional; 0 keeps the literal "
                             "0.2-point spread")
    parser.add_argument("--workers", type=int,
                        default=max(1, min(8, (os.cpu_count() or 4) - 1)))
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    unknown = [s for s in symbols if s not in SYMBOLS]
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    strategies = {s.strip() for s in args.strategy.split(",")}

    jobs = [(symbol, strategies, args.cost_bp) for symbol in symbols]
    if args.workers > 1 and len(jobs) > 1:
        with multiprocessing.Pool(args.workers) as pool:
            results = pool.map(study_symbol, jobs)
    else:
        results = [study_symbol(job) for job in jobs]

    report = {
        "study": "Drift VWAP Pullback and Maroy intraday momentum, non-BTC non-NQ",
        "in_sample": [IS_FROM, IS_TO], "out_of_sample": [OOS_FROM, OOS_TO],
        "account": {"initial_balance": INITIAL_BALANCE,
                    "entry_cost": (f"{args.cost_bp} bp of notional" if args.cost_bp
                                   else f"{ENTRY_SPREAD} price points at entry"),
                    "point_value": POINT_VALUE, "quantity_step": QUANTITY_STEP,
                    "margin": MARGIN},
        "frozen_sizing": {"drift": f"{DRIFT_RISK_FRACTION:.1%} equity risk per stop",
                          "maroy": f"{MAROY_LEVERAGE}x notional cap"},
        "excluded": EXCLUDED,
        "symbols": results,
    }

    for name in ("drift", "maroy"):
        if name not in strategies:
            continue
        cost = f"{args.cost_bp} bp" if args.cost_bp else f"{ENTRY_SPREAD} pt spread"
        print(f"\n{'=' * 118}")
        print(f"{name.upper()}   IS {IS_FROM}..{IS_TO}   OOS {OOS_FROM}..{OOS_TO}"
              f"   ${INITIAL_BALANCE:,.0f}   {cost}")
        print(f"{'=' * 118}")
        print(f"{'symbol':9}{'IS ret%':>9}{'IS Shp':>8}{'IS n':>7}"
              f"{'OOS ret%':>10}{'OOS Shp':>9}{'OOS DD%':>9}{'OOS n':>7}"
              f"{'edge bp':>9}{'null ret%':>11}  verdict")
        print("-" * 118)
        for row in results:
            block = row.get(name, {})
            if "error" in row or "skipped" in block or "real" not in block:
                reason = row.get("error") or block.get("skipped", "no result")
                print(f"{row['symbol']:9}{reason}")
                continue
            real, null = block["real"], block["null"]
            print(f"{row['symbol']:9}"
                  f"{real['is']['return_pct']:>9.1f}{real['is']['sharpe']:>8.2f}"
                  f"{real['is']['trades']:>7}"
                  f"{real['oos']['return_pct']:>10.1f}{real['oos']['sharpe']:>9.2f}"
                  f"{real['oos']['max_dd_pct']:>9.1f}{real['oos']['trades']:>7}"
                  f"{real['oos']['edge_bp']:>9.2f}"
                  f"{null['oos']['return_pct']:>11.1f}  {_verdict(block)}")
            block["verdict"] = _verdict(block)

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
