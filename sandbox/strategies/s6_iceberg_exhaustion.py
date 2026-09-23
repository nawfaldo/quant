"""S6: trade continuation after unusually strong replenishment fails to hold.

Economic hypothesis fixed before inspecting PnL:

    If aggressive executions repeatedly meet same-side replenishment but price
    still displaces through that side, the defending liquidity is exhausted;
    continuation toward the next liquidity pool should exceed taker costs over
    a 15-minute horizon.

This is explicitly a proxy, not a claim to identify individual hidden orders:
the retained one-minute data preserves side-specific replenished volume but not
the price-level refill count. The proxy is worth testing because replenishment
itself is computed per price at one-second resolution before being rolled up.

Only three entry axes are free. Direction, causal normalization, next-bar entry,
session, 12/20 bracket, 15-minute time stop, one-position occupancy, and the
0.20-point round-trip cost are fixed before the first PnL inspection.
"""

from collections import deque
import math

from sandbox import data, metrics
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

NAME = "S6 Iceberg Exhaustion Breakout"
OPEN_MINUTE = 570
CLOSE_MINUTE = 960
NORMALIZATION_OBSERVATIONS = 20

NO_FEATURES = {
    "source": "",
    "spread": 0.0,
    "microprice": 0.0,
    "midprice": 0.0,
    "top1_imbalance": 0.0,
    "top5_imbalance": 0.0,
    "top10_imbalance": 0.0,
    "executed_at_bid": 0.0,
    "executed_at_ask": 0.0,
    "bid_replenishment": 0.0,
    "ask_replenishment": 0.0,
    "trade_delta": 0.0,
    "depth_event_count": 0.0,
    "price_change": 0.0,
    "book_valid": False,
}


def zscore(value, history):
    """Causal sample z-score; the current value is not in ``history``."""
    if len(history) < NORMALIZATION_OBSERVATIONS:
        return None
    mean = sum(history) / len(history)
    variance = sum((sample - mean) ** 2 for sample in history) / (
        len(history) - 1
    )
    return (value - mean) / math.sqrt(variance) if variance > 1e-12 else None


def exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """First bar at which the common execution model will flatten a signal."""
    entry = bars[entry_index][O]
    entry_minute = (bars[entry_index][TS] % 86_400) // 60
    for index in range(entry_index, len(bars)):
        bar = bars[index]
        minute = (bar[TS] % 86_400) // 60
        if index > entry_index:
            if side == LONG:
                if bar[L] <= entry - stop or bar[H] >= entry + target:
                    return index
            elif bar[H] >= entry + stop or bar[L] <= entry - target:
                return index
            if minute >= entry_minute + time_stop:
                return index
        if minute < session_end:
            nxt = bars[index + 1] if index + 1 < len(bars) else None
            if (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            ):
                return index
    return len(bars) - 1


@register
class IcebergExhaustionBreakout(Strategy):
    name = NAME
    bars = "cached_level_two"
    execution = Execution(initial=1_000.0, slippage=0.2, session_end_min=945)

    defaults = {
        "replenishment_z": 2.0,
        "break_points": 3.0,
        "aggression_share": 0.525,
        "stop": 12.0,
        "target": 20.0,
        "time_stop": 15,
        "max_spread": 1.25,
        "entry_from": 575,
        "entry_to": 930,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "replenishment_z": [1.0, 2.0, 3.0],
        "break_points": [1.0, 3.0, 5.0],
        "aggression_share": [0.5, 0.525, 0.55],
    }

    def valid(self, params):
        return (
            params["replenishment_z"] > 0.0
            and params["break_points"] > 0.0
            and 0.5 <= params["aggression_share"] < 1.0
            and params["stop"] > 0.0
            and params["target"] > 0.0
            and params["time_stop"] > 0
            and params["entry_from"] < params["entry_to"]
        )

    def context(self):
        bars = data.load_cached_level_two_bars(self.symbol)
        features = data.load_cached_l2_features(self.symbol)
        rows = [None] * len(bars)
        histories = {}

        for index, bar in enumerate(bars):
            timestamp = bar[TS]
            minute = (timestamp % 86_400) // 60
            if not OPEN_MINUTE <= minute < CLOSE_MINUTE:
                continue
            feature = features.get(timestamp, NO_FEATURES)
            if not feature["book_valid"]:
                continue
            source = feature.get("source", "")
            rings = histories.setdefault(
                (source, minute),
                (deque(maxlen=NORMALIZATION_OBSERVATIONS),
                 deque(maxlen=NORMALIZATION_OBSERVATIONS)),
            )
            transformed = (
                math.log1p(feature["bid_replenishment"]),
                math.log1p(feature["ask_replenishment"]),
            )
            scores = tuple(zscore(value, ring) for value, ring in zip(transformed, rings))
            executed = feature["executed_at_bid"] + feature["executed_at_ask"]
            if all(score is not None for score in scores) and executed > 0.0:
                rows[index] = {
                    "bid_replenishment_z": scores[0],
                    "ask_replenishment_z": scores[1],
                    "ask_execution_share": feature["executed_at_ask"] / executed,
                    "price_change": feature["price_change"],
                    "spread": feature["spread"],
                    "feature": feature,
                }
            for value, ring in zip(transformed, rings):
                ring.append(value)
        return {"rows": rows, "features": features}

    @staticmethod
    def side_for(row, params):
        """Return the predeclared direction, or ``None`` when no setup exists."""
        threshold = params["replenishment_z"]
        move = params["break_points"]
        share = params["aggression_share"]
        if (
            row["ask_replenishment_z"] >= threshold
            and row["price_change"] >= move
            and row["ask_execution_share"] >= share
        ):
            return LONG
        if (
            row["bid_replenishment_z"] >= threshold
            and row["price_change"] <= -move
            and row["ask_execution_share"] <= 1.0 - share
        ):
            return SHORT
        return None

    def signals(self, bars, context, group, params):
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = (
            metrics.split_ts(params["to_date"]) + 86_400
            if params["to_date"]
            else None
        )
        output = []
        free_from = -1
        for index, row in enumerate(context["rows"]):
            if row is None or index < free_from:
                continue
            nxt = bars[index + 1] if index + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[index][TS] + 60:
                continue
            minute = (bars[index][TS] % 86_400) // 60
            if (
                not params["entry_from"] <= minute <= params["entry_to"]
                or row["spread"] > params["max_spread"]
            ):
                continue
            side = self.side_for(row, params)
            if side is None:
                continue
            free_from = exit_index(
                bars,
                index + 1,
                side,
                params["stop"],
                params["target"],
                params["time_stop"],
                self.execution.session_end_min,
            )
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue
            output.append(
                Signal(
                    index + 1,
                    side,
                    params["stop"],
                    params["target"],
                    params["time_stop"],
                )
            )
        return output
