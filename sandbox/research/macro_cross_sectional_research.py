"""Cross-sectional long/short across thirteen macro instruments, with a 2024-2026 holdout.

The family studies asked each symbol in isolation whether a price rule predicted
its own next move, and answered no on the indices
([[index-cfds-fail-like-every-other-family-study]]), the metal crosses
([[metal-crosses-lose-to-the-coin-flip-null]]) and oil
([[usoil-intraday-fails-twice]]). This module asks the question that protocol
structurally cannot see -- not "will DE40 go up" but "will DE40 beat copper" --
because ranking thirteen instruments against each other and trading the spread
removes the global risk factor they share.

It is deliberately *not* the stock cross-section
([[cross-sectional-turnover-cost-is-the-binding-constraint]]) rerun on other
tickers. Two structural differences drive the design:

  * The universe is heterogeneous. Bitcoin's daily vol is ~15x UK100's, so
    ranking on **raw** return sorts the panel by volatility, not by relative
    strength: crypto occupies both extremes almost every day and the book
    degenerates into a long/short crypto position wearing a macro costume.
    Every signal here is therefore divided by the symbol's own trailing
    volatility before it is ranked, and every leg is sized inverse-vol so the
    2k legs carry equal risk rather than equal dollars.

  * The costs are an order of magnitude smaller. The stock book paid 10.2bp of
    gross per rebalance and no gross edge came near it. This universe's
    estimated spreads run 0.03bp (BTC on Exness Zero,
    [[exness-zero-btcusd-is-spread-only]]) to ~20bp (natural gas), averaging
    ~3.6bp, and at GROSS=2.0 a rebalance costs roughly 7bp of equity. That is
    still the number to clear first, and `preflight` prints it beside the
    measured gross edge before any strategy is believed.

DESIGN

Three families, each a ranking rule plus a holding period:

  ``intraday``   rank on the trailing R buckets' vol-normalised return inside
                 the common session, hold H buckets, flat by the session close
  ``overnight``  rank on the session's own vol-normalised return, hold from the
                 last bucket's open to the next session's first bucket open
  ``daily``      rank on the trailing D sessions' vol-normalised return, enter
                 the next session's first open, hold M sessions open-to-open

Each with `direction` (momentum: long the winners; reversal: long the losers)
and `k`, the number of names a side. 350 cells total -- small on purpose,
because a large budget is what makes an in-sample number unreadable
([[coin-flip-control-beats-real-signals]]).

THE COMMON SESSION. These instruments do not share a clock: HK50 is dark after
13:00 New York, FR40 and STOXX50 before 02:00, crypto never. A rank is
meaningless if the universe changes size underneath it, so the panel is
restricted to 30-minute buckets from 04:00 to 11:30 New York -- the European
afternoon through the US morning -- and only buckets where all thirteen quote
survive. Timestamps are New York wall-clock encoded as fake UTC
([[questdb-stores-ny-wall-clock]]).

ENTRY TIMING. Signals are built from bucket t's close and enter at bucket
t+1's **open**; the leg return is open-to-open. Building a signal from a close
and filling at that same close is the fiction in [[entry-must-be-next-bar-open]].

REBALANCE. Non-overlapping: a cell holding H buckets rebalances every H
buckets. Overlapping tranches would average away the variance that makes the
t-statistic honest.

SIZING IS NOT SEARCHED. Gross leverage is fixed at `GROSS` = 2.0x equity, split
inverse-vol across the 2k legs. It is not an axis; searching sizing inside a
selection grid is how [[usoil-intraday-fails-twice]] produced a meaningless
number. `sensitivity` reports leverage separately, after selection.

COSTS ARE ESTIMATES, NOT MEASUREMENTS. `SPREAD_ABS` holds a typical Exness
spread in each symbol's own price units; `leg_costs()` converts it using the
symbol's median price in the evaluated window, so the units are right even
though the level is a judgement call. The 0/1/3/5/10/20/30bp sweep and
`breakeven_extra_bp` are the decision instruments -- read those, not the
headline ([[maroy-momentum-dies-on-real-costs]]).

SCORING. The t-statistic is computed on the per-rebalance portfolio return
series, never on equal-weighted session means ([[l2-discovery-found-nothing]]).

BENCHMARK AND NULL. The benchmark is the inverse-vol long-only basket of the
thirteen -- the factor this book exists to remove
([[zero-is-the-wrong-backtest-baseline]]). The null shuffles *which* symbols go
long and short while holding k, the schedule, the weights and the costs fixed,
so it prices exactly the selection this module performs.

WINDOW. 2018 is spent warming the 60-session volatility and 20-session lookback
windows ([[cold-start-oos-fakes-regime-edges]]). In-sample is 2019-01-01
through 2023-12-31; 2024-01-01 through 2026-08-10 is the untouched holdout.
Note that 2018-2019 is already spent as a second window for the index tables
([[index-cfds-fail-like-every-other-family-study]]), which is why it is used
for warm-up here and never as a holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import statistics
from datetime import datetime, timezone

from sandbox import data

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

#: Three sleeves. Palladium and platinum are excluded: their tables open in
#: late 2021 and would truncate the panel to under five years.
INDICES = ("aus200", "de40", "fr40", "hk50", "jp225", "stoxx50", "uk100")
COMMODITIES = ("ukoil", "xngusd", "xagusd", "xcuusd")
CRYPTO = ("btc", "ethusd")
SYMBOLS = INDICES + COMMODITIES + CRYPTO
SLEEVE = {s: name for name, group in
          (("index", INDICES), ("commodity", COMMODITIES), ("crypto", CRYPTO))
          for s in group}

#: Typical Exness spread in each symbol's own price units. Estimates, converted
#: to a fraction of the window's median price by `leg_costs`. Crypto is
#: the Exness Zero reading; the QuestDB rows are Binance USDT bars
#: ([[crypto-usd-tables-hold-usdt]]), so the price level is right but the venue
#: is not the one these spreads come from.
SPREAD_ABS = {
    "aus200": 1.0, "de40": 1.5, "fr40": 1.0, "hk50": 5.0, "jp225": 8.0,
    "stoxx50": 1.5, "uk100": 0.9,
    "ukoil": 0.03, "xngusd": 0.006, "xagusd": 0.02, "xcuusd": 0.002,
    "btc": 0.2, "ethusd": 0.2,
}

COST_SWEEP = (0.0, 1.0, 3.0, 5.0, 10.0, 20.0, 30.0)

PANEL_START = "2018-01-01"
IS_START = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 11, tzinfo=timezone.utc).timestamp())

#: The common session: 04:00 to 11:30 New York, in 30-minute buckets. A bucket
#: minute is kept only if it is present on most candidate days, which drops the
#: 04:30 slot the Hong Kong lunch break punches out of the panel.
BUCKET_MINUTES = tuple(range(240, 720, 30))
MIN_MINUTE_COVERAGE = 0.8

#: Trailing sessions used to estimate each symbol's volatility, for both the
#: rank normalisation and the leg weights. Not an axis -- it is a units
#: conversion, not a parameter to fit.
VOL_WINDOW = 60

#: Fixed, never searched.
GROSS = 2.0

MIN_REBALANCES = 100
SELECTION_DD_LIMIT = 20.0
MIN_IS_SHARPE = 0.5


# --------------------------------------------------------------------------- #
# aligned panel
# --------------------------------------------------------------------------- #


def panel():
    """`(timestamps, opens, closes)` aligned across all thirteen symbols.

    `opens[i][j]` is symbol j's open in bucket i. Only buckets where every
    symbol quotes are kept.
    """
    key = ("macro_xs_panel:" + ",".join(SYMBOLS) + ":" + PANEL_START + ":"
           + data._table_fingerprint([f"{s}_1m" for s in SYMBOLS]))

    def build():
        per = {}
        for symbol in SYMBOLS:
            sql = (
                "SELECT cast(timestamp as long) ts,first(open),last(close) "
                f"FROM {symbol}_1m WHERE timestamp >= '{PANEL_START}' "
                "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
            )
            rows = {}
            for row in data.query(sql):
                ts = int(row[0]) // 1_000_000
                if ts % 86_400 // 60 in BUCKET_MINUTES:
                    rows[ts] = (float(row[1]), float(row[2]))
            per[symbol] = rows
        common = set.intersection(*(set(r) for r in per.values()))
        # Drop bucket minutes that only a minority of days carry: a session
        # whose shape changes day to day makes `hold` mean different things.
        days = len({ts // 86_400 for ts in common})
        counts = {}
        for ts in common:
            counts[ts % 86_400 // 60] = counts.get(ts % 86_400 // 60, 0) + 1
        keep = {m for m, n in counts.items() if n >= MIN_MINUTE_COVERAGE * days}
        stamps = sorted(ts for ts in common if ts % 86_400 // 60 in keep)
        # A partial day would give the intraday family a short session and the
        # daily family a stale open; require the full bucket set.
        by_day = {}
        for ts in stamps:
            by_day.setdefault(ts // 86_400, []).append(ts)
        stamps = [ts for ts in stamps if len(by_day[ts // 86_400]) == len(keep)]
        opens = [[per[s][ts][0] for s in SYMBOLS] for ts in stamps]
        closes = [[per[s][ts][1] for s in SYMBOLS] for ts in stamps]
        return {"ts": stamps, "open": opens, "close": closes,
                "bucket_minutes": sorted(keep)}

    got = data._cached(f"macro_xs_panel_{len(SYMBOLS)}", key, build)
    return got["ts"], got["open"], got["close"]


def session_index(stamps):
    """`(day_of, first_bucket_of_day, last_bucket_of_day)` for each bucket."""
    day_of = [ts // 86_400 for ts in stamps]
    first, last = {}, {}
    for i, day in enumerate(day_of):
        first.setdefault(day, i)
        last[day] = i
    return day_of, first, last


def leg_costs(panel_data, lo, hi):
    """`SPREAD_ABS` as a *fraction* of each symbol's median price in [lo, hi).

    A fraction, not basis points: `run` charges it directly against a leg's
    return, and every display site multiplies by 1e4 itself.
    """
    stamps, _, closes = panel_data
    picked = [row for ts, row in zip(stamps, closes) if lo <= ts < hi]
    if not picked:
        picked = closes
    out = []
    for j, symbol in enumerate(SYMBOLS):
        median = statistics.median(row[j] for row in picked)
        out.append(SPREAD_ABS[symbol] / median)
    return out


# --------------------------------------------------------------------------- #
# volatility -- the units conversion that makes a heterogeneous rank meaningful
# --------------------------------------------------------------------------- #


#: `session_vol` is identical for every cell of a run and costs more than a
#: backtest does, so it is memoised on the panel it was built from.
_VOL_CACHE = {}


def session_vol(closes, sessions, last_of_day):
    """`{day: [stdev of the prior VOL_WINDOW session returns, per symbol]}`.

    Computed from *closed* sessions only, so the value used to rank on day d
    never sees day d's own move.
    """
    cached = _VOL_CACHE.get(id(closes))
    if cached is not None:
        return cached
    m = len(closes[0])
    daily = [[closes[last_of_day[day]][j] for j in range(m)] for day in sessions]
    rets = [[daily[i][j] / daily[i - 1][j] - 1.0 for j in range(m)]
            for i in range(1, len(daily))]
    out = {}
    for position in range(len(sessions)):
        if position - 1 < VOL_WINDOW:
            continue
        window = rets[position - 1 - VOL_WINDOW:position - 1]
        out[sessions[position]] = [
            max(statistics.pstdev([row[j] for row in window]), 1e-9)
            for j in range(m)
        ]
    _VOL_CACHE[id(closes)] = out
    return out


def weights(vol, picked):
    """Inverse-vol leg weights summing to `GROSS` of gross exposure.

    Equal dollars would hand bitcoin ~15x the risk of UK100 and make every
    portfolio statistic a statement about crypto.
    """
    legs = list(picked[0]) + list(picked[1])
    raw = {j: 1.0 / vol[j] for j in legs}
    total = sum(raw.values())
    return {j: GROSS * raw[j] / total for j in legs}


# --------------------------------------------------------------------------- #
# signals -- every one returns a per-bucket vol-normalised score a symbol
# --------------------------------------------------------------------------- #


def _normalise(values, vol, buckets_per_session):
    """`values` in units of the symbol's own volatility over the same horizon."""
    scale = math.sqrt(max(buckets_per_session, 1e-9))
    return [values[j] / (vol[j] / scale) for j in range(len(values))]


