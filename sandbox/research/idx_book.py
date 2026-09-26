"""One Stockbit account running any set of IDX strategies on any stocks.

    py -B -m sandbox.research.idx_book run        # BOOK as configured below
    py -B -m sandbox.research.idx_book study      # pick MANAGEMENT in-sample, judge it on the holdout
    py -B -m sandbox.research.idx_book pick       # the same for PICK: good stocks, best signal, rotation

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE: terminal output does
not reach the user. Paste the tables -- including the per-sleeve table -- into
the reply. Both commands also write `results/IDX_BOOK_<date>.md`.

WHAT YOU EDIT.

`BOOK` names the sleeves: a strategy from `idx_families.FAMILIES`, its
parameters, and the stocks it may trade ("all", or a tuple of codes). Any
strategy may run on any stock, and several sleeves may watch the same stock --
but the account holds at most ONE position per stock, so two sleeves never
double the same exposure. When more stocks fire than the account can take, the
sleeve listed FIRST wins, and within a sleeve its `rank_score` picks (most
beaten-down for the fades, strongest 60-session return for the trend rules,
highest yield for `dividend`). The default order is each sleeve's in-sample
score from the one-account study.

`SIZING` is how much each trade gets, and it is NOT searched: a sizing knob
tuned on the same data it is scored on just selects leverage for the sample's
best stretch ([[trailing-exits-beat-fixed-targets]]). Every trade risks
`risk_per_trade` of equity to its initial stop, capped by `max_position` of
equity, by cash, and by `max_heat` -- the summed risk every open position
still has against its entry (zero once its stop is at breakeven). A Rp1jt
account cannot buy fractions of a lot, so when the risk rule rounds to zero
lots, one lot is still taken if its risk stays under `min_lot_risk`. `mode = "equal"` swaps all of that for equity split
evenly over `max_positions` slots, the old account's rule, kept as a reference.

`MANAGEMENT` sits on top of every sleeve's own exit rule:

    entry        "open" buys at the next open, one tick worse; "limit" rests a
                 day order at the signal close and fills only if the day
                 trades down to it
    chase_atr    skip the buy if the next open gapped more than this many ATR
                 above the signal close (None: never skip)
    stop_fill    "close" reads the stop on the close and sells at the next open;
                 "intraday" is a Stockbit auto-order -- sold at the stop, or at
                 the open if it gapped through, and not at all at ARB
    breakeven_r  once the close is this many R above entry, the stop moves to
                 entry plus round-trip fees
    trail_atr    a book-wide chandelier: sell when the close falls this many ATR
                 under the best close since entry
    max_hold     sell after this many sessions

`PICK` decides which signals get the money: only stocks where the sleeve's
own point-in-time track record is good (`min_edge`), the best record first
across sleeves (`rank`), and selling a losing position to fund a stronger
signal when the account is full or short of cash (`rotate`). The record is
each sleeve's rule run on each stock with capital ignored (`shadow_trades`),
read only for trades that had closed by the day being decided.

`study` scores every MANAGEMENT combination on the first half of the calendar,
keeps the best one that passes the gates with 60% of its neighbours, and then
runs it -- and the default -- on the second half. `pick` does the same for
PICK with MANAGEMENT at its default. The BOOK's own parameters
were selected by `idx_families account` on the same first half, so the second
half is untouched by both choices.

THE UNIVERSE IS TODAY'S LQ45/ISSI SURVIVORS. A long-only book on stocks that
are in the index because they went up is flattered, the trend sleeves most.
"""
from __future__ import annotations

import argparse
import bisect
import itertools
import json
import math
import multiprocessing
import os
import statistics
from collections import defaultdict
from datetime import date

from sandbox.research import idx_families as fam
from sandbox.research import idx_stockbit_backtest as sb

D, O, H, L, C = range(5)
RESULTS = fam.RESULTS

CAPITAL = 1_000_000.0               # rupiah
SPLIT = 0.5                         # first half of the calendar selects

# --------------------------------------------------------------------------- #
# what the account trades
# --------------------------------------------------------------------------- #

