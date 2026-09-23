"""NQ continuation after a large aggressive trade prints in a candle wick.

The setup comes from the supplied notes: use regular trading hours, ignore
sub-institutional and monster prints, require a balanced book, and give the
market roughly 15--20 minutes to digest the trade.  Direction is determined by
the aggressor on the tape, deliberately not by candle colour.

Causality is explicit in both inputs:

* book balance comes from the final snapshot of the second *before* the print;
* wick membership is known only after the event minute closes, so entry is at
  the next contiguous minute open.

The raw cache keeps the broad 70 <= size < 200 range so threshold sweeps never
need to reread the tape.  The registered defaults focus on 100--190 contracts.
"""

from sandbox import data, metrics
from sandbox.data import C, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

NAME = "NQ Large Print Continuation"
TICK_SIZE = 0.25
RTH_OPEN = 9 * 60 + 30
RTH_CLOSE = 16 * 60
CACHE_MIN_SIZE = 70
CACHE_MAX_SIZE_EXCLUSIVE = 200


def _large_print_days(table):
    return [row[0][:10] for row in data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as string) day "
        f"FROM {table} WHERE size>={CACHE_MIN_SIZE} "
        f"AND size<{CACHE_MAX_SIZE_EXCLUSIVE} "
        "GROUP BY day ORDER BY day"
    )]


def _daily_query(table, source, day):
    """Raw prints joined to a strictly earlier, completed book second."""
    return f"""
SELECT t.event_ns,t.trade_sequence,t.side,t.price,t.size,t.best_bid,t.best_ask,
       f.top5_imbalance,f.book_valid
FROM (
 SELECT cast(timestamp AS LONG) event_ns,trade_sequence,side,price,size,
        best_bid,best_ask,
        dateadd('s',-1,timestamp_floor('s',timestamp)) context_ts
 FROM {table}
 WHERE timestamp IN '{day}' AND size>={CACHE_MIN_SIZE}
       AND size<{CACHE_MAX_SIZE_EXCLUSIVE}
) t
LEFT JOIN (
 SELECT timestamp,top5_imbalance,book_valid
 FROM nq_l2_features_1s
 WHERE timestamp IN '{day}' AND source='{source}'
) f ON t.context_ts=f.timestamp
ORDER BY event_ns,trade_sequence
""".strip()


def load_large_prints(symbol="nq", use_cache=True):
    """Return broad-range raw prints with uncontaminated pre-print book state."""
    tables = [("dbento", f"dbento_{symbol}_ticks"),
              ("bm", f"bm_{symbol}_ticks")]

    def build():
        output = []
        for source, table in tables:
            for day in _large_print_days(table):
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
                        "book_imbalance": (
                            float(row[7]) if row[7] is not None else None
                        ),
                        "book_valid": bool(row[8]),
                    })
        output.sort(key=lambda item: (item["event_ns"], item["sequence"]))
        return output

    if not use_cache:
        return build()
    fingerprint = data._table_fingerprint(
        [table for _source, table in tables] + ["nq_l2_features_1s"]
    )
    key = (
        f"large-print:v1:{symbol}:{CACHE_MIN_SIZE}:"
        f"{CACHE_MAX_SIZE_EXCLUSIVE}:{fingerprint}"
    )
    return data._cached(f"{symbol}_large_prints", key, build)


def _source_for_minute(features):
    return {int(timestamp): feature.get("source", "")
            for timestamp, feature in features.items()}


