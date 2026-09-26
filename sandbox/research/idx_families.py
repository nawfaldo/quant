"""Family study for IDX stocks on a Stockbit account, in the style of `cfd_families`.

    py -B -m sandbox.research.idx_families run --symbols all
    py -B -m sandbox.research.idx_families select --symbols tlkm,adro
    py -B -m sandbox.research.idx_families validate --symbols tlkm
    py -B -m sandbox.research.idx_families budget
    py -B -m sandbox.research.idx_families full --symbols all
    py -B -m sandbox.research.idx_families account            # one Rp1jt account

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE: terminal output does
not reach the user. Paste the table into the reply; `report` also writes it to
`results/IDX_FAMILY_STUDY_<date>.md`.

WHAT IS DIFFERENT FROM THE EXNESS STUDY, AND WHY.

* Long only, whole lots, multi-day to multi-week holds. Retail IDX accounts
  cannot short, and a 0.40% round trip (0.15% buy, 0.25% sell including the
  0.1% sales tax) rules out anything that trades daily.
* Daily bars, built from the `idx_<code>_4h` tables (two bars a session, 09:00
  and 13:00 Jakarta). TradingView serves 4h back to 2018; 1h reaches only 2023.
  The session in progress is dropped.
* Every fill is the one `idx_stockbit_backtest` would get: a signal on day i's
  close fills at day i+1's open one tick worse, refused (and retried next day)
  if that open sits at ARA for a buy or ARB for a sell, with IDX's actual limit
  history. Cash dividends are credited on the ex-date, after the tax of the day.
* Stops are read on the CLOSE and exit at the next open, like every other exit.
  No intraday stop is assumed, so a gap through a stop is paid in full.
* The split is HALF AND HALF of each stock's own history: the first half of its
  sessions selects, the second half is the holdout. A stock listed in 2024 gets
  a short in-sample and the trade floor refuses it rather than guessing.
* Stocks whose TradingView history is split- or rights-adjusted are refused by
  `idx_stockbit_backtest.load_bars` (off the tick grid), and the refusal is
  printed with the reason rather than silently skipped.

THE FAMILIES: `stoch_cross` buys a slow-stochastic golden cross (slow %K
crossing above %D) with a fixed-percent stop and target. Add more as an entry
function plus its grid in `FAMILIES`. An entry returning an int is read as an
ex-date bar index (for the `cum`/`ex`/`ex_5` exits). A family may size to a
`stop_atr` (ATR multiple) or a `stop_pct` (percent below the fill).

`full` runs every cell over each stock's whole history with no split and no
selection -- a plain backtest, not a study result.

Every family also carries `regime`: "market" refuses entries while an
equal-weight index of the loaded stocks sits below its 100-session SMA. The
index is built from today's LQ45/ISSI survivors, so it is flattered by the same
selection the stocks are.

SELECTION follows `cfd_families.choose`: gates, the "bare" check (the same
cell with trend and regime filters off must still make money), and 60% of the
numeric neighbours passing at a looser drawdown. Nothing here is a result until
it holds on the holdout -- and a long-only rule on stocks that are in the index
BECAUSE they went up should also be read against simply holding them.
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
from collections import defaultdict
from datetime import date

from sandbox.research import idx_stockbit_backtest as sb

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
D, O, H, L, C = range(5)

TIMEFRAME = "1d"                    # "1d" builds sessions from 4h; else the raw table
SPLIT = 0.5                         # in-sample share of each stock's bars


def as_date(moment):
    return moment.date() if hasattr(moment, "date") else moment

# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #

INITIAL_CAPITAL = 10_000_000.0      # rupiah, as `idx_stockbit_backtest`
RISK_FRACTION = 0.02                # equity risked to the stop, capped at cash
ATR_DAYS = 20
REGIME_SMA = 100

MIN_PROFIT_FACTOR = 1.05
MAX_DD = 20.0
ANNUAL_DD = 22.0
NEIGHBOUR_DD = 24.0
TRADES_PER_YEAR = 3                 # multi-week holds: 5/yr refused every trend family on TLKM
MIN_TRADES = 12
SESSIONS_PER_YEAR = 245
FULL_YEAR_SESSIONS = 120            # a calendar year counts toward the gate from here

UNIVERSE = ("aadi", "admr", "adro", "akra", "antm", "bumi", "cpin", "dewa",
            "essa", "excl", "hrta", "icbp", "indf", "indy", "inkp", "isat",
            "itmg", "jpfa", "klbf", "mapi", "mbma", "mdka", "medc", "pgas",
            "ptba", "tlkm", "untr", "unvr", "wifi")

STOPS = (2.0, 3.0, 5.0)
REGIMES = ("none", "market")
TRENDS = ("none", "sma50", "sma200")
DIP_EXITS = ("sma5", "days_5", "days_10", "trail_2")
TREND_EXITS = ("trail_2", "trail_3", "trail_4", "channel_20")
DIVIDEND_EXITS = ("cum", "ex", "ex_5")

CATEGORICAL = ("trend", "exit", "regime", "pair")

# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


def daily_bars(symbol):
    """(date, open, high, low, close) per session, from the 4h table."""
    rows = sorted(sb.load_bars(symbol, "4h", date(2000, 1, 1), trim=True))
    if not rows:
        raise RuntimeError(f"{symbol}: no clean (unadjusted) bars left after trimming")
    today = date.today()
    days = {}
    for moment, o, h, l, c, _v in rows:
        day = moment.date()
        if day >= today:
            continue                     # the session still forming
        bar = days.get(day)
        if bar is None:
            days[day] = [day, o, h, l, c]
        else:
            bar[H] = max(bar[H], h)
            bar[L] = min(bar[L], l)
            bar[C] = c
    return [tuple(bar) for _, bar in sorted(days.items())]


def intraday_bars(symbol, timeframe):
    """(datetime, open, high, low, close) per bar, today's session dropped."""
    today = date.today()
    return [(m, o, h, l, c) for m, o, h, l, c, _v
            in sorted(sb.load_bars(symbol, timeframe, date(2000, 1, 1)))
            if m.date() < today]