def trailing_return(closes, lookback, day_of, first_of_day, vols, per_session):
    """Vol-normalised return over the prior `lookback` buckets, within a session."""
    n, m = len(closes), len(closes[0])
    out = []
    for i in range(n):
        back = i - lookback
        vol = vols.get(day_of[i])
        if back < first_of_day[day_of[i]] or vol is None:
            out.append(None)
            continue
        raw = [closes[i][j] / closes[back][j] - 1.0 for j in range(m)]
        out.append(_normalise(raw, vol, per_session / lookback))
    return out


def session_return(opens, closes, day_of, first_of_day, vols):
    """Vol-normalised return from the session's first open to this bucket's close."""
    m = len(closes[0])
    out = []
    for i in range(len(closes)):
        vol = vols.get(day_of[i])
        if vol is None:
            out.append(None)
            continue
        first = first_of_day[day_of[i]]
        raw = [closes[i][j] / opens[first][j] - 1.0 for j in range(m)]
        out.append(_normalise(raw, vol, 1.0))
    return out


def daily_return(closes, sessions, back, last_of_day, vols):
    """Vol-normalised return over the prior `back` complete sessions."""
    m = len(closes[0])
    out = {}
    for position, day in enumerate(sessions):
        vol = vols.get(day)
        if position < back or vol is None:
            continue
        now = last_of_day[day]
        then = last_of_day[sessions[position - back]]
        raw = [closes[now][j] / closes[then][j] - 1.0 for j in range(m)]
        out[day] = _normalise(raw, vol, 1.0 / back)
    return out


