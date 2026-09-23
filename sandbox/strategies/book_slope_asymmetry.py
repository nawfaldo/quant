"""A2 book-slope asymmetry research candidate.

The feature builder emits the size-weighted absolute distance from midprice for
the bid and ask sides of the ten-level book.  The bounded asymmetry is:

    slope = (ask_depth_distance - bid_depth_distance)
            / (ask_depth_distance + bid_depth_distance)

A positive value means ask liquidity sits farther from mid than bid liquidity,
so the original A2 *path of least resistance* hypothesis predicts an upward
move.  A negative value predicts a downward move.  This module tests that
continuation hypothesis; it does not flip the sign after seeing results.

Research protocol:

* the signal is read at a completed minute and enters at the next contiguous
  minute's open;
* missing, crossed, or non-positive book measurements are rejected;
* the ratio is unitless and has similar source distributions, so no fitted
  normalization is required;
* the grid is deliberately coarse: four cuts, three structural session
  buckets, and three values on each bracket axis; the ten-minute holding
  horizon is fixed rather than searched;
* one position at a time, pessimistic stop-before-target ordering, spread on
  both legs, and a 15:45 flatten all come from the common execution model.

The session buckets are structural market regimes, not minute-by-minute
boundaries fitted to PnL: open 09:45--11:00, midday 11:00--14:00, and late
14:00--15:30 New York wall-clock time.
"""

from sandbox import data
from sandbox import metrics
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

EXIT_MIN = 945
WINDOWS = {
    "all": (585, 930),
    "open": (585, 660),
    "mid": (660, 840),
    "late": (840, 930),
}

NO_FEATURES = {
    "spread": 0.0,
    "bid_depth_distance": 0.0,
    "ask_depth_distance": 0.0,
    "book_valid": False,
}


def slope_asymmetry(feature):
    """Bounded ask-minus-bid distance asymmetry, or ``None`` if undefined."""
    bid = feature["bid_depth_distance"]
    ask = feature["ask_depth_distance"]
    denominator = ask + bid
    if bid <= 0.0 or ask <= 0.0 or denominator <= 0.0:
        return None
    return (ask - bid) / denominator


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """First bar on which a candidate position is flat again."""
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
class BookSlopeAsymmetry(Strategy):
    name = "Book Slope Asymmetry"
    bars = "level_two"
    execution = Execution(session_end_min=EXIT_MIN)

    defaults = {
        "slope_cut": 0.07,
        "entry_window": "all",
        "stop": 20,
        "rr": 1.5,
        "time_stop": 10,
        "max_spread": 1.25,
        # Optional research ablations. They are intentionally outside the main
        # grid so the initial 108-cell search remains the original A2 test.
        "confirmation": "none",
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "slope_cut": [0.05, 0.07, 0.09, 0.12],
        "entry_window": ["open", "mid", "late"],
        "stop": [10, 20, 30],
        "rr": [1.0, 1.5, 2.0],
    }

    def valid(self, params):
        return (
            params["slope_cut"] > 0.0
            and params["entry_window"] in WINDOWS
            and params["stop"] > 0.0
            and params["rr"] > 0.0
            and params["time_stop"] > 0
            and params["confirmation"] in {"none", "top5", "delta", "both"}
        )

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        rows = [None] * len(bars)
        confirmations = [None] * len(bars)
        for i, bar in enumerate(bars):
            feature = features.get(bar[TS], NO_FEATURES)
            if not feature["book_valid"]:
                continue
            value = slope_asymmetry(feature)
            if value is not None:
                rows[i] = (value, feature["spread"])
                confirmations[i] = (
                    feature["top5_imbalance"],
                    feature["trade_delta"],
                )
        return {"rows": rows, "confirmations": confirmations}

    def signals(self, bars, context, group, params):
        cut = params["slope_cut"]
        stop = params["stop"]
        target = stop * params["rr"]
        time_stop = params["time_stop"]
        entry_from, entry_to = WINDOWS[params["entry_window"]]
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
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
            if not entry_from <= minute <= entry_to:
                continue

            value, spread = row
            if spread > params["max_spread"]:
                continue
            if value >= cut:
                side = LONG
            elif value <= -cut:
                side = SHORT
            else:
                continue
            confirmation = params["confirmation"]
            if confirmation != "none":
                values = context.get("confirmations", [None] * len(bars))[i]
                if values is None:
                    continue
                top5, trade_delta = values
                sign = 1.0 if side == LONG else -1.0
                if confirmation in {"top5", "both"} and sign * top5 <= 0.0:
                    continue
                if confirmation in {"delta", "both"} and sign * trade_delta <= 0.0:
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