def load_universe(symbols):
    loaded, refused = {}, {}
    for symbol in symbols:
        try:
            loaded[symbol] = (daily_bars(symbol) if TIMEFRAME == "1d"
                              else intraday_bars(symbol, TIMEFRAME))
        except RuntimeError as error:
            refused[symbol] = str(error)
    return loaded, refused


def market_regime(universe):
    """Date -> is the equal-weight index of the loaded stocks above its SMA."""
    by_day = defaultdict(list)
    for bars in universe.values():
        for prev, bar in zip(bars, bars[1:]):
            by_day[bar[D]].append(bar[C] / prev[C] - 1)
    level, levels = 1.0, []
    for day in sorted(by_day):
        level *= 1 + statistics.fmean(by_day[day])
        levels.append((day, level))
    out, window = {}, []
    for day, value in levels:
        window.append(value)
        if len(window) > REGIME_SMA:
            window.pop(0)
        out[day] = len(window) == REGIME_SMA and value > statistics.fmean(window)
    return out


def dividend_map(symbol, bars):
    """Bar index of each ex-date (first session on or after it) -> (ex, amount)."""
    days = [as_date(bar[D]) for bar in bars]
    out = {}
    for ex, amount in sb.load_dividends(symbol).items():
        at = next((i for i, day in enumerate(days) if day >= ex), None)
        if at is not None and at > 0:
            out[at] = (ex, amount)
    return out


# --------------------------------------------------------------------------- #
# indicators (lists aligned to bars, None until warm)
# --------------------------------------------------------------------------- #


def sma(values, n):
    out, total = [None] * len(values), 0.0
    for i, value in enumerate(values):
        total += value
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def atr(bars, n):
    ranges = [bars[0][H] - bars[0][L]] + [
        max(bar[H], prev[C]) - min(bar[L], prev[C])
        for prev, bar in zip(bars, bars[1:])]
    return sma(ranges, n)


def rsi(closes, n):
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        up, down = max(change, 0.0), max(-change, 0.0)
        if i <= n:
            gain += up / n
            loss += down / n
        else:
            gain = (gain * (n - 1) + up) / n
            loss = (loss * (n - 1) + down) / n
        if i >= n:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def prior_extreme(bars, field, n, pick):
    values = [bar[field] for bar in bars]
    return [pick(values[i - n:i]) if i >= n else None for i in range(len(values))]


def cached(ctx, key, build):
    value = ctx["cache"].get(key)
    if value is None:
        value = ctx["cache"][key] = build()
    return value


def closes(ctx):
    return cached(ctx, "close", lambda: [bar[C] for bar in ctx["bars"]])


def sma_of(ctx, n):
    return cached(ctx, ("sma", n), lambda: sma(closes(ctx), n))


def prior_high(ctx, n):
    return cached(ctx, ("high", n), lambda: prior_extreme(ctx["bars"], H, n, max))


def prior_low(ctx, n):
    return cached(ctx, ("low", n), lambda: prior_extreme(ctx["bars"], L, n, min))


def slow_stochastic(bars, k, smooth, d):
    """(slow %K, %D): raw %K over k sessions, SMA-smoothed by `smooth`, %D its SMA over d."""
    raw = [None] * len(bars)
    for i in range(k - 1, len(bars)):
        window = bars[i - k + 1:i + 1]
        high, low = max(b[H] for b in window), min(b[L] for b in window)
        raw[i] = 50.0 if high == low else 100 * (bars[i][C] - low) / (high - low)

    def smoothed(values, n):
        out = [None] * len(values)
        for i in range(n - 1, len(values)):
            chunk = values[i - n + 1:i + 1]
            if None not in chunk:
                out[i] = sum(chunk) / n
        return out

    slow_k = smoothed(raw, smooth)
    return slow_k, smoothed(slow_k, d)


def stoch_of(ctx, k, smooth, d):
    return cached(ctx, ("stoch", k, smooth, d),
                  lambda: slow_stochastic(ctx["bars"], k, smooth, d))


def trend_ok(ctx, i, trend):
    if trend == "none":
        return True
    line = sma_of(ctx, int(trend[3:]))[i]
    return line is not None and ctx["bars"][i][C] > line


# --------------------------------------------------------------------------- #
# families: entry(i, ctx, params) -> truthy to buy at the next open
# (an int is taken as the ex-date bar index the dividend exits key on)
# --------------------------------------------------------------------------- #


class Family:
    __slots__ = ("entry", "axes", "trades_per_year", "min_trades")

    def __init__(self, entry, axes, trades_per_year=TRADES_PER_YEAR,
                 min_trades=MIN_TRADES):
        self.entry = entry
        self.axes = axes
        self.trades_per_year = trades_per_year
        self.min_trades = min_trades


def stoch_cross(i, ctx, p):
    """Slow %K crosses above %D on day i's close."""
    k, d = stoch_of(ctx, p["k"], p["smooth"], p["d"])
    if i == 0 or None in (k[i], d[i], k[i - 1], d[i - 1]):
        return False
    return k[i - 1] <= d[i - 1] and k[i] > d[i]


def dip_entry(i, ctx, p):
    n = p["drop_days"]
    reach = ctx["atr"][i]
    if i < n or reach is None:
        return False
    bars = ctx["bars"]
    return (bars[i][C] - bars[i - n][C] <= -p["drop_atr"] * reach
            and trend_ok(ctx, i, p["trend"]))


def rsi2_entry(i, ctx, p):
    value = cached(ctx, ("rsi", p["rsi_len"]),
                   lambda: rsi(closes(ctx), p["rsi_len"]))[i]
    return value is not None and value < p["level"] and trend_ok(ctx, i, p["trend"])


def high_52w_entry(i, ctx, p):
    top = prior_high(ctx, p["lookback"])[i]
    return top is not None and ctx["bars"][i][C] >= (1 - p["near"]) * top         and (p["near"] > 0 or ctx["bars"][i][C] > top)


def donchian_entry(i, ctx, p):
    top = prior_high(ctx, p["channel"])[i]
    return top is not None and ctx["bars"][i][C] > top


def ma_trend_entry(i, ctx, p):
    fast, slow = p["pair"]
    f, s = sma_of(ctx, fast), sma_of(ctx, slow)
    return (i > 0 and None not in (f[i], s[i], f[i - 1], s[i - 1])
            and f[i] > s[i] and f[i - 1] <= s[i - 1])


