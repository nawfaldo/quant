"""Replica of `live_trade/src/strategies/idk/nq_microprice_divergence.rs`.

Microprice is the size-weighted BBO, so where it sits inside the spread is a
fair-value estimate the mid does not carry:

    tilt   = (microprice - midprice) / spread
    tilt_e = EWMA(tilt, halflife)

A positive `tilt_e` means resting size leans to the offer; the strategy reads
that as continuation and buys, mirroring it for shorts.

Thresholds are raw rather than z-scored. `tilt` is a bounded, unitless ratio
with a stationary distribution, unlike the OFI contract counts that need
per-slot normalization in `ofi_momentum`.

Worth knowing when reading this alongside `ofi_momentum`: `tilt` is
`top1_imbalance / 2` exactly -- the ratio is 0.5000 on every feature row. It is
still a different statistic from deep OFI, which sums adds and cancels over ten
levels; correlation between the two EWMAs is 0.044. Read this as a top-of-book
imbalance strategy.

The bracket scales with trailing realised volatility: stop = `k` * the mean true
range of the last `atr_sessions` completed sessions, target = `rr` * stop. Only
completed sessions enter the ATR, so a session cannot see its own range.

As in `ofi_momentum`, the Rust strategy holds at most one position at a time, so
its signals are entangled with its own exits. `signals()` resolves each entry's
exit bar with `_exit_index` to reproduce that occupancy rule.
"""
import math

from sandbox import data
from sandbox import metrics
from sandbox.data import C, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960          # 09:30 .. 16:00
EXIT_MIN = 945                          # 15:45 session flatten

