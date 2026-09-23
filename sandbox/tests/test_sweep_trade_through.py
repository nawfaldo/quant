"""Focused tests for S4 raw-burst-to-minute signal construction."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.sweep_trade_through import event_rows


def bar(ts, price=100.0):
    return [ts, price, price + 1, price - 1, price, 0.0, 0]


def burst(
    end_ts,
    side=1,
    levels=12,
    volume=50,
    trades=8,
    start_offset_ms=10,
):
    end_ns = end_ts * 1_000_000_000
    start_ns = end_ns - start_offset_ms * 1_000_000
    return [
        start_ns,
        end_ns,
        side,
        levels,
        volume,
        trades,
        100.0,
        103.0,
        99.75,
        102.75,
        100.0,
        103.0,
    ]


class SweepTradeThroughTests(unittest.TestCase):
    def test_event_is_only_known_at_next_minute_open(self):
        day = 10 * 86_400
        bars = [bar(day + minute * 60) for minute in (570, 571, 572)]
        rows = event_rows(
            bars,
            [burst(day + 570 * 60 + 59)],
            levels=12,
            volume=50,
            entry_from=570,
            entry_to=914,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["index"], 1)
        self.assertEqual(rows[0]["entry_ts"], day + 571 * 60)
        self.assertEqual(rows[0]["side"], LONG)

    def test_thresholds_are_inclusive_and_direction_is_preserved(self):
        day = 10 * 86_400
        bars = [bar(day + minute * 60) for minute in (570, 571)]
        rows = event_rows(
            bars,
            [burst(day + 570 * 60 + 10, side=-1)],
            levels=12,
            volume=50,
            entry_from=570,
            entry_to=914,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], SHORT)

    def test_strongest_displacement_wins_when_minute_collides(self):
        day = 10 * 86_400
        bars = [bar(day + minute * 60) for minute in (570, 571)]
        weak = burst(day + 570 * 60 + 10, levels=12, volume=100)
        strong = burst(day + 570 * 60 + 20, side=-1, levels=16, volume=50)
        rows = event_rows(
            bars,
            [weak, strong],
            levels=8,
            volume=25,
            entry_from=570,
            entry_to=914,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], SHORT)
        self.assertEqual(rows[0]["levels"], 16)

    def test_missing_contiguous_next_minute_is_not_backfilled(self):
        day = 10 * 86_400
        bars = [bar(day + 570 * 60), bar(day + 572 * 60)]
        rows = event_rows(
            bars,
            [burst(day + 570 * 60 + 59)],
            levels=12,
            volume=50,
            entry_from=570,
            entry_to=914,
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
