"""Focused tests for A3 Kyle-lambda regime continuation."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.kyle_lambda_continuation import (
    KyleLambdaContinuation,
    minute_lambda,
    persistent_flow,
)


def bar(ts, price=20_000.0):
    return [ts, price, price + 1.0, price - 1.0, price, 0.0, 0]


class KyleLambdaContinuationTests(unittest.TestCase):
    def test_lambda_is_rebuilt_from_minute_sums(self):
        feature = {
            "aggressive_buy_volume": 60.0,
            "aggressive_sell_volume": 40.0,
            "price_change": -2.0,
            "delta_per_tick": 999_999.0,
        }
        self.assertEqual(minute_lambda(feature), 0.02)
        feature["aggressive_buy_volume"] = 0.0
        feature["aggressive_sell_volume"] = 0.0
        self.assertIsNone(minute_lambda(feature))

    def test_persistent_flow_requires_imbalance_and_agreement(self):
        long_rows = [
            (80.0, 20.0, 60.0),
            (70.0, 30.0, 40.0),
            (65.0, 35.0, 30.0),
        ]
        self.assertEqual(persistent_flow(long_rows, 2, 3)[0], LONG)

        short_rows = [
            (20.0, 80.0, -60.0),
            (30.0, 70.0, -40.0),
            (35.0, 65.0, -30.0),
        ]
        self.assertEqual(persistent_flow(short_rows, 2, 3)[0], SHORT)

        weak = [(55.0, 45.0, 10.0)] * 3
        self.assertIsNone(persistent_flow(weak, 2, 3))
        self.assertIsNone(persistent_flow([None, *long_rows[1:]], 2, 3))

    def test_cheap_lambda_continues_flow_at_next_open(self):
        start = 10 * 60 * 60
        bars = [bar(start + i * 60) for i in range(30)]
        strategy = KyleLambdaContinuation()
        params = strategy.all_params(
            {"lambda_z": -0.5, "flow_lookback": 3, "stop": 20}
        )
        lambda_rows = [None] * len(bars)
        lambda_rows[2] = (-1.0, 0.5)
        flow_rows = [None] * len(bars)
        flow_rows[:3] = [
            (80.0, 20.0, 60.0),
            (70.0, 30.0, 40.0),
            (65.0, 35.0, 30.0),
        ]

        signals = strategy.signals(
            bars,
            {
                "lambda_rows": lambda_rows,
                "flow_rows": flow_rows,
                "confirmations": [0.5] * len(bars),
            },
            "all",
            params,
        )
        self.assertEqual(signals[0].index, 3)
        self.assertEqual(signals[0].side, LONG)

        params["confirmation"] = "top5"
        confirmations = [None] * len(bars)
        confirmations[2] = -0.5
        self.assertEqual(
            strategy.signals(
                bars,
                {
                    "lambda_rows": lambda_rows,
                    "flow_rows": flow_rows,
                    "confirmations": confirmations,
                },
                "all",
                params,
            ),
            [],
        )

    def test_high_lambda_spread_and_bar_gaps_block_entry(self):
        start = 10 * 60 * 60
        strategy = KyleLambdaContinuation()
        params = strategy.all_params({"flow_lookback": 3})
        flow = [(80.0, 20.0, 60.0)] * 3

        bars = [bar(start + i * 60) for i in range(4)]
        rows = [None, None, (0.5, 0.5), None]
        self.assertEqual(
            strategy.signals(
                bars, {"lambda_rows": rows, "flow_rows": flow + [None]}, "all", params
            ),
            [],
        )

        bars = [bar(start), bar(start + 120), bar(start + 180), bar(start + 240)]
        rows = [None, None, (-1.0, 0.5), None]
        self.assertEqual(
            strategy.signals(
                bars, {"lambda_rows": rows, "flow_rows": flow + [None]}, "all", params
            ),
            [],
        )

        rows[2] = (-1.0, 2.0)
        self.assertEqual(
            strategy.signals(
                bars, {"lambda_rows": rows, "flow_rows": flow + [None]}, "all", params
            ),
            [],
        )

        bars[3] = bar(start + 4 * 60)
        rows[2] = (-1.0, 0.5)
        self.assertEqual(
            strategy.signals(
                bars, {"lambda_rows": rows, "flow_rows": flow + [None]}, "all", params
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
