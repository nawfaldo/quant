"""Intraday family study for the commodity and energy tables in QuestDB.

The protocol is deliberately separate from ``crypto_families_research``.  It
uses exchange/broker-specific sessions, 252 trading days a year, an initial
balance of USD 1,000, a cost expressed in instrument pips, and MT5 contract
multipliers.  Selection is 2020-2024 (or the available complete years for
newer tables); 2025 through 2026-08-06 is sealed holdout data.

Quantity is always MT5 lots.  P&L is therefore::

    price movement * lots * value_per_price_unit_per_lot

The multiplier is tick_value / tick_size, cross-checked against contract size
for these linear USD-profit instruments.  The metadata below was read from the
running Exness MT5 terminal on 2026-08-09.  XCUUSD is intentionally flagged:
QuestDB is in the historical 2.8-6.6 quote convention while the live terminal
is near 14,000.  Its research is useful, but it must not be deployed until the
symbol conversion is reconciled.

UKOIL AND THE METAL CROSSES (2026-08-10).

UKOIL is Dukascopy ``E_Brent``, 1m from 2011, and runs the full 2020-2024
protocol.  Exness also quotes UKOIL but only back to 2022-03, so the Dukascopy
table wins on depth.

The six metal crosses are native M30 from the Exness terminal.  Dukascopy was
tried first and abandoned: its cross archive holds 2012-2014 and then nothing
until 2024, so the selection window was empty.  HistData lists the gold crosses
but serves no files for them -- their download pages carry an empty token while
XAUUSD and XAGUSD carry a real one -- so no free vendor covers the gap.  Exness
is the only source with continuous history, and being the venue that would fill
the orders it is also the right one.

``first_full_year`` is 2022 because Exness M30 thins out below that: roughly one
bar a day through 2021-02, half-days through June, and full 24/5 coverage only
from 2021-07.  Selection therefore gets 2022-2024 and the 2025 to 2026-08
holdout is untouched.

Two things to keep in mind when reading cross results.  A lot's USD value
depends on the FX leg, so ``multiplier`` is a snapshot rather than a constant;
it cancels between risk sizing and P&L and survives only in lot rounding and the
margin ceiling.  And the terminal reports 25-28 point spreads on the gold
crosses, which is 2.5-2.8 pips, more than ten times the 0.2 the protocol
charges -- read the cost sweep, not the headline.
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
from sandbox.research.usoil_families_research import (
    annual_detail, donchian_signal, exit_plan, ma_cross_signal,
    momentum_signal, overnight_signal, pdr_signal, random_side,
    rolling_mean_sigma, summarize, valid, vwap_signal, zscore_signal,
)

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
TS, O, H, L, C, V = range(6)

INITIAL_BALANCE = 1_000.0
#: Quoted bid/ask spread, in pips. ZERO and measured, not assumed: this is an
#: Exness zero-spread account and 218 live fills across nine symbols on
#: 2026-08-12 quoted 0.00 on every one. Kept as its own knob so a spread account
#: is a one-line change.
SPREAD_PIPS = 0.0
#: Execution slippage, in pips. This is what the 0.2 that used to live in
#: `SPREAD_PIPS` always was. Measured median slippage is 0 on every symbol, so
#: 0.2 is a conservative allowance rather than an observation.
SLIPPAGE_PIPS = 0.2
IS_START = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())
FULL_IS_YEARS = tuple(range(2020, 2025))
MARGIN_FRACTION = 0.25       # conservative self-imposed 4x notional ceiling
RISK_FRACTION = 0.015
MIN_PROFIT_FACTOR = 1.05
MAX_DD = 20.0
ANNUAL_DD = 22.0
NEIGHBOUR_DD = 24.0
MIN_FILL_RATE = 70.0
COST_SWEEP_PIPS = (0.0, 0.2, 1.0, 3.0, 5.0, 10.0)


def _instrument(table, source, session, first_full_year, pip_size, tick_size,
                tick_value, contract_size, volume_min, volume_step,
                volume_max=200.0, warning=None, commission_per_lot=0.0):
    """``commission_per_lot`` is USD per lot per ROUND TRIP, read from the
    terminal on 2026-08-12 by round-tripping the minimum lot and reading the
    deals back. It is proportional with no per-deal floor (verified at 1x and
    10x minimum lot) and Exness bills all of it at entry.

    It defaults to 0.0 so an instrument that has not been measured is visibly
    free rather than silently guessed; the six live-relevant ones carry real
    numbers.
    """
    multiplier = tick_value / tick_size
    return {
        "table": table, "source": source, "session": session,
        "first_full_year": first_full_year, "pip_size": pip_size,
        "tick_size": tick_size, "tick_value": tick_value,
        "multiplier": multiplier, "contract_size": contract_size,
        "volume_min": volume_min, "volume_step": volume_step,
        "volume_max": volume_max, "warning": warning,
        "commission_per_lot": commission_per_lot,
    }


# ``pip_size`` is a conventional pip, not MT5's smallest quote point.  Silver
# has three quote decimals, so one pip is 0.01 (ten 0.001 ticks); the other
# symbols use one displayed point per pip.
# Sessions are New York wall-clock, matching the repository timestamp contract.
# Native 30m industrial-metal tables use their liquid 03:00-14:00 window.
# COMMISSION, read from the terminal on 2026-08-12, one min-lot round trip per
# symbol. USD per lot per round trip, all of it billed at entry (every closing
# deal booked 0.00). As basis points of notional these dwarf the 0.2-pip charge
# this study used to apply alone -- XPDUSD is 85 bp, XPTUSD 25 bp, UKOIL 7.4 bp
# against 0.006-0.07 bp for the pips. Read any pre-2026-08-12 result in this
# module as gross of commission.
#
# XCUUSD, XNIUSD and XZNUSD are 0.0 because their market was CLOSED when the
# measurement ran, not because they are free. They keep the honest default and
# must be measured before any of them is deployed.
INSTRUMENTS = {
    "xagusd": _instrument("xagusd_1m", "1m", (8 * 60, 16 * 60), 2020,
        .01, .001, 5.0, 5000.0, .01, .01, commission_per_lot=95.0),
    "xcuusd": _instrument("xcuusd_1m", "1m", (8 * 60, 14 * 60 + 30), 2020,
        .01, .01, .01, 1.0, .01, .01,
        warning="QuestDB quote scale (2.8-6.6) differs from live MT5 (~14,000); "
                "commission unmeasured, market closed 2026-08-12"),
    "xngusd": _instrument("xngusd_1m", "1m", (9 * 60, 14 * 60 + 30), 2020,
        .0001, .0001, 1.0, 10000.0, .01, .01, 20.0, commission_per_lot=70.0),
    "xpdusd": _instrument("xpdusd_1m", "1m", (8 * 60, 16 * 60), 2022,
        .01, .01, 1.0, 100.0, .01, .01, commission_per_lot=1171.0),
    "xptusd": _instrument("xptusd_1m", "1m", (8 * 60, 16 * 60), 2022,
        .01, .01, 1.0, 100.0, .01, .01, commission_per_lot=450.0),
    "xalusd": _instrument("xalusd_30m", "30m", (3 * 60, 14 * 60), 2023,
        .01, .01, .01, 1.0, .01, .01, commission_per_lot=4.0),
    "xniusd": _instrument("xniusd_30m", "30m", (3 * 60, 14 * 60), 2023,
        .01, .01, .01, 1.0, .01, .01,
        warning="commission unmeasured, market closed 2026-08-12"),
    "xznusd": _instrument("xznusd_30m", "30m", (3 * 60, 14 * 60), 2023,
        .01, .01, .01, 1.0, .01, .01,
        warning="commission unmeasured, market closed 2026-08-12"),
    # Brent.  Same 1000-barrel lot and 09:00-14:30 pit session as the USOIL
    # study, confirmed against this table: 2025 volume peaks in hours 09-11 and
    # thins after 14.
    "ukoil": _instrument("ukoil_1m", "1m", (9 * 60, 14 * 60 + 30), 2020,
        .01, .01, 10.0, 1000.0, .01, .01, commission_per_lot=65.0),
    # Metal crosses, native M30 straight from the Exness terminal (2026-08-10).
    # These supersede the Dukascopy 1m cross tables: same instruments, but real
    # broker bars with continuous 2021-07 onward coverage instead of an archive
    # that skips 2015-2023 entirely.  Contract rows are read values, not
    # convention.  Session is 08:00-16:00 like the other precious metals.
    "xauaud": _instrument("xauaud_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, .0706, 100.0, .01, .01, commission_per_lot=48.0),
    "xaueur": _instrument("xaueur_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, .1155, 100.0, .01, .01, commission_per_lot=31.0),
    "xaugbp": _instrument("xaugbp_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, .1349, 100.0, .01, .01, commission_per_lot=31.0),
    "xagaud": _instrument("xagaud_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, 3.5311, 5000.0, .01, .01, commission_per_lot=170.0),
    "xageur": _instrument("xageur_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, 5.7755, 5000.0, .01, .01, commission_per_lot=170.0),
    "xaggbp": _instrument("xaggbp_30m", "30m", (8 * 60, 16 * 60), 2022,
        .01, .001, 6.7442, 5000.0, .01, .01, commission_per_lot=170.0),
}

# A deliberately compact search.  The crypto grid's near-duplicate exits and
# thresholds made the first commodity run roughly 411k cells and added far more
# multiple-testing burden than information.  These four exits retain one fixed
# target, one extended target, one time exit and one volatility-scaled trail.
EXIT_MODES = ("rr_1", "rr_2", "time_4", "trail_1.5")
CATEGORICAL = oil.CATEGORICAL


def is_years(symbol):
    first = INSTRUMENTS[symbol]["first_full_year"]
    return tuple(y for y in FULL_IS_YEARS if y >= first)


def min_positive_years(symbol):
    years = is_years(symbol)
    return len(years) if len(years) <= 3 else len(years) - 1


def periods(symbol):
    opened, closed = INSTRUMENTS[symbol]["session"]
    per_session = (closed - opened) // 30 + 1
    return {
        "session": per_session, "atr": 2 * per_session,
        "vol": 20 * per_session, "long_vol": 100 * per_session,
        "trend": {"ema_20d": 20 * per_session, "ema_50d": 50 * per_session},
        "zscore": (per_session, 2 * per_session, 5 * per_session),
        "fast": (per_session, 2 * per_session),
        "slow": (5 * per_session, 10 * per_session, 20 * per_session),
    }


def axes(symbol):
    cfg, p = INSTRUMENTS[symbol], periods(symbol)
    opened, closed = cfg["session"]
    last = tuple(sorted({closed - 120, closed - 60}))
    signal = tuple(m for m in (opened + 60, opened + 150)
                   if m <= closed - 60)
    common = {"exit_mode": EXIT_MODES, "stop_atr": (1.0, 2.5, 3.5),
              "trend": ("none", "ema_20d", "ema_50d"),
              "vol_mode": ("none", "calm")}
    out = {
        "orb": {"direction": ("breakout", "fade"), "range_bars": (1, 2),
                "breakout_atr": (0.0, .25), "last_entry_minute": last, **common},
        "overnight": {"direction": ("breakout", "fade"),
                      "buffer_atr": (0.0, .25), "last_entry_minute": last, **common},
        "pdr": {"direction": ("breakout", "fade"), "buffer_atr": (0.0, .25),
                "last_entry_minute": last, **common},
        "donchian": {"channel": (p["session"], 2*p["session"], 4*p["session"]),
                     "last_entry_minute": last, **common},
        "ma_cross": {"fast": p["fast"], "slow": p["slow"],
                     "last_entry_minute": last, **common},
        "momentum": {"direction": ("breakout", "fade"), "signal_minute": signal,
                     "lookback": (p["session"], 2*p["session"]),
                     "threshold_atr": (.5, 1.0), **common},
        "gap": {"direction": ("fade", "follow"),
                "threshold_atr": (.25, .5, 1.0), **common},
        "vwap": {"direction": ("fade", "follow"),
                 "threshold_atr": (.5, 1.0, 1.5),
                 "last_entry_minute": last, **common},
        "zscore": {"direction": ("fade", "follow"), "period": p["zscore"],
                   "threshold_z": (1.5, 2.5),
                   "last_entry_minute": last, **common},
    }
    if symbol == "xngusd":
        out["storage"] = {"direction": ("breakout", "fade"),
                          "signal_minute": (10*60+30, 11*60),
                          "threshold_atr": (.5, 1.0), **common}
    return out


def candidates(spec):
    cells = [dict(zip(spec, values)) for values in itertools.product(*spec.values())]
    return [cell for cell in cells if valid(cell)]


def all_bars_30m(symbol, phase):
    cfg = INSTRUMENTS[symbol]
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    volume = "volume" if cfg["source"] == "1m" else "tick_volume"
    if cfg["source"] == "1m":
        sql = ("SELECT cast(timestamp as long),first(open),max(high),min(low),"
               f"last(close),sum({volume}) FROM {cfg['table']} "
               f"WHERE timestamp >= '2019-01-01' {upper} "
               "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR")
    else:
        sql = ("SELECT cast(timestamp as long),open,high,low,close,"
               f"{volume} FROM {cfg['table']} WHERE timestamp >= '2019-01-01' "
               f"{upper} ORDER BY timestamp")
    key = f"{sql}:{data._table_fingerprint([cfg['table']])}"
    def build():
        return [[int(row[0]) // 1_000_000, *(float(v) for v in row[1:])]
                for row in data.query(sql)]
    scope = "is" if phase == "select" else "full"
    return [tuple(row) for row in data._cached(f"commodity_{symbol}_{scope}", key, build)]


def in_session(symbol, ts):
    minute = ts % 86_400 // 60
    opened, closed = INSTRUMENTS[symbol]["session"]
    return opened <= minute <= closed


def trailing_volatility(bars, n, annual_periods):
    returns = [0.0] + [math.log(b[C] / a[C]) if a[C] > 0 and b[C] > 0 else 0.0
                       for a, b in zip(bars, bars[1:])]
    out, total, total_sq = [None] * len(bars), 0.0, 0.0
    for i, value in enumerate(returns):
        total += value; total_sq += value * value
        if i >= n:
            old = returns[i-n]; total -= old; total_sq -= old * old
        if i >= n-1:
            mean = total / n
            out[i] = math.sqrt(max(0.0, total_sq/n - mean*mean) * annual_periods)
    return out


def anchors(full, symbol):
    opened, closed = INSTRUMENTS[symbol]["session"]
    outside, closes = {}, {}
    for bar in full:
        minute, day = bar[TS] % 86_400 // 60, bar[TS] // 86_400
        if minute >= closed: day += 1
        if minute < opened or minute >= closed:
            item = outside.setdefault(day, [bar[H], bar[L]])
            item[0], item[1] = max(item[0], bar[H]), min(item[1], bar[L])
        else:
            closes[bar[TS] // 86_400] = bar[C]
    ordered = sorted(closes)
    prior = {day: closes[ordered[i-1]] for i, day in enumerate(ordered) if i}
    return {day: tuple(v) for day, v in outside.items()}, prior


def prior_ranges(bars):
    got = {}
    for bar in bars:
        item = got.setdefault(bar[TS] // 86_400, [bar[H], bar[L]])
        item[0], item[1] = max(item[0], bar[H]), min(item[1], bar[L])
    days = sorted(got)
    return {day: tuple(got[days[i-1]]) for i, day in enumerate(days) if i}


def session_vwap(bars):
    out, day, notional, volume = [], None, 0.0, 0.0
    for bar in bars:
        current = bar[TS] // 86_400
        if current != day: day, notional, volume = current, 0.0, 0.0
        notional += (bar[H]+bar[L]+bar[C])/3 * bar[V]; volume += bar[V]
        out.append(notional/volume if volume > 0 else None)
    return out


def context(symbol, phase):
    cfg, p, full = INSTRUMENTS[symbol], periods(symbol), all_bars_30m(symbol, phase)
    bars = [bar for bar in full if in_session(symbol, bar[TS])]
    closes = [bar[C] for bar in bars]
    outside, prior = anchors(full, symbol)
    means, sigmas = {}, {}
    for n in p["zscore"]: means[n], sigmas[n] = rolling_mean_sigma(closes, n)
    short = trailing_volatility(bars, p["vol"], 252.0*p["session"])
    long = trailing_volatility(bars, p["long_vol"], 252.0*p["session"])
    channels = (p["session"], 2*p["session"], 4*p["session"])
    ctx = {
        "atr": es.average_true_range(bars, periods=p["atr"]),
        "ema": {n: es.ema(closes, n) for n in p["trend"].values()},
        "fast": {n: es.ema(closes, n) for n in p["fast"]},
        "slow": {n: es.ema(closes, n) for n in p["slow"]},
        "high": {n: es.rolling_extreme([b[H] for b in bars], n, True) for n in channels},
        "low": {n: es.rolling_extreme([b[L] for b in bars], n, False) for n in channels},
        "mean": means, "sigma": sigmas, "vwap": session_vwap(bars),
        "overnight": outside, "prior_close": prior,
        "prior_range": prior_ranges(bars), "volatility": short,
        "calm": [None if s is None or l is None or l <= 0 else s < l
                 for s, l in zip(short, long)], "cfg": cfg, "periods": p,
    }
    sample = [v for v, b in zip(short, bars) if v is not None and IS_START <= b[TS] < IS_END]
    ctx["vol_target"] = statistics.median(sample) if sample else .3
    return bars, ctx


def accepts_trend(price, ctx, i, side, mode):
    if mode == "none": return True
    ref = ctx["ema"][ctx["periods"]["trend"][mode]][i]
    return ref is not None and (price > ref if side == 1 else price < ref)


def accepts_vol(ctx, i, mode):
    return mode == "none" or (ctx["calm"][i] == (mode == "calm"))


def orb_signal(i, bars, ctx, params, state):
    bar, opened = bars[i], ctx["cfg"]["session"][0]
    day, minute = bar[TS]//86400, bar[TS]%86400//60
    if state.get("day") != day: state.clear(); state.update(day=day, high=None, low=None)
    end = opened + 30*params["range_bars"]
    if minute < end:
        state["high"] = bar[H] if state["high"] is None else max(state["high"],bar[H])
        state["low"] = bar[L] if state["low"] is None else min(state["low"],bar[L]); return None
    if minute > params["last_entry_minute"] or state["high"] is None: return None
    atr = ctx["atr"][i]
    if not atr: return None
    side = 1 if bar[C] > state["high"]+params["breakout_atr"]*atr else (-1 if bar[C] < state["low"]-params["breakout_atr"]*atr else None)
    return -side if side and params["direction"] == "fade" else side


def gap_signal(i, bars, ctx, params, _state):
    bar = bars[i]
    if bar[TS]%86400//60 != ctx["cfg"]["session"][0]: return None
    previous, atr = ctx["prior_close"].get(bar[TS]//86400), ctx["atr"][i]
    if previous is None or not atr: return None
    drift = (bar[O]-previous)/atr; threshold = params["threshold_atr"]
    side = -1 if drift > threshold else (1 if drift < -threshold else None)
    return -side if side and params["direction"] == "follow" else side


def storage_signal(i, bars, ctx, params, _state):
    bar = bars[i]
    if es.weekday(bar[TS]) != 3 or bar[TS]%86400//60 != params["signal_minute"]: return None
    atr = ctx["atr"][i]
    if not atr: return None
    move = bar[C]-bar[O]; threshold = params["threshold_atr"]*atr
    side = 1 if move > threshold else (-1 if move < -threshold else None)
    return -side if side and params["direction"] == "fade" else side


SIGNALS = {"orb":orb_signal, "overnight":overnight_signal, "pdr":pdr_signal,
           "donchian":donchian_signal, "ma_cross":ma_cross_signal,
           "momentum":momentum_signal, "gap":gap_signal, "vwap":vwap_signal,
           "zscore":zscore_signal, "storage":storage_signal}


def spread_price(cfg, spread_pips=SPREAD_PIPS):
    """The quoted-spread component alone, in price units.

    Kept as its own function because the study's cost sweeps drive it directly.
    On the live zero-spread account `SPREAD_PIPS` is 0.0 and this returns 0;
    `cost_price` is what the backtest actually charges.
    """
    return spread_pips * cfg["pip_size"]


def cost_price(cfg, spread_pips=None, slippage_pips=None, commission=None):
    """Everything charged once at entry, in price units.

    Three components, because they are measured three different ways:

      * spread      quoted bid/ask. ZERO here -- 218 live fills on 2026-08-12
                    quoted 0.00 across nine Exness symbols.
      * slippage    the 0.2-pip allowance this module used to call `spread`.
                    Measured median slippage is 0, so it is conservative.
      * commission  USD per lot per round trip, billed entirely at entry.
                    Divided by `multiplier` to reach price units, exactly as
                    `Execution.entry_cost` divides by `point_value`.

    The commission term is the one that was missing entirely, and on these
    instruments it dominates by orders of magnitude: XALUSD's $4/lot is 12.1 bp
    of a $3,306 price where 0.2 pips is 0.006 bp, and XNGUSD's $70/lot is
    24.6 bp against 0.07 bp.
    """
    spread_pips = SPREAD_PIPS if spread_pips is None else spread_pips
    slippage_pips = SLIPPAGE_PIPS if slippage_pips is None else slippage_pips
    commission = cfg["commission_per_lot"] if commission is None else commission
    per_lot = commission / cfg["multiplier"] if cfg["multiplier"] else 0.0
    return (spread_pips + slippage_pips) * cfg["pip_size"] + per_lot


def quantity(equity, price, stop_distance, realized, ctx):
    cfg = ctx["cfg"]
    risk = equity * RISK_FRACTION
    if realized and realized > 0: risk *= min(1.0, ctx["vol_target"]/realized)
    raw = risk / (stop_distance * cfg["multiplier"])
    margin_lot = price * cfg["contract_size"] * MARGIN_FRACTION
    ceiling = min(cfg["volume_max"], equity/margin_lot) if margin_lot > 0 else 0.0
    step = cfg["volume_step"]
    lots = math.floor(min(raw, ceiling)/step + 1e-10)*step
    return round(lots, 8) if lots + 1e-10 >= cfg["volume_min"] else 0.0


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
             initial=INITIAL_BALANCE, spread_pips=SPREAD_PIPS, null_seed=None,
             include_trades=False):
    cfg, opened, closed = ctx["cfg"], *ctx["cfg"]["session"]
    equity = peak = initial; maximum_dd = 0.0; trades = []
    position = pending = None; traded_day = None; state = {}; signals = fills = 0
    for i, bar in enumerate(bars):
        ts = bar[TS]
        if ts < lo: continue
        if ts >= hi: break
        day, minute = ts//86400, ts%86400//60
        if position is not None:
            side, price, reason = position["side"], None, None
            if minute >= closed: price, reason = bar[O], "session"
            else:
                stop = position["stop"]
                if (side==1 and bar[L]<=stop) or (side==-1 and bar[H]>=stop):
                    price, reason = (min(bar[O],stop) if side==1 else max(bar[O],stop)), "stop"
                elif position["target"] is not None:
                    target=position["target"]
                    if (side==1 and bar[H]>=target) or (side==-1 and bar[L]<=target):
                        price, reason = (max(bar[O],target) if side==1 else min(bar[O],target)), "target"
                if price is None and position["max_bars"] is not None and i-position["index"]>=position["max_bars"]:
                    price, reason = bar[O], "time"
            if price is None and position["trail"] is not None and ctx["atr"][i]:
                atr=ctx["atr"][i]
                if side==1:
                    position["best"]=max(position["best"],bar[C]); position["stop"]=max(position["stop"],position["best"]-position["trail"]*atr)
                else:
                    position["best"]=min(position["best"],bar[C]); position["stop"]=min(position["stop"],position["best"]+position["trail"]*atr)
            elif price is not None:
                gross=side*(price-position["entry"]); points=gross-cost_price(cfg,spread_pips)
                pnl=points*position["lots"]*cfg["multiplier"]; equity+=pnl; peak=max(peak,equity)
                maximum_dd=max(maximum_dd,(peak-equity)/peak if peak>0 else 1.0)
                trades.append({"entry_ts":position["ts"],"exit_ts":ts,"side":side,"points":points,"gross":gross,"pnl":pnl,"atr":position["atr"],"entry":position["entry"],"bars":i-position["index"],"quantity":position["lots"],"reason":reason})
                position=None
        if position is None and pending is not None:
            if day==pending["day"] and minute<closed:
                lots=quantity(equity,bar[O],pending["distance"],pending["realized"],ctx)
                if lots>0:
                    target,max_bars,trail=exit_plan(params["exit_mode"],pending["distance"]); side=pending["side"]
                    position={"side":side,"entry":bar[O],"ts":ts,"index":i,"lots":lots,"atr":pending["atr"],"stop":bar[O]-side*pending["distance"],"target":None if target is None else bar[O]+side*target,"max_bars":max_bars,"trail":trail,"best":bar[O]}
                    traded_day=day; fills+=1
            pending=None
        if position is None and pending is None and traded_day!=day and opened<=minute<closed and accepts_vol(ctx,i,params["vol_mode"]):
            side=SIGNALS[family](i,bars,ctx,params,state)
            if side is not None and null_seed is not None: side=random_side(ts,null_seed)
            atr,realized=ctx["atr"][i],ctx["volatility"][i]
            if side is not None and atr and realized is not None and accepts_trend(bar[C],ctx,i,side,params["trend"]):
                # Count a signal only after every strategy filter has accepted
                # it.  A failure from here to entry is therefore lot/margin
                # granularity, which is exactly what fill_rate is meant to test.
                signals+=1
                pending={"side":side,"day":day,"atr":atr,"distance":params["stop_atr"]*atr,"realized":realized}
    result=summarize(trades,maximum_dd,equity,initial); result["annual"]=annual_detail(trades,initial)
    result["signals"],result["fills"],result["fill_rate"] = signals,fills,round(100*fills/signals,1) if signals else 0.0
    if trades:
        bp=[1e4*t["gross"]/t["entry"] for t in trades if t["entry"]]
        result["breakeven_spread_pips"]=round(statistics.fmean([t["gross"] for t in trades])/cfg["pip_size"],4)
        result["gross_bp_per_trade"]=round(statistics.fmean(bp),4)
        result["long_share"]=round(sum(t["side"]==1 for t in trades)/len(trades),3)
        # Remove the return a mechanical rule with the same long/short mix and
        # holding times would earn from unconditional session drift.
        window=[bar for bar in bars if lo<=bar[TS]<hi]
        moves=[1e4*(b[C]-a[C])/a[C] for a,b in zip(window,window[1:]) if a[C]>0]
        mu=statistics.fmean(moves) if moves else 0.0
        drift=statistics.fmean([t["side"]*t["bars"]*mu for t in trades])
        excess=[value-t["side"]*t["bars"]*mu for value,t in zip(bp,trades)]
        result["drift_bp_per_trade"]=round(drift,4)
        result["edge_vs_drift_bp"]=round(statistics.fmean(excess),4)
        if len(excess)>1:
            sd=statistics.stdev(excess)
            result["edge_vs_drift_t_stat"]=round(statistics.fmean(excess)/(sd/math.sqrt(len(excess))),2) if sd else 0.0
    if include_trades:
        result["trade_log"] = trades
    return result


def passes(symbol, stat, dd=MAX_DD):
    years=is_years(symbol); annual=stat["annual"]
    positive=sum(annual.get(str(y),{}).get("pnl",0)>0 for y in years)
    worst=max((annual.get(str(y),{}).get("max_dd_pct",100) for y in years),default=100)
    min_trades=max(50,25*len(years))
    return stat["trades"]>=min_trades and stat["pf"]>=MIN_PROFIT_FACTOR and stat["max_dd_pct"]<=dd and positive>=min_positive_years(symbol) and worst<=ANNUAL_DD and stat.get("fill_rate",0)>=MIN_FILL_RATE


def quality(symbol, stat):
    if not passes(symbol,stat): return -math.inf
    returns=[stat["annual"].get(str(y),{}).get("return_pct",0) for y in is_years(symbol)]
    return 100*math.log(stat["final"]/INITIAL_BALANCE)+min(returns)+.25*statistics.median(returns)-.5*statistics.pstdev(returns)


def neighbours(params,spec):
    out=[]
    for key,values in spec.items():
        if key in CATEGORICAL: continue
        at=values.index(params[key])
        for j in (at-1,at+1):
            if 0<=j<len(values):
                item={**params,key:values[j]}
                if valid(item): out.append(item)
    return out


def choose(symbol,family,results):
    spec=axes(symbol)[family]; ranked=[]
    for params in candidates(spec):
        stat=results[es.frozen(params)]; own=quality(symbol,stat)
        if not math.isfinite(own): continue
        bare=results.get(es.frozen({**params,"trend":"none","vol_mode":"none"}))
        if bare is None or bare["pnl"]<=0 or bare["pf"]<1: continue
        near=[results[es.frozen(p)] for p in neighbours(params,spec)]
        robust=[s for s in near if passes(symbol,s,NEIGHBOUR_DD)]
        if not near or len(robust)<math.ceil(.6*len(near)): continue
        ranked.append((own,params,stat,len(robust),len(near)))
    if not ranked: return None
    score,params,stat,robust,total=max(ranked,key=lambda x:x[0])
    return {"params":params,"in_sample":stat,"score":round(score,6),"robust_neighbours":f"{robust}/{total}"}


_WORKER={}
def _init_worker(symbol,phase):
    _WORKER["symbol"]=symbol; _WORKER["bars"],_WORKER["ctx"]=context(symbol,phase)
def _evaluate(job):
    family,params,spread_pips,null_seed=job
    stat=backtest(family,_WORKER["bars"],_WORKER["ctx"],params,spread_pips=spread_pips,null_seed=null_seed)
    return es.frozen(params),stat


def output_path(symbol): return os.path.join(RESULTS,f"commodity_families_{symbol}.json")
def seal(payload,path):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":")); payload["seal_sha256"]=hashlib.sha256(canonical.encode()).hexdigest()
    with open(path,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,sort_keys=True); f.write("\n")


def select(symbol,workers,spread_pips):
    family_rows={}; specs=axes(symbol)
    with multiprocessing.Pool(workers,_init_worker,(symbol,"select")) as pool:
        for family,spec in specs.items():
            jobs=[(family,p,spread_pips,None) for p in candidates(spec)]
            results=dict(pool.imap_unordered(_evaluate,jobs,chunksize=32))
            family_rows[family]=choose(symbol,family,results)
            winner=family_rows[family]; print(f"{symbol:<8} {family:<10} " + ("-" if winner is None else f"{winner['in_sample']['return_pct']:+.1f}% dd {winner['in_sample']['max_dd_pct']:.1f}%"),flush=True)
    cfg=INSTRUMENTS[symbol]
    payload={"sealed":True,"symbol":symbol,"protocol":{"in_sample_years":list(is_years(symbol)),"holdout":"2025-01-01 through 2026-08-06","initial_balance":INITIAL_BALANCE,"spread_pips":spread_pips,"slippage_pips":SLIPPAGE_PIPS,"commission_per_lot":cfg["commission_per_lot"],"pip_size":cfg["pip_size"],"spread_price":spread_price(cfg,spread_pips),"cost_price":cost_price(cfg,spread_pips),"sizing":"1.5% volatility-throttled stop risk, MT5 lots, 4x notional ceiling","contract":{k:cfg[k] for k in ("tick_size","tick_value","multiplier","contract_size","volume_min","volume_step","volume_max","commission_per_lot")},"session_minutes":list(cfg["session"]),"candidate_counts":{f:len(candidates(s)) for f,s in specs.items()},"warning":cfg["warning"]},"families":family_rows}
    seal(payload,output_path(symbol)); print(f"sealed {output_path(symbol)}")


def validate(symbol,spread_pips):
    path=output_path(symbol); payload=json.load(open(path,encoding="utf-8")); expected=payload.pop("seal_sha256"); payload.pop("validation",None)
    if hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()!=expected: raise SystemExit("selection seal mismatch")
    bars,ctx=context(symbol,"validate"); validation={}
    for family,winner in payload["families"].items():
        if winner is None: continue
        params=winner["params"]; base=backtest(family,bars,ctx,params,lo=IS_END,hi=OOS_END,spread_pips=spread_pips)
        sweep={str(v):backtest(family,bars,ctx,params,lo=IS_END,hi=OOS_END,spread_pips=v) for v in COST_SWEEP_PIPS}
        validation[family]={"oos":base,"cost_sweep_pips":sweep}
        print(f"{symbol} {family:<10} OOS {base['return_pct']:+.1f}% dd {base['max_dd_pct']:.1f}% n={base['trades']} PF={base['pf']:.2f} BE={base.get('breakeven_spread_pips',0):.1f} pips")
    payload["seal_sha256"]=expected; payload["validation"]=validation
    with open(path,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,sort_keys=True); f.write("\n")


def why(symbol, workers, spread_pips, requested=None):
    """Re-select cells after replacing every signal direction with a coin flip.

    Timing, filters, exits, sizing and the full parameter search remain intact;
    only long versus short is randomized.  A real candidate should beat the
    median holdout result manufactured by the same search budget.
    """
    available = axes(symbol)
    families = [f for f in (requested or available) if f in available]
    full_bars, full_ctx = context(symbol, "validate")
    rows = {}
    with multiprocessing.Pool(workers, _init_worker, (symbol, "select")) as pool:
        for family in families:
            rows[family] = []
            spec = available[family]
            universe = candidates(spec)
            for seed in (1, 2, 3):
                jobs = [(family, p, spread_pips, seed) for p in universe]
                selected = choose(symbol, family,
                                  dict(pool.imap_unordered(_evaluate, jobs, chunksize=32)))
                if selected is None:
                    rows[family].append(None)
                    print(f"{symbol} {family:<10} flip{seed}: no IS cell")
                    continue
                outside = backtest(family, full_bars, full_ctx, selected["params"],
                                   lo=IS_END, hi=OOS_END,
                                   spread_pips=spread_pips, null_seed=seed)
                rows[family].append({"seed": seed, "params": selected["params"],
                                     "in_sample": selected["in_sample"],
                                     "out_of_sample": outside})
                print(f"{symbol} {family:<10} flip{seed}: IS "
                      f"{selected['in_sample']['return_pct']:+.1f}% OOS "
                      f"{outside['return_pct']:+.1f}%")
    destination = os.path.join(RESULTS, f"commodity_null_{symbol}.json")
    existing = {}
    if os.path.exists(destination):
        existing = json.load(open(destination, encoding="utf-8")).get("null_control", {})
    existing.update(rows)
    with open(destination, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "spread_pips": spread_pips,
                   "null_control": existing}, f, indent=2, sort_keys=True)
        f.write("\n")


def preflight(symbols):
    for symbol in symbols:
        cfg=INSTRUMENTS[symbol]
        first=data.query(f"SELECT timestamp,close FROM {cfg['table']} LIMIT 1")[0]
        last=data.query(f"SELECT timestamp,close FROM {cfg['table']} ORDER BY timestamp DESC LIMIT 1")[0]
        print(f"{symbol:<8} {cfg['table']:<14} {first[0]} -> {last[0]}  price {first[1]} -> {last[1]}  multiplier ${cfg['multiplier']:g}/price/lot  step {cfg['volume_step']} lot" + (f"  WARNING: {cfg['warning']}" if cfg['warning'] else ""))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("phase",choices=("preflight","select","validate","why")); parser.add_argument("--symbols",nargs="+",choices=tuple(INSTRUMENTS),default=list(INSTRUMENTS)); parser.add_argument("--families",nargs="+",choices=tuple(SIGNALS)); parser.add_argument("--workers",type=int,default=max(1,min(6,(os.cpu_count() or 2)-1))); parser.add_argument("--spread-pips",type=float,default=SPREAD_PIPS); args=parser.parse_args()
    if args.phase=="preflight": preflight(args.symbols); return
    for symbol in args.symbols:
        if args.phase == "select":
            select(symbol, args.workers, args.spread_pips)
        elif args.phase == "validate":
            validate(symbol, args.spread_pips)
        else:
            why(symbol, args.workers, args.spread_pips, args.families)


if __name__ == "__main__": main()