def dividend_entry(i, ctx, p):
    """Returns the ex-date's bar index, so the exit knows which one it is."""
    ex = i + p["days_before"] + 1
    return ex if ex in ctx["dividends"] else False


#: Add a family as `"name": Family(entry_fn, {axis: values, ...})`.
#: Every family needs `exit`, `regime` and a `stop_atr` or `stop_pct` axis.
FAMILIES = {
    "stoch_cross": Family(stoch_cross, {
        "k": (5, 10, 14, 21, 30, 50), "smooth": (1, 3, 5, 8), "d": (3, 5, 8),
        "exit": ("tp",), "tp_pct": (50, 60, 70, 80, 90, 100, 120, 150),
        "stop_pct": (5.0, 6.0, 7.0, 8.0, 9.0, 10.0), "regime": ("none",)}),
    "dip": Family(dip_entry, {
        "trend": TRENDS, "drop_days": (3, 5, 10), "drop_atr": (1.5, 2.5, 3.5),
        "exit": DIP_EXITS, "stop_atr": STOPS, "regime": REGIMES}),
    "rsi2": Family(rsi2_entry, {
        "trend": TRENDS, "rsi_len": (2, 3), "level": (5, 10, 20),
        "exit": DIP_EXITS, "stop_atr": STOPS, "regime": REGIMES}),
    "high_52w": Family(high_52w_entry, {
        "lookback": (120, 250), "near": (0.0, 0.02, 0.05),
        "exit": TREND_EXITS, "stop_atr": STOPS, "regime": REGIMES}),
    "donchian": Family(donchian_entry, {
        "channel": (20, 40, 60, 100),
        "exit": TREND_EXITS, "stop_atr": STOPS, "regime": REGIMES}),
    "ma_trend": Family(ma_trend_entry, {
        "pair": ((10, 50), (10, 100), (10, 200), (20, 50), (20, 100),
                 (20, 200), (50, 100), (50, 200)),
        "exit": TREND_EXITS, "stop_atr": STOPS, "regime": REGIMES}),
    # One to three ex-dates a year per stock.
    "dividend": Family(dividend_entry, {
        "days_before": (3, 5, 10, 15), "exit": DIVIDEND_EXITS,
        "stop_atr": STOPS, "regime": REGIMES},
        trades_per_year=1, min_trades=8),
}


def candidates(axes):
    keys = list(axes)
    for values in itertools.product(*(axes[k] for k in keys)):
        yield dict(zip(keys, values))


def frozen(params):
    return tuple(sorted(params.items()))


# --------------------------------------------------------------------------- #
# backtest
# --------------------------------------------------------------------------- #


def exit_due(i, ctx, trade, mode):
    bars = ctx["bars"]
    close = bars[i][C]
    if close <= trade["stop"]:
        return True
    kind, _, arg = mode.partition("_")
    if kind == "tp":
        return close >= trade["target"]
    if kind == "trail":
        trade["best"] = max(trade["best"], close)
        return close < trade["best"] - float(arg) * ctx["atr"][i]
    if kind == "days":
        return i - trade["index"] + 1 >= int(arg)
    if mode == "sma5":
        line = sma_of(ctx, 5)[i]
        return line is not None and close > line
    if kind == "channel":
        low = prior_low(ctx, int(arg))[i]
        return low is not None and close < low
    ex = trade["ex"]
    if mode == "cum":
        return i >= ex - 2           # sell at the cum date's open
    if mode == "ex":
        return i >= ex - 1           # hold the cum close, sell at the ex open
    return i >= ex + 4               # ex_5


