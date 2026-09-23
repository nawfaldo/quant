import unittest

from sandbox.research import es_diverse_families_research as strategy


class EsDiverseFamiliesTests(unittest.TestCase):
    def test_candidate_count_covers_all_five_families(self):
        candidates = strategy.candidates()
        self.assertEqual(len(candidates), 1080)
        self.assertEqual({item["family"] for item in candidates}, {
            "open_reversal", "nr_breakout", "failed_ib",
            "prior_rejection", "vwap_reclaim",
        })

    def test_nr7_local_neighbourhood_changes_one_numeric_axis(self):
        params = {
            "family": "nr_breakout", "nr_lookback": 7, "range_bars": 3,
            "buffer_atr": 0.0, "stop_atr": 0.5,
            "exit_mode": "rr_1_5", "side_mode": "both",
        }
        neighbours = strategy.neighbours(params)
        self.assertEqual(len(neighbours), 6)
        for neighbour in neighbours:
            changed = [key for key in params if params[key] != neighbour[key]]
            self.assertEqual(len(changed), 1)
            self.assertNotIn(changed[0], strategy.CATEGORICAL)


if __name__ == "__main__":
    unittest.main()
