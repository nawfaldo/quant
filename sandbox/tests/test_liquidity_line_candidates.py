"""Focused invariants for Candidates C and D."""
import unittest

from sandbox import execution
from sandbox.liquidity_lines import LineRow
from sandbox.strategies.liquidity_line_candidates import (
    LiquidityLineMagnet,
    LiquidityLineReversal,
    resolve_day,
)


def row(second, *, mid=100.0, micro=100.1, top5=0.3, delta=10.0,
        high=100.5, low=99.5, close=100.0, bid=None, ask=None,
        bid_pct=99.0, ask_pct=99.0, bids=None, asks=None):
    bid = bid or (99.0, 100.0, 0.5, 0.0, 30.0, 0.0, 0.0, 0.0)
    ask = ask or (102.0, 100.0, 0.5, 0.0, 30.0, 0.0, 0.0, 0.0)
    bids = bids or ((99.0, bid[1]),)
    asks = asks or ((102.0, ask[1]),)
    return LineRow(
        20_000 * 86_400 + 600 * 60 + second, "dbento", 99.75, 100.25,
        mid, micro, top5, delta, mid, high, low, close,
        bid, ask, bids, asks, True, bid_pct, ask_pct,
    )


class LiquidityLineReversalTests(unittest.TestCase):
    def test_exact_replenished_bid_rejection_enters_next_second(self):
        defended = (99.0, 100.0, 0.5, 3.0, 30.0, 10.0, 2.0, 20.0)
        rows = [
            row(0, delta=-20.0, close=99.5, bid=defended),
            row(1, mid=100.0),
            row(2, mid=100.2),
        ]
        signals = LiquidityLineReversal().signals(rows)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, execution.LONG)
        self.assertEqual(signals[0].entry_index, 1)
        self.assertEqual(signals[0].stop, 2.0)

    def test_missing_exact_price_replenishment_blocks_candidate(self):
        touched = (99.0, 100.0, 0.5, 3.0, 30.0, 4.0, 2.0, 20.0)
        rows = [row(0, delta=-20.0, close=99.5, bid=touched), row(1), row(2)]
        self.assertEqual(LiquidityLineReversal().signals(rows), [])
        self.assertEqual(len(LiquidityLineReversal().signals(rows, mode="wall_touch")), 1)


class LiquidityLineMagnetTests(unittest.TestCase):
    def test_pressure_toward_persistent_ask_enters_next_second(self):
        rows = [row(0),
                row(1, micro=100.0, top5=0.0, delta=0.0,
                    asks=((102.0, 100.0),)),
                row(2, high=102.0)]
        signals = LiquidityLineMagnet().signals(rows)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, execution.LONG)
        fills = resolve_day(rows, signals)
        self.assertAlmostEqual(fills[0].points, 1.55)

    def test_line_pulled_before_entry_is_rejected(self):
        rows = [row(0),
                row(1, micro=100.0, top5=0.0, delta=0.0,
                    asks=((101.0, 20.0),)),
                row(2)]
        self.assertEqual(LiquidityLineMagnet().signals(rows), [])
        self.assertEqual(len(LiquidityLineMagnet().signals(rows, mode="pulled_before_entry")), 1)


if __name__ == "__main__":
    unittest.main()