#: Rust's default `OrderFlowFeatures` for a bar with no matching feature row
NO_FEATURES = {"midprice": 0.0, "microprice": 0.0, "spread": 0.0, "book_valid": False}


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """Index of the bar a position opened at `entry_index` leaves on.

    Mirrors `execution.resolve` for one position: stop before target, the time
    stop only once the bracket has not fired, and the session flatten last.
    """
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
            if (nxt is None or nxt[TS] // 86_400 != bar[TS] // 86_400
                    or (nxt[TS] % 86_400) // 60 >= session_end):
                return j
    return total - 1


@register
class MicropriceDivergence(Strategy):
    name = "Microprice Divergence"
    server_name = "NQ Microprice Divergence"
    bars = "level_two"
    #: `risk` mirrors ENTRY_RISK_FRACTION in the Rust file, not the `Execution`
    #: default. See `hourly_delta_reversal.py`.
    execution = Execution(session_end_min=EXIT_MIN, risk=0.01)

    defaults = {
        #: Halflife of the tilt EWMA, in minutes.
        "halflife": 1,
        #: raw |tilt_e| entry cut; the distribution is bounded so this transfers
        #: without the per-slot z-scoring `ofi_momentum` needs.
        "tilt": 0.15,
        # Bracket: trailing 20 completed-session ATR, matching Rust.
        "atr_sessions": 20,
        "k": 0.05,
        "rr": 2.0,
        "time_stop": 20,
        "max_spread": 1.25,
        # Entry window, minutes from midnight: the session open through 15:00,
        # late enough that a scaled bracket still has room before the 15:45
        # forced flatten.
        "entry_from": 570,
        "entry_to": 900,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "k": [0.03, 0.05, 0.07, 0.09],
        "rr": [1.25, 1.5, 2.0],
        "time_stop": [15, 20, 35],
    }

    def valid(self, params):
        return params["k"] > 0 and params["rr"] > 0

    def context(self):
        """Signal rows plus point-in-time ATR by session.

        The EWMA depends only on halflife; ATR uses completed sessions only.
        """
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        halflives = set(self.grid.get("halflife", [])) | {self.defaults["halflife"]}
        return {
            "rows": {h: self._statistics(bars, features, h) for h in sorted(halflives)},
            "atr": data.atr_by_day(bars, self.defaults["atr_sessions"]),
        }

    @staticmethod
    def _statistics(bars, features, halflife):
        rows = [None] * len(bars)
        alpha = 1.0 - 0.5 ** (1.0 / halflife)
        warmup = 4 * halflife
        tilt_e = None
        ewma_minutes = 0
        previous_minute = None
        day = None

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if ts // 86_400 != day:
                # RTH-only features, so sessions are separated by an overnight
                # gap the EWMA must not smooth across.
                day, tilt_e, ewma_minutes, previous_minute = ts // 86_400, None, 0, None
            if not (OPEN_MIN <= minute < CLOSE_MIN) or minute >= EXIT_MIN:
                continue
            feature = features.get(ts, NO_FEATURES)

            if previous_minute is not None and previous_minute + 1 != minute:
                tilt_e, ewma_minutes = None, 0
            previous_minute = minute
            # An invalid or crossed book has no meaningful position inside the
            # spread; it contributes nothing rather than a fabricated zero, and
            # it breaks the EWMA the same way a missing minute does.
            if not feature["book_valid"] or feature["spread"] <= 0.0:
                tilt_e, ewma_minutes = None, 0
                continue
            tilt = (feature["microprice"] - feature["midprice"]) / feature["spread"]
            tilt_e = tilt if tilt_e is None else tilt_e + alpha * (tilt - tilt_e)
            ewma_minutes += 1
            if ewma_minutes >= warmup:
                rows[i] = (tilt_e, feature["spread"])
        return rows

    def signals(self, bars, context, group, params):
        rows = context["rows"][params["halflife"]]
        cut, max_spread = params["tilt"], params["max_spread"]
        time_stop = params["time_stop"]
        entry_from, entry_to = params["entry_from"], params["entry_to"]
        session_end = self.execution.session_end_min
        # Signals are generated over the whole history so the EWMA warm-up comes
        # from bars before the window being evaluated.
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = metrics.split_ts(params["to_date"]) + 86_400 if params["to_date"] else None

        out = []
        free_from = -1          # first bar index at which the strategy is flat again
        for i, row in enumerate(rows):
            if row is None or i < free_from:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue        # fills happen on the contiguous next minute only
            ts = bars[i][TS]
            if not entry_from <= (ts % 86_400) // 60 <= entry_to:
                continue
            tilt_e, spread = row
            if spread > max_spread:
                continue
            # Continuation: the side microprice leans towards is the side traded.
            if tilt_e >= cut:
                side = LONG
            elif tilt_e <= -cut:
                side = SHORT
            else:
                continue

            atr = context["atr"].get(ts // 86_400)
            if atr is None or atr <= 0:
                continue
            stop = params["k"] * atr
            target = params["rr"] * stop
            # Occupied until the position leaves; the Rust strategy refuses a
            # signal while one is open, and its exits run before the signal
            # check, so the exit bar itself is free again.
            free_from = _exit_index(bars, i + 1, side, stop, target, time_stop,
                                    session_end)
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue
            out.append(Signal(i + 1, side, stop, target, time_stop))
        return out


class _MicropriceDivergenceVol(MicropriceDivergence):
    """Shared volatility-scaled bracket implementation.

    The entry signal is fixed; the exit is described by three related axes:
    volatility fraction, reward/risk, and clock.
    """

    defaults = {
        **MicropriceDivergence.defaults,
        # Structural close boundary: a position opened after 15:00 has too
        # little time before the 15:45 forced flatten for a volatility-scaled
        # bracket to express its intended payoff.
        "entry_to": 900,
        "k": 0.05,
        "rr": 2.0,
        "time_stop": 20,
    }

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        return {
            "rows": self._statistics(bars, features, self.defaults["halflife"]),
            # Both readings are point-in-time: ATR uses completed sessions only
            # and VIX is the last hourly close stamped no later than the bar.
            "atr": data.atr_by_day(bars, 20),
            "vix": data.vix_series(bars),
        }

    def volatility_unit(self, bars, context, index):
        raise NotImplementedError

    def signals(self, bars, context, group, params):
        rows = context["rows"]
        cut, max_spread = params["tilt"], params["max_spread"]
        time_stop = params["time_stop"]
        entry_from, entry_to = params["entry_from"], params["entry_to"]
        session_end = self.execution.session_end_min
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = metrics.split_ts(params["to_date"]) + 86_400 if params["to_date"] else None

        out = []
        free_from = -1
        for i, row in enumerate(rows):
            if row is None or i < free_from:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue
            ts = bars[i][TS]
            if not entry_from <= (ts % 86_400) // 60 <= entry_to:
                continue
            tilt_e, spread = row
            if spread > max_spread:
                continue
            if tilt_e >= cut:
                side = LONG
            elif tilt_e <= -cut:
                side = SHORT
            else:
                continue

            unit = self.volatility_unit(bars, context, i)
            if unit is None or unit <= 0:
                continue
            stop = params["k"] * unit
            target = params["rr"] * stop
            free_from = _exit_index(
                bars, i + 1, side, stop, target, time_stop, session_end
            )
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue
            out.append(Signal(i + 1, side, stop, target, time_stop))
        return out


@register
class MicropriceDivergenceAtr(_MicropriceDivergenceVol):
    """The ATR-scaled bracket, as an explicitly named alias.

    The touch imbalance predicts direction; the distance price can travel before
    that prediction goes stale is a property of the current volatility regime.
    So the bracket scales continuously with trailing realised volatility rather
    than using a fixed point distance.
    """

    name = "Microprice Divergence (atr)"
    grid = {
        "k": [0.03, 0.05, 0.07, 0.09],
        "rr": [1.25, 1.5, 2.0],
        "time_stop": [15, 20, 35],
    }

    def volatility_unit(self, bars, context, index):
        return context["atr"].get(bars[index][TS] // 86_400)


@register
class MicropriceDivergenceVix(_MicropriceDivergenceVol):
    """VIX-implied-move variant of the same bracket."""

    name = "Microprice Divergence (vix)"
    defaults = {**_MicropriceDivergenceVol.defaults, "k": 0.075, "rr": 1.25}
    grid = {
        "k": [0.05, 0.075, 0.10, 0.125],
        "rr": [1.25, 1.5, 2.0],
        "time_stop": [15, 20, 35],
    }

    def volatility_unit(self, bars, context, index):
        level = context["vix"][index]
        if level <= 0:
            return None
        return bars[index][C] * level / (100.0 * math.sqrt(252.0))