def candidate_rows(bars, prints, minute_sources, params):
    """Select the strongest valid print per completed event minute.

    Candle direction is intentionally absent.  A BUY in the upper wick is a
    long candidate even on a red candle; a SELL in the lower wick is a short
    candidate even on a green candle.
    """
    index_by_ts = {int(bar[TS]): index for index, bar in enumerate(bars)}
    grouped = {}

    for print_ in prints:
        size = float(print_["size"])
        if not float(params["min_size"]) <= size <= float(params["max_size"]):
            continue

        event_ts = int(print_["event_ns"]) // 1_000_000_000
        event_minute_ts = event_ts // 60 * 60
        event_minute = (event_minute_ts % 86_400) // 60
        if not int(params["entry_from"]) <= event_minute <= int(params["entry_to"]):
            continue

        source = print_.get("source", "")
        selected_source = minute_sources.get(event_minute_ts)
        if selected_source and source and source != selected_source:
            continue

        index = index_by_ts.get(event_minute_ts)
        if index is None or index + 1 >= len(bars):
            continue
        nxt = bars[index + 1]
        if int(nxt[TS]) != event_minute_ts + 60:
            continue

        side_text = str(print_["side"]).upper()
        if side_text not in {"BUY", "SELL"}:
            continue
        side = LONG if side_text == "BUY" else SHORT

        bid = float(print_.get("best_bid", 0.0))
        ask = float(print_.get("best_ask", 0.0))
        spread = ask - bid
        if bid <= 0.0 or ask <= bid or spread > float(params["max_spread"]):
            continue
        quote = ask if side == LONG else bid
        if abs(float(print_["price"]) - quote) > float(params["max_quote_distance"]):
            continue

        if params["require_balanced_book"]:
            imbalance = print_.get("book_imbalance")
            if (not print_.get("book_valid") or imbalance is None
                    or abs(float(imbalance)) > float(params["max_book_imbalance"])):
                continue

        bar = bars[index]
        price = float(print_["price"])
        if params["require_wick"]:
            in_directional_wick = (
                price > max(float(bar[O]), float(bar[C]))
                if side == LONG
                else price < min(float(bar[O]), float(bar[C]))
            )
            if not in_directional_wick:
                continue

        row = {
            "source": source,
            "index": index + 1,
            "entry_ts": int(nxt[TS]),
            "event_ts": event_ts,
            "event_minute_ts": event_minute_ts,
            "side": side,
            "size": size,
            "price": price,
        }
        grouped.setdefault(event_minute_ts, []).append(row)

    output = []
    for minute in sorted(grouped):
        rows = grouped[minute]
        biggest = max(row["size"] for row in rows)
        strongest = [row for row in rows if row["size"] == biggest]
        # Equal largest prints on both sides are a two-way auction, not a clear
        # institutional decision.  Otherwise the last equally sized print is
        # the freshest causal signal at the next open.
        if len({row["side"] for row in strongest}) != 1:
            continue
        output.append(max(strongest, key=lambda row: row["event_ts"]))
    return output


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    entry = float(bars[entry_index][O])
    entry_ts = int(bars[entry_index][TS])
    entry_minute = (entry_ts % 86_400) // 60
    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        if int(bar[TS]) // 86_400 != entry_ts // 86_400:
            return index
        minute = (int(bar[TS]) % 86_400) // 60
        if side == LONG:
            bracket = float(bar[L]) <= entry - stop or float(bar[H]) >= entry + target
        else:
            bracket = float(bar[H]) >= entry + stop or float(bar[L]) <= entry - target
        if bracket or minute >= entry_minute + time_stop:
            return index
        nxt = bars[index + 1] if index + 1 < len(bars) else None
        if (minute < session_end and (
                nxt is None
                or int(nxt[TS]) // 86_400 != int(bar[TS]) // 86_400
                or (int(nxt[TS]) % 86_400) // 60 >= session_end)):
            return index
    return len(bars)


@register
class LargePrintContinuation(Strategy):
    name = NAME
    bars = "cached_level_two"
    symbol = "nq"
    # $1,000 Forex model: 0.01 lot step, $1/point, 25% margin, 0.5% risk.
    execution = Execution(
        initial=1_000.0,
        slippage=0.2,
        margin=0.25,
        step=0.01,
        point_value=1.0,
        risk=0.005,
        leverage=1.0,
        session_end_min=RTH_CLOSE,
    )

    defaults = {
        "min_size": 100,
        "max_size": 190,
        "require_wick": True,
        "require_balanced_book": True,
        "max_book_imbalance": 0.20,
        "max_spread": 1.25,
        "max_quote_distance": 1.0,
        "stop": 15.0,
        "target": 30.0,
        "time_stop": 20,
        "entry_from": RTH_OPEN,
        # Event 15:38 -> entry 15:39 -> full 20-minute observation by 15:59.
        "entry_to": RTH_CLOSE - 22,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "min_size": [70, 100, 130],
        "max_book_imbalance": [0.10, 0.20, 0.30],
        "time_stop": [15, 20],
    }

    def valid(self, params):
        return (
            CACHE_MIN_SIZE <= params["min_size"] <= params["max_size"]
            and params["max_size"] < CACHE_MAX_SIZE_EXCLUSIVE
            and 0.0 <= params["max_book_imbalance"] <= 1.0
            and params["max_spread"] >= TICK_SIZE
            and params["max_quote_distance"] >= 0.0
            and params["stop"] > 0.0
            and params["target"] > 0.0
            and 15 <= params["time_stop"] <= 20
            and RTH_OPEN <= params["entry_from"] <= params["entry_to"]
            and params["entry_to"] + 1 + params["time_stop"] < RTH_CLOSE
        )

    def context(self):
        features = data.load_cached_l2_features(self.symbol)
        return {
            "prints": load_large_prints(self.symbol),
            "minute_sources": _source_for_minute(features),
        }

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []
        rows = candidate_rows(
            bars,
            context["prints"],
            context.get("minute_sources", {}),
            params,
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
                row["index"],
                row["side"],
                float(params["stop"]),
                float(params["target"]),
                int(params["time_stop"]),
            ))
            free_from = _exit_index(
                bars,
                row["index"],
                row["side"],
                float(params["stop"]),
                float(params["target"]),
                int(params["time_stop"]),
                self.execution.session_end_min,
            )
        return output
