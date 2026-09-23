"""A5 effort/result complement: air-pocket fade.

The hypothesis is the unoccupied complement of Absorption Reversal:

    large result + little effort -> thin-book displacement -> fade

``price_change`` is the result.  Trade count and aggressive volume are two
different effort measurements.  Each minute measurement is transformed with
``log1p`` and standardized causally against the previous 20 observations from
the same collector and minute-of-day.  The current observation is appended only
after its z-score is formed.

The pre-declared search has exactly three coarse axes: result z, trade-count z,
and aggressive-volume z.  Direction, five-minute horizon, symmetric ten-point
bracket, session, spread gate, and one-position occupancy are fixed.  This is a
minute-bar test of the economic idea, not a claim about sub-second air pockets.
"""

from collections import deque
import math

from sandbox import data
from sandbox import metrics
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960
FIRST_ENTRY, LAST_ENTRY = 585, 930
EXIT_MIN = 945
NORM_SESSIONS = 20

NO_FEATURES = {
    "source": "",
    "spread": 0.0,
    "aggressive_buy_volume": 0.0,
    "aggressive_sell_volume": 0.0,
    "price_change": 0.0,
    "trade_count": 0.0,
    "book_valid": False,
}


def effort_result(feature):
    """Return transformed ``(result, trade_count, volume)`` or ``None``.

    Minute volume is rebuilt from the two summed aggressive-volume columns.
    A minute with no trades has no measurable result/effort relationship and is
    rejected rather than treated as an exceptionally thin observation.
    """
    count = feature["trade_count"]
    volume = (
        feature["aggressive_buy_volume"] + feature["aggressive_sell_volume"]
    )
    move = feature["price_change"]
    if count <= 0.0 or volume <= 0.0 or move == 0.0:
        return None
    return math.log1p(abs(move)), math.log1p(count), math.log1p(volume)


def _zscore(value, history):
    if len(history) < NORM_SESSIONS:
        return None
    mean = sum(history) / len(history)
    variance = sum((sample - mean) ** 2 for sample in history) / (
        len(history) - 1
    )
    if variance <= 0.0:
        return None
    return (value - mean) / math.sqrt(variance)


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """First bar on which a candidate position is flat again."""
    entry = bars[entry_index][O]
    entry_minute = (bars[entry_index][TS] % 86_400) // 60
    for j in range(entry_index, len(bars)):
        bar = bars[j]
        minute = (bar[TS] % 86_400) // 60
        if j > entry_index:
            if side == LONG:
                if bar[L] <= entry - stop or bar[H] >= entry + target:
                    return j
            elif bar[H] >= entry + stop or bar[L] <= entry - target:
                return j
            if minute >= entry_minute + time_stop:
                return j
        if minute < session_end:
            nxt = bars[j + 1] if j + 1 < len(bars) else None
            if (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            ):
                return j
    return len(bars) - 1


@register
class AirPocketFade(Strategy):
    name = "Air Pocket Fade"
    bars = "level_two"
    execution = Execution(session_end_min=EXIT_MIN)

    defaults = {
        "move_z": 1.0,
        "count_z": 0.0,
        "volume_z": 0.0,
        "stop": 10.0,
        "target": 10.0,
        "time_stop": 5,
        "max_spread": 1.25,
        "entry_from": FIRST_ENTRY,
        "entry_to": LAST_ENTRY,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "move_z": [0.75, 1.0, 1.5],
        "count_z": [-0.25, 0.0, 0.25],
        "volume_z": [-0.25, 0.0, 0.25],
    }

    def valid(self, params):
        return (
            params["move_z"] > 0.0
            and params["stop"] > 0.0
            and params["target"] > 0.0
            and params["time_stop"] > 0
            and params["entry_from"] < params["entry_to"]
        )

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        rows = [None] * len(bars)
        histories = {}

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if not OPEN_MIN <= minute < CLOSE_MIN:
                continue
            feature = features.get(ts, NO_FEATURES)
            if not feature["book_valid"]:
                continue
            transformed = effort_result(feature)
            if transformed is None:
                continue

            source = feature.get("source", "")
            rings = histories.setdefault(
                (source, minute),
                tuple(deque(maxlen=NORM_SESSIONS) for _ in range(3)),
            )
            zscores = tuple(
                _zscore(value, ring)
                for value, ring in zip(transformed, rings)
            )
            if all(value is not None for value in zscores):
                rows[i] = (
                    zscores[0],
                    zscores[1],
                    zscores[2],
                    feature["price_change"],
                    feature["spread"],
                )
            for value, ring in zip(transformed, rings):
                ring.append(value)
        return {"rows": rows}

    def signals(self, bars, context, group, params):
        start = (
            metrics.split_ts(params["from_date"])
            if params["from_date"]
            else None
        )
        end = (
            metrics.split_ts(params["to_date"]) + 86_400
            if params["to_date"]
            else None
        )
        out = []
        free_from = -1

        for i, row in enumerate(context["rows"]):
            if row is None or i < free_from:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue
            minute = (bars[i][TS] % 86_400) // 60
            if not params["entry_from"] <= minute <= params["entry_to"]:
                continue

            move_z, count_z, volume_z, signed_move, spread = row
            if (
                move_z < params["move_z"]
                or count_z > params["count_z"]
                or volume_z > params["volume_z"]
                or spread > params["max_spread"]
            ):
                continue
            side = SHORT if signed_move > 0.0 else LONG
            free_from = _exit_index(
                bars,
                i + 1,
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
            out.append(
                Signal(
                    i + 1,
                    side,
                    params["stop"],
                    params["target"],
                    params["time_stop"],
                )
            )
        return out