#: sleeve -> {"family", "symbols", "params"}. Listed order is entry priority.
#: Parameters are the `idx_families account` winners (in-sample 2016-01-04 to
#: 2021-05-03, rerun after the Rp50-floor sell fix), minus their `slots`,
#: which SIZING replaces, in order of in-sample score. `rsi2` is out: with
#: BUMI able to sell at the floor, no rsi2 cell clears PF 1.05 in-sample.
BOOK = {
    "ma_trend": {"family": "ma_trend", "symbols": "all", "params": {
        "pair": (10, 50), "exit": "trail_4", "stop_atr": 3.0, "regime": "market"}},
    "high_52w": {"family": "high_52w", "symbols": "all", "params": {
        "lookback": 120, "near": 0.02, "exit": "channel_20", "stop_atr": 5.0,
        "regime": "none"}},
    "donchian": {"family": "donchian", "symbols": "all", "params": {
        "channel": 60, "exit": "trail_4", "stop_atr": 5.0, "regime": "market"}},
    "dip": {"family": "dip", "symbols": "all", "params": {
        "trend": "sma200", "drop_days": 3, "drop_atr": 2.5, "exit": "trail_2",
        "stop_atr": 3.0, "regime": "market"}},
    "dividend": {"family": "dividend", "symbols": "all", "params": {
        "days_before": 15, "exit": "ex_5", "stop_atr": 2.0, "regime": "none"}},
}

SIZING = {
    "mode": "risk",                 # "risk", or "equal" (equity / max_positions)
    "risk_per_trade": 0.03,         # equity lost if the initial stop is hit
    "max_position": 0.35,           # position value cap, fraction of equity
    "max_positions": 5,
    "max_heat": 0.12,               # summed open risk to stops, fraction of equity
    "min_lot_risk": 0.05,           # one lot allowed up to this risk when sizing rounds to 0
}

MANAGEMENT = {"entry": "open", "chase_atr": None, "stop_fill": "close",
              "breakeven_r": None, "trail_atr": None, "max_hold": None}

MANAGEMENT_GRID = {
    "entry": ("open", "limit"),
    "chase_atr": (None, 1.0),
    "stop_fill": ("close", "intraday"),
    "breakeven_r": (None, 1.0, 2.0),
    "trail_atr": (None, 3.0, 5.0),
    "max_hold": (None, 20, 60),
}
#: Which signals get the account's money.
#:   min_edge  only enter when the sleeve's track record on that stock -- its
#:             average trade, in %, over trades that CLOSED before the signal
#:             day, shrunk toward the sleeve's average on every stock -- is at
#:             least this (None: no filter). "Good stocks", point in time.
#:   rank      "priority" takes sleeves in BOOK order; "edge" takes the best
#:             track record first, whatever sleeve it comes from
#:   rotate    when a signal has no slot, or not half the cash its size wants,
#:             sell the worst position that is under water AND has a weaker
#:             record than the signal, and buy the signal with the proceeds
PICK = {"min_edge": None, "rank": "priority", "rotate": False}

PICK_GRID = {
    "min_edge": (None, 0.0, 1.0, 2.0),
    "rank": ("priority", "edge"),
    "rotate": (False, True),
}

#: Weight, in trades, of the sleeve-wide average when a stock's own record is
#: short. Fixed, not searched: with 2 trades on a stock the estimate is 2/7 its
#: own and 5/7 the sleeve's.
EDGE_SHRINK = 5

#: Axes whose values are labels rather than a scale; they have no neighbours.
LABELS = ("entry", "chase_atr", "stop_fill", "rank", "rotate")

#: Gates for the whole book, as `idx_families`' one-account study.
MAX_DD = fam.ACCOUNT_MAX_DD
ANNUAL_DD = fam.ACCOUNT_ANNUAL_DD
NEIGHBOUR_DD = fam.ACCOUNT_NEIGHBOUR_DD
MIN_PROFIT_FACTOR = fam.MIN_PROFIT_FACTOR
TRADES_PER_YEAR = 3

# --------------------------------------------------------------------------- #
# the account
# --------------------------------------------------------------------------- #


def limits(ctx, i):
    reference = ctx["reference"][i]
    return (sb.price_limits(fam.as_date(ctx["bars"][i][D]), reference)
            if reference else (math.inf, 0.0))