def backtest(ctx, family, params, lo, hi):
    bars, entry = ctx["bars"], FAMILIES[family].entry
    cash, shares = INITIAL_CAPITAL, 0
    pending = trade = None
    trades, curve = [], []
    waits = skipped = held = 0
    fees = received = 0.0

    def close_trade(price, moment, index):
        nonlocal cash, shares, trade, fees
        fee = price * shares * sb.SELL_FEE
        proceeds = price * shares - fee
        cash += proceeds
        fees += fee
        trade.update(exit_date=str(moment), exit=price, exit_index=index,
                     pnl=proceeds + trade["dividends"] - trade["cost"])
        trade["return_pct"] = 100 * trade["pnl"] / trade["cost"]
        trades.append(trade)
        shares, trade = 0, None

    for i in range(lo, hi):
        day, o, _h, _l, c = bars[i]
        if shares and i in ctx["dividends"]:
            ex, amount = ctx["dividends"][i]
            net = amount * shares * (1 - sb.dividend_tax(ex))
            cash += net
            received += net
            trade["dividends"] += net
        reference = ctx["reference"][i]
        upper, lower = (sb.price_limits(as_date(day), reference) if reference
                        else (math.inf, 0.0))

        if pending is not None and pending[0] == "buy":
            if o >= upper:
                waits += 1
            else:
                _, stop_distance, ex = pending
                price = min(o + sb.tick_size(o), upper)
                if "stop_pct" in params:
                    stop_distance = price * params["stop_pct"] / 100
                risk_lots = int(RISK_FRACTION * cash / stop_distance / sb.LOT)
                cash_lots = int(cash // (price * sb.LOT * (1 + sb.BUY_FEE)))
                lots = min(risk_lots, cash_lots)
                if lots > 0:
                    shares = lots * sb.LOT
                    fee = price * shares * sb.BUY_FEE
                    cash -= price * shares + fee
                    fees += fee
                    trade = {"entry_date": str(day), "entry": price, "index": i,
                             "shares": shares, "cost": price * shares + fee,
                             "dividends": 0.0, "stop": price - stop_distance,
                             "best": price, "ex": ex,
                             "target": price * (1 + params.get("tp_pct", math.inf) / 100)}
                else:
                    skipped += 1
                pending = None
        elif pending == "sell":
            if sb.sell_locked(o, lower):
                waits += 1
            else:
                close_trade(max(o - sb.tick_size(o), lower), day, i)
                pending = None

        if pending is None and i < hi - 1:
            if shares:
                if exit_due(i, ctx, trade, params["exit"]):
                    pending = "sell"
            elif params["regime"] == "none" or ctx["market_up"][i]:
                reach = ctx["atr"][i]
                signal = reach and entry(i, ctx, params)
                if signal:
                    ex = signal if type(signal) is int else None
                    pending = ("buy", params.get("stop_atr", 0) * reach, ex)
        held += bool(shares)
        curve.append((day, cash + shares * c))

    if shares:                           # the window ends: mark it out at the close
        last = bars[hi - 1]
        close_trade(last[C] - sb.tick_size(last[C]), last[D], hi - 1)
        curve[-1] = (last[D], cash)
    return summarise(trades, curve, fees, received, waits, skipped, held)


def summarise(trades, curve, fees, received, waits, skipped, held):
    peak, max_dd = INITIAL_CAPITAL, 0.0
    annual, year_start, year_peak, sessions = {}, INITIAL_CAPITAL, INITIAL_CAPITAL, 0
    current = last_date = None
    for day, equity in curve:
        if day.year != current:
            if current is not None:
                annual[str(current)]["sessions"] = sessions
            current, sessions = day.year, 0
            year_start = curve_prev if annual else INITIAL_CAPITAL
            year_peak = year_start
            annual[str(current)] = {"max_dd_pct": 0.0}
        sessions += as_date(day) != last_date      # sessions, not bars
        last_date = as_date(day)
        peak, year_peak = max(peak, equity), max(year_peak, equity)
        max_dd = max(max_dd, 100 * (1 - equity / peak))
        row = annual[str(current)]
        row["max_dd_pct"] = max(row["max_dd_pct"], 100 * (1 - equity / year_peak))
        row["pnl"] = equity - year_start
        row["return_pct"] = 100 * (equity / year_start - 1)
        curve_prev = equity
    if current is not None:
        annual[str(current)]["sessions"] = sessions
    final = curve[-1][1] if curve else INITIAL_CAPITAL
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    for row in annual.values():
        for key in ("max_dd_pct", "pnl", "return_pct"):
            row[key] = round(row.get(key, 0.0), 4)
    return {
        "final": round(final, 2),
        "return_pct": round(100 * (final / INITIAL_CAPITAL - 1), 4),
        "max_dd_pct": round(max_dd, 4),
        "trades": len(trades),
        "pf": round(min(wins / losses, 99.0) if losses else (99.0 if wins else 0.0), 4),
        "win_rate_pct": round(100 * sum(t["pnl"] > 0 for t in trades) / len(trades), 2)
        if trades else 0.0,
        "avg_trade_pct": round(statistics.fmean(t["return_pct"] for t in trades), 4)
        if trades else 0.0,
        "avg_hold_sessions": round(statistics.fmean(
            t["exit_index"] - t["index"] for t in trades), 2) if trades else 0.0,
        "exposure_pct": round(100 * held / len(curve), 2) if curve else 0.0,
        "fees": round(fees, 2), "dividends": round(received, 2),
        "limit_waits": waits, "unaffordable": skipped,
        "period": f"{curve[0][0]} to {curve[-1][0]}" if curve else "",
        "annual": annual,
    }


# --------------------------------------------------------------------------- #
# gates and selection (as cfd_families.passes / quality / choose)
# --------------------------------------------------------------------------- #


def gate_years(stat):
    return [y for y, row in stat["annual"].items()
            if row.get("sessions", 0) >= FULL_YEAR_SESSIONS]


def need_trades(family, sessions):
    spec = FAMILIES[family]
    return max(spec.min_trades,
               math.ceil(spec.trades_per_year * sessions / SESSIONS_PER_YEAR))


def passes(family, stat, sessions, dd=MAX_DD, annual_dd=None):
    annual_dd = ANNUAL_DD if annual_dd is None else annual_dd
    years = gate_years(stat)
    positive = sum(stat["annual"][y]["pnl"] > 0 for y in years)
    worst = max((stat["annual"][y]["max_dd_pct"] for y in years), default=100)
    return (bool(years)
            and stat["trades"] >= need_trades(family, sessions)
            and stat["pf"] >= MIN_PROFIT_FACTOR
            and stat["max_dd_pct"] <= dd
            and positive >= math.ceil(len(years) / 2)
            and worst <= annual_dd)


def quality(family, stat, sessions, dd=MAX_DD, annual_dd=None):
    if not passes(family, stat, sessions, dd, annual_dd):
        return -math.inf
    returns = [stat["annual"][y]["return_pct"] for y in gate_years(stat)]
    return (100 * math.log(1 + stat["return_pct"] / 100) + min(returns)
            + .25 * statistics.median(returns) - .5 * statistics.pstdev(returns))


def neighbours(params, axes):
    out = []
    for key, values in axes.items():
        if key in CATEGORICAL:
            continue
        at = values.index(params[key])
        for j in (at - 1, at + 1):
            if 0 <= j < len(values):
                out.append({**params, key: values[j]})
    return out


def gate_report(family, results, sessions, dd=MAX_DD, annual_dd=None):
    annual_dd = ANNUAL_DD if annual_dd is None else annual_dd
    counts = dict.fromkeys(("trades", "profit_factor", "max_dd",
                            "positive_years", "annual_dd"), 0)
    best = None
    for stat in results.values():
        years = gate_years(stat)
        positive = sum(stat["annual"][y]["pnl"] > 0 for y in years)
        worst = max((stat["annual"][y]["max_dd_pct"] for y in years), default=100)
        counts["trades"] += stat["trades"] < need_trades(family, sessions)
        counts["profit_factor"] += stat["pf"] < MIN_PROFIT_FACTOR
        counts["max_dd"] += stat["max_dd_pct"] > dd
        counts["positive_years"] += (not years) or positive < math.ceil(len(years) / 2)
        counts["annual_dd"] += worst > annual_dd
        if best is None or stat["return_pct"] > best["return_pct"]:
            best = stat
    total = len(results)
    return {"cells": total, "needs_trades": need_trades(family, sessions),
            "refused_by": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "binding": [k for k, v in counts.items() if v == total],
            "best_by_return": {k: best[k] for k in (
                "return_pct", "max_dd_pct", "trades", "pf")} if best else None}


def choose(family, results, sessions, axes=None, dd=MAX_DD, annual_dd=None,
           neighbour_dd=NEIGHBOUR_DD):
    axes = FAMILIES[family].axes if axes is None else axes
    ranked = []
    for key, stat in results.items():
        params = dict(key)
        own = quality(family, stat, sessions, dd, annual_dd)
        if not math.isfinite(own):
            continue
        # The same cell with its discretionary filters off. A family that only
        # works once a trend and a market filter are applied has shown a filter.
        bare = {**params, "regime": "none"}
        if "trend" in bare:
            bare["trend"] = "none"
        plain = results.get(frozen(bare))
        if plain is None or plain["return_pct"] <= 0 or plain["pf"] < 1:
            continue
        near = [results[frozen(p)] for p in neighbours(params, axes)]
        robust = [s for s in near
                  if passes(family, s, sessions, neighbour_dd, annual_dd)]
        if not near or len(robust) < math.ceil(.6 * len(near)):
            continue
        ranked.append((own, params, stat, len(robust), len(near)))
    if not ranked:
        return None
    score, params, stat, robust, total = max(ranked, key=lambda item: item[0])
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "robust_neighbours": f"{robust}/{total}"}


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #

_WORKER = {}


def session_references(bars):
    """Previous session's close per bar: what IDX's ARA/ARB limits key on."""
    out, previous, day, last = [], None, None, None
    for bar in bars:
        if as_date(bar[D]) != day:
            previous, day = last, as_date(bar[D])
        out.append(previous)
        last = bar[C]
    return out


def sessions_in(bars):
    return len({as_date(bar[D]) for bar in bars})


def build_context(symbol, bars, regime):
    return {"symbol": symbol, "bars": bars, "cache": {},
            "reference": session_references(bars),
            "atr": atr(bars, ATR_DAYS),
            "market_up": [regime.get(bar[D], False) for bar in bars],
            "dividends": dividend_map(symbol, bars),
            "index": {bar[D]: i for i, bar in enumerate(bars)}}


def _init_worker(universe, regime, capital):
    global INITIAL_CAPITAL
    INITIAL_CAPITAL = capital
    _WORKER["ctx"] = {s: build_context(s, bars, regime)
                      for s, bars in universe.items()}


def _evaluate(job):
    symbol, family, params, lo, hi = job
    return symbol, family, frozen(params), backtest(
        _WORKER["ctx"][symbol], family, params, lo, hi)


def split(bars):
    return int(len(bars) * SPLIT)


def output_path(symbol):
    return os.path.join(RESULTS, f"idx_families_{symbol}_{TIMEFRAME}.json")