# --------------------------------------------------------------------------- #
# portfolio
# --------------------------------------------------------------------------- #


def legs(score, k, direction, order=None):
    """The k longs and k shorts implied by `score`.

    `order` overrides the ranking with a supplied permutation, which is how the
    null randomises *which* names are picked while leaving k, the schedule, the
    weights and the costs exactly as the real cell has them.
    """
    if score is None:
        return None
    m = len(score)
    if order is None:
        order = sorted(range(m), key=lambda j: score[j])
    if 2 * k > m:
        return None
    low, high = order[:k], order[-k:]
    return (high, low) if direction == "momentum" else (low, high)


def run(pairs, leg_cost, extra_bp=0.0, initial=1000.0):
    """Compound a list of `(longs, shorts, entry, exit, vol)` rebalances."""
    equity = peak = initial
    drawdown = 0.0
    rets = []
    for longs, shorts, entry, exit_, vol in pairs:
        weight = weights(vol, (longs, shorts))
        gross = 0.0
        cost = 0.0
        for j in longs:
            gross += weight[j] * (exit_[j] / entry[j] - 1.0)
            cost += weight[j] * (leg_cost[j] + extra_bp / 1e4)
        for j in shorts:
            gross -= weight[j] * (exit_[j] / entry[j] - 1.0)
            cost += weight[j] * (leg_cost[j] + extra_bp / 1e4)
        step = gross - cost
        rets.append(step)
        equity *= (1.0 + step)
        if equity <= 0.0:
            equity = 0.0
            break
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return equity, drawdown, rets


