"""Faithful, tradable proxy for the large-print digestion event study.

This version fixes the two important differences in the first prototype:

* the largest stored print of the minute is selected *before* the 100--190 lot
  band is applied, so a smaller print cannot hide a 200+ lot monster;
* either wick qualifies, and positions leave on a fixed 15-minute horizon with
  no profit target.  A distant stop exists only as disaster protection and as
  the denominator for fixed-fraction sizing.

The available historical source is MBP-10 rather than the video's MBO.  This is
therefore an L2 proxy for the idea, not a claim to reconstruct parent orders.
"""

from sandbox import data, metrics
from sandbox.data import C, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

NAME = "NQ Whale Digestion"
TICK_SIZE = 0.25
RTH_OPEN = 570
RTH_CLOSE = 960
CACHE_MIN_SIZE = 70


def _print_days(table):
    return [row[0][:10] for row in data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as string) day "
        f"FROM {table} WHERE size>={CACHE_MIN_SIZE} GROUP BY day ORDER BY day"
    )]


def _daily_query(table, source, day):
    return f"""
SELECT t.event_ns,t.trade_sequence,t.side,t.price,t.size,t.best_bid,t.best_ask,
       f.top5_imbalance,f.book_valid
FROM (
 SELECT cast(timestamp AS LONG) event_ns,trade_sequence,side,price,size,
        best_bid,best_ask,
        dateadd('s',-1,timestamp_floor('s',timestamp)) context_ts
 FROM {table}
 WHERE timestamp IN '{day}' AND size>={CACHE_MIN_SIZE}
) t
LEFT JOIN (
 SELECT timestamp,top5_imbalance,book_valid
 FROM nq_l2_features_1s
 WHERE timestamp IN '{day}' AND source='{source}'
) f ON t.context_ts=f.timestamp
ORDER BY event_ns,trade_sequence
""".strip()


def load_whale_prints(symbol="nq", use_cache=True):
    """Raw 70+ prints, including monsters needed to identify each true maximum."""
    tables = (("dbento", f"dbento_{symbol}_ticks"),
              ("bm", f"bm_{symbol}_ticks"))

    def build():
        output = []
        for source, table in tables:
            for day in _print_days(table):
                for row in data.query(_daily_query(table, source, day)):
                    output.append({
                        "source": source,
                        "event_ns": int(row[0]),
                        "sequence": int(row[1] or 0),
                        "side": str(row[2]).upper(),
                        "price": float(row[3]),
                        "size": float(row[4]),
                        "best_bid": float(row[5] or 0.0),
                        "best_ask": float(row[6] or 0.0),
                        "book_imbalance": float(row[7]) if row[7] is not None else None,
                        "book_valid": bool(row[8]),
                    })
        output.sort(key=lambda row: (row["event_ns"], row["sequence"]))
        return output

    if not use_cache:
        return build()
    fingerprint = data._table_fingerprint(
        [table for _source, table in tables] + ["nq_l2_features_1s"]
    )
    key = f"whale-print:v1:{symbol}:{CACHE_MIN_SIZE}:{fingerprint}"
    return data._cached(f"{symbol}_whale_prints", key, build)


def _source_for_minute(features):
    return {int(timestamp): feature.get("source", "")
            for timestamp, feature in features.items()}


def whale_rows(bars, prints, minute_sources, params):
    """True minute maximums that pass the fixed, causal entry definition."""
    index_by_ts = {int(bar[TS]): index for index, bar in enumerate(bars)}
    grouped = {}

    # Group before applying size, book, quote, or wick filters.  The video asks
    # about the biggest trade of the minute, not the biggest convenient trade.
    for print_ in prints:
        event_ts = int(print_["event_ns"]) // 1_000_000_000
        minute_ts = event_ts // 60 * 60
        selected_source = minute_sources.get(minute_ts)
        source = print_.get("source", "")
        if selected_source and source and source != selected_source:
            continue
        grouped.setdefault(minute_ts, []).append(print_)

    output = []
    for minute_ts in sorted(grouped):
        prints_in_minute = grouped[minute_ts]
        biggest_size = max(float(row["size"]) for row in prints_in_minute)
        biggest = [row for row in prints_in_minute
                   if float(row["size"]) == biggest_size]
        sides = {str(row["side"]).upper() for row in biggest}
        if len(sides) != 1 or not sides <= {"BUY", "SELL"}:
            continue
        print_ = max(biggest, key=lambda row: (row["event_ns"], row["sequence"]))
        if not float(params["min_size"]) <= biggest_size <= float(params["max_size"]):
            continue

        event_minute = (minute_ts % 86_400) // 60
        if not int(params["entry_from"]) <= event_minute <= int(params["entry_to"]):
            continue
        index = index_by_ts.get(minute_ts)
        if index is None or index + 1 >= len(bars):
            continue
        nxt = bars[index + 1]
        if int(nxt[TS]) != minute_ts + 60:
            continue

        side = LONG if str(print_["side"]).upper() == "BUY" else SHORT
        bid = float(print_.get("best_bid", 0.0))
        ask = float(print_.get("best_ask", 0.0))
        spread = ask - bid
        if bid <= 0.0 or ask <= bid or spread > float(params["max_spread"]):
            continue
        quote = ask if side == LONG else bid
        if abs(float(print_["price"]) - quote) > float(params["max_quote_distance"]):
            continue

        imbalance = print_.get("book_imbalance")
        if (not print_.get("book_valid") or imbalance is None
                or abs(float(imbalance)) > float(params["max_book_imbalance"])):
            continue

        bar = bars[index]
        price = float(print_["price"])
        if not (price < min(float(bar[O]), float(bar[C]))
                or price > max(float(bar[O]), float(bar[C]))):
            continue

        output.append({
            "source": print_.get("source", ""),
            "index": index + 1,
            "entry_ts": int(nxt[TS]),
            "event_ts": int(print_["event_ns"]) // 1_000_000_000,
            "event_minute_ts": minute_ts,
            "side": side,
            "size": biggest_size,
            "price": price,
            "book_imbalance": float(imbalance),
        })
    return output


