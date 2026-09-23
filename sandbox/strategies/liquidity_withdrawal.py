"""A1 Liquidity Withdrawal Index, adds-only research candidate.

This is the existing-column variant from ``L2_STRATEGY_CANDIDATES.md``:

    bid_lwi = bid_cancels / (bid_adds + bid_cancels)
    ask_lwi = ask_cancels / (ask_adds + ask_cancels)
    signed_lwi = ask_lwi - bid_lwi

A positive signed value means ask-side makers are withdrawing more aggressively
than bid-side makers and is traded as upward pressure; a negative value is
traded as downward pressure. The published denominator is standing depth plus
adds. Standing depth is not emitted yet, so this module explicitly tests the
weaker adds-only statistic and must not be described as the published LWI.

The required redundancy check was run before this strategy was added. On
2026-07-29 minute sums, signed LWI's correlation with the raw signed Deep OFI
used by ``ofi_momentum`` was 0.079 over 138,800 Databento minutes and 0.369 over
3,345 Bookmap minutes. Correlation with activity-normalized OFI was much higher,
0.813 and 0.653 respectively. This is therefore distinct from the compiled raw
OFI signal, but not evidence for an independent family of normalized signals.

Raw LWI also has a feed-dependent scale: its 10th/90th percentiles were roughly
-0.00050/+0.00050 for Databento and -0.00703/+0.00667 for Bookmap. The signal is
therefore standardized against prior readings from the same source and
minute-of-day. The current reading is scored before it enters the trailing
window, keeping the statistic point-in-time.

Pre-declared experiment, before looking at PnL:

* expected direction: continue toward the side whose opposing makers withdraw;
* entry: next contiguous minute open, 09:45--15:30 New York;
* horizon: five minutes, the shortest practical minute-bar test (and explicitly
  not the 1--2 second effect documented in the literature);
* bracket: fixed stop with target = 1.5 * stop, stop before target;
* free axes: z threshold, normalization lookback, and stop width only.

Missing/invalid feature rows cannot form a signal. Both per-side denominators
must be positive; an undefined ratio is never replaced with zero.
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

NO_FEATURES = {
    "source": "",
    "spread": 0.0,
    "bid_add_volume": 0.0,
    "bid_cancel_volume": 0.0,
    "ask_add_volume": 0.0,
    "ask_cancel_volume": 0.0,
    "executed_at_bid": 0.0,
    "executed_at_ask": 0.0,
    "bid_standing_depth": 0.0,
    "ask_standing_depth": 0.0,
    "book_valid": False,
}

WITHDRAWAL_MODES = {"gross", "execution_adjusted", "standing_depth"}


def _withdrawals(feature, mode):
    """Return bid/ask maker-withdrawal proxies.

    The raw absolute-depth replay labels every size decrease as a cancellation,
    including trade-driven depletion.  ``execution_adjusted`` removes the
    executed volume observed on the same side and clamps at zero.  It remains a
    proxy because event-level action and standing-depth columns are not retained
    in the current derived table.
    """
    bid = feature["bid_cancel_volume"]
    ask = feature["ask_cancel_volume"]
    if mode == "execution_adjusted":
        bid = max(0.0, bid - feature["executed_at_bid"])
        ask = max(0.0, ask - feature["executed_at_ask"])
    return bid, ask


def signed_lwi(feature, mode="gross"):
    """Return ask LWI minus bid LWI, or ``None`` for undefined ratios."""
    if mode not in WITHDRAWAL_MODES:
        raise ValueError(f"unknown withdrawal mode {mode!r}")
    bid_cancel, ask_cancel = _withdrawals(feature, mode)
    if mode == "standing_depth":
        bid_cancel = max(0.0, bid_cancel - feature["executed_at_bid"])
        ask_cancel = max(0.0, ask_cancel - feature["executed_at_ask"])
        bid_activity = feature["bid_standing_depth"] + feature["bid_add_volume"]
        ask_activity = feature["ask_standing_depth"] + feature["ask_add_volume"]
    else:
        bid_activity = feature["bid_add_volume"] + bid_cancel
        ask_activity = feature["ask_add_volume"] + ask_cancel
    if bid_activity <= 0.0 or ask_activity <= 0.0:
        return None
    bid = bid_cancel / bid_activity
    ask = ask_cancel / ask_activity
    return ask - bid


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
class LiquidityWithdrawal(Strategy):
    name = "Liquidity Withdrawal (adds-only)"
    bars = "level_two"
    execution = Execution(session_end_min=EXIT_MIN)

    defaults = {
        "lwi_z": 2.0,
        "norm_sessions": 20,
        "stop": 25,
        "rr": 1.5,
        "time_stop": 5,
        "max_spread": 1.25,
        "entry_from": 585,
        "entry_to": 930,
        "from_date": None,
        "to_date": None,
        # Research correction outside the original optimization grid. ``gross``
        # exactly preserves the recorded pre-declared experiment.
        "withdrawal_mode": "gross",
    }

    grid = {
        "lwi_z": [1.5, 2.0, 2.5],
        "norm_sessions": [10, 20, 40],
        "stop": [15, 25, 40],
    }

    def valid(self, params):
        return (
            params["lwi_z"] > 0
            and params["norm_sessions"] >= 2
            and params["stop"] > 0
            and params["rr"] > 0
            and params["time_stop"] > 0
            and params["withdrawal_mode"] in WITHDRAWAL_MODES
        )

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        lookbacks = set(self.grid.get("norm_sessions", ())) | {
            self.defaults["norm_sessions"]
        }
        return {
            "rows": {
                (sessions, mode): self._statistics(
                    bars, features, sessions, mode
                )
                for sessions in sorted(lookbacks)
                for mode in sorted(WITHDRAWAL_MODES)
            }
        }

    @staticmethod
    def _statistics(bars, features, norm_sessions, withdrawal_mode="gross"):
        """Causal, source/slot-normalized signed LWI rows.

        Each slot receives at most one observation per session. Requiring a full
        window makes ``norm_sessions`` mean what it says and prevents a partial
        warm-up from silently changing the threshold.
        """
        rows = [None] * len(bars)
        rings = {}

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if not OPEN_MIN <= minute < CLOSE_MIN:
                continue
            feature = features.get(ts, NO_FEATURES)
            if not feature["book_valid"]:
                continue
            value = signed_lwi(feature, withdrawal_mode)
            if value is None:
                continue

            key = (feature.get("source", ""), minute)
            ring = rings.setdefault(key, deque(maxlen=norm_sessions))
            if len(ring) == norm_sessions:
                mean = sum(ring) / norm_sessions
                variance = sum((sample - mean) ** 2 for sample in ring) / (
                    norm_sessions - 1
                )
                if variance > 0.0:
                    rows[i] = (
                        (value - mean) / math.sqrt(variance),
                        feature["spread"],
                    )
            ring.append(value)
        return rows

    def signals(self, bars, context, group, params):
        rows = context["rows"][
            (params["norm_sessions"], params["withdrawal_mode"])
        ]
        cut = params["lwi_z"]
        stop = params["stop"]
        target = stop * params["rr"]
        time_stop = params["time_stop"]
        entry_from, entry_to = params["entry_from"], params["entry_to"]
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = (
            metrics.split_ts(params["to_date"]) + 86_400
            if params["to_date"]
            else None
        )

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

            zscore, spread = row
            if spread > params["max_spread"]:
                continue
            if zscore >= cut:
                side = LONG
            elif zscore <= -cut:
                side = SHORT
            else:
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
