"""Intraday family study for the seven index CFD tables in QuestDB.

Same shape as ``commodity_families_research``: nine families compete on 30m
bars resampled from the Dukascopy 1m imports, selection runs on 2020-2024 and
2025 through 2026-08-06 is sealed holdout.  Balance is USD 1,000, sizing is a
1.5% volatility-throttled stop risk in MT5 lots under a 4x notional ceiling.

CONTRACT METADATA is read values, not convention: every row below came from the
running Exness MT5 terminal on 2026-08-10 (``Exness-MT5Trial8``).  Index CFDs
all carry ``contract_size = 1``, so a lot is one index unit and the USD value of
a point is ``tick_value / tick_size`` outright.  Note the broker's own naming:
the German index is **DE30**, not DE40, and its volume minimum (0.07) is seven
times STOXX50's (0.03) while JP225's is 3.00 lots.

    symbol   USD per point per lot   volume_min
    AUS200   0.70662                 0.06
    DE30     1.15556                 0.07
    FR40     1.15556                 0.05
    HK50     0.12745                 0.07
    STOXX50  1.15556                 0.03
    UK100    1.34948                 0.05
    JP225    0.00631                 3.00

Because six of the seven settle in a non-USD currency, ``multiplier`` is an FX
snapshot rather than a constant -- the same caveat the metal crosses carry.  It
cancels between risk sizing and P&L and survives only in lot rounding and the
margin ceiling.  The margin ceiling therefore uses ``price * multiplier``, which
is the USD notional of a lot; for the USD-settled commodity tables that
expression reduces to the ``price * contract_size`` the older studies use.

COSTS.  The requested model is **zero spread**, with 0.2 index points on UK100.
That is close to what the terminal shows (UK100 quoted 0.57 points on
2026-08-10, the rest were closed) but it is still the most optimistic assumption
in this file, and [[zero-is-the-wrong-backtest-baseline]] says a number read
against zero is not readable at all.  Two things guard it.  ``validate`` sweeps
the cost in both index points and basis points and reports each winner's
break-even spread, and ``why`` re-runs the entire search with every signal
direction replaced by a coin flip -- at zero cost a null control earns real
money, so only a candidate's margin over its own null means anything.

SESSIONS AND THE SHIFTED CLOCK.  Tables hold New York wall-clock (AGENT.md).
The four European indices sit inside a NY day and use 03:00-11:30, which is
Frankfurt/Paris/London cash plus the US overlap where the volume profile peaks.
The three Asian indices do not: their cash sessions straddle NY midnight, which
the one-trade-per-day engine cannot express.  Their bars are therefore loaded
with a fixed ``shift_hours`` added to every timestamp, so the session lands
inside a single shifted day and all downstream day-bucketing, anchoring and
signal timing work unchanged.  Only the IS/OOS window comparisons and the
timestamps recorded on trades subtract the shift back out, so reported years and
holdout boundaries stay in real time.

Session bounds for the Asian three are padded, because Tokyo, Hong Kong and
Sydney do not share New York's daylight-saving dates: the NY-clock time of the
Tokyo open moves an hour twice a year, and Sydney's opposite-phase DST moves the
ASX open by two.  A fixed window would silently clip a season, so each window is
widened to contain both regimes.
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
# Index points, then basis points of price.  The point sweep is the one the
# request is phrased in; the bp sweep is the one that compares across a book
# holding FR40 at 8,700 and JP225 at 67,000.
COST_SWEEP_POINTS = (0.0, 0.2, 0.5, 1.0, 2.0, 5.0)
COST_SWEEP_BP = (0.0, 0.5, 1.0, 2.0, 5.0)


def _instrument(table, session, multiplier, volume_min, shift_hours=0,
                pip_size=1.0, tick_size=0.01, tick_value=None,
                volume_step=0.01, volume_max=300.0, warning=None,
                commission_per_lot=0.0):
    """``commission_per_lot`` is USD per lot per ROUND TRIP, measured on the
    live terminal 2026-08-12 by round-tripping the minimum lot.
    """
    return {
        "table": table, "source": "1m", "session": session,
        "first_full_year": 2020, "pip_size": pip_size, "tick_size": tick_size,
        "tick_value": tick_value if tick_value is not None else multiplier * tick_size,
        "multiplier": multiplier, "contract_size": 1.0,
        "volume_min": volume_min, "volume_step": volume_step,
        "volume_max": volume_max, "shift_hours": shift_hours,
        "warning": warning, "commission_per_lot": commission_per_lot,
    }


# A pip is one index point for every symbol here, so ``spread_points`` in the
# CLI and ``pip_size`` in the cost model are the same unit.
#
# Sessions below are minutes in the *shifted* clock.  For the European four the
# shift is zero, so they read directly as New York time.  For the Asian three
# add the shift back to recover NY: AUS200 19:00-02:00, HK50 21:00-04:00 and
# JP225 19:00-02:00 New York.
INSTRUMENTS = {
    # Europe: Frankfurt/Paris/London cash plus the US overlap.  Volume peaks at
    # 09:00-10:00 NY on all four, which is the New York open, not the local one.
    "de40": _instrument("de40_1m", (3 * 60, 11 * 60 + 30), 1.15556, 0.07,
        warning="Exness quotes this symbol as DE30, not DE40",
        commission_per_lot=1.29),
    "fr40": _instrument("fr40_1m", (3 * 60, 11 * 60 + 30), 1.15556, 0.05,
        commission_per_lot=1.24),
    "stoxx50": _instrument("stoxx50_1m", (3 * 60, 11 * 60 + 30), 1.15556, 0.03,
        commission_per_lot=1.33),
    "uk100": _instrument("uk100_1m", (3 * 60, 11 * 60 + 30), 1.34948, 0.05,
        commission_per_lot=1.40),
    # Asia: shifted six hours so the cash session sits inside one shifted day.
    "aus200": _instrument("aus200_1m", (60, 8 * 60), 0.70662, 0.06, shift_hours=6,
        commission_per_lot=1.50),
    "hk50": _instrument("hk50_1m", (3 * 60, 10 * 60), 0.12745, 0.07, shift_hours=6,
        commission_per_lot=1.27),
    "jp225": _instrument("jp225_1m", (60, 8 * 60), 0.0063112, 3.00,
        shift_hours=6, tick_size=0.1, volume_max=5000.0,
        commission_per_lot=0.04),
}

# Requested cost model: zero spread everywhere, 0.2 index points on UK100.
SPREAD_POINTS = {symbol: 0.0 for symbol in INSTRUMENTS}
SPREAD_POINTS["uk100"] = 0.2

EXIT_MODES = ("rr_1", "rr_2", "time_4", "trail_1.5")
CATEGORICAL = oil.CATEGORICAL


def shift_seconds(symbol):
    return INSTRUMENTS[symbol]["shift_hours"] * 3_600


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
    p = periods(symbol)
    opened, closed = INSTRUMENTS[symbol]["session"]
    last = tuple(sorted({closed - 120, closed - 60}))
    signal = tuple(m for m in (opened + 60, opened + 150) if m <= closed - 60)
    common = {"exit_mode": EXIT_MODES, "stop_atr": (1.0, 2.5, 3.5),
              "trend": ("none", "ema_20d", "ema_50d"),
              "vol_mode": ("none", "calm")}
    return {
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


def candidates(spec):
    cells = [dict(zip(spec, values)) for values in itertools.product(*spec.values())]
    return [cell for cell in cells if valid(cell)]


def all_bars_30m(symbol, phase):
    """30m bars with ``shift_hours`` already added to every timestamp.

    The shift is baked in here rather than applied at use sites because the
    shared signal functions read ``ts % 86400`` and ``ts // 86400`` directly.
    """
    cfg = INSTRUMENTS[symbol]
    shift = shift_seconds(symbol)
    # Widen the raw pull by the shift so a shifted bar never lands outside the
    # window it belongs to.
    upper = "AND timestamp < '2025-01-02'" if phase == "select" else ""
    sql = ("SELECT cast(timestamp as long),first(open),max(high),min(low),"
           "last(close),sum(volume) FROM " + cfg["table"] +
           " WHERE timestamp >= '2019-01-01' " + upper +
           " SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR")
    key = f"{sql}:shift{shift}:{data._table_fingerprint([cfg['table']])}"
    def build():
        return [[int(row[0]) // 1_000_000 + shift, *(float(v) for v in row[1:])]
                for row in data.query(sql)]
    scope = "is" if phase == "select" else "full"
    return [tuple(row) for row in data._cached(f"index_{symbol}_{scope}", key, build)]


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
    shift = shift_seconds(symbol)
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
        "shift": shift,
    }
    sample = [v for v, b in zip(short, bars)
              if v is not None and IS_START <= b[TS] - shift < IS_END]
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


SIGNALS = {"orb":orb_signal, "overnight":overnight_signal, "pdr":pdr_signal,
           "donchian":donchian_signal, "ma_cross":ma_cross_signal,
           "momentum":momentum_signal, "gap":gap_signal, "vwap":vwap_signal,
           "zscore":zscore_signal}


def cost_price(cfg, entry, spread_points, spread_bp, commission=None):
    """Round-trip cost in price units, charged wholly at entry.

    `commission` is USD per lot per round trip and reaches price units by
    dividing by `multiplier`, the same conversion `Execution.entry_cost` makes
    through `point_value`. Exness bills the whole round trip at the open, so
    "wholly at entry" is now the broker's behaviour rather than a convention.

    Measured 2026-08-12: AUS200 $1.50/lot, HK50 $1.27, FR40 $1.24, DE30 $1.29,
    UK100 $1.40, STOXX50 $1.33, JP225 $0.04. In basis points these run
    0.42-1.77, so they are the same order as the `spread_bp` figures rather than
    dwarfing them the way the commodity commissions do -- indices are the one
    family where the old cost model was roughly the right size.
    """
    commission = cfg["commission_per_lot"] if commission is None else commission
    per_lot = commission / cfg["multiplier"] if cfg["multiplier"] else 0.0
    return spread_points * cfg["pip_size"] + entry * spread_bp / 1e4 + per_lot


def quantity(equity, price, stop_distance, realized, ctx):
    """MT5 lots, floored to the volume step and capped by the margin ceiling.

    ``price * multiplier`` is the USD notional of one lot: ``multiplier`` is
    already USD per point per lot, and contract size is 1 index unit for every
    symbol here.  The USD-settled commodity study writes this as
    ``price * contract_size``, which is the same number whenever the profit
    currency is USD.
    """
    cfg = ctx["cfg"]
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0: return 0.0
    risk = equity * RISK_FRACTION
    if realized and realized > 0: risk *= min(1.0, ctx["vol_target"]/realized)
    raw = risk / (stop_distance * cfg["multiplier"])
    margin_lot = price * cfg["multiplier"] * MARGIN_FRACTION
    ceiling = min(cfg["volume_max"], equity/margin_lot) if margin_lot > 0 else 0.0
    step = cfg["volume_step"]
    lots = math.floor(min(raw, ceiling)/step + 1e-10)*step
    return round(lots, 8) if lots + 1e-10 >= cfg["volume_min"] else 0.0


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
             initial=INITIAL_BALANCE, spread_points=0.0, spread_bp=0.0,
             null_seed=None, include_trades=False):
    cfg, opened, closed = ctx["cfg"], *ctx["cfg"]["session"]
    shift = ctx["shift"]
    equity = peak = initial; maximum_dd = 0.0; trades = []
    position = pending = None; traded_day = None; state = {}; signals = fills = 0
    for i, bar in enumerate(bars):
        ts = bar[TS]
        real = ts - shift            # window bounds and reporting stay real time
        if real < lo: continue
        if real >= hi: break
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
                gross=side*(price-position["entry"])
                points=gross-cost_price(cfg,position["entry"],spread_points,spread_bp)
                pnl=points*position["lots"]*cfg["multiplier"]; equity+=pnl; peak=max(peak,equity)
                maximum_dd=max(maximum_dd,(peak-equity)/peak if peak>0 else 1.0)
                trades.append({"entry_ts":position["ts"]-shift,"exit_ts":real,"side":side,"points":points,"gross":gross,"pnl":pnl,"atr":position["atr"],"entry":position["entry"],"bars":i-position["index"],"quantity":position["lots"],"reason":reason})
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
                signals+=1
                pending={"side":side,"day":day,"atr":atr,"distance":params["stop_atr"]*atr,"realized":realized}
    result=summarize(trades,maximum_dd,equity,initial); result["annual"]=annual_detail(trades,initial)
    result["signals"],result["fills"],result["fill_rate"] = signals,fills,round(100*fills/signals,1) if signals else 0.0
    if trades:
        bp=[1e4*t["gross"]/t["entry"] for t in trades if t["entry"]]
        result["breakeven_spread_points"]=round(statistics.fmean([t["gross"] for t in trades])/cfg["pip_size"],4)
        result["gross_bp_per_trade"]=round(statistics.fmean(bp),4)
        result["long_share"]=round(sum(t["side"]==1 for t in trades)/len(trades),3)
        window=[bar for bar in bars if lo<=bar[TS]-shift<hi]
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
    family,params,points,null_seed=job
    stat=backtest(family,_WORKER["bars"],_WORKER["ctx"],params,spread_points=points,null_seed=null_seed)
    return es.frozen(params),stat


def output_path(symbol): return os.path.join(RESULTS,f"index_families_{symbol}.json")
def seal(payload,path):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":")); payload["seal_sha256"]=hashlib.sha256(canonical.encode()).hexdigest()
    with open(path,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,sort_keys=True); f.write("\n")


def select(symbol,workers,spread_points):
    family_rows={}; specs=axes(symbol)
    with multiprocessing.Pool(workers,_init_worker,(symbol,"select")) as pool:
        for family,spec in specs.items():
            jobs=[(family,p,spread_points,None) for p in candidates(spec)]
            results=dict(pool.imap_unordered(_evaluate,jobs,chunksize=32))
            family_rows[family]=choose(symbol,family,results)
            winner=family_rows[family]; print(f"{symbol:<8} {family:<10} " + ("-" if winner is None else f"{winner['in_sample']['return_pct']:+.1f}% dd {winner['in_sample']['max_dd_pct']:.1f}%"),flush=True)
    cfg=INSTRUMENTS[symbol]
    opened,closed=cfg["session"]; sh=cfg["shift_hours"]
    payload={"sealed":True,"symbol":symbol,"protocol":{"in_sample_years":list(is_years(symbol)),"holdout":"2025-01-01 through 2026-08-06","initial_balance":INITIAL_BALANCE,"spread_points":spread_points,"pip_size":cfg["pip_size"],"sizing":"1.5% volatility-throttled stop risk, MT5 lots, 4x notional ceiling","contract":{k:cfg[k] for k in ("tick_size","tick_value","multiplier","contract_size","volume_min","volume_step","volume_max")},"session_minutes_shifted":[opened,closed],"shift_hours":sh,"session_minutes_new_york":[(opened-sh*60)%1440,(closed-sh*60)%1440],"candidate_counts":{f:len(candidates(s)) for f,s in specs.items()},"warning":cfg["warning"]},"families":family_rows}
    seal(payload,output_path(symbol)); print(f"sealed {output_path(symbol)}")


def validate(symbol,spread_points):
    path=output_path(symbol); payload=json.load(open(path,encoding="utf-8")); expected=payload.pop("seal_sha256"); payload.pop("validation",None)
    if hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()!=expected: raise SystemExit("selection seal mismatch")
    bars,ctx=context(symbol,"validate"); validation={}
    for family,winner in payload["families"].items():
        if winner is None: continue
        params=winner["params"]
        base=backtest(family,bars,ctx,params,lo=IS_END,hi=OOS_END,spread_points=spread_points)
        points={str(v):backtest(family,bars,ctx,params,lo=IS_END,hi=OOS_END,spread_points=v) for v in COST_SWEEP_POINTS}
        bps={str(v):backtest(family,bars,ctx,params,lo=IS_END,hi=OOS_END,spread_bp=v) for v in COST_SWEEP_BP}
        validation[family]={"oos":base,"cost_sweep_points":points,"cost_sweep_bp":bps}
        print(f"{symbol} {family:<10} OOS {base['return_pct']:+.1f}% dd {base['max_dd_pct']:.1f}% n={base['trades']} PF={base['pf']:.2f} t={base.get('edge_vs_drift_t_stat',0):.2f} BE={base.get('breakeven_spread_points',0):.2f}pts")
    payload["seal_sha256"]=expected; payload["validation"]=validation
    with open(path,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2,sort_keys=True); f.write("\n")


def why(symbol, workers, spread_points, requested=None):
    """Re-select cells after replacing every signal direction with a coin flip.

    Timing, filters, exits, sizing and the full parameter search stay intact;
    only long versus short is randomized.  A real candidate has to beat the
    holdout result the same search budget manufactures from noise, which at a
    zero spread is not a small number -- see
    [[crypto-null-baseline-is-strongly-positive]].
    """
    available = axes(symbol)
    families = [f for f in (requested or available) if f in available]
    full_bars, full_ctx = context(symbol, "validate")
    rows = {}
    with multiprocessing.Pool(workers, _init_worker, (symbol, "select")) as pool:
        for family in families:
            rows[family] = []
            universe = candidates(available[family])
            # Five seeds rather than the commodity study's three: a cell here
            # costs ~20ms, so a tighter null distribution is nearly free, and a
            # zero-spread study needs the null more than a 0.2-pip one does.
            for seed in (1, 2, 3, 4, 5):
                jobs = [(family, p, spread_points, seed) for p in universe]
                selected = choose(symbol, family,
                                  dict(pool.imap_unordered(_evaluate, jobs, chunksize=32)))
                if selected is None:
                    rows[family].append(None)
                    print(f"{symbol} {family:<10} flip{seed}: no IS cell", flush=True)
                    continue
                outside = backtest(family, full_bars, full_ctx, selected["params"],
                                   lo=IS_END, hi=OOS_END,
                                   spread_points=spread_points, null_seed=seed)
                rows[family].append({"seed": seed, "params": selected["params"],
                                     "in_sample": selected["in_sample"],
                                     "out_of_sample": outside})
                print(f"{symbol} {family:<10} flip{seed}: IS "
                      f"{selected['in_sample']['return_pct']:+.1f}% OOS "
                      f"{outside['return_pct']:+.1f}%", flush=True)
    destination = os.path.join(RESULTS, f"index_null_{symbol}.json")
    existing = {}
    if os.path.exists(destination):
        existing = json.load(open(destination, encoding="utf-8")).get("null_control", {})
    existing.update(rows)
    with open(destination, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "spread_points": spread_points,
                   "null_control": existing}, f, indent=2, sort_keys=True)
        f.write("\n")


def preflight(symbols):
    for symbol in symbols:
        cfg=INSTRUMENTS[symbol]
        first=data.query(f"SELECT timestamp,close FROM {cfg['table']} LIMIT 1")[0]
        last=data.query(f"SELECT timestamp,close FROM {cfg['table']} ORDER BY timestamp DESC LIMIT 1")[0]
        bars,_=context(symbol,"select")
        window=[b for b in bars if IS_START<=b[TS]-shift_seconds(symbol)<IS_END]
        days=len({b[TS]//86400 for b in window})
        opened,closed=cfg["session"]; sh=cfg["shift_hours"]
        print(f"{symbol:<8} {cfg['table']:<12} {first[0][:10]} -> {last[0][:10]}  "
              f"px {first[1]:.0f}->{last[1]:.0f}  ${cfg['multiplier']:g}/pt/lot  "
              f"min {cfg['volume_min']} lot  session {opened//60:02d}:{opened%60:02d}-{closed//60:02d}:{closed%60:02d}"
              f"{f' (shift +{sh}h)' if sh else ''}  IS {days} days, {len(window)} bars"
              + (f"  WARNING: {cfg['warning']}" if cfg['warning'] else ""))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("phase",choices=("preflight","select","validate","why"))
    parser.add_argument("--symbols",nargs="+",choices=tuple(INSTRUMENTS),default=list(INSTRUMENTS))
    parser.add_argument("--families",nargs="+",choices=tuple(SIGNALS))
    parser.add_argument("--workers",type=int,default=max(1,min(14,(os.cpu_count() or 2)-2)))
    parser.add_argument("--spread-points",type=float,default=None,
                        help="override the instrument-specific default")
    args=parser.parse_args()
    if args.phase=="preflight": preflight(args.symbols); return
    for symbol in args.symbols:
        points=SPREAD_POINTS[symbol] if args.spread_points is None else args.spread_points
        if args.phase == "select":
            select(symbol, args.workers, points)
        elif args.phase == "validate":
            validate(symbol, points)
        else:
            why(symbol, args.workers, points, args.families)


if __name__ == "__main__": main()
