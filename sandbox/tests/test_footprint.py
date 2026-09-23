"""The per-minute footprint reduction in `data._footprint_minute`."""
import unittest

from sandbox import data

TICK = 0.25


def ladder(rows):
    """`{price: [ask_volume, bid_volume]}` from `(price, ask, bid)` triples."""
    return {price: [ask, bid] for price, ask, bid in rows}


class FootprintMinuteTest(unittest.TestCase):
    def test_finished_auction_has_one_empty_side_at_the_extreme(self):
        # Nobody sold back at the high: the auction up there finished.
        summary = data._footprint_minute(
            ladder([(100.00, 5, 5), (100.25, 5, 5), (100.50, 9, 0)]), TICK)
        self.assertFalse(summary["unfinished_high"])

    def test_both_sides_printing_at_the_high_is_unfinished(self):
        summary = data._footprint_minute(
            ladder([(100.00, 5, 5), (100.25, 5, 5), (100.50, 9, 3)]), TICK)
        self.assertTrue(summary["unfinished_high"])

    def test_unfinished_low_is_the_mirror(self):
        finished = data._footprint_minute(
            ladder([(100.00, 0, 9), (100.25, 5, 5)]), TICK)
        self.assertFalse(finished["unfinished_low"])
        unfinished = data._footprint_minute(
            ladder([(100.00, 4, 9), (100.25, 5, 5)]), TICK)
        self.assertTrue(unfinished["unfinished_low"])

    def test_stacked_buy_counts_consecutive_diagonal_imbalances(self):
        # Ask at P beats bid at P-tick threefold on three levels running.
        summary = data._footprint_minute(
            ladder([(100.00, 1, 2),
                    (100.25, 30, 2),
                    (100.50, 30, 2),
                    (100.75, 30, 2)]), TICK)
        self.assertEqual(summary["stacked_buy"], 3)
        self.assertEqual(summary["stacked_sell"], 0)

    def test_stacked_sell_compares_against_the_ask_above(self):
        summary = data._footprint_minute(
            ladder([(100.00, 2, 30),
                    (100.25, 2, 30),
                    (100.50, 2, 30),
                    (100.75, 2, 1)]), TICK)
        self.assertEqual(summary["stacked_sell"], 3)
        self.assertEqual(summary["stacked_buy"], 0)

    def test_a_run_is_broken_by_a_balanced_level(self):
        summary = data._footprint_minute(
            ladder([(100.00, 30, 2),
                    (100.25, 5, 30),     # balanced against the ask above
                    (100.50, 30, 2)]), TICK)
        self.assertEqual(summary["stacked_buy"], 1)

    def test_thin_levels_are_below_the_noise_floor(self):
        # 3 lots against 0 is a 3x ratio but under the minimum volume.
        summary = data._footprint_minute(
            ladder([(100.00, 3, 0), (100.25, 3, 0), (100.50, 3, 0)]), TICK)
        self.assertEqual(summary["stacked_buy"], 0)

    def test_poc_and_extreme_deltas(self):
        summary = data._footprint_minute(
            ladder([(100.00, 1, 4), (100.25, 40, 40), (100.50, 7, 2)]), TICK)
        self.assertEqual(summary["poc"], 100.25)
        self.assertEqual(summary["delta_at_high"], 5)
        self.assertEqual(summary["delta_at_low"], -3)
        self.assertEqual(summary["levels"], 3)

    def test_empty_ladder_has_no_summary(self):
        self.assertIsNone(data._footprint_minute({}, TICK))


if __name__ == "__main__":
    unittest.main()
