"""S4 raw-tape sweep / trade-through continuation.

Pre-declared experiment (written before inspecting any S4 PnL):

    A genuine sweep is a maximal run of same-side trade prints whose adjacent
    receive timestamps are no more than ``gap_ms`` apart.  It qualifies only
    when the best quote moves in the aggressor's direction by at least
    ``levels`` NQ ticks and the run trades at least ``volume`` contracts.
    Enter in the sweep direction at the next complete minute open.

The importer currently stores ``is_exec_start``/``is_exec_end`` as false on
every row, so those columns cannot define an execution.  Ordered raw trades,
their side, and their per-trade BBO are used directly instead.  This is not a
one-second proxy: burst boundaries are determined at nanosecond resolution.

Only three coarse entry axes are free.  The bracket is fixed at a 15-point
stop, 30-point target, and 30-minute time stop.  At most one position is open,
and multiple qualifying bursts observed in one minute collapse to the strongest
quote displacement before the next-minute decision.
"""

from datetime import datetime, timezone
import glob
import json
import math
import os

from sandbox import data
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

TICK_SIZE = 0.25
RTH_FROM = 570
LAST_EVENT = 914
MIN_CACHED_LEVELS = 8
MIN_CACHED_VOLUME = 25


def _iso_months(first_ts, last_ts):
    first = datetime.fromtimestamp(first_ts, timezone.utc)
    last = datetime.fromtimestamp(last_ts, timezone.utc)
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        nxt = (year + 1, 1) if month == 12 else (year, month + 1)
        yield (
            f"{year}-{month:02d}-01T00:00:00.000000000Z",
            f"{nxt[0]}-{nxt[1]:02d}-01T00:00:00.000000000Z",
        )
        year, month = nxt


def _burst_query(gap_ms, lower, upper):
    gap_ns = int(gap_ms * 1_000_000)
    return f"""
WITH ordered AS (
 SELECT timestamp,trade_sequence,side,price,size,best_bid,best_ask,
        CASE WHEN side='BUY' THEN 1 ELSE -1 END side_num,
        lag(cast(timestamp AS LONG))
            OVER (ORDER BY timestamp,trade_sequence) prior_ns,
        lag(CASE WHEN side='BUY' THEN 1 ELSE -1 END)
            OVER (ORDER BY timestamp,trade_sequence) prior_side_num
 FROM dbento_nq_ticks
 WHERE timestamp>='{lower}' AND timestamp<'{upper}' AND size>0
), marked AS (
 SELECT *,
        CASE WHEN prior_side_num IS NULL OR side_num!=prior_side_num
                  OR cast(timestamp AS LONG)-prior_ns>{gap_ns}
             THEN 1 ELSE 0 END new_burst
 FROM ordered
), grouped AS (
 SELECT *,
        sum(new_burst) OVER (
            ORDER BY timestamp,trade_sequence
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) burst_id
 FROM marked
), bursts AS (
 SELECT burst_id,
        cast(first(timestamp) AS LONG) start_ns,
        cast(last(timestamp) AS LONG) end_ns,
        first(side_num) side_num,
        sum(size) volume,
        count() trades,
        min(price) min_price,
        max(price) max_price,
        first(best_bid) first_bid,
        last(best_bid) last_bid,
        first(best_ask) first_ask,
        last(best_ask) last_ask
 FROM grouped
 GROUP BY burst_id
), measured AS (
 SELECT *,
        CASE WHEN side_num=1
             THEN (last_ask-first_ask)/{TICK_SIZE}
             ELSE (first_bid-last_bid)/{TICK_SIZE}
        END levels
 FROM bursts
 WHERE volume>={MIN_CACHED_VOLUME}
)
SELECT start_ns,end_ns,side_num,levels,volume,trades,
       min_price,max_price,first_bid,last_bid,first_ask,last_ask
FROM measured
WHERE levels>={MIN_CACHED_LEVELS}
ORDER BY start_ns
""".strip()


def load_bursts(gap_ms):
    """Cached raw-tape bursts for one gap definition.

    Month-sized queries bound QuestDB's window-function memory.  A gap between
    sessions is vastly longer than every grid value, so a month boundary cannot
    merge two real bursts.
    """
    existing = glob.glob(
        os.path.join(
            data.CACHE_DIR,
            f"nq_s4_bursts_{gap_ms}ms.*.json",
        )
    )
    if existing:
        path = max(existing, key=os.path.getmtime)
        with open(path) as handle:
            return json.load(handle)

    fingerprint = data._table_fingerprint(["dbento_nq_ticks"])
    bounds = data.query(
        "SELECT cast(min(timestamp) AS LONG),cast(max(timestamp) AS LONG) "
        "FROM dbento_nq_ticks"
    )[0]
    first_ts = int(bounds[0]) // 1_000_000_000
    last_ts = int(bounds[1]) // 1_000_000_000

    def build():
        rows = []
        for lower, upper in _iso_months(first_ts, last_ts):
            rows.extend(data.query(_burst_query(gap_ms, lower, upper)))
        return rows

    key = (
        f"s4-bursts:v1:gap={gap_ms}:levels={MIN_CACHED_LEVELS}:"
        f"volume={MIN_CACHED_VOLUME}:{fingerprint}"
    )
    return data._cached(f"nq_s4_bursts_{gap_ms}ms", key, build)