def summarize(equity, drawdown, rets, stamps, initial=1000.0):
    if not rets:
        return {"rebalances": 0, "return_pct": 0.0, "max_dd_pct": 0.0,
                "t": 0.0, "monthly_sharpe": 0.0, "positive_months": "0/0",
                "mean_bp": 0.0, "final": initial}
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    t = mean / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
    months = {}
    for ts, r in zip(stamps, rets):
        key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + r
    values = list(months.values())
    msd = statistics.pstdev(values) if len(values) > 1 else 0.0
    sharpe = (statistics.fmean(values) / msd * math.sqrt(12)) if msd > 0 else 0.0
    return {
        "rebalances": len(rets),
        "return_pct": round(100.0 * (equity / initial - 1.0), 2),
        "max_dd_pct": round(100.0 * drawdown, 2),
        "t": round(t, 2),
        "monthly_sharpe": round(sharpe, 3),
        "positive_months": f"{sum(1 for v in values if v > 0)}/{len(values)}",
        "mean_bp": round(1e4 * mean, 3),
        "final": round(equity, 2),
    }


# --------------------------------------------------------------------------- #
# families
# --------------------------------------------------------------------------- #


def build_pairs(family, params, panel_data, lo, hi, shuffle=None):
    """Every (longs, shorts, entry, exit, vol) this cell takes inside [lo, hi)."""
    stamps, opens, closes = panel_data
    day_of, first_of, last_of = session_index(stamps)
    sessions = sorted(set(day_of))
    vols = session_vol(closes, sessions, last_of)
    per_session = len(stamps) / max(len(sessions), 1)
    k, direction = params["k"], params["direction"]
    m = len(SYMBOLS)
    out, when = [], []

    def pick(score, day):
        return legs(score, k, direction, None if shuffle is None else shuffle(m))

    if family == "daily":
        scores = daily_return(closes, sessions, params["lookback"], last_of, vols)
        hold = params["hold"]
        position = 0
        while position + 1 + hold < len(sessions):
            day = sessions[position]
            if day not in scores:
                position += 1
                continue
            entry_i = first_of[sessions[position + 1]]
            exit_i = first_of[sessions[position + 1 + hold]]
            if not lo <= stamps[entry_i] < hi:
                position += hold
                continue
            picked = pick(scores[day], day)
            if picked:
                out.append((picked[0], picked[1], opens[entry_i], opens[exit_i],
                            vols[day]))
                when.append(stamps[entry_i])
            position += hold
        return out, when

    if family == "overnight":
        scores = session_return(opens, closes, day_of, first_of, vols)
        for position in range(len(sessions) - 1):
            day = sessions[position]
            signal_i = last_of[day] - 1
            entry_i = last_of[day]
            exit_i = first_of[sessions[position + 1]]
            if signal_i < first_of[day] or day not in vols:
                continue
            if not lo <= stamps[entry_i] < hi:
                continue
            picked = pick(scores[signal_i], day)
            if picked:
                out.append((picked[0], picked[1], opens[entry_i], opens[exit_i],
                            vols[day]))
                when.append(stamps[entry_i])
        return out, when

    scores = trailing_return(closes, params["lookback"], day_of, first_of, vols,
                             per_session)
    hold = params["hold"]
    i = 0
    while i < len(stamps) - 1:
        day = day_of[i]
        entry_i, exit_i = i + 1, i + 1 + hold
        if exit_i > last_of[day] or day_of[entry_i] != day:
            i = last_of[day] + 1 if last_of[day] >= i else i + 1
            continue
        if scores[i] is None or not lo <= stamps[entry_i] < hi:
            i += 1
            continue
        picked = pick(scores[i], day)
        if picked:
            out.append((picked[0], picked[1], opens[entry_i], opens[exit_i],
                        vols[day]))
            when.append(stamps[entry_i])
        i += hold
    return out, when


AXES = {
    "intraday": {"direction": ("momentum", "reversal"),
                 "lookback": (1, 2, 4, 8),
                 "hold": (1, 2, 4, 8),
                 "k": (2, 3, 4, 5)},
    "overnight": {"direction": ("momentum", "reversal"),
                  "k": (2, 3, 4, 5)},
    "daily": {"direction": ("momentum", "reversal"),
              "lookback": (1, 2, 3, 5, 10, 20),
              "hold": (1, 2, 5, 10),
              "k": (2, 3, 4, 5)},
}


def cells(family):
    axes = AXES[family]
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def evaluate(family, params, panel_data, lo, hi, leg_cost, extra_bp=0.0,
             shuffle=None, initial=1000.0):
    pairs, when = build_pairs(family, params, panel_data, lo, hi, shuffle)
    equity, drawdown, rets = run(pairs, leg_cost, extra_bp, initial)
    return summarize(equity, drawdown, rets, when, initial)


