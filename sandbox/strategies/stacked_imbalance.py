"""The stacked-imbalance continuation setup.

This is the setup every footprint platform ships a scanner for and every
footprint course teaches — Quantower, ATAS, Sierra, OrderFlowLabs, Optimus all
describe it in the same terms, so it belongs to the tradition rather than to
one creator.

The claim: inside one bar, each price level is a mini-auction. Comparing the
volume that traded at the offer at price P against the volume that traded at
the bid at P-tick — the *diagonal* comparison, because those two are the sides
of the same auction — an imbalance is one side winning by a wide multiple.
Three or more consecutive levels imbalanced the same way is aggression walking
the book rather than noise, and the published rule is to join it: enter in the
direction of the stack and stop just beyond the last stacked level.

The bar's own extreme stands in for "the last stacked level" here. The
per-minute footprint summary in `data.load_footprint_features` records the
length of the longest run, not the prices in it, and the extreme of a bar whose
levels were imbalanced in one direction is where that run ends.
"""
from sandbox import data, metrics
from sandbox.data import C, D, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN = 9 * 60 + 30
LAST_ENTRY = 15 * 60
EXIT_MIN = 16 * 60


@register
class StackedImbalance(Strategy):
    """Join aggression that walked three or more consecutive price levels."""

    name = "Stacked Imbalance Continuation"
    bars = "level_two"
    execution = Execution(initial=1_000.0, spread=0.2, session_end_min=EXIT_MIN)

    defaults = {
        "min_stack": 3,          # the canonical "three in a row"
        "stop_at_extreme": True,  # stop beyond the bar that printed the stack
        "stop_buffer": 1.0,
        "stop": 15.0,            # used when `stop_at_extreme` is off, and as a cap
        "min_stop": 4.0,
        "rr": 2.0,
        "trail_frac": 0.0,
        "time_stop": 60,
        "require_delta": True,   # "combine with delta strength to validate"
        "max_trades": 3,
        "entry_from": 0,
        "entry_to": LAST_ENTRY,
        "skip_days": "",
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "min_stack": [3, 4, 5],
        "stop": [10.0, 15.0, 20.0],
        "rr": [1.5, 2.0, 3.0],
    }

    def valid(self, params):
        return (params["min_stack"] >= 2
                and params["stop"] > 0
                and params["min_stop"] > 0
                and params["time_stop"] > 0
                and params["max_trades"] >= 1
                and (params["rr"] > 0 or params["trail_frac"] > 0)
                and params["entry_from"] <= params["entry_to"] <= LAST_ENTRY)

    def context(self):
        return {"footprint": data.load_footprint_features(self.symbol)}

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []

        footprint = (context or {}).get("footprint", {})
        skip = self._skip_set(params["skip_days"])
        from_ts = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        to_ts = metrics.split_ts(params["to_date"]) if params["to_date"] else None
        stack = int(params["min_stack"])
        cap = int(params["max_trades"])
        entry_from = int(params["entry_from"]) or OPEN_MIN
        trail = float(params["trail_frac"])

        signals = []
        day = None
        taken = 0
        for index, bar in enumerate(bars):
            ts = int(bar[TS])
            current = ts // 86_400
            if current != day:
                day, taken = current, 0
            if taken >= cap:
                continue
            if from_ts is not None and ts < from_ts:
                continue
            if to_ts is not None and ts >= to_ts:
                continue
            if (current + 3) % 7 in skip:
                continue
            if index + 1 >= len(bars) or bars[index + 1][TS] // 86_400 != current:
                continue
            entry_minute = (bars[index + 1][TS] % 86_400) // 60
            if not entry_from <= entry_minute <= int(params["entry_to"]):
                continue

            printed = footprint.get(ts)
            if not printed:
                continue
            long_stack = printed["stacked_buy"] >= stack
            short_stack = printed["stacked_sell"] >= stack
            if long_stack == short_stack:
                # Neither side stacked, or both did in the same minute — the
                # latter is a two-way fight, not the one-way aggression the
                # setup is about.
                continue
            side = LONG if long_stack else SHORT
            if params["require_delta"] and (
                    bar[D] <= 0 if side == LONG else bar[D] >= 0):
                continue

            stop = self._stop(bar, side, params)
            if stop is None:
                continue
            signals.append(Signal(index + 1, side, stop, stop * float(params["rr"]),
                                  int(params["time_stop"]),
                                  stop * trail if trail > 0 else None))
            taken += 1
        return signals

    @staticmethod
    def _skip_set(value):
        if not value:
            return set()
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        return {names[item.strip().lower()] for item in str(value).split(",")
                if item.strip().lower() in names}

    @staticmethod
    def _stop(bar, side, params):
        """Risk for one entry: beyond the stacking bar, or the flat distance.

        Measured from the stacking bar's close, the last price known when the
        signal forms. The engine fills at the next open, so an overnight or
        one-minute gap moves the entry but not the level the stop was reasoned
        about.
        """
        if not params["stop_at_extreme"]:
            return float(params["stop"])
        buffer_ = float(params["stop_buffer"])
        distance = (bar[C] - bar[L] + buffer_ if side == LONG
                    else bar[H] - bar[C] + buffer_)
        return min(float(params["stop"]), max(float(params["min_stop"]), distance))
