"""Nine intraday strategy families on eight crypto pairs, with a 2025-2026 holdout.

This is the USOIL/XAUUSD protocol (`usoil_families_research`,
`xauusd_families_research`) re-pointed at the eight crypto tables, reusing their
signal functions, summary code and selection gates unchanged so every
instrument this repo has studied stays directly comparable. Nine price-only
families -- three breakout, two trend following, two momentum, two mean
reversion -- the same 10 exit modes, 5 stop widths, 3 trend filters and 3
volatility regimes, the same coin-flip null control, and the same "select at
10,000, report at 1,000" discipline. The oil-specific EIA family is dropped;
nothing replaces it.

  breakout        ``orb``        opening-range breakout or fade
                  ``overnight``  break of the range formed outside the session
                  ``pdr``        break or fade of the prior session's range
  trend following ``donchian``   Donchian channel breakout
                  ``ma_cross``   fast/slow EMA crossover
  momentum        ``momentum``   intraday time-series momentum or fade
                  ``gap``        session open against the prior session close
  mean reversion  ``vwap``       distance from the running session VWAP
                  ``zscore``     close against a rolling mean, in sigmas

SYMBOLS. btcjpy, btckrw, ethusd, ethbtc, xrpusd, bchusd, dogeusd, solusd.

CLOCK. Every `*_1m` table in this database is New York wall-clock encoded as
fake UTC (AGENT.md); `btc_1m` was verified against NQ by minute-return
correlation and these eight are the same importer. Nothing here converts
timestamps.

SESSION. 09:30-16:00 New York, the equity session, matching
`btc_families_research` so the crypto results are comparable with the existing
BTC work. Crypto trades 24/7, so this deliberately discards ~73% of the bars;
that was a requested constraint, not a data limitation. Fourteen 30-minute
buckets, 09:30 through 16:00, with 16:00 the forced-flatten bucket.

QUOTE CURRENCY AND THE MULTIPLIER -- the part that is easy to get wrong.
Three of these pairs do not settle in dollars:

    btcjpy   P&L accrues in JPY      btckrw   P&L accrues in KRW
    ethbtc   P&L accrues in BTC

A $1,000 account is denominated in dollars, so a JPY-denominated point value
sized against a dollar risk budget is simply the wrong number -- by a factor of
~144 for JPY and ~1,413 for KRW. `fx_series` therefore converts every trade's
P&L, every notional cap and every risk-budget calculation into USD, using a
cross implied by the data already in this database rather than an external feed:

    USD per JPY  =  btc_1m close / btcjpy_1m close
    USD per KRW  =  btc_1m close / btckrw_1m close
    USD per BTC  =  btc_1m close

Sanity check on 2025-06: the implied USDJPY is 144.3 and the implied USDKRW is
1,413, both correct to within a fraction of a percent, which is what licenses
the method. It is a daily median of the minute ratio, so exchange-level
microstructure noise and the Upbit "kimchi premium"'s minute wobble do not leak
into P&L; the premium's slow drift does remain, and is a real residual
uncertainty of a few percent on the two BTC crosses.

CONTRACT AND STEP. Exness crypto CFDs are quoted with a contract size of one
unit of the base currency and a 0.01 lot minimum, so quantity is carried in
**base units** (BTC, ETH) and the step is 0.01. This assumption is only
load-bearing where it is also most confident: on btcjpy/btckrw 0.01 BTC is a
600-1,200 dollar position and the step genuinely binds a $1,000 account, which
is why `why` reports a balance ladder and fill rates. On dogeusd and xrpusd a
0.01-unit step is far below any position the risk budget would take, so the
exact contract size there cannot change a result.

COSTS. The requested 0.2 entry spread is an Exness BTCUSD quote, and these
eight instruments span nine orders of magnitude in price -- 0.03 for dogeusd to
132,000,000 for btckrw -- so 0.2 cannot be applied literally: it is 0.03bp on
btckrw and 11,900bp on dogeusd. The cost model is therefore **relative**: the
same 0.2-on-BTC fraction, expressed in basis points of the entry price, charged
wholly at entry on every symbol. `SPREAD_BPS` below derives it from the data.
That figure is very small, so `validate` re-runs every selected cell across
0/1/3/5/10/20bp and reports each one's break-even spread. Read those columns,
not the headline: [[maroy-momentum-dies-on-real-costs]] is exactly this failure.

BENCHMARKS ARE MANDATORY. Crypto rose enormously across the holdout, so a
long-biased rule posts a wonderful out-of-sample number for reasons that have
nothing to do with skill. `benchmarks()` reports buy-and-hold and a mechanical
always-long-intraday control for every window and every symbol --
[[zero-is-the-wrong-backtest-baseline]].

SEARCH BUDGET. ~71,000 cells per symbol across nine families, eight symbols,
so ~569,000 in-sample fits. That is an enormous multiple-testing burden and no
in-sample number here should be read without the `why` phase's coin-flip null,
which on USOIL manufactured +622% at t=4.19 from pure noise --
[[coin-flip-control-beats-real-signals]].
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
# Instrument-agnostic pieces, reused verbatim so crypto, gold and oil cannot
# drift: these read only `params` and `ctx`, never a session or contract
# constant. `orb` and `gap` are the two that do, and are redefined below.
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

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

TS, O, H, L, C, V = range(6)


# --------------------------------------------------------------------------- #
# instruments
# --------------------------------------------------------------------------- #

#: `quote` is the currency each pair's price is expressed in, which decides the
#: FX leg. `step` is the tradeable increment in base units. `warmup` is the
#: first date with enough history for the 100-session volatility window to be
#: warm by IS_START; dogeusd and solusd start later than the rest.
SYMBOLS = {
    "btcjpy":  {"quote": "JPY", "step": 0.01, "warmup": "2018-01-01"},
    "btckrw":  {"quote": "KRW", "step": 0.01, "warmup": "2018-01-01"},
    "ethusd":  {"quote": "USD", "step": 0.01, "warmup": "2018-01-01"},
    "ethbtc":  {"quote": "BTC", "step": 0.01, "warmup": "2018-01-01"},
    "xrpusd":  {"quote": "USD", "step": 0.01, "warmup": "2018-01-01"},
    "bchusd":  {"quote": "USD", "step": 0.01, "warmup": "2018-01-01"},
    "dogeusd": {"quote": "USD", "step": 0.01, "warmup": "2019-07-05"},
    "solusd":  {"quote": "USD", "step": 0.01, "warmup": "2020-04-10"},
    #: Not part of the eight-symbol study -- BTC was already covered by the
    #: `btc_families` and Maroy work. It is registered here only so the shared
    #: engine can run the compiled BTC Donchian cell as a book sleeve.
    "btc":     {"quote": "USD", "step": 0.01, "warmup": "2017-08-17"},
}

#: solusd has no 2019-2020 history and dogeusd none before 2019-07, so their
#: in-sample year sets are shorter and their positive-year gate scales with it.
SYMBOL_IS_START = {"solusd": 2021, "dogeusd": 2020}

SELECT_BALANCE = 10_000.0
REPORT_BALANCES = (10_000.0, 1_000.0)
MICRO_BALANCE = 1_000.0

#: Exness crypto CFD leverage is 1:200, i.e. a 0.005 margin fraction. The repo's
#: usual 0.25 is a self-imposed 4x cap from the `idk` environment, not a broker
#: limit, and on a $1,000 account it would bind long before the risk budget did
#: and turn this study into an arithmetic exercise -- see
#: [[thousand-dollar-account-is-margin-capped]].
MARGIN = 0.005

WARMUP_START = "2018-01-01"
IS_START = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())
FULL_IS_YEARS = tuple(range(2019, 2025))

#: New York minutes, matching btc_families_research.
SESSION_OPEN_MINUTE = 9 * 60 + 30       # 09:30
SESSION_CLOSE_MINUTE = 16 * 60          # 16:00, the flatten bucket

BARS_PER_SESSION = 14                   # 30-minute buckets, 09:30 through 16:00
ATR_BARS = 2 * BARS_PER_SESSION
VOLATILITY_BARS = 20 * BARS_PER_SESSION
LONG_VOLATILITY_BARS = 100 * BARS_PER_SESSION
#: Crypto trades every calendar day, so a year holds 365 sessions, not 252.
ANNUAL_PERIODS = 365.0 * BARS_PER_SESSION

TREND_PERIODS = {"ema_20d": 20 * BARS_PER_SESSION, "ema_50d": 50 * BARS_PER_SESSION}
ZSCORE_PERIODS = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 5 * BARS_PER_SESSION)
MA_FAST = (BARS_PER_SESSION, 2 * BARS_PER_SESSION)
MA_SLOW = (5 * BARS_PER_SESSION, 10 * BARS_PER_SESSION, 20 * BARS_PER_SESSION)

#: 14:00 and 15:00 New York; both leave at least two buckets before the flatten,
#: so a late entry is still a trade rather than an instant close.
LAST_ENTRY = (14 * 60, 15 * 60)

#: The requested drawdown budget is ~15%, so the selection band is built around
#: it rather than the 20-22% the oil and gold passes allowed. The floor exists
#: because a cell with almost no drawdown is almost always a cell with almost no
#: trades -- [[lot-granularity-fakes-low-drawdown]].
MIN_TRADES = 200
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 3.0
SELECTION_DD_LIMIT = 15.0
ANNUAL_DD_LIMIT = 15.0
NEIGHBOUR_DD_LIMIT = 18.0
MIN_FILL_RATE = 80.0


def is_years(symbol):
    """The in-sample years this symbol actually has data for."""
    start = SYMBOL_IS_START.get(symbol, 2019)
    return tuple(year for year in FULL_IS_YEARS if year >= start)


def min_positive_years(symbol):
    """Five of six, scaled down for the two symbols with shorter histories."""
    return max(3, len(is_years(symbol)) - 1)


# --------------------------------------------------------------------------- #
# FX: every P&L figure in this module is US dollars
# --------------------------------------------------------------------------- #


def _daily_close(table):
    """`{unix_day: mean close}` for `table`, one entry a calendar day."""
    sql = (f"SELECT cast(timestamp as long) d, avg(close) c FROM {table} "
           f"SAMPLE BY 1d ALIGN TO CALENDAR")
    key = f"{sql}:{data._table_fingerprint([table])}"

    def build():
        return {str(int(row[0]) // 1_000_000 // 86_400): float(row[1])
                for row in data.query(sql) if row[1] is not None}

    return {int(k): v for k, v in
            data._cached(f"{table}_daily_close", key, build).items()}


def fx_series(symbol):
    """`{unix_day: USD per unit of the quote currency}`, forward filled.

    USD pairs return an empty dict, which `fx_at` reads as a constant 1.0 --
    the common case costs nothing.
    """
    quote = SYMBOLS[symbol]["quote"]
    if quote == "USD":
        return {}
    btc = _daily_close("btc_1m")
    if quote == "BTC":
        return btc
    table = {"JPY": "btcjpy_1m", "KRW": "btckrw_1m"}[quote]
    other = _daily_close(table)
    return {day: btc[day] / other[day] for day in btc.keys() & other.keys()
            if other[day] > 0.0}


def fx_lookup(series, first_day, last_day):
    """A dense forward-filled list indexed by `day - first_day`.

    Built once per worker rather than per trade: the backtest loop touches this
    on every fill, and a dict miss with a fallback scan would dominate.
    `btc_1m` ends three days before the crypto tables, so the tail is carried
    forward rather than dropping those sessions.
    """
    if not series:
        return None, 0
    out = []
    last = None
    for day in range(first_day, last_day + 1):
        value = series.get(day)
        if value is not None and value > 0.0:
            last = value
        out.append(last)
    # Back-fill the head so an early session cannot see a None.
    seed = next((value for value in out if value is not None), 1.0)
    return [seed if value is None else value for value in out], first_day


def fx_at(lookup, base, ts):
    if lookup is None:
        return 1.0
    index = ts // 86_400 - base
    if index < 0:
        return lookup[0]
    if index >= len(lookup):
        return lookup[-1]
    return lookup[index]


# --------------------------------------------------------------------------- #
# costs
# --------------------------------------------------------------------------- #

#: The requested spread, in the units it was quoted in: 0.2 US dollars on
#: Exness BTCUSD. Converted to a relative figure against BTC's own price so it
#: can be charged on instruments priced from 0.03 to 132,000,000.
REQUESTED_SPREAD_USD = 0.2


def spread_bps():
    """`REQUESTED_SPREAD_USD` as basis points of BTC's mean holdout price."""
    btc = _daily_close("btc_1m")
    window = [value for day, value in btc.items()
              if IS_END // 86_400 <= day < OOS_END // 86_400]
    reference = statistics.fmean(window) if window else statistics.fmean(btc.values())
    return 1e4 * REQUESTED_SPREAD_USD / reference


#: Charged at entry, in basis points of the entry price. The cost sweep in
#: `validate` is the number that actually decides these candidates.
SPREAD_BPS = None       # resolved lazily by `default_spread_bps`
COST_SWEEP = (0.0, 1.0, 3.0, 5.0, 10.0, 20.0)


def default_spread_bps():
    global SPREAD_BPS
    if SPREAD_BPS is None:
        SPREAD_BPS = round(spread_bps(), 5)
    return SPREAD_BPS


#: Broker commission, USD per LOT per round trip, measured on the live Exness
#: terminal 2026-08-12 by round-tripping the minimum lot. Billed entirely at
#: entry, which is where this engine charges its cost already.
#:
#: `lot` is how many base units one MT5 lot is. It is 1 for the USD-quoted coins
#: and **100 for ETHBTC**, which is the same contract size that made the live
#: ETHBTC sleeve trade 100x its intended size -- see
#: `to_lot_volume` in `live_trade/src/live/portfolio.rs`.
#:
#: As basis points these are 1.4-6.4 bp, against the 0.008-0.67 bp that
#: `REQUESTED_SPREAD_USD` produces. Commission is 10-500x the cost this study
#: has been charging, so read every pre-2026-08-12 crypto result as gross of it.
COMMISSION_PER_LOT = {
    "btc": {"usd": 9.0, "lot": 1.0},
    "ethusd": {"usd": 1.0, "lot": 1.0},
    "ethbtc": {"usd": 120.0, "lot": 100.0},
}


def commission_bps(symbol, price, quote_rate=1.0):
    """Commission as basis points of one unit's entry price.

    `price` is in the symbol's quote currency and `quote_rate` converts one of
    that currency into USD -- 1.0 for the USD pairs, the BTC price for ETHBTC.
    Both are needed because commission is quoted in dollars while the engine
    charges cost as a fraction of a possibly non-USD price.
    """
    spec = COMMISSION_PER_LOT.get(symbol)
    if not spec or price <= 0 or quote_rate <= 0:
        return 0.0
    usd_per_unit = spec["usd"] / spec["lot"]
    return 1e4 * usd_per_unit / (price * quote_rate)


# --------------------------------------------------------------------------- #
# bars and context
# --------------------------------------------------------------------------- #


def all_bars_30m(symbol, phase):
    """Every 30-minute bucket from the warm-up start, oldest first.

    Out-of-session buckets are kept because the overnight and gap anchors are
    built from them; the traded list is filtered out of this one. The selector's
    SQL cannot return a holdout row.
    """
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    start = SYMBOLS[symbol]["warmup"]
    sql = (
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM {symbol}_1m "
        f"WHERE timestamp >= '{start}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    key = f"{sql}:{data._table_fingerprint([f'{symbol}_1m'])}"

    def build():
        return [[int(row[0]) // 1_000_000, *(float(value) for value in row[1:])]
                for row in data.query(sql)]

    scope = "is" if phase == "select" else "full"
    return [tuple(row) for row in
            data._cached(f"{symbol}_30m_{scope}", key, build)]


def in_session(ts):
    return SESSION_OPEN_MINUTE <= ts % 86_400 // 60 <= SESSION_CLOSE_MINUTE


def trailing_annual_volatility(bars, periods):
    """Causal realised volatility over the prior `periods` buckets, annualised."""
    returns = [0.0]
    for previous, current in zip(bars, bars[1:]):
        returns.append(math.log(current[C] / previous[C]) if previous[C] > 0 else 0.0)
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
    """Per session day: the out-of-session range, and the prior session close.

    The evening belongs to the session it precedes, so the "overnight" window
    for day D is 16:00 on D-1 through 09:30 on D.
    """
    outside = {}
    session_close = {}
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
            session_close[bar[TS] // 86_400] = bar[C]
    ordered = sorted(session_close)
    prior = {day: session_close[ordered[index - 1]]
             for index, day in enumerate(ordered) if index}
    return {day: tuple(value) for day, value in outside.items()}, prior


def prior_session_range(bars):
    """`{day: (high, low)}` of the *previous* traded session."""
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


def context(symbol, phase):
    full = all_bars_30m(symbol, phase)
    bars = [bar for bar in full if in_session(bar[TS])]
    closes = [bar[C] for bar in bars]
    outside, prior = session_anchors(full)
    channels = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION)
    means, sigmas = {}, {}
    for period in ZSCORE_PERIODS:
        means[period], sigmas[period] = rolling_mean_sigma(closes, period)
    short = trailing_annual_volatility(bars, VOLATILITY_BARS)
    long = trailing_annual_volatility(bars, LONG_VOLATILITY_BARS)
    lookup, base = fx_lookup(fx_series(symbol),
                             bars[0][TS] // 86_400, bars[-1][TS] // 86_400)
    ctx = {
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
        # "Calm" is short-horizon volatility under its own long-horizon level:
        # self-referential, so it needs no external index, and causal on both legs.
        "calm": [None if s is None or l is None or l <= 0.0 else s < l
                 for s, l in zip(short, long)],
        "fx": lookup,
        "fx_base": base,
        "step": SYMBOLS[symbol]["step"],
    }
    # The volatility throttle has to be per symbol. A single 0.6 target, as the
    # BTC pass used, would leave ethbtc untouched and clamp dogeusd on almost
    # every bar, so the "same" sizing rule would mean different things on
    # different instruments. The target is the median of the symbol's own
    # in-sample realised volatility -- measured on 2019-2024 only, never the
    # holdout.
    sample = [value for value, bar in zip(short, bars)
              if value is not None and IS_START <= bar[TS] < IS_END]
    ctx["vol_target"] = statistics.median(sample) if sample else 0.6
    return bars, ctx


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
                 "signal_minute": (10 * 60, 11 * 60 + 30, 13 * 60),
                 "lookback": (4, 14, 28), "threshold_atr": (0.5, 1.0, 1.5), **COMMON},
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
# redefined here, against 09:30 rather than oil's 09:00.
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


def quantity(mode, equity, price, stop_distance, realized, ctx, fx, margin=MARGIN):
    """Base units to trade under `mode`, floored to the lot step.

    `equity`, and therefore the risk budget, is in **US dollars**; `price` and
    `stop_distance` arrive in the pair's quote currency. `fx` reconciles the
    two. Getting this wrong is a factor-of-1,400 error on btckrw, not a rounding
    difference.
    """
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0 or fx <= 0.0:
        return 0.0
    step = ctx["step"]
    price_usd = price * fx
    ceiling = equity / margin / price_usd
    if mode == "flat":
        return step if ceiling >= step else 0.0
    family, level = mode.split("_")
    if family == "notional":
        raw = equity * float(level.rstrip("x")) / price_usd
    else:
        fraction = float(level.rstrip("pct")) / 100.0
        if family == "riskvol" and realized and realized > 0.0:
            fraction *= min(1.0, ctx["vol_target"] / realized)
        raw = equity * fraction / (stop_distance * fx)
    return max(0.0, math.floor(min(raw, ceiling) / step) * step)


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
             initial=SELECT_BALANCE, spread_bp=None, size_mode=DEFAULT_SIZE,
             null_seed=None, margin=MARGIN):
    """Signals to sized trades over `[lo, hi)`, with every figure in US dollars.

    Ordering matches the Rust strategies and the ES/BTC/oil/gold passes: the
    session flatten is checked first, then the stop, then the target, then the
    time stop; a pending entry fills at the next bar's open; at most one
    position is open at a time and at most one trade is taken per session.

    Two things differ from the single-instrument passes, both forced by this
    universe rather than chosen:

      * the spread is **relative** (`spread_bp` basis points of the entry
        price), because a fixed price-unit cost is meaningless across nine
        orders of magnitude of price;
      * every P&L, notional and risk figure crosses `ctx["fx"]` into dollars.
    """
    if spread_bp is None:
        spread_bp = default_spread_bps()
    step = ctx["step"]
    fx_table, fx_base = ctx["fx"], ctx["fx_base"]
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
                points = gross - position["spread"]
                pnl = points * position["quantity"] * position["fx"]
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": position["ts"], "exit_ts": ts, "side": side,
                               "points": points, "gross": gross, "pnl": pnl,
                               "atr": position["atr"], "entry": position["entry"],
                               "bars": index - position["index"],
                               "quantity": position["quantity"], "reason": reason})
                position = None

        if position is None and pending is not None:
            # A missing bucket can jump the session boundary; never carry a
            # pending entry out of the window that formed it.
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                fx = fx_at(fx_table, fx_base, ts)
                amount = quantity(size_mode, equity, bar[O], pending["distance"],
                                  pending["realized"], ctx, fx, margin)
                if amount >= step:
                    side, entry = pending["side"], bar[O]
                    target, max_bars, trail = exit_plan(params["exit_mode"],
                                                        pending["distance"])
                    position = {
                        "side": side, "entry": entry, "ts": ts, "index": index,
                        "quantity": amount, "atr": pending["atr"], "fx": fx,
                        "spread": entry * spread_bp / 1e4,
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
            # The null control keeps *when* the rule fires and replaces only
            # *which way* it bets, so trade count, timing, stop width and exit
            # logic are untouched.
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

    # Which side the money came from. The 2025-2026 holdout FELL on every USD
    # pair here -- ethusd -43%, xrpusd -52%, dogeusd -78%, solusd -62% -- so a
    # short-biased rule collects the decline and posts a fine holdout number for
    # exactly the non-skill reason a long-biased rule would have posted one in a
    # bull market. This is the same hazard as [[zero-is-the-wrong-backtest-baseline]]
    # with the sign flipped, and it is invisible in a headline return.
    if trades:
        longs = [t for t in trades if t["side"] == 1]
        shorts = [t for t in trades if t["side"] == -1]
        result["long_share"] = round(len(longs) / len(trades), 3)
        result["long_pnl"] = round(sum(t["pnl"] for t in longs), 2)
        result["short_pnl"] = round(sum(t["pnl"] for t in shorts), 2)
        result["long_gross_bp"] = (
            round(statistics.fmean([1e4 * t["gross"] / t["entry"] for t in longs]), 3)
            if longs else 0.0)
        result["short_gross_bp"] = (
            round(statistics.fmean([1e4 * t["gross"] / t["entry"] for t in shorts]), 3)
            if shorts else 0.0)

        # The side-mix drift control, and the number that decides whether a
        # holdout result is skill or just the bear market being harvested by a
        # short-biased rule.
        #
        # `mu` is the mean per-bucket drift of the instrument itself over the
        # same window. A mechanical rule holding the same sides for the same
        # number of buckets, with no predictive content whatsoever, earns
        # `side * bars_held * mu` a trade. `edge_vs_drift_bp` is what the rule
        # earned ABOVE that. A rule whose entire gross edge is drift shows
        # roughly zero here regardless of how large its headline return is.
        window = [bar for bar in bars if lo <= bar[TS] < hi]
        moves = [1e4 * (b[C] - a[C]) / a[C] for a, b in zip(window, window[1:])
                 if a[C] > 0.0]
        mu = statistics.fmean(moves) if moves else 0.0
        drift = statistics.fmean([t["side"] * t["bars"] * mu for t in trades])
        result["drift_bp_per_trade"] = round(drift, 4)
        result["mean_bars_held"] = round(
            statistics.fmean([t["bars"] for t in trades]), 2)

    gross = [trade["gross"] for trade in trades]
    if len(gross) > 1:
        # Quote-currency points are not comparable across symbols, and barely
        # comparable across years within one of them: dogeusd's 30-minute bar
        # is 0.0004 and btckrw's is 200,000. Every edge figure below is
        # therefore normalised, in the two ways that are price- and
        # volatility-neutral:
        #
        #   *_atr  gross in units of the ATR the trade was sized against
        #   *_bp   gross in basis points of the entry price -- which makes
        #          `gross_bp_per_trade` directly the break-even spread in the
        #          same units the cost model charges.
        in_atr = [t["gross"] / t["atr"] for t in trades if t["atr"]]
        in_bp = [1e4 * t["gross"] / t["entry"] for t in trades if t["entry"]]
        if len(in_atr) > 1:
            atr_sd = statistics.stdev(in_atr)
            result["gross_atr_per_trade"] = round(statistics.fmean(in_atr), 4)
            result["gross_atr_t_stat"] = (
                round(statistics.fmean(in_atr) / (atr_sd / math.sqrt(len(in_atr))), 2)
                if atr_sd else 0.0)
        if len(in_bp) > 1:
            bp_sd = statistics.stdev(in_bp)
            result["gross_bp_per_trade"] = round(statistics.fmean(in_bp), 4)
            result["breakeven_spread_bp"] = round(statistics.fmean(in_bp), 4)
            result["gross_bp_t_stat"] = (
                round(statistics.fmean(in_bp) / (bp_sd / math.sqrt(len(in_bp))), 2)
                if bp_sd else 0.0)
            excess = [value - t["side"] * t["bars"] * mu
                      for value, t in zip(in_bp, trades) if t["entry"]]
            excess_sd = statistics.stdev(excess)
            result["edge_vs_drift_bp"] = round(statistics.fmean(excess), 4)
            result["edge_vs_drift_t_stat"] = (
                round(statistics.fmean(excess) / (excess_sd / math.sqrt(len(excess))), 2)
                if excess_sd else 0.0)
        result["spread_bp"] = round(spread_bp, 4)
    return result


# --------------------------------------------------------------------------- #
# benchmarks -- mandatory reading on this universe
# --------------------------------------------------------------------------- #


def benchmarks(bars, ctx, lo, hi, initial=SELECT_BALANCE, spread_bp=None):
    """What the symbol gives you with no strategy at all.

    Crypto rose enormously across the holdout, so an intraday rule that ends the
    window up is not thereby good -- it has to beat these. `always_long` is the
    one that matters: buy every session open, flatten every session close,
    paying the same spread. It isolates how much of a long-biased result is
    simply drift showing up inside the session.

    The position is a constant **one-times-initial-balance notional**, not one
    lot step. A fixed step is not comparable across this universe: 0.01 BTC is a
    600 dollar position and 0.01 DOGE is two hundredths of a cent, so a
    step-sized control reports gold-plated numbers on btckrw and exactly zero on
    dogeusd -- as the first probe run of this module did. Constant notional is
    also un-compounded, so the control measures drift rather than a compounding
    schedule.
    """
    if spread_bp is None:
        spread_bp = default_spread_bps()
    window = [bar for bar in bars if lo <= bar[TS] < hi]
    if len(window) < 2:
        return {}
    hold = 100.0 * (window[-1][C] - window[0][O]) / window[0][O]

    equity = peak = initial
    drawdown = 0.0
    trades = 0
    gross_bp = []
    day = None
    entry = None
    for index, bar in enumerate(window):
        current = bar[TS] // 86_400
        minute = bar[TS] % 86_400 // 60
        if entry is not None and (current != day or minute >= SESSION_CLOSE_MINUTE):
            price, amount, fx = entry
            gross = bar[O] - price
            equity += (gross - price * spread_bp / 1e4) * amount * fx
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 1.0)
            gross_bp.append(1e4 * gross / price)
            trades += 1
            entry = None
        if entry is None and minute < SESSION_CLOSE_MINUTE and index + 1 < len(window):
            fx = fx_at(ctx["fx"], ctx["fx_base"], bar[TS])
            entry = (bar[O], initial / (bar[O] * fx), fx)
            day = current
    return {
        "buy_and_hold_pct": round(hold, 2),
        "always_long_return_pct": round(100.0 * (equity - initial) / initial, 2),
        "always_long_max_dd_pct": round(100.0 * drawdown, 2),
        "always_long_trades": trades,
        "always_long_gross_bp_per_trade": (
            round(statistics.fmean(gross_bp), 4) if gross_bp else 0.0),
    }


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def passes(symbol, stat, limit=SELECTION_DD_LIMIT, annual_limit=ANNUAL_DD_LIMIT):
    annual = stat["annual"]
    years = is_years(symbol)
    positive = sum(1 for year in years
                   if annual.get(str(year), {}).get("pnl", 0.0) > 0.0)
    worst = max((annual.get(str(year), {}).get("max_dd_pct", 100.0)
                 for year in years), default=100.0)
    return (stat["trades"] >= MIN_TRADES
            and stat["pf"] >= MIN_PROFIT_FACTOR
            and stat["max_dd_pct"] <= limit
            and positive >= min_positive_years(symbol)
            and worst <= annual_limit)


