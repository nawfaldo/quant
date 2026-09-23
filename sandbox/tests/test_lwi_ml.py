"""Focused invariants for the leakage-safe LWI ML experiment."""
import unittest

from sandbox import execution
from sandbox.research.lwi_ml import Candidate
from sandbox.research.lwi_ml import apply_occupancy, lwi_sides, model_features


def fill(entry_ts, exit_ts, points=1.0):
    return execution.Fill(entry_ts, exit_ts, execution.LONG, points, 20_000.0, 25)


def candidate(signal_index, exit_index):
    return Candidate(signal_index, exit_index, fill(signal_index, exit_index), (0.0,))


class LwiMlTests(unittest.TestCase):
    def test_lwi_sides_requires_both_denominators(self):
        feature = {
            "bid_add_volume": 2.0,
            "bid_cancel_volume": 2.0,
            "ask_add_volume": 1.0,
            "ask_cancel_volume": 3.0,
        }
        self.assertEqual(lwi_sides(feature), (0.5, 0.75))
        feature["ask_add_volume"] = 0.0
        feature["ask_cancel_volume"] = 0.0
        self.assertIsNone(lwi_sides(feature))

    def test_rejected_candidate_does_not_consume_occupancy(self):
        rows = [candidate(1, 5), candidate(2, 3), candidate(4, 6)]
        accepted = apply_occupancy(rows, scores=[0.4, 0.8, 0.8])
        self.assertEqual([row.signal_index for row in accepted], [2, 4])

    def test_features_are_finite_and_pre_entry(self):
        bar = [10 * 60 * 60, 20_000.0, 20_001.0, 19_999.0, 20_000.0, 0, 0]
        feature = {
            "bid_add_volume": 40.0,
            "bid_cancel_volume": 60.0,
            "ask_add_volume": 20.0,
            "ask_cancel_volume": 80.0,
            "aggressive_buy_volume": 100.0,
            "aggressive_sell_volume": 50.0,
            "trade_delta": 50.0,
            "top1_imbalance": 0.1,
            "top5_imbalance": 0.2,
            "top10_imbalance": 0.3,
            "microprice": 20_000.25,
            "midprice": 20_000.0,
            "price_change": 1.0,
            "replenishment_score": 0.2,
            "trade_count": 10.0,
            "depth_event_count": 100.0,
        }
        values = model_features(
            bar, feature, (2.5, 0.5), execution.LONG, 585, 930
        )
        self.assertEqual(len(values), 14)
        self.assertTrue(all(value == value for value in values))


if __name__ == "__main__":
    unittest.main()
