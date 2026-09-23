"""Focused tests for the A4 toxicity off-switch."""

import unittest

from sandbox.execution import LONG, Execution, Signal
from sandbox.strategies.base import Strategy
from sandbox.toxicity_gate import ToxicityOverlay, minute_toxicity, toxicity_series


def bar(ts, price=20_000.0):
    return [ts, price, price + 1.0, price - 1.0, price, 0.0, 1]


def feature(buy, sell):
    return {
        "aggressive_buy_volume": buy,
        "aggressive_sell_volume": sell,
        "trade_delta": buy - sell,
    }


class OneSignal(Strategy):
    name = "one"
    execution = Execution()
    defaults = {}
    grid = {}

    def context(self):
        return None

    def signals(self, bars, context, group, params):
        return [Signal(3, LONG, 10.0, 20.0)]


class ToxicityGateTests(unittest.TestCase):
    def test_minute_proxy_rebuilds_from_summed_inputs(self):
        self.assertAlmostEqual(minute_toxicity(feature(75.0, 25.0)), 0.5)
        self.assertIsNone(minute_toxicity(feature(0.0, 0.0)))
        self.assertIsNone(minute_toxicity(None))

    def test_series_is_causal_and_requires_contiguous_complete_history(self):
        bars = [bar(600 + 60 * index) for index in range(5)]
        features = {
            bars[index][0]: feature(60.0 + index * 10.0, 40.0)
            for index in range(5)
        }
        readings = toxicity_series(bars, features, 1)
        self.assertIsNone(readings[0])
        self.assertAlmostEqual(readings[1], 0.2)
        # Changing the entry minute cannot alter its already-known reading.
        features[bars[1][0]] = feature(100.0, 0.0)
        self.assertAlmostEqual(readings[1], 0.2)

        gapped = [bar(600), bar(660), bar(780), bar(840)]
        gapped_features = {row[0]: feature(75.0, 25.0) for row in gapped}
        self.assertIsNone(toxicity_series(gapped, gapped_features, 2)[2])

    def test_overlay_blocks_high_toxicity_and_fails_open(self):
        bars = [bar(600 + 60 * index) for index in range(5)]
        low = [None] * len(bars)
        low[3] = 0.10
        high = [None] * len(bars)
        high[3] = 0.25
        overlay = ToxicityOverlay(OneSignal())
        params = overlay.all_params(
            {"toxicity_lookback": 1, "toxicity_cutoff": 0.20}
        )

        context = {"base": None, "toxicity": {1: low}}
        self.assertEqual(len(overlay.signals(bars, context, "all", params)), 1)
        context["toxicity"][1] = high
        self.assertEqual(overlay.signals(bars, context, "all", params), [])
        context["toxicity"][1] = [None] * len(bars)
        self.assertEqual(len(overlay.signals(bars, context, "all", params)), 1)


if __name__ == "__main__":
    unittest.main()