def seal(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def select(symbols, workers):
    universe, refused = load_universe(UNIVERSE)
    for symbol in symbols:
        if symbol in refused:
            print(f"{symbol:<6} REFUSED  {refused[symbol][:100]}")
    regime = market_regime(universe)
    targets = [s for s in symbols if s in universe]
    jobs = [(s, family, params, 0, split(universe[s]))
            for s in targets for family, spec in FAMILIES.items()
            for params in candidates(spec.axes)]
    print(f"{len(jobs):,} cells over {len(targets)} stocks, {workers} workers",
          flush=True)
    results = defaultdict(lambda: defaultdict(dict))
    with multiprocessing.Pool(workers, _init_worker,
                              (universe, regime, INITIAL_CAPITAL)) as pool:
        for symbol, family, key, stat in pool.imap_unordered(_evaluate, jobs,
                                                             chunksize=64):
            results[symbol][family][key] = stat
    for symbol in targets:
        bars = universe[symbol]
        cut = split(bars)
        sessions = sessions_in(bars[:cut])
        rows, refusals = {}, {}
        for family in FAMILIES:
            winner = choose(family, results[symbol][family], sessions)
            rows[family] = winner
            if winner is None:
                refusals[family] = gate_report(family, results[symbol][family],
                                                sessions)
                report = refusals[family]
                best = report["best_by_return"] or {}
                binding = ", ".join(report["binding"]) or next(iter(report["refused_by"]))
                print(f"{symbol:<6} {family:<9} -  refused by {binding}; best cell "
                      f"{best.get('return_pct', 0):+.1f}% dd "
                      f"{best.get('max_dd_pct', 0):.1f}% n={best.get('trades', 0)}")
            else:
                s = winner["in_sample"]
                print(f"{symbol:<6} {family:<9} IS {s['return_pct']:+.1f}% "
                      f"dd {s['max_dd_pct']:.1f}% n={s['trades']}", flush=True)
        payload = {
            "sealed": True, "symbol": symbol, "timeframe": TIMEFRAME,
            "protocol": {
                "account": "Stockbit, long only, whole lots",
                "fees": {"buy": sb.BUY_FEE, "sell": sb.SELL_FEE},
                "fill": "next bar open, one tick worse; refused at ARA/ARB "
                        "and retried; stops read on the close",
                "dividends": "credited on the ex-date after the tax of the day",
                "sizing": f"{RISK_FRACTION:.0%} of equity to a stop_atr x "
                          f"ATR{ATR_DAYS} stop, capped at cash",
                "split": f"first {SPLIT:.0%} of the stock's own bars selects",
                "in_sample": f"{bars[0][D]} to {bars[cut - 1][D]}",
                "holdout": f"{bars[cut][D]} to {bars[-1][D]}",
                "initial_capital": INITIAL_CAPITAL,
                "regime": f"equal-weight index of {len(universe)} loaded stocks "
                          f"above its {REGIME_SMA}-session SMA",
                "candidate_counts": {f: len(list(candidates(s.axes)))
                                     for f, s in FAMILIES.items()},
            },
            "families": rows,
            "refused": refusals,
        }
        seal(payload, output_path(symbol))
    return refused


def validate(symbols):
    universe, _refused = load_universe(UNIVERSE)
    regime = market_regime(universe)
    for symbol in symbols:
        path = output_path(symbol)
        if symbol not in universe or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        expected = payload.pop("seal_sha256")
        payload.pop("validation", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode()).hexdigest() != expected:
            raise SystemExit(f"{symbol}: selection seal mismatch")
        bars = universe[symbol]
        ctx = build_context(symbol, bars, regime)
        validation = {}
        for family, winner in payload["families"].items():
            if winner is None:
                continue
            params = {k: tuple(v) if isinstance(v, list) else v
                      for k, v in winner["params"].items()}
            oos = backtest(ctx, family, params, split(bars), len(bars))
            validation[family] = {"oos": oos}
            print(f"{symbol:<6} {family:<9} OOS {oos['return_pct']:+.1f}% "
                  f"dd {oos['max_dd_pct']:.1f}% n={oos['trades']} "
                  f"PF={oos['pf']:.2f} avg {oos['avg_trade_pct']:+.2f}%")
        payload["seal_sha256"] = expected
        payload["validation"] = validation
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")


def describe(params):
    return " ".join(f"{k}={'/'.join(map(str, v)) if isinstance(v, list) else v}"
                    for k, v in sorted(params.items()))


def report(symbols, refused=None):
    lines = ["| stock | family | params | IS ret | IS dd | IS n | OOS ret | "
             "OOS dd | OOS n | OOS PF | OOS avg trade |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    tried = passed = held = 0
    for symbol in symbols:
        path = output_path(symbol)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        for family, winner in payload["families"].items():
            tried += 1
            if winner is None:
                continue
            passed += 1
            s = winner["in_sample"]
            o = payload.get("validation", {}).get(family, {}).get("oos")
            if o is None:
                continue
            held += o["return_pct"] > 0
            lines.append(
                f"| {symbol} | {family} | {describe(winner['params'])} | "
                f"{s['return_pct']:+.1f}% | {s['max_dd_pct']:.1f}% | {s['trades']} | "
                f"{o['return_pct']:+.1f}% | {o['max_dd_pct']:.1f}% | {o['trades']} | "
                f"{o['pf']:.2f} | {o['avg_trade_pct']:+.2f}% |")
    header = [f"# IDX family study, {date.today()}", "",
              f"{TIMEFRAME} bars, Stockbit costs, {SPLIT:.0%}/{1 - SPLIT:.0%} "
              f"split per stock, Rp{INITIAL_CAPITAL:,.0f} start. "
              f"{passed} of {tried} stock-family pairs selected a cell in-sample; "
              f"{held} of {passed} made money on the holdout.", ""]
    if refused:
        header += ["Refused (split/rights-adjusted history): "
                   + ", ".join(sorted(refused)), ""]
    text = "\n".join(header + lines) + "\n"
    path = os.path.join(RESULTS, f"IDX_FAMILY_STUDY_{TIMEFRAME}_{date.today()}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)
    print(f"written {os.path.abspath(path)}")


def full(symbols):
    """Every cell over each stock's whole history: no split, no selection."""
    universe, refused = load_universe(UNIVERSE)
    regime = market_regime(universe)
    lines = ["| stock | family | period | return | max dd | trades | win % | PF | "
             "avg trade | avg hold | exposure |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    out = {}
    for symbol in symbols:
        if symbol in refused:
            print(f"{symbol:<6} REFUSED  {refused[symbol][:100]}")
            continue
        if symbol not in universe:
            continue
        bars = universe[symbol]
        ctx = build_context(symbol, bars, regime)
        for family, spec in FAMILIES.items():
            for params in candidates(spec.axes):
                s = backtest(ctx, family, params, 0, len(bars))
                out.setdefault(symbol, {}).setdefault(family, []).append(
                    {"params": params, "full": s})
                lines.append(
                    f"| {symbol} | {family} | {s['period']} | {s['return_pct']:+.1f}% | "
                    f"{s['max_dd_pct']:.1f}% | {s['trades']} | {s['win_rate_pct']:.0f} | "
                    f"{s['pf']:.2f} | {s['avg_trade_pct']:+.2f}% | "
                    f"{s['avg_hold_sessions']:.0f} | {s['exposure_pct']:.0f}% |")
    stamp = date.today()
    text = "\n".join([f"# IDX full-history backtest, {stamp}", "",
                      f"Whole history per stock, Stockbit costs, no split, no selection, "
                      f"Rp{INITIAL_CAPITAL:,.0f} start.",
                      ""] + lines) + "\n"
    if refused:
        text += ("\nRefused (split/rights-adjusted history): "
                 + ", ".join(sorted(refused)) + "\n")
    base = os.path.join(RESULTS, f"IDX_FULL_{'_'.join(FAMILIES)}_{TIMEFRAME}_{stamp}")
    with open(base + ".md", "w", encoding="utf-8") as handle:
        handle.write(text)
    with open(base + ".json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, default=str)
    print(text)
    print(f"written {os.path.abspath(base)}.md/.json")


# --------------------------------------------------------------------------- #
# one account: a family trades every stock at once, one shared balance
# --------------------------------------------------------------------------- #
#
# The per-stock study gives each stock ~4 in-sample years and 12-50 trades
# against every cell, so its winners were mostly the luckiest cell (13 of 28
# held out of sample). Here ONE setting of a family runs across the whole
# universe on one balance, so a cell's trades are pooled and its selection is
# one choice rather than one per stock.
#
# The balance is split into `slots` equal positions, sized at the signal's
# equity and filled at the next open. When more stocks fire than slots are
# free, the family's `rank_score` picks: the most beaten-down for the fades,
# the strongest 60-session return for the trend families, the highest yield
# for `dividend`. A stock whose lot costs more than a slot is passed over.
#
# The gates are looser on drawdown than the per-stock study's 20%, because an
# unlevered one-to-three stock account cannot reach it: its drawdown is the
# stocks'. That is a statement about the account, not a relaxed standard.

ACCOUNT_CAPITAL = 1_000_000.0
ACCOUNT_SLOTS = (1, 2, 3)
ACCOUNT_MAX_DD = 35.0
ACCOUNT_ANNUAL_DD = 35.0
ACCOUNT_NEIGHBOUR_DD = 40.0
ACCOUNT_FAMILIES = ("dip", "rsi2", "high_52w", "donchian", "ma_trend", "dividend")
FADES = ("dip", "rsi2")
TRENDERS = ("high_52w", "donchian", "ma_trend")


def rank_score(family, ctx, i, signal):
    """Higher is bought first when more stocks fire than slots are free."""
    c = closes(ctx)
    if family in FADES:
        return -(c[i] / c[i - 5] - 1) if i >= 5 else 0.0
    if family in TRENDERS:
        return c[i] / c[i - 60] - 1 if i >= 60 else 0.0
    if family == "dividend":
        return ctx["dividends"][signal][1] / c[i]
    return 0.0


def account_axes(family):
    return {**FAMILIES[family].axes, "slots": ACCOUNT_SLOTS}


def account_backtest(ctxs, calendar, regime, family, params, lo, hi):
    entry = FAMILIES[family].entry
    slots = params["slots"]
    cash = INITIAL_CAPITAL
    positions, last_close = {}, {}
    sells, buys = set(), []
    trades, curve = [], []
    fees = received = 0.0
    waits = skipped = held = 0

    def limits(ctx, i):
        reference = ctx["reference"][i]
        return (sb.price_limits(as_date(ctx["bars"][i][D]), reference)
                if reference else (math.inf, 0.0))

    def close_trade(symbol, price, day, index):
        nonlocal cash, fees
        t = positions.pop(symbol)
        fee = price * t["shares"] * sb.SELL_FEE
        proceeds = price * t["shares"] - fee
        cash += proceeds
        fees += fee
        t.update(exit_date=str(day), exit=price, exit_index=index,
                 pnl=proceeds + t["dividends"] - t["cost"])
        t["return_pct"] = 100 * t["pnl"] / t["cost"]
        trades.append(t)

    for n in range(lo, hi):
        day = calendar[n]
        today = {s: ctx["index"].get(day) for s, ctx in ctxs.items()}
        for s, t in positions.items():
            i = today[s]
            if i is not None and i in ctxs[s]["dividends"]:
                ex, amount = ctxs[s]["dividends"][i]
                net = amount * t["shares"] * (1 - sb.dividend_tax(ex))
                cash += net
                received += net
                t["dividends"] += net

        # The morning: sales first, so their cash is there for the buys.
        for s in sorted(sells):
            i = today[s]
            if i is None:
                continue
            o = ctxs[s]["bars"][i][O]
            _upper, lower = limits(ctxs[s], i)
            if sb.sell_locked(o, lower):
                waits += 1
                continue
            close_trade(s, max(o - sb.tick_size(o), lower), day, i)
            sells.discard(s)
        for s, stop_distance, ex, target in buys:
            i = today[s]
            if i is None or s in positions or len(positions) >= slots:
                continue
            o = ctxs[s]["bars"][i][O]
            upper, _lower = limits(ctxs[s], i)
            if o >= upper:
                waits += 1
                continue
            price = min(o + sb.tick_size(o), upper)
            if "stop_pct" in params:
                stop_distance = price * params["stop_pct"] / 100
            lots = int(min(target, cash) // (price * sb.LOT * (1 + sb.BUY_FEE)))
            if lots <= 0:
                skipped += 1
                continue
            shares = lots * sb.LOT
            fee = price * shares * sb.BUY_FEE
            cash -= price * shares + fee
            fees += fee
            positions[s] = {"symbol": s, "entry_date": str(day), "entry": price,
                            "index": i, "shares": shares,
                            "cost": price * shares + fee, "dividends": 0.0,
                            "stop": price - stop_distance, "best": price, "ex": ex,
                            "target": price * (1 + params.get("tp_pct", math.inf) / 100)}
        buys = []

        for s, i in today.items():
            if i is not None:
                last_close[s] = ctxs[s]["bars"][i][C]

        # The close: exits, then entries into whatever slots the exits free.
        if n < hi - 1:
            for s, t in positions.items():
                i = today[s]
                if (i is not None and s not in sells
                        and exit_due(i, ctxs[s], t, params["exit"])):
                    sells.add(s)
            free = slots - (len(positions) - len(sells))
            if free > 0 and (params["regime"] == "none" or regime.get(day, False)):
                equity = cash + sum(t["shares"] * last_close[s]
                                    for s, t in positions.items())
                target = equity / slots
                picks = []
                for s, ctx in ctxs.items():
                    i = today[s]
                    if i is None or s in positions:
                        continue
                    reach = ctx["atr"][i]
                    if not reach:
                        continue
                    signal = entry(i, ctx, params)
                    if not signal:
                        continue
                    close = ctx["bars"][i][C]
                    if close * sb.LOT * (1 + sb.BUY_FEE) > target:
                        skipped += 1         # one lot is bigger than a slot
                        continue
                    ex = signal if type(signal) is int else None
                    picks.append((rank_score(family, ctx, i, signal), s,
                                  params.get("stop_atr", 0) * reach, ex))
                picks.sort(key=lambda pick: (-pick[0], pick[1]))
                buys = [(s, distance, ex, target)
                        for _score, s, distance, ex in picks[:free]]

        held += bool(positions)
        curve.append((day, cash + sum(t["shares"] * last_close[s]
                                      for s, t in positions.items())))

    for s in list(positions):             # the window ends: mark out at the close
        ctx = ctxs[s]
        i = max(j for j in range(len(ctx["bars"])) if ctx["bars"][j][D] <= calendar[hi - 1])
        close = ctx["bars"][i][C]
        close_trade(s, close - sb.tick_size(close), ctx["bars"][i][D], i)
    if curve:
        curve[-1] = (curve[-1][0], cash)
    stat = summarise(trades, curve, fees, received, waits, skipped, held)
    by_symbol = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for t in trades:
        by_symbol[t["symbol"]]["trades"] += 1
        by_symbol[t["symbol"]]["pnl"] = round(by_symbol[t["symbol"]]["pnl"] + t["pnl"], 2)
    stat["by_symbol"] = dict(sorted(by_symbol.items(), key=lambda kv: -kv[1]["pnl"]))
    return stat


def _init_account_worker(universe, regime, capital):
    global INITIAL_CAPITAL
    INITIAL_CAPITAL = capital
    _WORKER["ctx"] = {s: build_context(s, bars, regime) for s, bars in universe.items()}
    _WORKER["calendar"] = sorted({bar[D] for bars in universe.values() for bar in bars})
    _WORKER["regime"] = regime


def _evaluate_account(job):
    family, params, lo, hi = job
    return family, frozen(params), account_backtest(
        _WORKER["ctx"], _WORKER["calendar"], _WORKER["regime"], family, params, lo, hi)


def account(families, workers):
    if TIMEFRAME != "1d":
        raise SystemExit("the account study runs on daily sessions only")
    universe, refused = load_universe(UNIVERSE)
    for symbol, why in sorted(refused.items()):
        print(f"{symbol:<6} REFUSED  {why[:100]}")
    regime = market_regime(universe)
    calendar = sorted({bar[D] for bars in universe.values() for bar in bars})
    cut = int(len(calendar) * SPLIT)
    jobs = [(family, params, 0, cut) for family in families
            for params in candidates(account_axes(family))]
    print(f"Rp{INITIAL_CAPITAL:,.0f} account, {len(universe)} stocks, "
          f"in-sample {calendar[0]} to {calendar[cut - 1]}, holdout "
          f"{calendar[cut]} to {calendar[-1]}; {len(jobs):,} cells, "
          f"{workers} workers", flush=True)
    results = defaultdict(dict)
    with multiprocessing.Pool(workers, _init_account_worker,
                              (universe, regime, INITIAL_CAPITAL)) as pool:
        for family, key, stat in pool.imap_unordered(_evaluate_account, jobs,
                                                     chunksize=16):
            results[family][key] = stat

    ctxs = {s: build_context(s, bars, regime) for s, bars in universe.items()}
    rows = {}
    for family in families:
        winner = choose(family, results[family], cut, account_axes(family),
                        ACCOUNT_MAX_DD, ACCOUNT_ANNUAL_DD, ACCOUNT_NEIGHBOUR_DD)
        if winner is None:
            rows[family] = {"refused": gate_report(
                family, results[family], cut, ACCOUNT_MAX_DD, ACCOUNT_ANNUAL_DD)}
            continue
        winner["oos"] = account_backtest(ctxs, calendar, regime, family,
                                         winner["params"], cut, len(calendar))
        rows[family] = winner

    stamp = date.today()
    lines = [f"# IDX one-account study, {stamp}", "",
             f"One Rp{INITIAL_CAPITAL:,.0f} Stockbit account trading all "
             f"{len(universe)} stocks with one setting per family. In-sample "
             f"{calendar[0]} to {calendar[cut - 1]}, holdout {calendar[cut]} to "
             f"{calendar[-1]}. Gates: max dd {ACCOUNT_MAX_DD:.0f}%, PF "
             f"{MIN_PROFIT_FACTOR}, half the years positive, 60% of neighbours.", "",
             "| family | params | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | "
             "OOS PF | OOS avg trade | OOS top stocks (pnl Rp) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for family in families:
        row = rows[family]
        if "refused" in row:
            report_ = row["refused"]
            best = report_["best_by_return"] or {}
            binding = ", ".join(report_["binding"]) or next(iter(report_["refused_by"]))
            lines.append(f"| {family} | refused by {binding} | best cell "
                         f"{best.get('return_pct', 0):+.1f}% | "
                         f"{best.get('max_dd_pct', 0):.1f}% | {best.get('trades', 0)} "
                         f"| | | | | | |")
            continue
        s, o = row["in_sample"], row["oos"]
        top = ", ".join(f"{sym} {v['pnl']:+,.0f} ({v['trades']})"
                        for sym, v in list(o["by_symbol"].items())[:3])
        lines.append(
            f"| {family} | {describe(row['params'])} | {s['return_pct']:+.1f}% | "
            f"{s['max_dd_pct']:.1f}% | {s['trades']} | {o['return_pct']:+.1f}% | "
            f"{o['max_dd_pct']:.1f}% | {o['trades']} | {o['pf']:.2f} | "
            f"{o['avg_trade_pct']:+.2f}% | {top} |")
    text = "\n".join(lines) + "\n"
    base = os.path.join(RESULTS, f"IDX_ACCOUNT_STUDY_{stamp}")
    with open(base + ".md", "w", encoding="utf-8") as handle:
        handle.write(text)
    seal({"capital": INITIAL_CAPITAL, "split": SPLIT,
          "in_sample": f"{calendar[0]} to {calendar[cut - 1]}",
          "holdout": f"{calendar[cut]} to {calendar[-1]}",
          "stocks": sorted(universe), "families": rows},
         base + ".json")
    print(text)
    print(f"written {os.path.abspath(base)}.md/.json")


def expand(argument):
    if not argument or argument == "all":
        return list(UNIVERSE)
    return [s.strip().lower() for s in argument.split(",") if s.strip()]


def main():
    global INITIAL_CAPITAL, TIMEFRAME, SPLIT
    parser = argparse.ArgumentParser(description="IDX family study, Stockbit costs")
    parser.add_argument("command", choices=("run", "select", "validate",
                                            "report", "budget", "full",
                                            "account"))
    parser.add_argument("--symbols", default="all")
    parser.add_argument("--capital", type=float, default=None,
                        help="starting balance in rupiah (default Rp10jt per "
                             "stock, Rp1jt for `account`)")
    parser.add_argument("--families", default=",".join(ACCOUNT_FAMILIES),
                        help="families the `account` command runs")
    parser.add_argument("--timeframe", default=TIMEFRAME,
                        help="1d (sessions from 4h) or a raw table suffix like 1h")
    parser.add_argument("--split", type=float, default=SPLIT,
                        help="in-sample share of each stock's bars")
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args()
    TIMEFRAME, SPLIT = args.timeframe, args.split
    if args.capital is not None:
        INITIAL_CAPITAL = args.capital
    elif args.command == "account":
        INITIAL_CAPITAL = ACCOUNT_CAPITAL
    symbols = expand(args.symbols)
    if args.command == "account":
        account([f.strip() for f in args.families.split(",") if f.strip()],
                args.workers)
        return
    if args.command == "budget":
        for family, spec in FAMILIES.items():
            print(f"{family:<9} {len(list(candidates(spec.axes))):>5} cells")
        print(f"total     {sum(len(list(candidates(s.axes))) for s in FAMILIES.values()):>5}"
              f" per stock")
        return
    if args.command == "full":
        full(symbols)
        return
    refused = None
    if args.command in ("run", "select"):
        refused = select(symbols, args.workers)
    if args.command in ("run", "validate"):
        validate(symbols)
    if args.command in ("run", "report"):
        report(symbols, refused)


if __name__ == "__main__":
    main()
