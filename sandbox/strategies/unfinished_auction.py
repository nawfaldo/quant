"""Michael Valtos (Orderflows): the unfinished-auction magnet.

Valtos — 25 years on bank and commodity desks, JP Morgan and Cargill among
them — teaches reading the footprint for what the auction *failed* to do, and
the unfinished auction is the cleanest example.

At the extreme of a bar, a finished auction leaves one side empty: the last
buyer paid up there, nobody sold back, and the auction concluded. When both
sides printed at the extreme the auction never resolved. The standard reading
is that the market has business left at that price and tends to return to it,
so an unfinished extreme is traded as a magnet rather than as a reversal.

This implementation is the magnet, not a fade:

  1. A session bar prints a new extreme whose footprint is unfinished. The
     price of that extreme is remembered as an open level.
  2. Price walks `min_distance` points away from it.
  3. Enter *toward* the level — long when the unfinished business sits above,
     short when it sits below — targeting the level itself.
  4. The level is consumed once traded, or once price reaches it.

The target is therefore the distance to the unfinished price rather than a
multiple of the stop, which is what makes this setup different in shape from
every fixed-bracket strategy in this package: its reward is set by where the
business is, and only its risk is a parameter.
"""
from sandbox import data, metrics
from sandbox.data import C, H, L, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN = 9 * 60 + 30
LAST_ENTRY = 15 * 60
EXIT_MIN = 16 * 60


@register
class UnfinishedAuction(Strategy):
    """Trade back toward an unfinished auction left at a session extreme."""

    name = "Orderflows Unfinished Auction"
    bars = "level_two"
    execution = Execution(initial=1_000.0, spread=0.2, session_end_min=EXIT_MIN)

    defaults = {
        "min_distance": 10.0,   # how far price must leave before the trade arms
        "max_distance": 60.0,   # beyond this the level is stale, not a magnet
        "stop": 15.0,
        "min_target": 5.0,
        "trail_frac": 0.0,
        "time_stop": 120,
        "max_trades": 2,
        "warmup": 15,           # minutes of session before extremes count
        "entry_to": LAST_ENTRY,
        "skip_days": "",
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "min_distance": [5.0, 10.0, 20.0],
        "stop": [10.0, 15.0, 20.0],
        "max_distance": [40.0, 60.0, 100.0],
    }

    def valid(self, params):
        return (params["stop"] > 0
                and params["min_target"] > 0
                and params["time_stop"] > 0
                and params["max_trades"] >= 1
                and 0 < params["min_distance"] < params["max_distance"]
                and params["warmup"] >= 0
                and params["entry_to"] <= LAST_ENTRY)

    def context(self):
        return {"footprint": data.load_footprint_features(self.symbol)}

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []

        footprint = (context or {}).get("footprint", {})
        skip = self._skip_set(params["skip_days"])
        from_ts = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        to_ts = metrics.split_ts(params["to_date"]) if params["to_date"] else None

        signals = []
        day = None
        state = None
        for index, bar in enumerate(bars):
            ts = int(bar[TS])
            current = ts // 86_400
            minute = (ts % 86_400) // 60
            if current != day:
                day = current
                state = {"high": None, "low": None,
                         "open_high": None, "open_low": None, "taken": 0}

            printed = footprint.get(ts)
            armed = minute >= OPEN_MIN + int(params["warmup"])

            # -- consume levels price has already returned to ----------------
            if state["open_high"] is not None and bar[H] >= state["open_high"]:
                state["open_high"] = None
            if state["open_low"] is not None and bar[L] <= state["open_low"]:
                state["open_low"] = None

            tradable = (
                armed
                and state["taken"] < int(params["max_trades"])
                and (from_ts is None or ts >= from_ts)
                and (to_ts is None or ts < to_ts)
                and (current + 3) % 7 not in skip
                and index + 1 < len(bars)
                and bars[index + 1][TS] // 86_400 == current
                and (bars[index + 1][TS] % 86_400) // 60 <= int(params["entry_to"])
            )
            if tradable:
                signal = self._entry(bar, index, state, params)
                if signal is not None:
                    signals.append(signal)
                    state["taken"] += 1

            # -- record new unfinished extremes, after any decision ----------
            if printed and armed:
                if (state["high"] is None or bar[H] > state["high"]) and printed[
                        "unfinished_high"]:
                    state["open_high"] = bar[H]
                if (state["low"] is None or bar[L] < state["low"]) and printed[
                        "unfinished_low"]:
                    state["open_low"] = bar[L]
            state["high"] = bar[H] if state["high"] is None else max(state["high"], bar[H])
            state["low"] = bar[L] if state["low"] is None else min(state["low"], bar[L])
        return signals

    def _entry(self, bar, index, state, params):
        """One signal toward whichever unfinished level is at a tradable distance."""
        close = bar[C]
        stop = float(params["stop"])
        trail = float(params["trail_frac"])
        minimum, maximum = float(params["min_distance"]), float(params["max_distance"])

        for level, side in ((state["open_high"], LONG), (state["open_low"], SHORT)):
            if level is None:
                continue
            distance = level - close if side == LONG else close - level
            if not minimum <= distance <= maximum:
                continue
            if distance < float(params["min_target"]):
                continue
            return Signal(index + 1, side, stop, distance, int(params["time_stop"]),
                          stop * trail if trail > 0 else None)
        return None

    @staticmethod
    def _skip_set(value):
        if not value:
            return set()
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        return {names[item.strip().lower()] for item in str(value).split(",")
                if item.strip().lower() in names}