# --------------------------------------------------------------------------- #
# benchmark
# --------------------------------------------------------------------------- #


def benchmark(panel_data, lo, hi, initial=1000.0):
    """Inverse-vol long-only basket of the thirteen, held open-to-open daily.

    This is the factor the long/short book exists to remove. Beating it is not
    required -- a market-neutral book with a lower return but no beta is still
    worth having -- but a long/short that merely tracks it has removed nothing.
    """
    stamps, opens, closes = panel_data
    day_of, first_of, last_of = session_index(stamps)
    sessions = sorted(set(day_of))
    vols = session_vol(closes, sessions, last_of)
    equity = peak = initial
    drawdown = 0.0
    rets, when = [], []
    for position in range(len(sessions) - 1):
        day = sessions[position]
        if day not in vols:
            continue
        a, b = first_of[day], first_of[sessions[position + 1]]
        if not lo <= stamps[a] < hi:
            continue
        raw = {j: 1.0 / vols[day][j] for j in range(len(SYMBOLS))}
        total = sum(raw.values())
        step = sum(raw[j] / total * (opens[b][j] / opens[a][j] - 1.0)
                   for j in range(len(SYMBOLS)))
        rets.append(step)
        when.append(stamps[a])
        equity *= (1.0 + step)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return summarize(equity, drawdown, rets, when, initial)


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #


def output_path():
    return os.path.join(RESULTS, "macro_cross_sectional.json")


