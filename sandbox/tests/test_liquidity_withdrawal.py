"""Focused tests for the A1 adds-only Liquidity Withdrawal candidate."""
import unittest

from sandbox.data import TS
from sandbox.execution import LONG, SHORT
from sandbox.strategies.liquidity_withdrawal import LiquidityWithdrawal, signed_lwi


def feature(
    bid_cancel,
    ask_cancel,
    source="dbento",
    spread=0.5,
    executed_at_bid=0.0,
    executed_at_ask=0.0,
    bid_standing_depth=0.0,
    ask_standing_depth=0.0,
):
    return {
        "source": source,
        "spread": spread,
        "bid_add_volume": 1.0 - bid_cancel,
        "bid_cancel_volume": bid_cancel,
        "ask_add_volume": 1.0 - ask_cancel,
        "ask_cancel_volume": ask_cancel,
        "executed_at_bid": executed_at_bid,
        "executed_at_ask": executed_at_ask,
        "bid_standing_depth": bid_standing_depth,
        "ask_standing_depth": ask_standing_depth,
        "book_valid": True,
    }


def bar(ts, price=20_000.0):
    return [ts, price, price + 1.0, price - 1.0, price, 0.0, 0]


class LiquidityWithdrawalTests(unittest.TestCase):
    def test_signed_lwi_uses_per_side_ratios(self):
        self.assertAlmostEqual(signed_lwi(feature(0.2, 0.8)), 0.6)
        missing = feature(0.2, 0.8)
        missing["bid_add_volume"] = 0.0
        missing["bid_cancel_volume"] = 0.0
        self.assertIsNone(signed_lwi(missing))

    def test_execution_adjusted_lwi_removes_trade_depletion(self):
        row = feature(
            0.6,
            0.8,
            executed_at_bid=0.4,
            executed_at_ask=0.1,
        )
        expected_bid = 0.2 / (0.4 + 0.2)
        expected_ask = 0.7 / (0.2 + 0.7)
        self.assertAlmostEqual(
            signed_lwi(row, "execution_adjusted"),
            expected_ask - expected_bid,
        )

    def test_standing_depth_mode_uses_published_denominator_proxy(self):
        row = feature(
            0.6,
            0.8,
            executed_at_bid=0.1,
            executed_at_ask=0.2,
            bid_standing_depth=4.0,
            ask_standing_depth=2.0,
        )
        expected_bid = 0.5 / (4.0 + 0.4)
        expected_ask = 0.6 / (2.0 + 0.2)
        self.assertAlmostEqual(
            signed_lwi(row, "standing_depth"),
            expected_ask - expected_bid,
        )

    def test_statistics_are_causal_and_source_specific(self):
        minute = 10 * 60 * 60
        bars = [bar(day * 86_400 + minute) for day in range(5)]
        features = {
            bars[0][TS]: feature(0.6, 0.4),
            bars[1][TS]: feature(0.5, 0.5),
            bars[2][TS]: feature(0.4, 0.6),
            # A new source must warm up independently; it cannot inherit the
            # much tighter Databento scale.
            bars[3][TS]: feature(0.4, 0.9, source="bm"),
            bars[4][TS]: feature(0.4, 0.8, source="bm"),
        }
        rows = LiquidityWithdrawal._statistics(bars, features, norm_sessions=2)
        self.assertIsNone(rows[0])
        self.assertIsNone(rows[1])
        self.assertGreater(rows[2][0], 0.0)
        self.assertIsNone(rows[3])
        self.assertIsNone(rows[4])

    def test_signal_direction_and_next_minute_entry(self):
        start = 10 * 60 * 60
        bars = [bar(start + i * 60) for i in range(12)]
        strategy = LiquidityWithdrawal()
        params = strategy.all_params(
            {"norm_sessions": 2, "lwi_z": 2.0, "stop": 25}
        )

        long_rows = [None] * len(bars)
        long_rows[0] = (2.1, 0.5)
        long_signals = strategy.signals(
            bars, {"rows": {(2, "gross"): long_rows}}, "all", params
        )
        self.assertEqual(long_signals[0].index, 1)
        self.assertEqual(long_signals[0].side, LONG)

        short_rows = [None] * len(bars)
        short_rows[0] = (-2.1, 0.5)
        short_signals = strategy.signals(
            bars, {"rows": {(2, "gross"): short_rows}}, "all", params
        )
        self.assertEqual(short_signals[0].side, SHORT)


if __name__ == "__main__":
    unittest.main()
