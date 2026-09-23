"""Focused tests for the A2 book-slope asymmetry candidate."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.book_slope_asymmetry import BookSlopeAsymmetry, slope_asymmetry


def bar(ts, price=20_000.0):
    return [ts, price, price + 1.0, price - 1.0, price, 0.0, 0]


class BookSlopeAsymmetryTests(unittest.TestCase):
    def test_asymmetry_is_bounded_and_rejects_undefined_books(self):
        self.assertAlmostEqual(
            slope_asymmetry(
                {"bid_depth_distance": 2.0, "ask_depth_distance": 6.0}
            ),
            0.5,
        )
        self.assertIsNone(
            slope_asymmetry(
                {"bid_depth_distance": 0.0, "ask_depth_distance": 6.0}
            )
        )

    def test_original_continuation_direction_enters_next_minute(self):
        start = 10 * 60 * 60
        bars = [bar(start + i * 60) for i in range(30)]
        strategy = BookSlopeAsymmetry()
        params = strategy.all_params(
            {
                "slope_cut": 0.07,
                "entry_window": "all",
                "stop": 20,
                "time_stop": 10,
            }
        )

        long_rows = [None] * len(bars)
        long_rows[0] = (0.08, 0.5)
        long_signals = strategy.signals(
            bars, {"rows": long_rows}, "all", params
        )
        self.assertEqual(long_signals[0].index, 1)
        self.assertEqual(long_signals[0].side, LONG)

        short_rows = [None] * len(bars)
        short_rows[0] = (-0.08, 0.5)
        short_signals = strategy.signals(
            bars, {"rows": short_rows}, "all", params
        )
        self.assertEqual(short_signals[0].index, 1)
        self.assertEqual(short_signals[0].side, SHORT)

    def test_spread_and_noncontiguous_next_bar_block_entries(self):
        start = 10 * 60 * 60
        strategy = BookSlopeAsymmetry()
        params = strategy.all_params({"entry_window": "all"})

        bars = [bar(start), bar(start + 60)]
        rows = [(0.2, 2.0), None]
        self.assertEqual(strategy.signals(bars, {"rows": rows}, "all", params), [])

        bars = [bar(start), bar(start + 120)]
        rows = [(0.2, 0.5), None]
        self.assertEqual(strategy.signals(bars, {"rows": rows}, "all", params), [])

    def test_optional_confirmations_must_agree_with_the_trade_side(self):
        start = 10 * 60 * 60
        bars = [bar(start + i * 60) for i in range(30)]
        strategy = BookSlopeAsymmetry()
        rows = [(0.2, 0.5)] + [None] * (len(bars) - 1)
        confirmations = [(-0.5, 100.0)] + [None] * (len(bars) - 1)

        top5 = strategy.all_params(
            {"entry_window": "all", "confirmation": "top5"}
        )
        self.assertEqual(
            strategy.signals(
                bars,
                {"rows": rows, "confirmations": confirmations},
                "all",
                top5,
            ),
            [],
        )

        delta = strategy.all_params(
            {"entry_window": "all", "confirmation": "delta"}
        )
        self.assertEqual(
            strategy.signals(
                bars,
                {"rows": rows, "confirmations": confirmations},
                "all",
                delta,
            )[0].side,
            LONG,
        )


if __name__ == "__main__":
    unittest.main()
