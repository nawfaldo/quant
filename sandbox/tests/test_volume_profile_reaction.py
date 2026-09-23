"""Focused tests for S1 value-area first-touch construction."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.volume_profile_reaction import (
    first_touches,
    fixed_horizon_observations,
    prior_profiles_for_bars,
)


def bar(ts, open_, high, low, close):
    return [ts, open_, high, low, close, 0.0, 0]


class VolumeProfileReactionTests(unittest.TestCase):
    def test_prior_profile_must_be_immediately_previous_session(self):
        day = 86_400
        bars = [
            bar(day + 570 * 60, 100, 101, 99, 100),
            bar(2 * day + 570 * 60, 100, 101, 99, 100),
            bar(3 * day + 570 * 60, 100, 101, 99, 100),
        ]
        profile = (100.0, 105.0, 95.0, 110.0, 90.0)
        prior = prior_profiles_for_bars(bars, {1: profile})
        self.assertEqual(prior, {2: profile})

    def test_next_minute_entry_and_fade_direction(self):
        day = 2 * 86_400
        bars = [
            bar(day + 570 * 60, 100, 104, 99, 104),
            bar(day + 571 * 60, 104, 105, 103, 104),
            bar(day + 572 * 60, 104, 104, 103, 103),
            bar(day + 573 * 60, 103, 104, 95, 96),
            bar(day + 574 * 60, 96, 97, 94, 95),
            bar(day + 575 * 60, 95, 96, 94, 95),
        ]
        prior = {2: (100.0, 105.0, 95.0, 110.0, 90.0)}
        touches = first_touches(bars, prior)
        self.assertEqual([row.side for row in touches], [SHORT, LONG])
        self.assertEqual([row.entry_index for row in touches], [2, 4])

    def test_overlapping_horizon_keeps_only_first_touch(self):
        day = 2 * 86_400
        bars = [
            bar(day + 570 * 60, 100, 105, 99, 104),
            bar(day + 571 * 60, 104, 104, 103, 103),
            bar(day + 572 * 60, 103, 104, 95, 96),
            bar(day + 573 * 60, 96, 97, 94, 95),
            bar(day + 574 * 60, 95, 96, 94, 95),
        ]
        prior = {2: (100.0, 105.0, 95.0, 110.0, 90.0)}
        touches = first_touches(bars, prior)
        rows = fixed_horizon_observations(
            bars, touches, horizon=2, spread=0.2
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].touch.side, SHORT)
        self.assertAlmostEqual(rows[0].fill.points, 7.8)

    def test_ambiguous_bar_spanning_both_edges_is_discarded(self):
        day = 2 * 86_400
        bars = [
            bar(day + 570 * 60, 100, 106, 94, 100),
            bar(day + 571 * 60, 100, 101, 99, 100),
        ]
        prior = {2: (100.0, 105.0, 95.0, 110.0, 90.0)}
        self.assertEqual(first_touches(bars, prior), [])


if __name__ == "__main__":
    unittest.main()
