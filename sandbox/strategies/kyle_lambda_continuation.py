"""A3 Kyle-lambda regime continuation research candidate.

This is the minute-safe ratio proposed in ``L2_STRATEGY_CANDIDATES.md``:

    lambda = abs(sum(price_change)) /
             sum(aggressive_buy_volume + aggressive_sell_volume)

It deliberately does not use ``delta_per_tick`` because that one-second ratio
is summed by the minute loader.  Summing ratios does not reconstruct a minute
ratio.

Pre-declared experiment, before looking at PnL:

* thesis: directional aggressive flow should continue when its price impact is
  cheap; a high/rising-lambda interpretation is a separate hypothesis;
* lambda is standardized causally against the prior 20 observations from the
  same data source and minute-of-day, using log1p to tame the right tail;
* persistent flow means the trailing signed aggressive-volume imbalance is at
  least 20%, with at least 60% of active minutes agreeing with its direction;
* signal is read after a completed minute and enters the next contiguous open;
* one position at a time, 09:45--15:30 entries, ten-minute time stop, fixed
  1.5 reward/risk, 15:45 flatten, and the common pessimistic/cost model;
* only three coarse axes are searched: cheap-lambda z threshold, flow
  persistence lookback, and stop width.

Missing or invalid feature rows fail closed for entry formation.  They never
become zero-impact or zero-flow observations.
"""

from collections import deque
import math

from sandbox import data
from sandbox import metrics
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960
EXIT_MIN = 945
NORM_SESSIONS = 20
MIN_FLOW_IMBALANCE = 0.20
MIN_DIRECTIONAL_AGREEMENT = 0.60

NO_FEATURES = {
    "source": "",
    "spread": 0.0,
    "top5_imbalance": 0.0,
    "aggressive_buy_volume": 0.0,
    "aggressive_sell_volume": 0.0,
    "trade_delta": 0.0,
    "price_change": 0.0,
    "book_valid": False,
}


def minute_lambda(feature):
    """Return the minute ratio rebuilt from sums, or ``None`` when undefined."""
    aggressive = (
        feature["aggressive_buy_volume"] + feature["aggressive_sell_volume"]
    )
    if aggressive <= 0.0:
        return None
    return abs(feature["price_change"]) / aggressive


def persistent_flow(rows, end, lookback):
    """Return ``(side, imbalance)`` for persistent flow ending at ``end``.

    ``rows`` contains ``(buy_volume, sell_volume, trade_delta)`` or ``None``.
    The window must be contiguous and entirely valid.  Direction is based on
    summed delta; agreement counts only minutes with non-zero signed flow.
    """
    start = end - lookback + 1
    if start < 0:
        return None
    window = rows[start : end + 1]
    if len(window) != lookback or any(row is None for row in window):
        return None

    aggressive = sum(buy + sell for buy, sell, _delta in window)
    delta = sum(row[2] for row in window)
    if aggressive <= 0.0 or delta == 0.0:
        return None
    imbalance = abs(delta) / aggressive
    if imbalance < MIN_FLOW_IMBALANCE:
        return None

    sign = 1.0 if delta > 0.0 else -1.0
    active = [row[2] for row in window if row[2] != 0.0]
    if not active:
        return None
    agreement = sum(1 for value in active if sign * value > 0.0) / len(active)
    if agreement < MIN_DIRECTIONAL_AGREEMENT:
        return None
    return (LONG if sign > 0.0 else SHORT, imbalance)


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """First bar on which one candidate position is flat again."""
    entry = bars[entry_index][O]
    entry_minute = (bars[entry_index][TS] % 86_400) // 60
    total = len(bars)
    for j in range(entry_index, total):
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
            nxt = bars[j + 1] if j + 1 < total else None
            if (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            ):
                return j
    return total - 1


