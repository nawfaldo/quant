"""Causality and confirmation tests for Candidate A."""
import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.l2_opening_range_breakout import L2OpeningRangeBreakout


def bar(day, minute, open_, high, low, close):
    row = [0.0] * 7
    row[TS] = day * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, 0.0, 1,
    )
    return row


def feature(delta=10.0, imbalance=0.3, micro=100.1, mid=100.0):
    return {"trade_delta": delta, "top5_imbalance": imbalance,
            "microprice": micro, "midprice": mid, "spread": 0.25,
            "book_valid": True}


class L2OpeningRangeBreakoutTest(unittest.TestCase):
    def setUp(self):
        self.strategy = L2OpeningRangeBreakout()
        self.day = 20_000
        self.opening = [bar(self.day, m, 100, 101, 99, 100)
                        for m in range(570, 585)]

    def params(self, **overrides):
        params = self.strategy.all_params({"opening_range": 15, "buffer": 0.0})
        params.update(overrides)
        return params

    def test_enters_on_minute_after_confirmed_break(self):
        decision = bar(self.day, 585, 100, 103, 100, 102)
        entry = bar(self.day, 586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        context = {"features": {decision[TS]: feature()}, "vix": [20.0] * len(bars)}
        signals = self.strategy.signals(bars, context, "all", self.params())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, len(self.opening) + 1)
        self.assertEqual(signals[0].side, "long")
        self.assertEqual(signals[0].target, 30.0)

    def test_rejects_disagreeing_l2(self):
        decision = bar(self.day, 585, 100, 103, 100, 102)
        entry = bar(self.day, 586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        bad = feature(delta=-10.0)
        context = {"features": {decision[TS]: bad}, "vix": [20.0] * len(bars)}
        self.assertEqual(self.strategy.signals(bars, context, "all", self.params()), [])

    def test_one_trade_per_session(self):
        first = bar(self.day, 585, 100, 103, 100, 102)
        next_bar = bar(self.day, 586, 102, 104, 101, 103)
        later = bar(self.day, 587, 103, 105, 102, 104)
        after = bar(self.day, 588, 104, 106, 103, 105)
        bars = self.opening + [first, next_bar, later, after]
        context = {"features": {first[TS]: feature(), later[TS]: feature()},
                   "vix": [20.0] * len(bars)}
        signals = self.strategy.signals(bars, context, "all", self.params())
        self.assertEqual(len(signals), 1)

    def test_control_does_not_require_features(self):
        decision = bar(self.day, 585, 100, 103, 100, 102)
        entry = bar(self.day, 586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        context = {"features": {}, "vix": [0.0] * len(bars)}
        signals = self.strategy.signals(
            bars, context, "all", self.params(require_l2=False)
        )
        self.assertEqual(len(signals), 1)


if __name__ == "__main__":
    unittest.main()
