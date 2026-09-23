"""Candidate A: L2-confirmed NQ opening-range breakout.

The opening range and confirming minute are both complete before a signal is
emitted.  ``Signal.index`` points at the following executable minute, keeping
the implementation causal.  The class exposes the candidate document's
original 24-cell grid; broader exit/regime/sizing experiments live in the
research driver so the first experiment remains identifiable.
"""
from sandbox import data, metrics
from sandbox.data import C, H, L, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN = 9 * 60 + 30
LAST_ENTRY = 15 * 60
EXIT_MIN = 16 * 60

NO_FEATURES = {
    "midprice": 0.0,
    "spread": 0.0,
    "microprice": 0.0,
    "top5_imbalance": 0.0,
    "trade_delta": 0.0,
    "book_valid": False,
}


@register
class L2OpeningRangeBreakout(Strategy):
    """One causal, L2-confirmed opening-range breakout per RTH session."""

    name = "L2 Opening Range Breakout"
    bars = "level_two"
    execution = Execution(initial=1_000.0, slippage=0.2, session_end_min=EXIT_MIN)

    defaults = {
        "opening_range": 30,
        "buffer": 0.0,
        "min_imbalance": 0.20,
        "stop": 15.0,
        "rr": 2.0,
        "time_stop": 90,
        "entry_from": 0,       # 0 means immediately after the completed range
        "entry_to": LAST_ENTRY,
        "skip_days": "",
        "vix_min": 0.0,        # 0 disables that side of the VIX gate
        "vix_max": 0.0,
        "require_l2": True,
        "from_date": None,
        "to_date": None,
    }

    # The immutable Candidate-A coarse grid: 2 * 2 * 3 * 2 = 24 cells.
    grid = {
        "opening_range": [15, 30],
        "buffer": [0.0, 1.0],
        "min_imbalance": [0.10, 0.20, 0.30],
        "stop": [10.0, 15.0],
    }

    def valid(self, params):
        return (
            params["opening_range"] in (15, 30)
            and params["stop"] > 0
            and params["rr"] > 0
            and params["time_stop"] > 0
            and OPEN_MIN + params["opening_range"] <= params["entry_to"] <= LAST_ENTRY
            and (params["vix_max"] <= 0 or params["vix_max"] >= params["vix_min"])
        )

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        return {
            "features": data.load_l2_features(self.symbol),
            "vix": data.vix_series(bars),
        }

    @staticmethod
    def _skip_set(value):
        if not value:
            return set()
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        return {names[item.strip().lower()] for item in str(value).split(",")
                if item.strip().lower() in names}

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []

        features = (context or {}).get("features", {})
        vix = (context or {}).get("vix", [0.0] * len(bars))
        skipped = self._skip_set(params["skip_days"])
        from_ts = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        to_ts = metrics.split_ts(params["to_date"]) if params["to_date"] else None
        range_end = OPEN_MIN + int(params["opening_range"])
        first_decision = max(range_end, int(params["entry_from"]) or range_end)
        target = float(params["stop"]) * float(params["rr"])

        signals = []
        day = None
        range_high = range_low = None
        traded = False

        for i, bar in enumerate(bars):
            ts = int(bar[TS])
            current_day = ts // 86_400
            minute = (ts % 86_400) // 60
            if current_day != day:
                day = current_day
                range_high = range_low = None
                traded = False

            if OPEN_MIN <= minute < range_end:
                range_high = bar[H] if range_high is None else max(range_high, bar[H])
                range_low = bar[L] if range_low is None else min(range_low, bar[L])
                continue

            if traded or range_high is None or minute < first_decision:
                continue
            if from_ts is not None and ts < from_ts:
                continue
            if to_ts is not None and ts >= to_ts:
                continue
            weekday = (current_day + 3) % 7
            if weekday in skipped:
                continue
            if i + 1 >= len(bars) or bars[i + 1][TS] // 86_400 != current_day:
                continue
            entry_minute = (bars[i + 1][TS] % 86_400) // 60
            if entry_minute > int(params["entry_to"]):
                continue

            level = vix[i] if i < len(vix) else 0.0
            if params["vix_min"] > 0 and (level <= 0 or level < params["vix_min"]):
                continue
            if params["vix_max"] > 0 and (level <= 0 or level >= params["vix_max"]):
                continue

            close = bar[C]
            long_break = close > range_high + float(params["buffer"])
            short_break = close < range_low - float(params["buffer"])
            if not long_break and not short_break:
                continue

            if params["require_l2"]:
                f = features.get(ts, NO_FEATURES)
                valid_book = (f.get("book_valid", False) and f.get("spread", 0.0) > 0
                              and f.get("midprice", 0.0) > 0)
                imbalance = f.get("top5_imbalance", 0.0)
                delta = f.get("trade_delta", 0.0)
                micro = f.get("microprice", 0.0)
                mid = f.get("midprice", 0.0)
                long_break = (long_break and valid_book and delta > 0
                              and imbalance >= params["min_imbalance"] and micro >= mid)
                short_break = (short_break and valid_book and delta < 0
                               and imbalance <= -params["min_imbalance"] and micro <= mid)

            if long_break or short_break:
                signals.append(Signal(i + 1, LONG if long_break else SHORT,
                                      float(params["stop"]), target,
                                      int(params["time_stop"])))
                traded = True

        return signals
