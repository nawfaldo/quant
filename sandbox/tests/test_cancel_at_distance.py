"""Focused tests for the S5 cancel-at-distance definition."""

import unittest

from sandbox.strategies.cancel_at_distance import (
    Definition,
    qualifies,
    selected_events,
)


def event(timestamp, side, **overrides):
    row = {
        "signal_ts": timestamp,
        "side": side,
        "layer_z": 3.0,
        "cancel_z": 2.0,
        "layer_distance_ticks": 4.0,
        "cancel_distance_ticks": 4.0,
        "layer_volume": 100.0,
        "cancel_volume": 100.0,
    }
    row.update(overrides)
    return row


class CancelAtDistanceTests(unittest.TestCase):
    def test_only_three_coarse_axes_are_free(self):
        definition = Definition()
        self.assertEqual(
            set(definition.grid),
            {"layer_z", "distance_ticks", "cancel_ratio"},
        )
        cells = 1
        for values in definition.grid.values():
            cells *= len(values)
        self.assertEqual(cells, 27)

    def test_fixed_cancel_and_ratio_thresholds(self):
        params = Definition().defaults
        self.assertTrue(qualifies(event(1, "bid"), params))
        self.assertFalse(qualifies(event(1, "bid", cancel_z=1.99), params))
        self.assertFalse(qualifies(event(1, "bid", cancel_volume=99.0), params))

    def test_ambiguous_two_sided_second_is_skipped(self):
        rows = [
            event(1, "bid"),
            event(1, "ask"),
            event(2, "ask"),
        ]
        selected = selected_events(rows, Definition().defaults)
        actual = [(row["signal_ts"], row["side"]) for row in selected]
        self.assertEqual(actual, [(2, "ask")])

    def test_strongest_qualifying_same_side_event_wins(self):
        rows = [
            event(1, "bid", cancel_z=2.1),
            event(1, "bid", cancel_z=4.0),
        ]
        selected = selected_events(rows, Definition().defaults)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["cancel_z"], 4.0)


if __name__ == "__main__":
    unittest.main()