def quality(symbol, stat):
    """Compounded growth, rewarded for the worst year and penalised for spread.

    The requested property is consistency, not peak return, so the worst
    in-sample year enters at full weight and the dispersion across years is
    subtracted. Identical to the oil and gold objective so the instruments stay
    comparable.
    """
    if not passes(symbol, stat):
        return -math.inf
    returns = [stat["annual"].get(str(year), {}).get("return_pct", 0.0)
               for year in is_years(symbol)]
    return (100.0 * math.log(stat["final"] / SELECT_BALANCE)
            + min(returns) + 0.25 * statistics.median(returns)
            - 0.5 * statistics.pstdev(returns))


def neighbours(params, axes):
    """One-step perturbations along the numeric axes only."""
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


def select_family(symbol, family, results):
    axes = AXES[family]
    universe = candidates(axes)
    ranked = []
    for params in universe:
        stat = results[es.frozen(params)]
        own = quality(symbol, stat)
        if not math.isfinite(own):
            continue
        if not SELECTION_DD_FLOOR <= stat["max_dd_pct"] <= SELECTION_DD_LIMIT:
            continue
        # Eligibility on the $1,000 account, decided in sample only. Reading a
        # workable stop width off the holdout would be selection on the test set.
        if stat.get("fill_rate_micro", 0.0) < MIN_FILL_RATE:
            continue
        # A regime filter must refine an already profitable unfiltered cell,
        # never rescue a broken one.
        bare = results.get(es.frozen({**params, "vol_mode": "none", "trend": "none"}))
        if bare is None or bare["pnl"] <= 0.0 or bare["pf"] < 1.0:
            continue
        nearby = [results[es.frozen(item)] for item in neighbours(params, axes)]
        robust = [item for item in nearby
                  if passes(symbol, item, limit=NEIGHBOUR_DD_LIMIT)]
        if not nearby or len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(symbol, item) for item in robust if passes(symbol, item)]
        if not strict:
            continue
        ranked.append((own, params, stat, statistics.median(strict),
                       len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "plateau_score": round(plateau, 6),
            "robust_neighbours": f"{robust}/{total}"}


# --------------------------------------------------------------------------- #
# parallel evaluation
# --------------------------------------------------------------------------- #

_WORKER = {}


def _init_worker(symbol, phase):
    _WORKER["bars"], _WORKER["ctx"] = context(symbol, phase)
    _WORKER["symbol"] = symbol


def _evaluate_cell(job):
    family, params, spread_bp, null_seed = job
    bars, ctx = _WORKER["bars"], _WORKER["ctx"]
    stat = backtest(family, bars, ctx, params, spread_bp=spread_bp,
                    null_seed=null_seed)
    small = backtest(family, bars, ctx, params, initial=MICRO_BALANCE,
                     spread_bp=spread_bp, null_seed=null_seed)
    stat["fill_rate_micro"] = (round(100.0 * small["trades"] / stat["trades"], 1)
                               if stat["trades"] else 0.0)
    stat["micro_return_pct"] = small["return_pct"]
    stat["micro_max_dd_pct"] = small["max_dd_pct"]
    stat["micro_trades"] = small["trades"]
    return es.frozen(params), stat


def evaluate_all(family, pool, spread_bp, null_seed=None):
    universe = candidates(AXES[family])
    jobs = [(family, params, spread_bp, null_seed) for params in universe]
    results = {}
    for key, stat in pool.imap_unordered(_evaluate_cell, jobs, chunksize=32):
        results[key] = stat
    return results


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #


def output_path(symbol):
    return os.path.join(RESULTS, f"crypto_families_{symbol}.json")


def seal(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(symbol, workers, spread_bp):
    started = datetime.now()
    with multiprocessing.Pool(workers, _init_worker, (symbol, "select")) as pool:
        families = {}
        for family in AXES:
            families[family] = select_family(
                symbol, family, evaluate_all(family, pool, spread_bp))
            mark = "-" if families[family] is None else (
                f"{families[family]['in_sample']['return_pct']:+.1f}% "
                f"dd {families[family]['in_sample']['max_dd_pct']:.1f}%")
            print(f"  {symbol:<8} {family:<10} {mark}", flush=True)
    bars, ctx = context(symbol, "select")
    payload = {
        "sealed": True,
        "symbol": symbol,
        "protocol": {
            "bars": f"{symbol}_1m causally aggregated to 30m; out-of-session "
                    "buckets used only for the overnight and gap anchors",
            "clock": "New York wall-clock encoded as fake UTC; nothing converts",
            "session": "09:30-16:00 New York; entries inside it only, forced "
                       "flatten at the close",
            "in_sample": f"{is_years(symbol)[0]}-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-08-06, untouched by selection",
            "candidate_counts": {f: len(candidates(AXES[f])) for f in AXES},
            "selection_gate": (
                f">={min_positive_years(symbol)} of {len(is_years(symbol))} "
                f"in-sample years profitable; selected DD {SELECTION_DD_FLOOR}-"
                f"{SELECTION_DD_LIMIT}%; annual DD <={ANNUAL_DD_LIMIT}%; "
                f">={MIN_TRADES} trades; PF >={MIN_PROFIT_FACTOR}; regime filters "
                "must refine an already profitable unfiltered cell; >=60% of "
                f"numeric neighbours robust; >={MIN_FILL_RATE}% of signals "
                f"fillable on ${MICRO_BALANCE:,.0f} in sample"),
            "sizing": f"{DEFAULT_SIZE} against a per-symbol volatility target of "
                      f"{ctx['vol_target']:.3f} (in-sample median realised "
                      f"volatility), floored to the {SYMBOLS[symbol]['step']} "
                      f"base-unit step inside {MARGIN:.4f} margin (Exness 1:200)",
            "quote_currency": SYMBOLS[symbol]["quote"],
            "fx": "P&L, notional and risk are US dollars; non-USD quotes cross "
                  "via a daily-median rate implied by btc_1m",
            "select_balance": SELECT_BALANCE,
            "report_balances": list(REPORT_BALANCES),
            "entry_spread_bp": spread_bp,
            "entry_spread_note": (
                f"{REQUESTED_SPREAD_USD} USD on Exness BTCUSD expressed "
                "relatively; see the cost sweep in `validate`"),
            "benchmarks": {
                "in_sample": benchmarks(bars, ctx, IS_START, IS_END,
                                        spread_bp=spread_bp)},
        },
        "families": families,
    }
    seal(payload, output_path(symbol))
    print(f"  {symbol}: sealed in {(datetime.now() - started).seconds}s -> "
          f"{output_path(symbol)}", flush=True)


def validate(symbol, spread_bp):
    bars, ctx = context(symbol, "validate")
    path = output_path(symbol)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    payload.pop("holdout_benchmarks", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit(f"{symbol} selection seal mismatch; rerun select first")

    control = benchmarks(bars, ctx, IS_END, OOS_END, spread_bp=spread_bp)
    print(f"\n=== {symbol.upper()} HOLDOUT 2025-2026 ===")
    print(f"benchmark: {json.dumps(control, sort_keys=True)}")

    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        entry = {}
        for balance in REPORT_BALANCES:
            stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                            initial=balance, spread_bp=spread_bp)
            entry[f"oos_{balance:.0f}"] = stat
        # The cost sweep is what actually decides these candidates: the headline
        # spread is a fraction of a basis point, so a cell that only works there
        # is not deployable at any real broker quote.
        sweep = {}
        for level in COST_SWEEP:
            inside = backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
                              initial=SELECT_BALANCE, spread_bp=level)
            outside = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                               initial=SELECT_BALANCE, spread_bp=level)
            sweep[f"{level:.1f}"] = {
                "is_return_pct": inside["return_pct"], "is_pf": inside["pf"],
                "oos_return_pct": outside["return_pct"], "oos_pf": outside["pf"],
                "oos_max_dd_pct": outside["max_dd_pct"]}
        entry["cost_sweep_bp"] = sweep
        validation[family] = entry

        small = entry[f"oos_{MICRO_BALANCE:.0f}"]
        big = entry[f"oos_{SELECT_BALANCE:.0f}"]
        print(f"\n{family}: IS {winner['in_sample']['return_pct']:+.1f}% "
              f"dd {winner['in_sample']['max_dd_pct']:.1f}% "
              f"| OOS@10k {big['return_pct']:+.1f}% dd {big['max_dd_pct']:.1f}% "
              f"n={big['trades']} PF {big['pf']:.2f} "
              f"| OOS@1k {small['return_pct']:+.1f}% dd {small['max_dd_pct']:.1f}%")
        print(f"  break-even spread {big.get('breakeven_spread_bp', 0):.3f}bp; "
              "cost sweep OOS return% " + ", ".join(
                  f"{k}bp->{v['oos_return_pct']:+.0f}" for k, v in sweep.items()))

    payload["seal_sha256"] = expected
    payload["validation"] = validation
    payload["holdout_benchmarks"] = control
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def shortlist(symbol, top):
    """The `top` promotable families for `symbol`, best holdout Sharpe first.

    Reads the same report rows the promotion rule uses, minus its coin-flip
    clause -- which is the clause this shortlist exists to feed. Nulling only
    the cells that could actually be recommended costs a fraction of the full
    grid: one family for three seeds rather than nine.
    """
    path = output_path(symbol)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    ranked = []
    for family, winner in payload["families"].items():
        entry = payload.get("validation", {}).get(family)
        if winner is None or entry is None:
            continue
        if _verdict(entry, None):        # None: ignore the null clause here
            continue
        ranked.append((entry["oos_10000"]["monthly_sharpe"], family))
    ranked.sort(reverse=True)
    return [family for _score, family in ranked[:top]]


