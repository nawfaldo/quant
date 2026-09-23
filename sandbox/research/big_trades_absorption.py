"""Marco Boesing's "big trades" absorption/accretion method, on NQ level two.

Source: IQCapital, "Ex-Institutional Trader Exposes the ONLY Way to Follow Big
Money (Live On Chart)" -- https://www.youtube.com/watch?v=ODC4LhyJNcM

What the video actually specifies, in the speaker's own terms:

  * The tape is bucketed into roughly 500ms clusters ("this version of the big
    trades indicator takes 500 milliseconds worth of time and bunches all orders
    together"), because institutions slice with an algo rather than print once.
  * A cluster is *green* when the aggressor lifted the offer (market buy) and
    *red* when it hit the bid (market sell). Only market orders move the market.
  * How big a cluster has to be is not a constant: "every day is another day...
    I'm looking in the first half hour, how big is the battle?" So the size
    threshold is calibrated from the session's own first thirty minutes.
  * After the cluster, price decides which of two things happened.
    **Accretion** -- the aggressor moved the market, so trade *with* it.
    **Absorption** -- "there is a buyer that can't move the market", so trade
    *against* it. "And this is everything I use because there is nothing more."
  * Context is a session VWAP anchored at the cash open with standard-deviation
    bands. VWAP plays no part in the entry ("Entries? No, because I can't see
    the battle") -- it says whether price is cheap or expensive, and the
    speaker's advice is "just go with the flow": long above VWAP, short below.
  * Stop goes just past the cluster's own extreme -- "I always hide my stop
    behind institutional levels" -- which he quotes at seven to seventeen ticks.
  * Target is the next VWAP band. Two other exits are named: a price-action
    level near a band, and "there is a new battle... if the seller would win, I
    would close manually", which is implemented as the opposing-battle exit.
  * He trades the first two to three hours of the CME session and stops because
    it is dinner time in Germany, so entries end at 12:30 New York.

What the video does not specify, and what this module fixes with a declared
constant rather than a search:

  * Scaling in is explicitly discretionary ("Discretionary, yeah") and moves the
    stop to break-even each time. It is not implemented. One entry, one stop,
    one target -- which makes this a *lower* bound on the described method and
    removes the axis that produced his 2.5:1 average.
  * Macro/sentiment stand-asides ("Fed week... I would wait") are judgement
    calls off a Bloomberg ticker. Not implemented; every qualifying day trades.

The grid below varies only the six axes the video leaves genuinely open (how
big a cluster counts, how long to wait before judging it, how far price has to
travel to count as moved, which of the two setups to take, whether to obey the
VWAP side filter, and band-target versus a fixed reward multiple).

Costs are the live Exness **Pro** account this repo trades, taken from
`combined_book.py`: NQ pays no commission and the whole cost is the measured
in-session spread, 0.300 bp of notional, plus the standard 0.2-point slippage
allowance. On a ~29,700 index that is about 1.09 points a round trip -- roughly
four and a half ticks against a stop the video puts at seven. That ratio is why
`edge` runs first and at zero cost: it reports the break-even spread directly,
and if no cell clears 1.09 there is nothing for `select` to find.

    py -B -m sandbox.research.big_trades_absorption edge     --workers 6
    py -B -m sandbox.research.big_trades_absorption select   --workers 6
    py -B -m sandbox.research.big_trades_absorption validate --workers 6
    py -B -m sandbox.research.big_trades_absorption null     --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from sandbox import data, metrics, trials

STRATEGY = "Big Trades Absorption"
HERE = os.path.dirname(__file__)
SELECTION = os.path.join(HERE, "big_trades_absorption_selection.json")
EDGE_OUTPUT = os.path.join(HERE, "big_trades_absorption_edge.json")
VALIDATE_OUTPUT = os.path.join(HERE, "big_trades_absorption_validate.json")
NULL_OUTPUT = os.path.join(HERE, "big_trades_absorption_null.json")

# --------------------------------------------------------------------------- #
# instrument and account
# --------------------------------------------------------------------------- #
TICK = 0.25
SYMBOL = "nq"

#: Exness Pro NQ. `combined_book.PRO_SPREAD_BP["nq"]` is 0.300 bp measured in
#: session, at `PRO_PRICE["nq"]` = 29731. Reproduced as literals rather than
#: imported because `combined_book` pulls in the whole live book on import.
PRO_SPREAD_BP = 0.300
PRO_PRICE = 29_731.0
ENTRY_SPREAD = PRO_SPREAD_BP / 1e4 * PRO_PRICE          # 0.892 points
#: Slippage allowance, charged on top of the spread on both Exness account
#: types. Measured median slippage across 218 live fills is 0.
ENTRY_SLIPPAGE = 0.2
#: Pro charges no per-lot commission on NQ; the spread *is* the cost.
COMMISSION_PER_LOT = 0.0

INITIAL_BALANCE = 1_000.0
FOREX_POINT_VALUE = 1.0
FOREX_QUANTITY_STEP = 0.01
FOREX_MARGIN = 0.25
RISK_FRACTION = 0.005

# --------------------------------------------------------------------------- #
# session clock (New York wall-clock, the convention every table here uses)
# --------------------------------------------------------------------------- #
OPEN_MIN = 9 * 60 + 30
#: "I'm looking in the first half hour, what how big is the battle?"
CALIBRATION_END_MIN = 10 * 60
#: "usually I have two to three hours and then it's already late."
LAST_ENTRY_MIN = 12 * 60 + 30
FLATTEN_MIN = 15 * 60 + 55

BUCKET_MS = 500
#: Buckets per coarse block for the bracket scan. Purely an index over the same
#: rows; it changes speed, never a fill.
BLOCK = 128

# --------------------------------------------------------------------------- #
# rule constants the video states or implies, fixed rather than searched
# --------------------------------------------------------------------------- #
#: "stop loss over the big trade" -- one tick clear of the cluster's extreme.
STOP_BUFFER_TICKS = 1
#: He quotes seven- and seventeen-tick stops. The clamp is wider than both so it
#: only rejects degenerate clusters, and a signal outside it is dropped, not
#: silently resized into a different trade.
MIN_STOP_TICKS = 4
MAX_STOP_TICKS = 40
#: A band sitting almost on top of the entry is not a target.
MIN_TARGET_TICKS = 4
#: Ceiling on the band target in units of risk, so one freak band distance
#: cannot turn a 7-tick stop into a 200-tick lottery ticket.
MAX_RR = 10.0

# --------------------------------------------------------------------------- #
# the search grid: the six axes the video leaves open
# --------------------------------------------------------------------------- #
#: Threshold = the k-th largest single-side cluster of the first half hour.
RANK_K = (5, 10, 20)
#: "even few seconds later it is not with them. It is after them."
CONFIRM_MS = (1000, 2000, 3000, 5000)
#: How far price must travel to count as moved (or as failing to move).
MOVE_TICKS = (1, 2, 4)
MODES = ("absorption", "accretion", "both")
VWAP_FILTERS = (True, False)
#: `band` is the video's rule. `rr2` is the control: same entries, a fixed 2R
#: target, so a band result cannot be credited to the entry when it belongs to
#: the exit.
TARGET_MODES = ("band", "rr2")

WINDOWS = {
    # dbento_nq_ticks starts 2025-02-12 and the bookmap feed takes over
    # 2026-07-17 at a different volume scale, so the dbento span is the sample.
    "is": ("2025-02-12", "2025-12-31"),
    "oos": ("2026-01-01", "2026-07-16"),
}

GATES = {
    "min_trades": 100,
    "min_months": 6,
    "max_drawdown_pct": 35.0,
    "max_top_month_share": 0.50,
    "max_loss_streak": 3,
}


def cells():
    """Every grid cell, in a stable order."""
    out = []
    for rank_k in RANK_K:
        for confirm_ms in CONFIRM_MS:
            for move_ticks in MOVE_TICKS:
                for mode in MODES:
                    for vwap_filter in VWAP_FILTERS:
                        for target_mode in TARGET_MODES:
                            out.append((rank_k, confirm_ms, move_ticks, mode,
                                        vwap_filter, target_mode))
    return out


def cell_key(cell):
    rank_k, confirm_ms, move_ticks, mode, vwap_filter, target_mode = cell
    return (f"k{rank_k}|c{confirm_ms}|m{move_ticks}|{mode}|"
            f"{'vwap' if vwap_filter else 'anyside'}|{target_mode}")


def cell_fields(cell):
    rank_k, confirm_ms, move_ticks, mode, vwap_filter, target_mode = cell
    return {"rank_k": rank_k, "confirm_ms": confirm_ms, "move_ticks": move_ticks,
            "mode": mode, "vwap_filter": vwap_filter, "target_mode": target_mode}


# --------------------------------------------------------------------------- #
# one session's tape, reduced to 500ms clusters
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Session:
    """One RTH session as parallel arrays over 500ms clusters.

    Index `j` is a position in the compacted series: clusters with no trade at
    all are absent, so `bucket[j]` (the absolute 500ms ordinal) is what carries
    real time and index arithmetic never assumes contiguity.
    """
    bucket: list        # absolute 500ms ordinal since epoch
    minute: list        # minute of the New York day
    buy: list           # aggressive buy volume (traded at the offer)
    sell: list          # aggressive sell volume (traded at the bid)
    open: list
    high: list
    low: list
    close: list
    vwap: list          # running session VWAP, causal
    sd: list            # running volume-weighted stdev around it, causal
    block_high: list
    block_low: list
    last_index: int     # last cluster before the flatten time


def _day_list(source, from_date, to_date):
    table = f"{source}_{SYMBOL}_ticks"
    rows = data.query(
        f"SELECT cast(timestamp_floor('d',timestamp) as string) d FROM {table} "
        f"WHERE timestamp >= '{from_date}' AND timestamp < '{_exclusive(to_date)}' "
        "GROUP BY d ORDER BY d")
    return [row[0][:10] for row in rows]


def _exclusive(to_date):
    return (date.fromisoformat(to_date) + timedelta(days=1)).isoformat()


def load_session(day, source="dbento", bucket_ms=BUCKET_MS):
    """Reduce one session's raw prints to the cluster series above.

    The reduction is done by QuestDB, not in Python: the full table is ~97M
    prints and only the per-cluster aggregate is ever used. `p2v` is the second
    moment, which is what makes a running volume-weighted stdev possible without
    a second pass over the session.
    """
    nanos = bucket_ms * 1_000_000
    rows = data.query(
        f"SELECT cast(timestamp as long)/{nanos} b,"
        "sum(CASE WHEN side='BUY' THEN size ELSE 0 END) bv,"
        "sum(CASE WHEN side='SELL' THEN size ELSE 0 END) sv,"
        "first(price) o,max(price) h,min(price) l,last(price) c,"
        "sum(price*size) pv,sum(price*price*size) p2v "
        f"FROM {source}_{SYMBOL}_ticks WHERE size>0 AND timestamp IN '{day}' "
        "GROUP BY b ORDER BY b")
    if not rows:
        return None

    bucket, minute, buy, sell = [], [], [], []
    op, hi, lo, cl, vwap, sd = [], [], [], [], [], []
    cum_v = cum_pv = cum_p2v = 0.0
    for row in rows:
        b = int(row[0])
        seconds = b * bucket_ms // 1000
        bucket.append(b)
        minute.append((seconds % 86_400) // 60)
        buy.append(float(row[1]))
        sell.append(float(row[2]))
        op.append(float(row[3]))
        hi.append(float(row[4]))
        lo.append(float(row[5]))
        cl.append(float(row[6]))
        cum_pv += float(row[7])
        cum_p2v += float(row[8])
        cum_v += float(row[1]) + float(row[2])
        mean = cum_pv / cum_v if cum_v > 0 else float(row[6])
        variance = cum_p2v / cum_v - mean * mean if cum_v > 0 else 0.0
        vwap.append(mean)
        sd.append(math.sqrt(variance) if variance > 0 else 0.0)

    block_high, block_low = [], []
    for start in range(0, len(bucket), BLOCK):
        block_high.append(max(hi[start:start + BLOCK]))
        block_low.append(min(lo[start:start + BLOCK]))

    last_index = -1
    for index, value in enumerate(minute):
        if value < FLATTEN_MIN:
            last_index = index
    if last_index < 0:
        return None

    return Session(bucket, minute, buy, sell, op, hi, lo, cl, vwap, sd,
                   block_high, block_low, last_index)


# --------------------------------------------------------------------------- #
# clusters -> signals
# --------------------------------------------------------------------------- #
def thresholds(session, ranks=RANK_K):
    """`{k: size}` -- the k-th largest single-side cluster of the first half hour.

    This is the video's own sizing rule ("is the battle with 100 contracts or
    with 500?") and it is causal by construction: the calibration window closes
    at 10:00 and the first entry is only considered afterwards. It also absorbs
    the two feeds' different volume scales, since a rank is relative.
    """
    volumes = []
    for index, value in enumerate(session.minute):
        if OPEN_MIN <= value < CALIBRATION_END_MIN:
            volumes.append(session.buy[index])
            volumes.append(session.sell[index])
    volumes.sort(reverse=True)
    return {k: volumes[k - 1] for k in ranks if len(volumes) >= k}


def _advance(session, index, buckets):
    """Last cluster whose real time is within `buckets` 500ms slots of `index`.

    Index arithmetic would silently shorten the confirmation window across a
    quiet stretch, so the walk is on `bucket`, the absolute ordinal.
    """
    limit = session.bucket[index] + buckets
    end = index
    n = len(session.bucket)
    while end + 1 < n and session.bucket[end + 1] <= limit:
        end += 1
    return end


def bubbles(session, size):
    """`[(index, direction)]` for clusters at or above `size` on one side.

    Direction is the *aggressor's*: +1 for a green cluster (market buys lifting
    the offer), -1 for a red one. When both sides clear the threshold in the
    same 500ms the larger side is the aggressor, which is the same judgement the
    indicator makes when it colours the bubble.
    """
    out = []
    for index, value in enumerate(session.minute):
        # Nothing before 10:00 is tradeable: the threshold is read off the first
        # half hour, so a cluster inside that window would be judged by a size
        # it helped define.
        if not CALIBRATION_END_MIN <= value < FLATTEN_MIN:
            continue
        buy, sell = session.buy[index], session.sell[index]
        if buy < size and sell < size:
            continue
        out.append((index, 1 if buy >= sell else -1))
    return out


def signals(session, bubble_list, confirm_ms, move_ticks, bucket_ms=BUCKET_MS):
    """Classify each cluster as accretion or absorption and place the entry.

    `move` is the displacement of the *close* over the confirmation window,
    measured from the cluster's own close. The aggressor moved the market
    (accretion) or the market moved against it (absorption); in between is the
    neutral band the video's "he tries, and he really tries" language leaves
    room for, and it is not traded either way.

    The entry is the open of the first cluster *after* the confirmation window,
    so no part of the decision reads a price the entry has not already passed.
    """
    move = move_ticks * TICK
    span = max(1, confirm_ms // bucket_ms)
    out = []
    for index, aggressor in bubble_list:
        end = _advance(session, index, span)
        if end <= index or end + 1 > session.last_index:
            continue
        entry_index = end + 1
        if session.minute[entry_index] > LAST_ENTRY_MIN:
            continue
        displacement = aggressor * (session.close[end] - session.close[index])
        if displacement >= move:
            kind, direction = "accretion", aggressor
        elif displacement <= -move:
            kind, direction = "absorption", -aggressor
        else:
            continue
        extreme = (min(session.low[index:end + 1]) if direction > 0
                   else max(session.high[index:end + 1]))
        out.append((entry_index, direction, kind, extreme))
    return out


# --------------------------------------------------------------------------- #
# brackets
# --------------------------------------------------------------------------- #
def _bracket(session, entry_index, direction, stop_price, target_price, memo):
    """First cluster at which the bracket resolves, stop taking precedence.

    Same conservative convention as `execution.py` and `drift_vwap_pullback`:
    a cluster that touches both legs is scored as the stop, the entry cluster
    itself cannot resolve, and a gap through a level fills at the open rather
    than at the level. The block scan is an index over the identical rows, so it
    changes how many are visited and never which one resolves.
    """
    key = (entry_index, direction, round(stop_price, 4), round(target_price, 4))
    cached = memo.get(key)
    if cached is not None:
        return cached

    end = session.last_index
    index = entry_index + 1
    result = None
    while index <= end:
        block = index // BLOCK
        block_end = min((block + 1) * BLOCK - 1, end)
        if index == block * BLOCK and block_end == (block + 1) * BLOCK - 1:
            if direction > 0:
                touched = (session.block_low[block] <= stop_price
                           or session.block_high[block] >= target_price)
            else:
                touched = (session.block_high[block] >= stop_price
                           or session.block_low[block] <= target_price)
            if not touched:
                index = block_end + 1
                continue
        for step in range(index, block_end + 1):
            if direction > 0:
                if session.low[step] <= stop_price:
                    result = (step, min(session.open[step], stop_price), "stop")
                    break
                if session.high[step] >= target_price:
                    result = (step, max(session.open[step], target_price), "target")
                    break
            else:
                if session.high[step] >= stop_price:
                    result = (step, max(session.open[step], stop_price), "stop")
                    break
                if session.low[step] <= target_price:
                    result = (step, min(session.open[step], target_price), "target")
                    break
        if result is not None:
            break
        index = block_end + 1

    if result is None:
        result = (end, session.close[end], "session_close")
    memo[key] = result
    return result


def _target_price(session, entry_index, direction, entry, stop_points, target_mode):
    """The video's next-VWAP-band target, or the fixed-multiple control.

    "The next VWAP level" is read as the next one worth going to. A band can sit
    a tick above the entry, and every worked example on the chart is a multiple
    of the risk -- "I have round about seven ticks, but can make 26" -- so a
    level nearer than the stop is stepped over rather than taken as the target.
    That is a reading of an underspecified rule, not a tuned parameter: it is
    fixed at 1R and never enters the grid.
    """
    if target_mode != "band":
        multiple = float(target_mode[2:])
        return entry + direction * multiple * stop_points
    vwap, sd = session.vwap[entry_index], session.sd[entry_index]
    ladder = [vwap + k * sd for k in (-3, -2, -1, 0, 1, 2, 3)]
    floor = max(MIN_TARGET_TICKS * TICK, stop_points)
    if direction > 0:
        above = [level for level in ladder if level >= entry + floor]
        return min(above) if above else None
    below = [level for level in ladder if level <= entry - floor]
    return max(below) if below else None


# --------------------------------------------------------------------------- #
# one session, every cell
# --------------------------------------------------------------------------- #
def run_session(session, grid=None, flip=None):
    """`{cell_key: [trade, ...]}` for one session.

    Signal construction is shared across cells that agree on the three axes that
    define it, and bracket resolution is memoised on the bracket itself, so the
    432 cells cost far less than 432 passes. `flip`, when given, is a
    `random.Random` used to replace every direction with a coin toss -- the
    control that answers "would any rule that traded at these moments have
    looked like this?".
    """
    grid = grid if grid is not None else cells()
    sizes = thresholds(session)
    bubble_cache, signal_cache, memo = {}, {}, {}
    out = {}

    for cell in grid:
        rank_k, confirm_ms, move_ticks, mode, vwap_filter, target_mode = cell
        size = sizes.get(rank_k)
        if size is None or size <= 0:
            out[cell_key(cell)] = []
            continue
        if rank_k not in bubble_cache:
            bubble_cache[rank_k] = bubbles(session, size)
        signal_key = (rank_k, confirm_ms, move_ticks)
        if signal_key not in signal_cache:
            signal_cache[signal_key] = signals(
                session, bubble_cache[rank_k], confirm_ms, move_ticks)
        found = signal_cache[signal_key]
        # "there is a new battle... if the seller would win, I would close."
        # A winning push against the position, whatever setup opened it.
        battles = [(index, direction) for index, direction, kind, _ in found
                   if kind == "accretion"]

        trades = []
        free_from = 0
        for entry_index, direction, kind, extreme in found:
            if mode != "both" and kind != mode:
                continue
            if entry_index < free_from:
                continue
            entry = session.open[entry_index]
            buffer = STOP_BUFFER_TICKS * TICK
            stop_price = extreme - buffer if direction > 0 else extreme + buffer
            stop_points = direction * (entry - stop_price)
            if not MIN_STOP_TICKS * TICK <= stop_points <= MAX_STOP_TICKS * TICK:
                continue
            if flip is not None:
                # Matched control: the same moment, the same risk, the same
                # exits -- only the direction the tape implied is discarded.
                direction = 1 if flip.random() < 0.5 else -1
                stop_price = entry - direction * stop_points
            if vwap_filter and direction * (entry - session.vwap[entry_index]) <= 0:
                continue
            target_price = _target_price(session, entry_index, direction, entry,
                                         stop_points, target_mode)
            if target_price is None:
                continue
            target_points = direction * (target_price - entry)
            if target_points <= 0 or target_points > MAX_RR * stop_points:
                continue

            exit_index, exit_price, reason = _bracket(
                session, entry_index, direction, stop_price, target_price, memo)
            for battle_index, battle_direction in battles:
                if battle_index <= entry_index:
                    continue
                if battle_index >= exit_index:
                    break
                if battle_direction != direction:
                    exit_index = battle_index
                    exit_price = session.open[battle_index]
                    reason = "new_battle"
                    break

            seconds = session.bucket[entry_index] * BUCKET_MS // 1000
            trades.append({
                "ts": seconds,
                "dir": direction,
                "entry": round(entry, 2),
                "stop_points": round(stop_points, 3),
                "target_points": round(target_points, 3),
                "gross_points": round(direction * (exit_price - entry), 3),
                "reason": reason,
                "kind": kind,
            })
            free_from = exit_index + 1
        out[cell_key(cell)] = trades
    return out


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Account:
    initial: float = INITIAL_BALANCE
    spread: float = ENTRY_SPREAD
    slippage: float = ENTRY_SLIPPAGE
    commission_per_lot: float = COMMISSION_PER_LOT
    point_value: float = FOREX_POINT_VALUE
    quantity_step: float = FOREX_QUANTITY_STEP
    margin: float = FOREX_MARGIN
    risk: float = RISK_FRACTION

    @property
    def entry_cost(self):
        """Spread + slippage + commission in price points, charged at entry.

        Same shape as `drift_vwap_pullback.Account.entry_cost`, so the two NQ
        research paths in this repo cannot drift apart on cost.
        """
        commission = (self.commission_per_lot / self.point_value
                      if self.point_value else 0.0)
        return self.spread + self.slippage + commission


FREE = Account(spread=0.0, slippage=0.0, commission_per_lot=0.0)


def account_curve(trades, account=Account()):
    """Compound `trades` through one live-equity account, oldest first.

    Risk is 0.5% of *current* equity over the trade's own stop, capped by the
    Forex margin leg, floored by the 0.01-lot step. A signal the account cannot
    afford is counted as unfilled rather than filled at a size it never had.
    """
    equity = account.initial
    cost = account.entry_cost
    sized, unfilled = [], 0
    for trade in sorted(trades, key=lambda row: row["ts"]):
        stop_points, entry = trade["stop_points"], trade["entry"]
        if stop_points <= 0 or entry <= 0 or equity <= 0:
            unfilled += 1
            continue
        raw = min(equity * account.risk / stop_points,
                  equity / account.margin / entry)
        quantity = math.floor((raw + 1e-12) / account.quantity_step) * account.quantity_step
        if quantity < account.quantity_step:
            unfilled += 1
            continue
        pnl = (trade["gross_points"] - cost) * quantity * account.point_value
        equity += pnl
        sized.append((trade["ts"], pnl))
    return sized, equity, unfilled


def summarise(trades, account=Account(), span=None):
    """Edge, account and consistency figures for one cell."""
    gross = [trade["gross_points"] for trade in trades]
    net = [value - account.entry_cost for value in gross]
    sized, equity, unfilled = account_curve(trades, account)
    stat = metrics.stats(sized, initial=account.initial, span=span)
    mean = statistics.fmean(gross) if gross else 0.0
    sd = statistics.stdev(gross) if len(gross) > 1 else 0.0
    reasons = {}
    for trade in trades:
        reasons[trade["reason"]] = reasons.get(trade["reason"], 0) + 1
    stat.update({
        "signals": len(trades),
        "unfilled": unfilled,
        # Zero-cost mean points a trade *is* the break-even spread.
        "gross_points_per_trade": round(mean, 4),
        "gross_t": round(mean / (sd / math.sqrt(len(gross))), 3)
        if sd > 0 and len(gross) > 1 else 0.0,
        "net_points_per_trade": round(statistics.fmean(net), 4) if net else 0.0,
        "entry_cost_points": round(account.entry_cost, 4),
        "final_equity": round(equity, 2),
        "return_pct": round(100 * (equity / account.initial - 1), 2),
        "drawdown_pct": round(100 * stat["max_dd"] / account.initial, 2),
        "avg_stop_ticks": round(statistics.fmean(
            trade["stop_points"] for trade in trades) / TICK, 2) if trades else 0.0,
        "avg_target_ticks": round(statistics.fmean(
            trade["target_points"] for trade in trades) / TICK, 2) if trades else 0.0,
        "exit_reasons": reasons,
    })
    stat.pop("months", None)
    return stat


def passes(stat):
    """The pre-declared gates. Selection reads only these, then monthly Sharpe."""
    return (stat["trades"] >= GATES["min_trades"]
            and stat["n_months"] >= GATES["min_months"]
            and stat["drawdown_pct"] <= GATES["max_drawdown_pct"]
            and stat["top_month_share"] <= GATES["max_top_month_share"]
            and stat["max_loss_streak"] <= GATES["max_loss_streak"]
            and stat["pnl"] > 0)


# --------------------------------------------------------------------------- #
# the pass over a window
# --------------------------------------------------------------------------- #
_WORKER = {}


def _worker(day):
    session = load_session(day, _WORKER["source"])
    if session is None or len(session.bucket) < 10_000:
        return {}
    seed = _WORKER.get("seed")
    flip = random.Random(f"{seed}:{day}") if seed is not None else None
    return run_session(session, _WORKER["grid"], flip)


def _init(source, grid, seed):
    _WORKER["source"] = source
    _WORKER["grid"] = grid
    _WORKER["seed"] = seed


def collect(window, source="dbento", workers=1, grid=None, seed=None):
    """`{cell_key: [trade, ...]}` over every session in `window`."""
    grid = grid if grid is not None else cells()
    from_date, to_date = WINDOWS[window] if isinstance(window, str) else window
    days = _day_list(source, from_date, to_date)
    if not days:
        raise SystemExit(f"no {source} sessions between {from_date} and {to_date}")

    merged = {cell_key(cell): [] for cell in grid}
    if workers > 1:
        with ProcessPoolExecutor(workers, initializer=_init,
                                 initargs=(source, grid, seed)) as pool:
            results = pool.map(_worker, days, chunksize=4)
            for result in results:
                for key, trades in result.items():
                    merged[key].extend(trades)
    else:
        _init(source, grid, seed)
        for day in days:
            for key, trades in _worker(day).items():
                merged[key].extend(trades)
    return merged, days


def window_span(window):
    from_date, to_date = WINDOWS[window] if isinstance(window, str) else window
    return metrics.split_ts(from_date), metrics.split_ts(_exclusive(to_date))


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #
def phase_edge(args):
    """Zero-cost gross points a trade, per cell. Run this first.

    Mean gross points a trade at zero cost *is* the break-even spread. The live
    Pro cost is printed beside it; a cell below that line cannot be rescued by
    any sizing, filter or exit change, and `select` has nothing to find.
    """
    grid = cells()
    merged, days = collect("is", args.source, args.workers, grid)
    span = window_span("is")
    rows = []
    for cell in grid:
        trades = merged[cell_key(cell)]
        if len(trades) < 30:
            continue
        stat = summarise(trades, FREE, span)
        rows.append({**cell_fields(cell), "cell": cell_key(cell),
                     "trades": stat["trades"],
                     "gross_points_per_trade": stat["gross_points_per_trade"],
                     "gross_t": stat["gross_t"],
                     "avg_stop_ticks": stat["avg_stop_ticks"],
                     "avg_target_ticks": stat["avg_target_ticks"],
                     "win_rate": stat["win_rate"],
                     "exit_reasons": stat["exit_reasons"]})
    rows.sort(key=lambda row: -row["gross_points_per_trade"])
    cost = Account().entry_cost
    report = {
        "strategy": STRATEGY,
        "phase": "edge",
        "source_video": "https://www.youtube.com/watch?v=ODC4LhyJNcM",
        "window": WINDOWS["is"],
        "sessions": len(days),
        "feed": args.source,
        "cells": len(grid),
        "live_entry_cost_points": round(cost, 4),
        "cells_clearing_live_cost": sum(
            1 for row in rows if row["gross_points_per_trade"] > cost),
        "top": rows[:25],
        "note": ("gross_points_per_trade at zero cost is the break-even spread; "
                 "the live Exness Pro NQ round trip is "
                 f"{cost:.3f} points ({cost / TICK:.1f} ticks)"),
    }
    _write(EDGE_OUTPUT, report)
    _print_edge(report)
    if args.record_trials:
        report["trials_total"] = trials.record(STRATEGY, len(grid), "edge scan")
        _write(EDGE_OUTPUT, report)
    return report


def phase_select(args):
    """Rank the grid on the in-sample window under the live Pro cost."""
    grid = cells()
    merged, days = collect("is", args.source, args.workers, grid)
    span = window_span("is")
    account = Account()
    ranked = []
    for cell in grid:
        stat = summarise(merged[cell_key(cell)], account, span)
        row = {**cell_fields(cell), "cell": cell_key(cell), **stat}
        row["passes"] = passes(stat)
        ranked.append(row)
    survivors = [row for row in ranked if row["passes"]]
    survivors.sort(key=lambda row: -row["msharpe"])

    selected = {}
    for row in survivors:
        selected.setdefault(row["mode"], row)
    report = {
        "strategy": STRATEGY,
        "phase": "select",
        "window": WINDOWS["is"],
        "sessions": len(days),
        "feed": args.source,
        "account": asdict(account),
        "entry_cost_points": round(account.entry_cost, 4),
        "gates": GATES,
        "cells": len(grid),
        "survivors": len(survivors),
        "selected": selected,
        "top": survivors[:15],
        "best_failing": sorted(
            (row for row in ranked if not row["passes"] and row["trades"] >= 30),
            key=lambda row: -row["pnl"])[:5],
    }
    _write(SELECTION, report)
    _print_select(report)
    if args.record_trials:
        report["trials_total"] = trials.record(STRATEGY, len(grid), "selection grid")
        _write(SELECTION, report)
    return report


def phase_validate(args):
    """Score the sealed cells on the untouched window, exactly once."""
    if not os.path.exists(SELECTION):
        raise SystemExit("run `select` first; there is nothing sealed to validate")
    with open(SELECTION) as handle:
        selection = json.load(handle)
    chosen = selection.get("selected") or {}
    if not chosen:
        raise SystemExit("selection sealed no cell; there is nothing to validate")

    grid = [(row["rank_k"], row["confirm_ms"], row["move_ticks"], row["mode"],
             row["vwap_filter"], row["target_mode"]) for row in chosen.values()]
    merged, days = collect("oos", args.source, args.workers, grid)
    span = window_span("oos")
    account = Account()
    rows = []
    for cell in grid:
        stat = summarise(merged[cell_key(cell)], account, span)
        rows.append({**cell_fields(cell), "cell": cell_key(cell), **stat,
                     "is_msharpe": chosen[cell[3]]["msharpe"],
                     "is_return_pct": chosen[cell[3]]["return_pct"]})
    report = {
        "strategy": STRATEGY,
        "phase": "validate",
        "window": WINDOWS["oos"],
        "sessions": len(days),
        "feed": args.source,
        "entry_cost_points": round(account.entry_cost, 4),
        "rows": rows,
    }
    _write(VALIDATE_OUTPUT, report)
    _print_validate(report)
    return report


def phase_null(args):
    """The coin-flip control: same moments, random direction.

    Every real cell is re-run with its direction replaced by a coin toss at the
    same clusters, with the same stop geometry and the same exits. A real cell
    that does not beat this distribution has not shown that reading the tape
    told it anything; it has shown that trading at those moments did.
    """
    grid = cells()
    span = window_span("is")
    account = Account()
    draws = []
    for seed in range(args.draws):
        merged, days = collect("is", args.source, args.workers, grid, seed=seed)
        best = None
        for cell in grid:
            stat = summarise(merged[cell_key(cell)], account, span)
            if stat["trades"] < 30:
                continue
            if best is None or stat["gross_points_per_trade"] > best["gross"]:
                best = {"seed": seed, "cell": cell_key(cell),
                        "gross": stat["gross_points_per_trade"],
                        "return_pct": stat["return_pct"],
                        "msharpe": stat["msharpe"], "trades": stat["trades"]}
        if best is not None:
            draws.append(best)
        print(f"  draw {seed}: best cell {best}")
    report = {
        "strategy": STRATEGY,
        "phase": "null",
        "window": WINDOWS["is"],
        "sessions": len(days),
        "draws": draws,
        "best_gross_points_per_trade": round(
            max((row["gross"] for row in draws), default=0.0), 4),
        "note": ("the coin-flip maximum over the same grid is the bar a real "
                 "cell has to clear, not zero"),
    }
    _write(NULL_OUTPUT, report)
    print(f"\ncoin-flip best gross points/trade over {len(draws)} draws: "
          f"{report['best_gross_points_per_trade']}")
    return report


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def _write(path, report):
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)


def _print_edge(report):
    print(f"\n{STRATEGY} -- edge scan (zero cost)")
    print(f"  window {report['window'][0]}..{report['window'][1]}  "
          f"{report['sessions']} sessions  {report['cells']} cells  "
          f"feed {report['feed']}")
    print(f"  live Exness Pro NQ round trip: "
          f"{report['live_entry_cost_points']} points "
          f"({report['live_entry_cost_points'] / TICK:.1f} ticks)")
    print(f"  cells whose zero-cost edge clears it: "
          f"{report['cells_clearing_live_cost']} / {report['cells']}\n")
    print(f"  {'cell':<42} {'trades':>7} {'gross pts':>10} {'t':>7} "
          f"{'stop':>6} {'tgt':>6} {'win':>6}")
    for row in report["top"][:15]:
        print(f"  {row['cell']:<42} {row['trades']:>7} "
              f"{row['gross_points_per_trade']:>10.4f} {row['gross_t']:>7.2f} "
              f"{row['avg_stop_ticks']:>6.1f} {row['avg_target_ticks']:>6.1f} "
              f"{row['win_rate']:>6.3f}")


def _print_select(report):
    print(f"\n{STRATEGY} -- selection on {report['window'][0]}..{report['window'][1]}")
    print(f"  {report['sessions']} sessions  {report['cells']} cells  "
          f"{report['survivors']} passed the gates  "
          f"entry cost {report['entry_cost_points']} points")
    if not report["survivors"]:
        print("  no cell passed. Best by PnL among cells with >=30 trades:")
        rows = report["best_failing"]
    else:
        rows = report["top"]
    print(f"\n  {'cell':<42} {'trades':>7} {'ret%':>8} {'dd%':>7} "
          f"{'msharpe':>8} {'net pts':>8}")
    for row in rows:
        print(f"  {row['cell']:<42} {row['trades']:>7} {row['return_pct']:>8.2f} "
              f"{row['drawdown_pct']:>7.2f} {row['msharpe']:>8.3f} "
              f"{row['net_points_per_trade']:>8.3f}")


def _print_validate(report):
    print(f"\n{STRATEGY} -- holdout {report['window'][0]}..{report['window'][1]} "
          f"({report['sessions']} sessions)")
    print(f"\n  {'cell':<42} {'trades':>7} {'ret%':>8} {'IS ret%':>8} "
          f"{'msharpe':>8} {'IS msh':>8} {'net pts':>8}")
    for row in report["rows"]:
        print(f"  {row['cell']:<42} {row['trades']:>7} {row['return_pct']:>8.2f} "
              f"{row['is_return_pct']:>8.2f} {row['msharpe']:>8.3f} "
              f"{row['is_msharpe']:>8.3f} {row['net_points_per_trade']:>8.3f}")


PHASES = {"edge": phase_edge, "select": phase_select,
          "validate": phase_validate, "null": phase_null}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=sorted(PHASES))
    parser.add_argument("--source", default="dbento", choices=("dbento", "bm"),
                        help="order-flow feed; dbento covers 2025-02..2026-07")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--draws", type=int, default=5,
                        help="coin-flip draws for the null phase")
    parser.add_argument("--record-trials", action="store_true",
                        help="charge this grid to the cumulative trial budget")
    args = parser.parse_args(argv)
    return PHASES[args.phase](args)


if __name__ == "__main__":
    main()