def shadow_trades(ctx, params, family):
    """Every trade the sleeve's own rule takes on one stock, capital ignored.

    (exit date, return %) per trade, fees and dividends included, filled the
    way the account fills: next open, one tick worse. This is the record
    `edge_of` reads, and it is only ever read for trades that had CLOSED by
    the day being decided.
    """
    bars, entry = ctx["bars"], fam.FAMILIES[family].entry
    out, trade, buy, sell = [], None, None, False
    for i, (day, o, _h, _l, c) in enumerate(bars):
        if trade is not None and i in ctx["dividends"]:
            ex, amount = ctx["dividends"][i]
            trade["dividends"] += amount * (1 - sb.dividend_tax(ex))
        if sell:
            price = o - sb.tick_size(o)
            gross = price * (1 - sb.SELL_FEE) + trade["dividends"]
            out.append((day, 100 * (gross / (trade["entry"] * (1 + sb.BUY_FEE)) - 1)))
            trade, sell = None, False
        if buy is not None:
            price = o + sb.tick_size(o)
            distance = price * params["stop_pct"] / 100 if "stop_pct" in params else buy[0]
            trade = {"entry": price, "index": i, "stop": price - distance,
                     "best": price, "ex": buy[1], "dividends": 0.0,
                     "target": price * (1 + params.get("tp_pct", math.inf) / 100)}
            buy = None
        if i == len(bars) - 1:
            break
        if trade is not None:
            sell = c <= trade["stop"] or fam.exit_due(i, ctx, trade, params["exit"])
        elif params["regime"] == "none" or ctx["market_up"][i]:
            reach = ctx["atr"][i]
            signal = reach and entry(i, ctx, params)
            if signal:
                buy = (params.get("stop_atr", 0) * reach,
                       signal if type(signal) is int else None)
    return out


def build_tracks(ctxs, book):
    """sleeve -> {stock or "*": (exit days, running sum of returns)}."""
    tracks = {}
    for name, sleeve in book.items():
        per, pooled = {}, []
        for s, ctx in ctxs.items():
            rows = shadow_trades(ctx, sleeve["params"], sleeve["family"])
            pooled += rows
            per[s] = rows
        per["*"] = sorted(pooled)
        tracks[name] = {}
        for key, rows in per.items():
            days, sums, total = [], [], 0.0
            for day, ret in rows:
                total += ret
                days.append(day)
                sums.append(total)
            tracks[name][key] = (days, sums)
    return tracks


def edge_of(tracks, sleeve, stock, day):
    """Shrunk average trade (%) of `sleeve` on `stock`, from trades closed by `day`."""
    if not tracks:
        return 0.0

    def tally(key):
        days, sums = tracks[sleeve].get(key, ((), ()))
        k = bisect.bisect_right(days, day)
        return k, (sums[k - 1] if k else 0.0)

    n_all, s_all = tally("*")
    prior = s_all / n_all if n_all else 0.0
    n, s = tally(stock)
    return (s + EDGE_SHRINK * prior) / (n + EDGE_SHRINK)