def seal(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def cost_budget(panel_data, leg_cost):
    """What a rebalance costs, before any strategy is written.

    The stock cross-section died here and nowhere else, so the number comes
    first ([[cross-sectional-turnover-cost-is-the-binding-constraint]]). With
    inverse-vol weights the drag depends on *which* legs are picked, so it is
    reported as the equal-weight bound and the inverse-vol bound.
    """
    mean_bp = statistics.fmean(leg_cost) * 1e4
    inverse = {}
    stamps, _, closes = panel_data
    day_of, _, last_of = session_index(stamps)
    sessions = sorted(set(day_of))
    vols = session_vol(closes, sessions, last_of)
    sample = [vols[d] for d in sessions if d in vols]
    if sample:
        typical = [statistics.median(row[j] for row in sample)
                   for j in range(len(SYMBOLS))]
        raw = {j: 1.0 / typical[j] for j in range(len(SYMBOLS))}
        total = sum(raw.values())
        inverse["all_legs_bp"] = round(
            1e4 * GROSS * sum(raw[j] / total * leg_cost[j]
                              for j in range(len(SYMBOLS))), 3)
        inverse["typical_daily_vol_pct"] = {
            SYMBOLS[j]: round(100.0 * typical[j], 3) for j in range(len(SYMBOLS))
        }
    return {
        "per_symbol_bp": {SYMBOLS[j]: round(leg_cost[j] * 1e4, 3)
                          for j in range(len(SYMBOLS))},
        "mean_leg_bp": round(mean_bp, 3),
        "equal_weight_rebalance_bp": round(GROSS * mean_bp, 3),
        "gross_leverage": GROSS,
        **inverse,
    }


def gross_edge(panel_data, lo, hi):
    """Mean gross (zero-cost) return a rebalance for the plainest cell of each
    family, so the edge available can be compared with the cost budget without
    consulting a search."""
    zero = [0.0] * len(SYMBOLS)
    out = {}
    for family, probe in (("intraday", {"lookback": 1, "hold": 1, "k": 3}),
                          ("overnight", {"k": 3}),
                          ("daily", {"lookback": 1, "hold": 1, "k": 3})):
        for direction in ("momentum", "reversal"):
            params = dict(probe, direction=direction)
            stat = evaluate(family, params, panel_data, lo, hi, zero)
            out[f"{family}/{direction}"] = {
                "gross_bp_per_rebalance": stat["mean_bp"],
                "t": stat["t"],
                "rebalances": stat["rebalances"],
            }
    return out


def preflight(panel_data):
    stamps = panel_data[0]
    day_of, _, _ = session_index(stamps)
    leg_cost = leg_costs(panel_data, IS_START, OOS_END)
    budget = cost_budget(panel_data, leg_cost)
    edge = gross_edge(panel_data, IS_START, IS_END)
    print("\ncost budget")
    for symbol, value in budget["per_symbol_bp"].items():
        print(f"  {symbol:<9} {value:>8.3f} bp   ({SLEEVE[symbol]})")
    print(f"  mean leg                 {budget['mean_leg_bp']:.3f} bp")
    print(f"  rebalance @ {GROSS:.0f}x gross   "
          f"{budget['equal_weight_rebalance_bp']:.3f} bp (equal weight), "
          f"{budget.get('all_legs_bp', float('nan')):.3f} bp (inverse-vol, all legs)")
    print("\ngross edge in sample, zero cost, plainest cell a family")
    for name, row in edge.items():
        verdict = ("clears" if row["gross_bp_per_rebalance"]
                   > budget["equal_weight_rebalance_bp"] else "under cost")
        print(f"  {name:<22} {row['gross_bp_per_rebalance']:>8.3f} bp  "
              f"t {row['t']:+5.2f}  n={row['rebalances']:>5}  {verdict}")
    print("\npanel")
    print(f"  buckets {len(stamps):,}  sessions {len(set(day_of)):,}  "
          f"buckets/session {len(stamps) / len(set(day_of)):.1f}")
    print("  benchmark IS :", json.dumps(benchmark(panel_data, IS_START, IS_END)))
    print("  benchmark OOS:", json.dumps(benchmark(panel_data, IS_END, OOS_END)))
    return {"cost_budget": budget, "gross_edge_in_sample": edge}


def select(panel_data, leg_cost):
    """The best in-sample cell a family, plus why the rest were dropped.

    Gate attrition is recorded rather than discarded: "nothing passed" is only
    a statement about the universe if the cells died on edge, and only a
    statement about the gate if they died on the trade minimum. `best_unfiltered`
    is the ceiling the grid reached with every gate switched off -- if that is
    also weak, no loosening of the gate would have produced a candidate.
    """
    picked, attrition = {}, {}
    for family in AXES:
        ranked, all_cells = [], []
        drops = {"rebalances": 0, "drawdown": 0, "sharpe": 0}
        for params in cells(family):
            stat = evaluate(family, params, panel_data, IS_START, IS_END, leg_cost)
            if stat["rebalances"] < MIN_REBALANCES:
                # Excluded from `all_cells` too: a cell that never traded scores
                # a monthly Sharpe of exactly 0.0, which sorts above every real
                # losing cell and would report an empty book as the ceiling.
                drops["rebalances"] += 1
                continue
            all_cells.append((stat["monthly_sharpe"], params, stat))
            if stat["max_dd_pct"] > SELECTION_DD_LIMIT:
                drops["drawdown"] += 1
                continue
            if stat["monthly_sharpe"] < MIN_IS_SHARPE:
                drops["sharpe"] += 1
                continue
            ranked.append((stat["monthly_sharpe"], params, stat))
        ranked.sort(key=lambda item: item[0], reverse=True)
        all_cells.sort(key=lambda item: item[0], reverse=True)
        picked[family] = ({"params": ranked[0][1], "in_sample": ranked[0][2],
                           "passing_cells": len(ranked)}
                          if ranked else None)
        attrition[family] = {
            "cells": len(all_cells),
            "dropped": drops,
            "passed": len(ranked),
            "best_unfiltered": {"params": all_cells[0][1],
                                "in_sample": all_cells[0][2]},
        }
        mark = "-" if not ranked else (
            f"{ranked[0][2]['return_pct']:+.1f}% mSh {ranked[0][0]:.2f} "
            f"dd {ranked[0][2]['max_dd_pct']:.1f}% "
            f"(of {len(ranked)}/{len(cells(family))} passing)")
        print(f"  {family:<10} {mark}", flush=True)
        best = all_cells[0]
        print(f"             dropped {drops['rebalances']} on trades, "
              f"{drops['drawdown']} on drawdown, {drops['sharpe']} on sharpe; "
              f"ungated best mSh {best[0]:+.2f} "
              f"({best[2]['return_pct']:+.1f}%, dd {best[2]['max_dd_pct']:.1f}%, "
              f"t {best[2]['t']:+.2f}, {best[1]})", flush=True)
    return picked, attrition


def validate(panel_data, picked, leg_cost, seeds=50):
    out = {}
    for family, winner in picked.items():
        if winner is None:
            continue
        params = winner["params"]
        oos = evaluate(family, params, panel_data, IS_END, OOS_END, leg_cost)
        sweep = {f"{bp:.1f}": evaluate(family, params, panel_data, IS_END,
                                       OOS_END, leg_cost, extra_bp=bp)
                 for bp in COST_SWEEP}
        lo_bp, hi_bp = -30.0, 200.0
        for _ in range(40):
            mid = (lo_bp + hi_bp) / 2
            got = evaluate(family, params, panel_data, IS_END, OOS_END,
                           leg_cost, extra_bp=mid)
            if got["return_pct"] > 0:
                lo_bp = mid
            else:
                hi_bp = mid
        nulls = []
        for seed in range(seeds):
            rng = random.Random(10_000 + seed)

            def shuffle(m, rng=rng):
                order = list(range(m))
                rng.shuffle(order)
                return order

            nulls.append(evaluate(family, params, panel_data, IS_END, OOS_END,
                                  leg_cost, shuffle=shuffle)["return_pct"])
        nulls.sort()
        beat = sum(1 for value in nulls if oos["return_pct"] > value)
        out[family] = {
            "params": params,
            "passed_selection_gate": winner.get("gated", True),
            "in_sample": winner["in_sample"],
            "in_sample_passing_cells": winner["passing_cells"],
            "out_of_sample": oos,
            "cost_sweep_bp": {k: v["return_pct"] for k, v in sweep.items()},
            "breakeven_extra_bp": round(lo_bp, 2),
            "null_median_pct": round(statistics.median(nulls), 2),
            "null_max_pct": round(nulls[-1], 2),
            "null_p95_pct": round(nulls[int(0.95 * len(nulls))], 2),
            "null_percentile": round(100.0 * beat / len(nulls), 1),
        }
        flag = "" if winner.get("gated", True) else "  [UNGATED - not a candidate]"
        print(f"  {family:<10} OOS {oos['return_pct']:+7.2f}%  "
              f"t {oos['t']:+.2f}  mSh {oos['monthly_sharpe']:+.2f}  "
              f"dd {oos['max_dd_pct']:.1f}%  n={oos['rebalances']}  "
              f"null p{out[family]['null_percentile']:.0f} "
              f"(med {out[family]['null_median_pct']:+.1f}%, "
              f"max {out[family]['null_max_pct']:+.1f}%){flag}", flush=True)
    return out


def verdict(picked, validated):
    """Promotable / not, stated once so the JSON cannot be read optimistically.

    A cell is promotable only if it passed the in-sample gate on its own, made
    money out of sample, and beat its own coin-flip null at the 95th percentile
    ([[coin-flip-control-beats-real-signals]]).
    """
    promotable = [
        family for family, row in validated.items()
        if row["passed_selection_gate"]
        and row["out_of_sample"]["return_pct"] > 0
        and row["out_of_sample"]["return_pct"] > row["null_p95_pct"]
    ]
    return {
        "promotable": promotable,
        "families_passing_selection": [f for f, w in picked.items() if w],
        "summary": ("no candidate" if not promotable else
                    f"promotable: {', '.join(promotable)}"),
    }


def sensitivity(panel_data, picked, leg_cost):
    """Leverage sensitivity, run *after* selection and never inside it."""
    global GROSS
    base = GROSS
    out = {}
    for family, winner in picked.items():
        if winner is None:
            continue
        row = {}
        for lever in (1.0, 2.0, 4.0, 8.0):
            GROSS = lever
            got = evaluate(family, winner["params"], panel_data, IS_END,
                           OOS_END, leg_cost)
            row[f"{lever:.0f}x"] = {"return_pct": got["return_pct"],
                                    "max_dd_pct": got["max_dd_pct"]}
        out[family] = row
    GROSS = base
    return out


def why(panel_data, picked, leg_cost):
    """Diagnostics that explain a result rather than score it.

    * `raw_rank` reruns the winner without vol normalisation, which measures how
      much of the result is the normalisation and how much is the signal.
    * `sleeve_exposure` counts how often each sleeve appears long and short: a
      "macro cross-section" that is really long/short crypto shows up here.
    * `zero_cost_ceiling` reruns the whole grid with costs switched off. This is
      the difference between the two possible negative results: if nothing wins
      even at zero cost the universe has no cross-sectional signal, and if cells
      win at zero cost and lose at the real spread the signal exists but is
      smaller than the turnover
      ([[cross-sectional-turnover-cost-is-the-binding-constraint]]).
    """
    out = {}
    for family, winner in picked.items():
        if winner is None:
            continue
        pairs, _ = build_pairs(family, winner["params"], panel_data,
                               IS_END, OOS_END)
        counts = {name: {"long": 0, "short": 0} for name in ("index", "commodity", "crypto")}
        for longs, shorts, _e, _x, _v in pairs:
            for j in longs:
                counts[SLEEVE[SYMBOLS[j]]]["long"] += 1
            for j in shorts:
                counts[SLEEVE[SYMBOLS[j]]]["short"] += 1
        total = sum(v["long"] + v["short"] for v in counts.values()) or 1
        out[family] = {
            "sleeve_exposure_pct": {
                name: {side: round(100.0 * n / total, 1)
                       for side, n in row.items()}
                for name, row in counts.items()
            },
        }

    zero = [0.0] * len(SYMBOLS)
    ceiling = {}
    for family in AXES:
        ranked = []
        for params in cells(family):
            stat = evaluate(family, params, panel_data, IS_START, IS_END, zero)
            if stat["rebalances"] >= MIN_REBALANCES:
                ranked.append((stat["monthly_sharpe"], params, stat))
        if not ranked:
            continue
        ranked.sort(key=lambda item: item[0], reverse=True)
        _, params, stat = ranked[0]
        oos_zero = evaluate(family, params, panel_data, IS_END, OOS_END, zero)
        # A zero-cost holdout number needs its own null: without it, "the gross
        # signal persisted" is indistinguishable from the ceiling that random
        # direction reaches on the same schedule.
        nulls = []
        for seed in range(50):
            rng = random.Random(20_000 + seed)

            def shuffle(m, rng=rng):
                order = list(range(m))
                rng.shuffle(order)
                return order

            nulls.append(evaluate(family, params, panel_data, IS_END, OOS_END,
                                  zero, shuffle=shuffle)["return_pct"])
        nulls.sort()
        ceiling[family] = {
            "params": params,
            "in_sample_zero_cost": stat,
            "in_sample_with_costs": evaluate(family, params, panel_data,
                                             IS_START, IS_END, leg_cost),
            "out_of_sample_zero_cost": oos_zero,
            "out_of_sample_zero_cost_null_median_pct": round(statistics.median(nulls), 2),
            "out_of_sample_zero_cost_null_p95_pct": round(nulls[int(0.95 * len(nulls))], 2),
            "out_of_sample_zero_cost_null_percentile": round(
                100.0 * sum(1 for v in nulls if oos_zero["return_pct"] > v) / len(nulls), 1),
            "cost_multiple_needed": (
                round(GROSS * statistics.fmean(leg_cost) * 1e4 / stat["mean_bp"], 2)
                if stat["mean_bp"] > 0 else None),
        }
        print(f"  {family:<10} zero-cost best mSh {stat['monthly_sharpe']:+.2f} "
              f"({stat['return_pct']:+.1f}%, {stat['mean_bp']:+.2f} bp/rebalance, "
              f"t {stat['t']:+.2f}) -> with costs "
              f"{ceiling[family]['in_sample_with_costs']['return_pct']:+.1f}%, "
              f"zero-cost OOS "
              f"{oos_zero['return_pct']:+.1f}% "
              f"(null p{ceiling[family]['out_of_sample_zero_cost_null_percentile']:.0f}, "
              f"med {ceiling[family]['out_of_sample_zero_cost_null_median_pct']:+.1f}%); "
              f"needs {ceiling[family]['cost_multiple_needed']}x the gross edge "
              f"to clear cost", flush=True)
    out["zero_cost_ceiling"] = ceiling
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preflight", "run"))
    parser.add_argument("--seeds", type=int, default=50)
    args = parser.parse_args()

    panel_data = panel()
    stamps = panel_data[0]
    span = (datetime.fromtimestamp(stamps[0], tz=timezone.utc),
            datetime.fromtimestamp(stamps[-1], tz=timezone.utc))
    print(f"panel: {len(stamps):,} aligned buckets across {len(SYMBOLS)} symbols "
          f"{span[0]:%Y-%m-%d}..{span[1]:%Y-%m-%d}")
    print(f"grid: {sum(len(cells(f)) for f in AXES)} cells across {len(AXES)} families")

    if args.phase == "preflight":
        preflight(panel_data)
        return

    leg_cost = leg_costs(panel_data, IS_START, OOS_END)
    print("\nselect (in sample 2019-01-01..2023-12-31)")
    picked, attrition = select(panel_data, leg_cost)

    # A family with no passing cell still gets carried to the holdout, flagged
    # `gated: false`. It is not a candidate and must never be read as one --
    # but "the best the grid could do also failed out of sample" is evidence,
    # and leaving the row blank throws that evidence away.
    carried = {}
    for family, winner in picked.items():
        if winner is not None:
            carried[family] = dict(winner, gated=True)
        else:
            best = attrition[family]["best_unfiltered"]
            carried[family] = {"params": best["params"],
                               "in_sample": best["in_sample"],
                               "passing_cells": 0, "gated": False}

    print(f"\nvalidate (holdout 2024-01-01..2026-08-10, {args.seeds} null seeds)")
    validated = validate(panel_data, carried, leg_cost, args.seeds)
    print("\nzero-cost ceiling (is there any signal at all?)")
    diagnostics = why(panel_data, carried, leg_cost)
    payload = {
        "sealed": True,
        "verdict": verdict(picked, validated),
        "selection_attrition": attrition,
        "protocol": {
            "universe": list(SYMBOLS),
            "sleeves": {"index": list(INDICES), "commodity": list(COMMODITIES),
                        "crypto": list(CRYPTO)},
            "gross_leverage": GROSS,
            "warmup": f"{PANEL_START}..2018-12-31",
            "in_sample": "2019-01-01..2023-12-31",
            "out_of_sample": "2024-01-01..2026-08-10",
            "grid_cells": {f: len(cells(f)) for f in AXES},
            "session": "04:00..11:30 New York, 30m buckets, all symbols quoting",
            "rank": f"return divided by trailing {VOL_WINDOW}-session volatility",
            "weights": "inverse volatility, gross fixed, never searched",
            "entry": "signal from bucket t close, fill at bucket t+1 open",
            "costs": "each leg pays its own estimated spread once a round trip",
            "null": "which symbols go long/short is shuffled; k, schedule, "
                    "weights and costs held fixed",
            "selection_gate": f"mSharpe >= {MIN_IS_SHARPE}, dd <= "
                              f"{SELECTION_DD_LIMIT}%, >= {MIN_REBALANCES} rebalances",
        },
        "cost_budget": cost_budget(panel_data, leg_cost),
        "gross_edge_in_sample": gross_edge(panel_data, IS_START, IS_END),
        "benchmark": {
            "in_sample": benchmark(panel_data, IS_START, IS_END),
            "out_of_sample": benchmark(panel_data, IS_END, OOS_END),
        },
        "families": validated,
        "leverage_sensitivity": sensitivity(panel_data, carried, leg_cost),
        "diagnostics": diagnostics,
    }
    seal(payload, output_path())
    print(f"\nsealed -> {output_path()}")


if __name__ == "__main__":
    main()
