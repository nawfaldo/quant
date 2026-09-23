"""Causality checks for the derived liquidity-line representation."""
import unittest

from tools.build_nq_l2_features_1s import Event
from sandbox.liquidity_lines import LineAccumulator, LineRow, attach_percentiles, compact, expand


def depth(second, side, price, size, sequence=1):
    return Event(second * 1_000_000_000, 0, sequence, "D", side, price, size)


def trade(second, side, price, size, sequence=1):
    return Event(second * 1_000_000_000, 1, sequence, "T", side, price, size)


class LineAccumulatorTests(unittest.TestCase):
    def test_age_survives_size_updates_and_exact_flows_are_retained(self):
        state = LineAccumulator()
        state.on_event(depth(1, "BID", 100.0, 20.0))
        state.on_event(depth(1, "ASK", 100.25, 10.0))
        state.on_event(trade(2, "SELL", 100.0, 8.0))
        emitted = state.on_event(depth(2, "BID", 100.0, 26.0))
        emitted += state.on_event(depth(3, "ASK", 100.25, 11.0))
        row = expand(compact("dbento", emitted[-1]))
        self.assertEqual(row.bid[0], 100.0)
        self.assertEqual(row.bid[4], 2.0)
        self.assertEqual(row.bid[5], 6.0)
        self.assertEqual(row.bid[7], 8.0)

    def test_percentile_uses_prior_session_only(self):
        def line_row(day, size):
            line = (100.0, size, 1.0, 0.0, 20.0, 0.0, 0.0, 0.0)
            return LineRow(day * 86_400 + 600 * 60, "dbento", 100.0, 100.25,
                           100.125, 100.125, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                           line, line, ((100.0, size),), ((100.0, size),), True)

        ranked = attach_percentiles([line_row(10, 10.0), line_row(11, 20.0)])
        self.assertEqual(ranked[0].bid_pct, 0.0)
        self.assertEqual(ranked[1].bid_pct, 100.0)


if __name__ == "__main__":
    unittest.main()