def exit_index(bars, entry_index, side, stop, time_stop, session_end):
    entry = float(bars[entry_index][O])
    entry_ts = int(bars[entry_index][TS])
    entry_minute = (entry_ts % 86_400) // 60
    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        if int(bar[TS]) // 86_400 != entry_ts // 86_400:
            return index
        minute = (int(bar[TS]) % 86_400) // 60
        stopped = (float(bar[L]) <= entry - stop if side == LONG
                   else float(bar[H]) >= entry + stop)
        if stopped or minute >= entry_minute + time_stop:
            return index
        nxt = bars[index + 1] if index + 1 < len(bars) else None
        if (minute < session_end and (
                nxt is None
                or int(nxt[TS]) // 86_400 != int(bar[TS]) // 86_400
                or (int(nxt[TS]) % 86_400) // 60 >= session_end)):
            return index
    return len(bars)


@register
class WhaleDigestion(Strategy):
    name = NAME
    bars = "cached_level_two"
    symbol = "nq"
    execution = Execution(
        initial=1_000.0,
        spread=0.2,
        margin=0.25,
        step=0.01,
        point_value=1.0,
        risk=0.0025,
        leverage=1.0,
        session_end_min=RTH_CLOSE,
    )
    defaults = {
        "min_size": 100,
        "max_size": 190,
        "max_book_imbalance": 0.20,
        "max_spread": 1.25,
        "max_quote_distance": 1.0,
        "stop": 240.0,
        "target": 0.0,
        "time_stop": 15,
        "entry_from": RTH_OPEN,
        # One cutoff for both grid horizons: event 15:38 -> entry 15:39 -> a
        # complete 20-minute observation ending at 15:59.
        "entry_to": 938,
        "from_date": None,
        "to_date": None,
    }
    # Three cells only for the chronological 80/20 experiment. The 15-minute
    # horizon comes from the source hypothesis and is not optimized.
    grid = {
        "stop": [80.0, 160.0, 240.0],
    }

    def valid(self, params):
        return (
            CACHE_MIN_SIZE <= params["min_size"] <= params["max_size"]
            and params["max_size"] < 200
            and 0.0 <= params["max_book_imbalance"] <= 1.0
            and params["max_spread"] >= TICK_SIZE
            and params["max_quote_distance"] >= 0.0
            and params["stop"] > 0.0
            and params["target"] == 0.0
            and params["time_stop"] == 15
            and RTH_OPEN <= params["entry_from"] <= params["entry_to"]
            and params["entry_to"] + 1 + params["time_stop"] < RTH_CLOSE
        )

    def context(self):
        features = data.load_cached_l2_features(self.symbol)
        return {
            "prints": load_whale_prints(self.symbol),
            "minute_sources": _source_for_minute(features),
        }

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []
        rows = whale_rows(
            bars, context["prints"], context.get("minute_sources", {}), params
        )
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = (metrics.split_ts(params["to_date"]) + 86_400
               if params["to_date"] else None)
        output = []
        free_from = -1
        for row in rows:
            if row["index"] < free_from:
                continue
            if start is not None and row["entry_ts"] < start:
                continue
            if end is not None and row["entry_ts"] >= end:
                continue
            output.append(Signal(
                row["index"], row["side"], float(params["stop"]), 0.0,
                int(params["time_stop"]),
            ))
            free_from = exit_index(
                bars, row["index"], row["side"], float(params["stop"]),
                int(params["time_stop"]), self.execution.session_end_min,
            )
        return output