def lots_for(price, stop_distance, equity, cash, heat, sizing):
    """Shares to buy under SIZING, whole lots; 0 when nothing fits."""
    lot_cost = price * sb.LOT * (1 + sb.BUY_FEE)
    if sizing["mode"] == "equal":
        return int(min(equity / sizing["max_positions"], cash) // lot_cost) * sb.LOT
    by_value = int(min(sizing["max_position"] * equity, cash) // lot_cost)
    room = sizing["max_heat"] * equity - heat
    lot_risk = stop_distance * sb.LOT
    if by_value < 1 or room <= 0 or lot_risk <= 0:
        return 0
    lots = min(by_value, int(min(sizing["risk_per_trade"] * equity, room) // lot_risk))
    if lots == 0 and lot_risk <= min(sizing["min_lot_risk"] * equity, room):
        lots = 1
    return lots * sb.LOT


def run_book(ctxs, calendar, regime, book, sizing, manage, lo, hi, capital,
             pick=None, tracks=None):
    pick = PICK if pick is None else pick
    cash = capital
    positions, last_close = {}, {}
    sells, orders = {}, []
    trades, curve = [], []
    counts = defaultdict(int)
    fees = received = 0.0
    held = 0
    members = {name: (list(ctxs) if s["symbols"] == "all"
                      else [x for x in s["symbols"] if x in ctxs])
               for name, s in book.items()}

    def marked():
        return sum(t["shares"] * last_close[s] for s, t in positions.items())

    def heat():
        # Risk still at stake against the cost basis: zero once the stop is at
        # or above entry. Measured from the mark instead, a running winner's
        # open profit counted as risk and blocked every new entry.
        return sum(t["shares"] * max(0.0, t["entry"] - t["stop"])
                   for t in positions.values())

    def close_trade(symbol, price, day, index, reason):
        nonlocal cash, fees
        t = positions.pop(symbol)
        fee = price * t["shares"] * sb.SELL_FEE
        proceeds = price * t["shares"] - fee
        cash += proceeds
        fees += fee
        t.update(exit_date=str(day), exit=price, exit_index=index, reason=reason,
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

        # The open: yesterday's sell orders first, so their cash funds the buys.
        for s in sorted(sells):
            i = today[s]
            if i is None:
                continue
            o = ctxs[s]["bars"][i][O]
            _upper, lower = limits(ctxs[s], i)
            if sb.sell_locked(o, lower):
                counts["sell_blocked_at_arb"] += 1
                continue
            close_trade(s, max(o - sb.tick_size(o), lower), day, i, sells.pop(s))
        equity = cash + marked()
        for order in orders:
            s = order["symbol"]
            i = today[s]
            if (i is None or s in positions
                    or len(positions) >= sizing["max_positions"]):
                continue
            bar = ctxs[s]["bars"][i]
            o = bar[O]
            upper, _lower = limits(ctxs[s], i)
            if o >= upper:
                counts["buy_blocked_at_ara"] += 1
                continue
            if (manage["chase_atr"] is not None
                    and o > order["close"] + manage["chase_atr"] * order["atr"]):
                counts["gap_skipped"] += 1
                continue
            if manage["entry"] == "limit":
                if bar[L] > order["close"]:
                    counts["limit_unfilled"] += 1
                    continue
                price = min(o + sb.tick_size(o), order["close"])
            else:
                price = min(o + sb.tick_size(o), upper)
            params = book[order["sleeve"]]["params"]
            distance = (price * params["stop_pct"] / 100 if "stop_pct" in params
                        else order["distance"])
            shares = lots_for(price, distance, equity, cash, heat(), sizing)
            if shares == 0:
                counts["too_small_to_size"] += 1
                continue
            fee = price * shares * sb.BUY_FEE
            cash -= price * shares + fee
            fees += fee
            last_close.setdefault(s, price)
            positions[s] = {
                "sleeve": order["sleeve"], "symbol": s, "entry_date": str(day),
                "entry": price, "index": i, "shares": shares,
                "cost": price * shares + fee, "dividends": 0.0,
                "stop": price - distance, "risk_per_share": distance,
                "best": price, "peak": price, "ex": order["ex"],
                "target": price * (1 + params.get("tp_pct", math.inf) / 100)}
        orders = []

        # The session: Stockbit auto-order stops trigger on the day's low.
        if manage["stop_fill"] == "intraday":
            for s in list(positions):
                t, i = positions[s], today[s]
                if i is None or s in sells:
                    continue
                bar = ctxs[s]["bars"][i]
                if bar[L] > t["stop"]:
                    continue
                _upper, lower = limits(ctxs[s], i)
                if sb.sell_locked(bar[O], lower):
                    counts["stop_blocked_at_arb"] += 1
                    continue
                price = max(min(bar[O], t["stop"]) - sb.tick_size(t["stop"]), lower)
                close_trade(s, price, day, i, "stop")

        for s, i in today.items():
            if i is not None:
                last_close[s] = ctxs[s]["bars"][i][C]

        # The close: exits, then entries for whatever room is left.
        if n < hi - 1:
            for s, t in positions.items():
                i = today[s]
                if i is None or s in sells:
                    continue
                ctx, c = ctxs[s], last_close[s]
                reach = ctx["atr"][i] or 0.0
                if (manage["breakeven_r"] is not None
                        and c >= t["entry"] + manage["breakeven_r"] * t["risk_per_share"]):
                    t["stop"] = max(t["stop"], t["entry"] * (1 + sb.BUY_FEE + sb.SELL_FEE))
                t["peak"] = max(t["peak"], c)
                reason = None
                if c <= t["stop"]:
                    reason = "stop"
                elif fam.exit_due(i, ctx, t, book[t["sleeve"]]["params"]["exit"]):
                    reason = "rule"
                elif (manage["trail_atr"] is not None
                      and c < t["peak"] - manage["trail_atr"] * reach):
                    reason = "trail"
                elif (manage["max_hold"] is not None
                      and i - t["index"] + 1 >= manage["max_hold"]):
                    reason = "time"
                if reason:
                    sells[s] = reason
            room = sizing["max_positions"] - (len(positions) - len(sells))
            if room > 0 or pick["rotate"]:
                picks = []
                for rank, (name, sleeve) in enumerate(book.items()):
                    params = sleeve["params"]
                    if params["regime"] == "market" and not regime.get(day, False):
                        continue
                    family = sleeve["family"]
                    entry = fam.FAMILIES[family].entry
                    for s in members[name]:
                        i = today[s]
                        if i is None or s in positions:
                            continue
                        ctx = ctxs[s]
                        reach = ctx["atr"][i]
                        if not reach:
                            continue
                        signal = entry(i, ctx, params)
                        if not signal:
                            continue
                        edge = edge_of(tracks, name, s, day)
                        if pick["min_edge"] is not None and edge < pick["min_edge"]:
                            counts["below_min_edge"] += 1
                            continue
                        score = fam.rank_score(family, ctx, i, signal)
                        order_key = ((-edge, rank, -score) if pick["rank"] == "edge"
                                     else (rank, -score))
                        picks.append((order_key, s, name, signal, reach, edge))
                picks.sort(key=lambda p: (p[0], p[1]))
                equity_now = cash + marked()
                heat_now = heat()
                spare = cash + sum(positions[x]["shares"] * last_close[x] * (1 - sb.SELL_FEE)
                                   for x in sells)
                chosen = set()
                for _key, s, name, signal, reach, edge in picks:
                    if s in chosen:
                        continue
                    params = book[name]["params"]
                    close = last_close[s]
                    distance = (close * params["stop_pct"] / 100 if "stop_pct" in params
                                else params.get("stop_atr", 0) * reach)
                    if pick["rotate"]:
                        want = lots_for(close, distance, equity_now, math.inf, heat_now, sizing)
                        have = lots_for(close, distance, equity_now, spare, heat_now, sizing)
                        if want == 0:
                            continue             # cannot be sized even with cash to spare
                        if room <= 0 or 2 * have < want:
                            # Short of a slot or of half its cash: make room by
                            # selling the worst position that is under water and
                            # whose own record is weaker than this signal's.
                            victims = sorted(
                                (last_close[x] / t["entry"] - 1, x)
                                for x, t in positions.items()
                                if x not in sells and last_close[x] < t["entry"]
                                and edge_of(tracks, t["sleeve"], x, day) < edge)
                            if victims:
                                victim = victims[0][1]
                                sells[victim] = "rotate"
                                spare += (positions[victim]["shares"] * last_close[victim]
                                          * (1 - sb.SELL_FEE))
                                room += 1
                                counts["rotations"] += 1
                            elif room <= 0:
                                counts["no_room"] += 1
                                continue
                        spare -= min(have, want) * close * (1 + sb.BUY_FEE)
                        heat_now += min(have, want) * distance
                    elif room <= 0:
                        break
                    chosen.add(s)
                    room -= 1
                    orders.append({
                        "symbol": s, "sleeve": name, "atr": reach, "close": close,
                        "distance": params.get("stop_atr", 0) * reach,
                        "ex": signal if type(signal) is int else None})

        held += bool(positions)
        curve.append((day, cash + marked()))

    for s in list(positions):               # the window ends: mark out at the close
        ctx = ctxs[s]
        i = max(j for j, bar in enumerate(ctx["bars"]) if bar[D] <= calendar[hi - 1])
        c = ctx["bars"][i][C]
        close_trade(s, c - sb.tick_size(c), ctx["bars"][i][D], i, "end")
    if curve:
        curve[-1] = (curve[-1][0], cash)

    fam.INITIAL_CAPITAL = capital
    stat = fam.summarise(trades, curve, fees, received, 0, 0, held)
    stat["events"] = dict(counts)
    stat["exit_reasons"] = dict(sorted(
        {r: sum(t["reason"] == r for t in trades) for r in {t["reason"] for t in trades}}.items()))
    stat["sleeves"] = sleeve_table(trades, book)
    stat["top_stocks"] = top_stocks(trades)
    return stat


def closed_trade_dd(trades, capital):
    """Peak-to-trough of cumulative closed P&L, as a share of the capital."""
    running = peak = worst = 0.0
    for t in sorted(trades, key=lambda t: t["exit_date"]):
        running += t["pnl"]
        peak = max(peak, running)
        worst = max(worst, peak - running)
    return 100 * worst / capital


def sleeve_table(trades, book):
    out = {}
    total = sum(t["pnl"] for t in trades)
    for name in book:
        mine = [t for t in trades if t["sleeve"] == name]
        pnl = sum(t["pnl"] for t in mine)
        out[name] = {
            "trades": len(mine), "pnl": round(pnl, 2),
            "share_pct": round(100 * pnl / total, 1) if total else 0.0,
            "win_rate_pct": round(100 * sum(t["pnl"] > 0 for t in mine) / len(mine), 1)
            if mine else 0.0,
            "avg_trade_pct": round(statistics.fmean(t["return_pct"] for t in mine), 2)
            if mine else 0.0,
            "closed_dd_pct": round(closed_trade_dd(mine, fam.INITIAL_CAPITAL), 1),
        }
    return out


def top_stocks(trades, n=5):
    by = defaultdict(float)
    for t in trades:
        by[t["symbol"]] += t["pnl"]
    return [(s, round(v)) for s, v in sorted(by.items(), key=lambda kv: -kv[1])[:n]]


# --------------------------------------------------------------------------- #
# gates and selection
# --------------------------------------------------------------------------- #


def passes(stat, sessions, dd=MAX_DD):
    years = fam.gate_years(stat)
    positive = sum(stat["annual"][y]["pnl"] > 0 for y in years)
    worst = max((stat["annual"][y]["max_dd_pct"] for y in years), default=100)
    return (bool(years)
            and stat["trades"] >= max(12, math.ceil(
                TRADES_PER_YEAR * sessions / fam.SESSIONS_PER_YEAR))
            and stat["pf"] >= MIN_PROFIT_FACTOR
            and stat["max_dd_pct"] <= dd
            and positive >= math.ceil(len(years) / 2)
            and worst <= ANNUAL_DD)


def quality(stat, sessions):
    if not passes(stat, sessions):
        return -math.inf
    returns = [stat["annual"][y]["return_pct"] for y in fam.gate_years(stat)]
    return (100 * math.log(1 + stat["return_pct"] / 100) + min(returns)
            + .25 * statistics.median(returns) - .5 * statistics.pstdev(returns))


def neighbours(cell, grid):
    out = []
    for key, values in grid.items():
        if key in LABELS:
            continue
        at = values.index(cell[key])
        for j in (at - 1, at + 1):
            if 0 <= j < len(values):
                out.append({**cell, key: values[j]})
    return out


def cells(grid):
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, values))


def key_of(cell, grid):
    return tuple(cell[k] for k in grid)


# --------------------------------------------------------------------------- #
# workers and commands
# --------------------------------------------------------------------------- #

_WORKER = {}

#: What each search varies; the other layer stays at its configured default.
SEARCHES = {"manage": ("management", MANAGEMENT_GRID, MANAGEMENT),
            "pick": ("signal picking", PICK_GRID, PICK)}


def layers(kind, cell):
    """(MANAGEMENT, PICK) for a cell of search `kind`."""
    return (cell, PICK) if kind == "manage" else (MANAGEMENT, cell)


def load():
    universe, refused = fam.load_universe(fam.UNIVERSE)
    regime = fam.market_regime(universe)
    ctxs = {s: fam.build_context(s, bars, regime) for s, bars in universe.items()}
    calendar = sorted({bar[D] for bars in universe.values() for bar in bars})
    return universe, refused, regime, ctxs, calendar, build_tracks(ctxs, BOOK)


def _init(universe, regime, calendar):
    ctxs = {s: fam.build_context(s, bars, regime) for s, bars in universe.items()}
    _WORKER.update(ctxs=ctxs, regime=regime, calendar=calendar,
                   tracks=build_tracks(ctxs, BOOK))


def _evaluate(job):
    kind, cell, lo, hi = job
    manage, pick = layers(kind, cell)
    stat = run_book(_WORKER["ctxs"], _WORKER["calendar"], _WORKER["regime"],
                    BOOK, SIZING, manage, lo, hi, CAPITAL, pick, _WORKER["tracks"])
    stat.pop("sleeves"), stat.pop("top_stocks")
    return key_of(cell, SEARCHES[kind][1]), stat


def money(value):
    return f"Rp{value:+,.0f}"


def describe(cell):
    return ", ".join(f"{k}={v}" for k, v in cell.items()
                     if v is not None and v is not False) or "none"


def book_lines(title, stat):
    return [f"| {title} | {stat['return_pct']:+.1f}% | {stat['max_dd_pct']:.1f}% | "
            f"{stat['trades']} | {stat['win_rate_pct']:.0f}% | {stat['pf']:.2f} | "
            f"{stat['avg_trade_pct']:+.2f}% | {stat['exposure_pct']:.0f}% |"]


def sleeve_lines(stat, alone=None):
    lines = ["| sleeve | trades | P&L | share | win % | avg trade | closed dd | "
             "alone: return | alone: dd | in book / alone |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, row in stat["sleeves"].items():
        solo = (alone or {}).get(name)
        ratio = (f"{row['pnl'] / (solo['final'] - CAPITAL):.2f}"
                 if solo and solo["final"] != CAPITAL else "-")
        lines.append(
            f"| {name} | {row['trades']} | {money(row['pnl'])} | {row['share_pct']:.0f}% | "
            f"{row['win_rate_pct']:.0f}% | {row['avg_trade_pct']:+.2f}% | "
            f"{row['closed_dd_pct']:.1f}% | "
            + (f"{solo['return_pct']:+.1f}% | {solo['max_dd_pct']:.1f}% | {ratio} |"
               if solo else "- | - | - |"))
    return lines


def standalone(ctxs, calendar, regime, tracks, manage, pick, lo, hi):
    return {name: run_book(ctxs, calendar, regime, {name: BOOK[name]}, SIZING,
                           manage, lo, hi, CAPITAL, pick, tracks) for name in BOOK}


def header(calendar, cut, refused):
    lines = [f"Rp{CAPITAL:,.0f} Stockbit account, {len(BOOK)} sleeves "
             f"({', '.join(BOOK)}), sizing: {SIZING['mode']} "
             f"(risk {SIZING['risk_per_trade']:.0%}/trade, max position "
             f"{SIZING['max_position']:.0%}, max {SIZING['max_positions']} positions, "
             f"heat {SIZING['max_heat']:.0%}). In-sample {calendar[0]} to "
             f"{calendar[cut - 1]}, holdout {calendar[cut]} to {calendar[-1]}.", ""]
    if refused:
        lines += ["Refused stocks: " + ", ".join(sorted(refused)), ""]
    return lines


def write(name, lines, payload):
    stamp = date.today()
    base = os.path.join(RESULTS, f"IDX_BOOK_{name}_{stamp}")
    text = "\n".join(lines) + "\n"
    with open(base + ".md", "w", encoding="utf-8") as handle:
        handle.write(text)
    with open(base + ".json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    print(text)
    print(f"written {os.path.abspath(base)}.md/.json")


BOOK_TABLE = ["| run | return | max dd | trades | win % | PF | avg trade | invested |",
              "|---|---|---|---|---|---|---|---|"]


def detail_lines(stat):
    return ["", "Top stocks (holdout): " + ", ".join(
        f"{s} {money(v)}" for s, v in stat["top_stocks"]),
        f"Exit reasons (holdout): {stat['exit_reasons']}",
        f"Blocked, skipped or rotated (holdout): {stat['events']}"]


def run():
    _universe, refused, regime, ctxs, calendar, tracks = load()
    cut = int(len(calendar) * SPLIT)
    windows = {"in-sample": (0, cut), "holdout": (cut, len(calendar)),
               "full": (0, len(calendar))}
    stats = {w: run_book(ctxs, calendar, regime, BOOK, SIZING, MANAGEMENT, lo, hi,
                         CAPITAL, PICK, tracks) for w, (lo, hi) in windows.items()}
    alone = standalone(ctxs, calendar, regime, tracks, MANAGEMENT, PICK, cut, len(calendar))
    lines = ["# IDX book", ""] + header(calendar, cut, refused)
    lines += [f"Management: {describe(MANAGEMENT)}. Picking: {describe(PICK)}.", ""]
    lines += BOOK_TABLE
    for w, stat in stats.items():
        lines += book_lines(w, stat)
    lines += ["", "## Holdout, per sleeve", ""] + sleeve_lines(stats["holdout"], alone)
    lines += detail_lines(stats["holdout"])
    write("run", lines, {"book": BOOK, "sizing": SIZING, "management": MANAGEMENT,
                         "pick": PICK, "stats": stats, "standalone_holdout": alone})


def search(kind, workers):
    """Score every cell of one layer in-sample, pick one, judge it on the holdout."""
    universe, refused, regime, ctxs, calendar, tracks = load()
    cut = int(len(calendar) * SPLIT)
    title, grid, default = SEARCHES[kind]
    everything = list(cells(grid))
    print(f"{len(everything)} {title} cells on the in-sample half, {workers} workers",
          flush=True)
    with multiprocessing.Pool(workers, _init, (universe, regime, calendar)) as pool:
        results = dict(pool.imap_unordered(
            _evaluate, [(kind, c, 0, cut) for c in everything]))

    ranked = []
    for cell in everything:
        stat = results[key_of(cell, grid)]
        score = quality(stat, cut)
        if not math.isfinite(score):
            continue
        near = [results[key_of(c, grid)] for c in neighbours(cell, grid)]
        robust = sum(passes(s, cut, NEIGHBOUR_DD) for s in near)
        if near and robust < math.ceil(.6 * len(near)):
            continue
        ranked.append((score, cell, f"{robust}/{len(near)}"))
    ranked.sort(key=lambda item: -item[0])
    chosen = ranked[0][1] if ranked else None

    def book(cell, lo, hi):
        manage, pick = layers(kind, cell)
        return run_book(ctxs, calendar, regime, BOOK, SIZING, manage, lo, hi,
                        CAPITAL, pick, tracks)

    halves = (("in-sample", (0, cut)), ("holdout", (cut, len(calendar))))
    stats = {"default": {w: book(default, lo, hi) for w, (lo, hi) in halves}}
    if chosen is not None:
        stats["chosen"] = {w: book(chosen, lo, hi) for w, (lo, hi) in halves}

    lines = [f"# IDX book: {title} study", ""] + header(calendar, cut, refused)
    lines += [f"Default: {describe(default)}. {len(everything)} cells scored "
              f"in-sample; {len(ranked)} pass the gates with robust neighbours.", ""]
    lines += ["| run | window | return | max dd | trades | win % | PF | avg trade | invested |",
              "|---|---|---|---|---|---|---|---|---|"]
    for label, by_window in stats.items():
        for window, stat in by_window.items():
            lines += [line.replace("| " + label, f"| {label} | {window}", 1)
                      for line in book_lines(label, stat)]
    if len(everything) <= 32:
        # Small enough to show every cell on both halves: whether the idea holds
        # across its grid, or only in the one cell the search happened to pick.
        lines += ["", "Every cell (in-sample selects; holdout shown for context only):", "",
                  "| cell | IS return | IS dd | holdout return | holdout dd | holdout trades |",
                  "|---|---|---|---|---|---|"]
        beat = 0
        for cell in everything:
            s = results[key_of(cell, grid)]
            o = book(cell, cut, len(calendar))
            beat += o["return_pct"] > stats["default"]["holdout"]["return_pct"]
            mark = " (chosen)" if cell == chosen else ""
            lines.append(f"| {describe(cell)}{mark} | {s['return_pct']:+.1f}% | "
                         f"{s['max_dd_pct']:.1f}% | {o['return_pct']:+.1f}% | "
                         f"{o['max_dd_pct']:.1f}% | {o['trades']} |")
        lines += ["", f"{beat} of {len(everything)} cells beat the default on the holdout."]
    final = chosen or default
    manage, pick = layers(kind, final)
    alone = standalone(ctxs, calendar, regime, tracks, manage, pick, cut, len(calendar))
    best = stats["chosen" if chosen else "default"]["holdout"]
    lines += ["", f"## Holdout per sleeve ({'chosen' if chosen else 'default'}: "
              f"{describe(final)})", ""] + sleeve_lines(best, alone)
    lines += detail_lines(best)
    write(kind, lines, {"book": BOOK, "sizing": SIZING, "grid": grid,
                        "chosen": chosen, "ranked": [(s, c, r) for s, c, r in ranked[:20]],
                        "stats": stats, "standalone_holdout": alone})


def main():
    parser = argparse.ArgumentParser(description="One IDX account, many sleeves")
    parser.add_argument("command", choices=("run", "study", "pick"))
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args()
    if args.command == "run":
        run()
    else:
        search("manage" if args.command == "study" else "pick", args.workers)


if __name__ == "__main__":
    main()