def why(symbol, workers, spread_bp, families=None):
    """The coin-flip null: the same selection, with trade direction randomised.

    On USOIL this scored +622% in sample at t=4.19, proving the machine
    manufactures winners from noise at this search budget. A crypto family is
    only interesting if it beats what randomness scores through the same
    machine -- [[coin-flip-control-beats-real-signals]].

    `families` restricts the sweep. The full nine-family run also answers a
    second question -- whether the *pass rate* itself is noise -- but at three
    seeds a sweep it costs ~37 minutes a symbol, and choosing what to trade only
    needs the shortlisted cells.
    """
    bars, ctx = context(symbol, "validate")
    with open(output_path(symbol), encoding="utf-8") as handle:
        payload = json.load(handle)
    live = [f for f, w in payload["families"].items() if w is not None]
    if families is not None:
        live = [f for f in live if f in families]
    if not live:
        print(f"{symbol}: no family survived selection; nothing to null-test")
        return

    rows = {}
    with multiprocessing.Pool(workers, _init_worker, (symbol, "select")) as pool:
        for family in live:
            real = payload["families"][family]
            oos = payload.get("validation", {}).get(family, {}).get("oos_10000", {})
            print(f"\n{symbol} {family:<10} real   IS {real['in_sample']['return_pct']:+9.2f}%  "
                  f"g/ATR {real['in_sample'].get('gross_atr_per_trade', 0):+.4f}  "
                  f"OOS {oos.get('return_pct', float('nan')):+9.2f}%")
            rows[family] = []
            for seed in (1, 2, 3):
                chosen = select_family(
                    symbol, family,
                    evaluate_all(family, pool, spread_bp, null_seed=seed))
                if chosen is None:
                    print(f"{symbol} {family:<10} flip{seed}  no cell cleared the gates")
                    rows[family].append(None)
                    continue
                outside = backtest(family, bars, ctx, dict(chosen["params"]),
                                   lo=IS_END, hi=OOS_END, spread_bp=spread_bp,
                                   null_seed=seed)
                print(f"{symbol} {family:<10} flip{seed} IS "
                      f"{chosen['in_sample']['return_pct']:+9.2f}%  "
                      f"g/ATR {chosen['in_sample'].get('gross_atr_per_trade', 0):+.4f}  "
                      f"OOS {outside['return_pct']:+9.2f}%")
                rows[family].append({"params": chosen["params"],
                                     "in_sample": chosen["in_sample"],
                                     "out_of_sample": outside})

    # Merge rather than overwrite: a narrowed re-run must not discard families
    # an earlier full-grid run already paid for.
    destination = os.path.join(RESULTS, f"crypto_null_{symbol}.json")
    merged = {}
    if os.path.exists(destination):
        with open(destination, encoding="utf-8") as handle:
            merged = json.load(handle).get("null_control", {})
    merged.update(rows)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"symbol": symbol, "spread_bp": spread_bp,
                   "null_control": merged}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

