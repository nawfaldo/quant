"""Focused tests for A5 effort/result air-pocket fade."""

import math
import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.air_pocket_fade import (
    AirPocketFade,
    NORM_SESSIONS,
    _zscore,
    effort_result,
)


def bar(ts, price=20_000.0):
    return [ts, price, price + 1.0, price - 1.0, price, 0.0, 0]


class AirPocketFadeTests(unittest.TestCase):
    def test_effort_result_rebuilds_minute_volume(self):
        feature = {
            "price_change": -3.0,
            "trade_count": 4.0,
            "aggressive_buy_volume": 60.0,
            "aggressive_sell_volume": 40.0,
        }
        result, count, volume = effort_result(feature)
        self.assertAlmostEqual(result, math.log1p(3.0))
        self.assertAlmostEqual(count, math.log1p(4.0))
        self.assertAlmostEqual(volume, math.log1p(100.0))
        feature["trade_count"] = 0.0
        self.assertIsNone(effort_result(feature))

    def test_zscore_requires_twenty_prior_observations(self):
        self.assertIsNone(_zscore(2.0, list(range(NORM_SESSIONS - 1))))
        self.assertGreater(_zscore(30.0, list(range(NORM_SESSIONS))), 1.0)

    def test_large_low_effort_move_fades_at_next_open(self):
        start = 10 * 60 * 60
        bars = [bar(start + i * 60) for i in range(20)]
        rows = [None] * len(bars)
        rows[2] = (1.5, -0.5, -0.5, 4.0, 0.5)
        rows[10] = (1.5, -0.5, -0.5, -4.0, 0.5)
        strategy = AirPocketFade()
        signals = strategy.signals(
            bars, {"rows": rows}, "all", strategy.all_params()
        )
        self.assertEqual(signals[0].index, 3)
        self.assertEqual(signals[0].side, SHORT)
        self.assertEqual(signals[1].index, 11)
        self.assertEqual(signals[1].side, LONG)

    def test_ordinary_move_high_effort_spread_and_gap_block(self):
        start = 10 * 60 * 60
        strategy = AirPocketFade()
        params = strategy.all_params()
        bars = [bar(start + i * 60) for i in range(8)]

        cases = [
            (0.5, -0.5, -0.5, 4.0, 0.5),
            (1.5, 0.5, -0.5, 4.0, 0.5),
            (1.5, -0.5, 0.5, 4.0, 0.5),
            (1.5, -0.5, -0.5, 4.0, 2.0),
        ]
        for row in cases:
            rows = [None] * len(bars)
            rows[2] = row
            self.assertEqual(
                strategy.signals(bars, {"rows": rows}, "all", params), []
            )

        gapped = list(bars)
        gapped[3] = bar(start + 4 * 60)
        rows = [None] * len(bars)
        rows[2] = (1.5, -0.5, -0.5, 4.0, 0.5)
        self.assertEqual(
            strategy.signals(gapped, {"rows": rows}, "all", params), []
        )


if __name__ == "__main__":
    unittest.main()