def _exit_index(bars, entry_index, side, stop, target, max_minutes, session_end):
    """First index at which the one-position-at-a-time strategy is flat."""
    entry = bars[entry_index][O]
    entry_ts = bars[entry_index][TS]
    entry_minute = (entry_ts % 86_400) // 60
    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        if bar[TS] // 86_400 != entry_ts // 86_400:
            return index
        minute = (bar[TS] % 86_400) // 60
        if side == LONG:
            bracket = bar[L] <= entry - stop or bar[H] >= entry + target
        else:
            bracket = bar[H] >= entry + stop or bar[L] <= entry - target
        if bracket or minute >= entry_minute + max_minutes:
            return index
        nxt = bars[index + 1] if index + 1 < len(bars) else None
        if (
            minute < session_end
            and (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            )
        ):
            return index
    return len(bars)


def event_rows(bars, bursts, levels, volume, entry_from, entry_to):
    """Strongest qualifying, causally complete burst per decision minute."""
    index_by_ts = {bar[TS]: index for index, bar in enumerate(bars)}
    strongest = {}
    for raw in bursts:
        (
            start_ns,
            end_ns,
            side_num,
            displaced,
            traded,
            trades,
            min_price,
            max_price,
            first_bid,
            last_bid,
            first_ask,
            last_ask,
        ) = raw
        if displaced < levels or traded < volume:
            continue
        event_ts = int(end_ns) // 1_000_000_000
        event_minute = (event_ts % 86_400) // 60
        if not entry_from <= event_minute <= entry_to:
            continue
        entry_ts = (event_ts // 60 + 1) * 60
        index = index_by_ts.get(entry_ts)
        if index is None:
            continue
        duration_ms = max(0.0, (int(end_ns) - int(start_ns)) / 1_000_000)
        side = LONG if int(side_num) == 1 else SHORT
        price_range = float(max_price) - float(min_price)
        quote_spread = (
            float(first_ask) - float(first_bid)
            + float(last_ask) - float(last_bid)
        ) / 2
        row = {
            "index": index,
            "entry_ts": entry_ts,
            "event_ts": event_ts,
            "side": side,
            "levels": float(displaced),
            "volume": float(traded),
            "trades": int(trades),
            "duration_ms": duration_ms,
            "price_range": price_range,
            "quote_spread": quote_spread,
        }
        score = (row["levels"], math.log1p(row["volume"]), row["trades"])
        old = strongest.get(entry_ts)
        if old is None or score > old[0]:
            strongest[entry_ts] = (score, row)
    return [item[1] for item in sorted(strongest.values(), key=lambda pair: pair[1]["entry_ts"])]


@register
class SweepTradeThrough(Strategy):
    name = "S4 Sweep Trade Through"
    bars = "cached_level_two"
    symbol = "nq"
    execution = Execution(session_end_min=945, risk=0.005, leverage=1.0)
    defaults = {
        "gap_ms": 25,
        "levels": 12,
        "volume": 50,
        "stop": 15.0,
        "target": 30.0,
        "time_stop": 30,
        "entry_from": RTH_FROM,
        "entry_to": LAST_EVENT,
        "from_date": None,
        "to_date": "2026-07-16",
    }
    grid = {
        "gap_ms": [10, 25, 50],
        "levels": [8, 12, 16],
        "volume": [25, 50, 100],
    }

    def context(self):
        return {
            "bursts": {
                gap: load_bursts(gap)
                for gap in self.grid["gap_ms"]
            }
        }

    def candidates(self, bars, context, params):
        return event_rows(
            bars,
            context["bursts"][params["gap_ms"]],
            params["levels"],
            params["volume"],
            params["entry_from"],
            params["entry_to"],
        )

    def signals(self, bars, context, group, params):
        candidates = self.candidates(bars, context, params)
        start = None
        end = None
        if params["from_date"]:
            from sandbox.metrics import split_ts

            start = split_ts(params["from_date"])
        if params["to_date"]:
            from sandbox.metrics import split_ts

            end = split_ts(params["to_date"]) + 86_400

        out = []
        free_from = -1
        for row in candidates:
            if row["index"] <= free_from:
                continue
            if start is not None and row["entry_ts"] < start:
                continue
            if end is not None and row["entry_ts"] >= end:
                continue
            signal = Signal(
                row["index"],
                row["side"],
                params["stop"],
                params["target"],
                params["time_stop"],
            )
            out.append(signal)
            free_from = _exit_index(
                bars,
                row["index"],
                row["side"],
                params["stop"],
                params["target"],
                params["time_stop"],
                self.execution.session_end_min,
            )
        return out