#: The promotion rule, fixed here BEFORE any holdout number was read, so that
#: "best per symbol" cannot quietly become "whichever cell happened to win".
#: Every clause is a holdout measurement except the last.
#:
#:   1. positive out of sample at the requested spread AND at a realistic 5bp
#:   2. holdout drawdown within the requested budget plus slack
#:   3. holdout profit factor above the same floor selection used
#:   4. break-even spread comfortably above a real broker quote
#:   5. enough holdout trades for the mean to mean anything
#:   6. it must beat the coin-flip null's median holdout return
PROMOTE_MIN_OOS_RETURN = 0.0
PROMOTE_COST_BP = 5.0
PROMOTE_MAX_OOS_DD = 20.0
PROMOTE_MIN_OOS_PF = 1.05
PROMOTE_MIN_BREAKEVEN_BP = 5.0
PROMOTE_MIN_OOS_TRADES = 60
#: Clause 7, added after the first validate run showed the holdout is a bear
#: market on every USD pair: the edge has to survive subtracting the drift a
#: same-side-mix rule would have collected for free.
PROMOTE_MIN_EDGE_VS_DRIFT_BP = 2.0


def _null_median(symbol, family):
    """Median holdout return of the three coin-flip runs, or None if absent."""
    path = os.path.join(RESULTS, f"crypto_null_{symbol}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)["null_control"].get(family) or []
    got = [row["out_of_sample"]["return_pct"] for row in rows if row]
    return statistics.median(got) if got else None


def _verdict(entry, null):
    """Why a candidate is or is not promotable, as a list of failed clauses."""
    big = entry["oos_10000"]
    at_cost = entry["cost_sweep_bp"][f"{PROMOTE_COST_BP:.1f}"]
    failed = []
    if big["return_pct"] <= PROMOTE_MIN_OOS_RETURN:
        failed.append("negative out of sample")
    if at_cost["oos_return_pct"] <= PROMOTE_MIN_OOS_RETURN:
        failed.append(f"dies at {PROMOTE_COST_BP:.0f}bp")
    if big["max_dd_pct"] > PROMOTE_MAX_OOS_DD:
        failed.append(f"holdout dd {big['max_dd_pct']:.0f}%")
    if big["pf"] < PROMOTE_MIN_OOS_PF:
        failed.append(f"holdout PF {big['pf']:.2f}")
    if big.get("breakeven_spread_bp", 0.0) < PROMOTE_MIN_BREAKEVEN_BP:
        failed.append(f"break-even {big.get('breakeven_spread_bp', 0):.1f}bp")
    if big["trades"] < PROMOTE_MIN_OOS_TRADES:
        failed.append(f"only {big['trades']} holdout trades")
    if big.get("edge_vs_drift_bp", 0.0) < PROMOTE_MIN_EDGE_VS_DRIFT_BP:
        failed.append(f"edge is drift ({big.get('edge_vs_drift_bp', 0):.1f}bp net)")
    if null is not None and big["return_pct"] <= null:
        failed.append(f"loses to coin flip ({null:+.0f}%)")
    return failed


def report(symbols):
    """Every surviving candidate, ranked, with the holdout evidence beside it."""
    rows = []
    for symbol in symbols:
        path = output_path(symbol)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        control = payload.get("holdout_benchmarks", {})
        for family, winner in payload["families"].items():
            entry = payload.get("validation", {}).get(family)
            if winner is None or entry is None:
                continue
            big, small = entry["oos_10000"], entry["oos_1000"]
            null = _null_median(symbol, family)
            failed = _verdict(entry, null)
            rows.append({
                "symbol": symbol, "family": family,
                "params": winner["params"],
                "is_return_pct": winner["in_sample"]["return_pct"],
                "is_max_dd_pct": winner["in_sample"]["max_dd_pct"],
                "is_pf": winner["in_sample"]["pf"],
                "oos_return_pct": big["return_pct"],
                "oos_max_dd_pct": big["max_dd_pct"],
                "oos_pf": big["pf"], "oos_trades": big["trades"],
                "oos_monthly_sharpe": big["monthly_sharpe"],
                "oos_positive_months": f"{big['positive_months']}/{big['n_months']}",
                "oos_1000_return_pct": small["return_pct"],
                "oos_1000_max_dd_pct": small["max_dd_pct"],
                "oos_1000_trades": small["trades"],
                "breakeven_bp": big.get("breakeven_spread_bp", 0.0),
                "oos_long_share": big.get("long_share"),
                "oos_gross_bp": big.get("gross_bp_per_trade", 0.0),
                "oos_drift_bp": big.get("drift_bp_per_trade", 0.0),
                "oos_edge_vs_drift_bp": big.get("edge_vs_drift_bp", 0.0),
                "oos_edge_vs_drift_t": big.get("edge_vs_drift_t_stat", 0.0),
                "oos_return_at_5bp": entry["cost_sweep_bp"]["5.0"]["oos_return_pct"],
                "null_median_oos": null,
                "benchmark_always_long_oos": control.get("always_long_return_pct"),
                "benchmark_buy_and_hold_oos": control.get("buy_and_hold_pct"),
                "promotable": not failed,
                "failed_clauses": failed,
            })

    header = (f"{'symbol':<8} {'family':<10} {'IS ret%':>9} {'OOS ret%':>9} "
              f"{'OOS dd%':>8} {'PF':>6} {'n':>5} {'mSh':>6} {'+mo':>7} "
              f"{'be bp':>7} {'@5bp%':>8} {'long%':>6} {'gross':>7} {'drift':>7} "
              f"{'edge':>7} {'t':>6}  verdict")
    print("\n" + header)
    print("-" * len(header))
    for row in sorted(rows, key=lambda r: (r["symbol"], -r["oos_return_pct"])):
        print(f"{row['symbol']:<8} {row['family']:<10} "
              f"{row['is_return_pct']:>9.1f} "
              f"{row['oos_return_pct']:>9.1f} {row['oos_max_dd_pct']:>8.1f} "
              f"{row['oos_pf']:>6.2f} {row['oos_trades']:>5} "
              f"{row['oos_monthly_sharpe']:>6.2f} {row['oos_positive_months']:>7} "
              f"{row['breakeven_bp']:>7.1f} {row['oos_return_at_5bp']:>8.1f} "
              f"{100 * (row['oos_long_share'] or 0):>5.0f}% "
              f"{row['oos_gross_bp']:>7.1f} {row['oos_drift_bp']:>7.1f} "
              f"{row['oos_edge_vs_drift_bp']:>7.1f} {row['oos_edge_vs_drift_t']:>6.2f}  "
              f"{'PROMOTABLE' if row['promotable'] else '; '.join(row['failed_clauses'])}")

    print("\nBEST PER SYMBOL (promotable only, ranked by holdout monthly Sharpe):")
    for symbol in symbols:
        live = [r for r in rows if r["symbol"] == symbol and r["promotable"]]
        if not live:
            blocked = [r for r in rows if r["symbol"] == symbol]
            reason = ("no family cleared selection" if not blocked
                      else "every surviving family failed the promotion rule")
            print(f"  {symbol:<8} -- {reason}")
            continue
        best = max(live, key=lambda r: r["oos_monthly_sharpe"])
        print(f"  {symbol:<8} {best['family']:<10} "
              f"OOS {best['oos_return_pct']:+.1f}% dd {best['oos_max_dd_pct']:.1f}% "
              f"PF {best['oos_pf']:.2f} mSharpe {best['oos_monthly_sharpe']:.2f} "
              f"| $1,000: {best['oos_1000_return_pct']:+.1f}% "
              f"dd {best['oos_1000_max_dd_pct']:.1f}% "
              f"({best['oos_1000_trades']} trades)")

    destination = os.path.join(RESULTS, "crypto_families_report.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"promotion_rule": {
            "min_oos_return_pct": PROMOTE_MIN_OOS_RETURN,
            "must_survive_cost_bp": PROMOTE_COST_BP,
            "max_oos_dd_pct": PROMOTE_MAX_OOS_DD,
            "min_oos_pf": PROMOTE_MIN_OOS_PF,
            "min_breakeven_bp": PROMOTE_MIN_BREAKEVEN_BP,
            "min_oos_trades": PROMOTE_MIN_OOS_TRADES,
            "must_beat_coin_flip_null": True},
            "candidates": rows}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate", "why", "probe",
                                          "report"))
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--spread-bp", type=float, default=None)
    parser.add_argument("--shortlist", type=int, default=None,
                        help="null-test only the top N promotable families a "
                             "symbol, instead of all nine")
    args = parser.parse_args()
    spread_bp = args.spread_bp if args.spread_bp is not None else default_spread_bps()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"entry spread {spread_bp:.5f}bp "
          f"({REQUESTED_SPREAD_USD} USD on BTC, expressed relatively)")
    if args.phase == "report":
        report(symbols)
        return
    print(f"candidate cells per symbol: "
          f"{sum(len(candidates(a)) for a in AXES.values()):,} across "
          f"{len(AXES)} families; {args.workers} workers")
    for symbol in symbols:
        if args.phase == "select":
            select(symbol, args.workers, spread_bp)
        elif args.phase == "validate":
            validate(symbol, spread_bp)
        elif args.phase == "why":
            picked = (shortlist(symbol, args.shortlist)
                      if args.shortlist else None)
            if args.shortlist:
                print(f"{symbol}: null-testing {picked or 'nothing (no promotable cell)'}")
            why(symbol, args.workers, spread_bp, picked)
        else:
            bars, ctx = context(symbol, "select")
            span = (datetime.fromtimestamp(bars[0][TS], tz=timezone.utc),
                    datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc))
            print(f"{symbol:<8} {len(bars):>7,} session bars "
                  f"{span[0]:%Y-%m-%d}..{span[1]:%Y-%m-%d}  "
                  f"quote {SYMBOLS[symbol]['quote']}  "
                  f"vol_target {ctx['vol_target']:.3f}  "
                  f"benchmark {json.dumps(benchmarks(bars, ctx, IS_START, IS_END))}")


if __name__ == "__main__":
    main()
