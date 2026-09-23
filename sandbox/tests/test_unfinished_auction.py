"""Magnet, causality and level-lifecycle tests for the unfinished auction."""
import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.unfinished_auction import UnfinishedAuction

DAY = 20_000


def bar(minute, open_, high, low, close):
    row = [0.0] * 7
    row[TS] = DAY * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, 0.0, 1,
    )
    return row


def printed(high=False, low=False):
    return {"unfinished_high": high, "unfinished_low": low, "stacked_buy": 0,
            "stacked_sell": 0, "poc": 100.0, "delta_at_high": 0.0,
            "delta_at_low": 0.0, "levels": 8}


class UnfinishedAuctionTest(unittest.TestCase):
    def setUp(self):
        self.strategy = UnfinishedAuction()

    def params(self, **overrides):
        merged = self.strategy.all_params({"warmup": 0})
        merged.update(overrides)
        return merged

    def run_signals(self, bars, footprint, params):
        return self.strategy.signals(bars, {"footprint": footprint}, "all", params)

    def test_price_leaving_an_unfinished_high_arms_a_long_back_to_it(self):
        peak = bar(600, 100, 120, 100, 119)         # unfinished at 120
        away = bar(601, 119, 119, 105, 105)         # 15 points below the level
        entry = bar(602, 105, 110, 104, 108)
        bars = [peak, away, entry]
        signals = self.run_signals(
            bars, {peak[TS]: printed(high=True)},
            self.params(min_distance=10.0, max_distance=60.0, stop=15.0))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, 2)
        self.assertEqual(signals[0].side, "long")
        # The target is the distance to the level, not a multiple of the stop.
        self.assertAlmostEqual(signals[0].target, 15.0)
        self.assertAlmostEqual(signals[0].stop, 15.0)

    def test_a_finished_extreme_leaves_no_level(self):
        peak = bar(600, 100, 120, 100, 119)
        away = bar(601, 119, 119, 105, 105)
        entry = bar(602, 105, 110, 104, 108)
        bars = [peak, away, entry]
        self.assertEqual(
            self.run_signals(bars, {peak[TS]: printed(high=False)}, self.params()), [])

    def test_price_must_travel_the_minimum_distance(self):
        peak = bar(600, 100, 120, 100, 119)
        near = bar(601, 119, 119, 117, 118)   # only 2 points away
        entry = bar(602, 118, 119, 117, 118)
        bars = [peak, near, entry]
        self.assertEqual(
            self.run_signals(bars, {peak[TS]: printed(high=True)},
                             self.params(min_distance=10.0)), [])

    def test_a_stale_level_beyond_max_distance_is_not_a_magnet(self):
        peak = bar(600, 100, 120, 100, 119)
        far = bar(601, 119, 119, 20, 20)      # 100 points away
        entry = bar(602, 20, 25, 19, 22)
        bars = [peak, far, entry]
        self.assertEqual(
            self.run_signals(bars, {peak[TS]: printed(high=True)},
                             self.params(max_distance=60.0)), [])

    def test_a_level_is_consumed_once_price_returns_to_it(self):
        peak = bar(600, 100, 120, 100, 119)
        away = bar(601, 119, 119, 105, 105)
        back = bar(602, 105, 121, 105, 120)   # trades through the level
        later = bar(603, 120, 120, 105, 105)  # away again
        entry = bar(604, 105, 110, 104, 108)
        bars = [peak, away, back, later, entry]
        signals = self.run_signals(
            bars, {peak[TS]: printed(high=True)}, self.params(min_distance=10.0))
        # Only the first departure trades; after the revisit the level is gone.
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, 2)

    def test_unfinished_low_arms_a_short(self):
        trough = bar(600, 120, 120, 100, 101)
        away = bar(601, 101, 118, 101, 118)
        entry = bar(602, 118, 119, 117, 118)
        bars = [trough, away, entry]
        signals = self.run_signals(
            bars, {trough[TS]: printed(low=True)},
            self.params(min_distance=10.0, max_distance=60.0))
        self.assertEqual(signals[0].side, "short")
        self.assertAlmostEqual(signals[0].target, 18.0)

    def test_only_new_extremes_register_a_level(self):
        peak = bar(600, 100, 120, 100, 119)          # unfinished, but...
        inside = bar(601, 119, 115, 110, 112)        # ...this is not a new high
        away = bar(602, 112, 112, 105, 105)
        entry = bar(603, 105, 110, 104, 108)
        bars = [peak, inside, away, entry]
        footprint = {inside[TS]: printed(high=True)}
        self.assertEqual(
            self.run_signals(bars, footprint, self.params(min_distance=5.0)), [])

    def test_warmup_suppresses_the_opening_minutes(self):
        peak = bar(570, 100, 120, 100, 119)
        away = bar(571, 119, 119, 105, 105)
        entry = bar(572, 105, 110, 104, 108)
        bars = [peak, away, entry]
        footprint = {peak[TS]: printed(high=True)}
        self.assertEqual(
            self.run_signals(bars, footprint, self.params(warmup=15)), [])
        self.assertEqual(
            len(self.run_signals(bars, footprint, self.params(warmup=0))), 1)

    def test_sessions_reset_their_levels(self):
        peak = bar(600, 100, 120, 100, 119)
        away = bar(601, 119, 119, 105, 105)
        bars = [peak, away]
        second = []
        for row in bars:
            copy = list(row)
            copy[TS] += 86_400
            second.append(copy)
        # The next day's first bar cannot trade a level from the day before.
        signals = self.run_signals(
            bars + second, {peak[TS]: printed(high=True)},
            self.params(min_distance=10.0))
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