@register
class KyleLambdaContinuation(Strategy):
    name = "Kyle Lambda Continuation"
    bars = "level_two"
    execution = Execution(session_end_min=EXIT_MIN)

    defaults = {
        "lambda_z": -0.5,
        "flow_lookback": 5,
        "stop": 20,
        "rr": 1.5,
        "time_stop": 10,
        "max_spread": 1.25,
        # Fixed research ablation, deliberately outside the optimization grid.
        "confirmation": "none",
        "entry_from": 585,
        "entry_to": 930,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "lambda_z": [-1.0, -0.5, 0.0],
        "flow_lookback": [3, 5, 10],
        "stop": [10, 20, 30],
    }

    def valid(self, params):
        return (
            params["lambda_z"] <= 0.0
            and params["flow_lookback"] >= 2
            and params["stop"] > 0.0
            and params["rr"] > 0.0
            and params["time_stop"] > 0
            and params["confirmation"] in {"none", "top5"}
        )

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        lambda_rows = [None] * len(bars)
        flow_rows = [None] * len(bars)
        sources = [None] * len(bars)
        confirmations = [None] * len(bars)
        rings = {}

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if not OPEN_MIN <= minute < CLOSE_MIN:
                continue
            feature = features.get(ts, NO_FEATURES)
            if not feature["book_valid"]:
                continue
            sources[i] = feature.get("source", "")
            confirmations[i] = feature["top5_imbalance"]
            value = minute_lambda(feature)
            if value is None:
                continue

            source = feature.get("source", "")
            ring = rings.setdefault(
                (source, minute), deque(maxlen=NORM_SESSIONS)
            )
            transformed = math.log1p(value)
            if len(ring) == NORM_SESSIONS:
                mean = sum(ring) / NORM_SESSIONS
                variance = sum((sample - mean) ** 2 for sample in ring) / (
                    NORM_SESSIONS - 1
                )
                if variance > 0.0:
                    lambda_rows[i] = (
                        (transformed - mean) / math.sqrt(variance),
                        feature["spread"],
                    )
            ring.append(transformed)
            flow_rows[i] = (
                feature["aggressive_buy_volume"],
                feature["aggressive_sell_volume"],
                feature["trade_delta"],
            )
        return {
            "lambda_rows": lambda_rows,
            "flow_rows": flow_rows,
            "sources": sources,
            "confirmations": confirmations,
        }

    def signals(self, bars, context, group, params):
        cut = params["lambda_z"]
        lookback = params["flow_lookback"]
        stop = params["stop"]
        target = stop * params["rr"]
        time_stop = params["time_stop"]
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = (
            metrics.split_ts(params["to_date"]) + 86_400
            if params["to_date"]
            else None
        )

        out = []
        free_from = -1
        for i, row in enumerate(context["lambda_rows"]):
            if row is None or i < free_from:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue
            minute = (bars[i][TS] % 86_400) // 60
            if not params["entry_from"] <= minute <= params["entry_to"]:
                continue

            zscore, spread = row
            if zscore > cut or spread > params["max_spread"]:
                continue
            first_flow = i - lookback + 1
            if (
                first_flow < 0
                or bars[first_flow][TS] != bars[i][TS] - (lookback - 1) * 60
            ):
                continue
            sources = context.get("sources")
            if sources is not None and (
                sources[i] is None
                or any(source != sources[i] for source in sources[first_flow : i + 1])
            ):
                continue
            flow = persistent_flow(context["flow_rows"], i, lookback)
            if flow is None:
                continue
            side, _imbalance = flow
            if params["confirmation"] == "top5":
                top5 = context.get("confirmations", [None] * len(bars))[i]
                sign = 1.0 if side == LONG else -1.0
                if top5 is None or sign * top5 <= 0.0:
                    continue

            free_from = _exit_index(
                bars,
                i + 1,
                side,
                stop,
                target,
                time_stop,
                self.execution.session_end_min,
            )
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue
            out.append(Signal(i + 1, side, stop, target, time_stop))
        return out
