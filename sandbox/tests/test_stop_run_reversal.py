"""Focused invariants for S2's sweep-then-fail sequence."""

import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.stop_run_reversal import StopRunReversal


def bar(day, minute, open_, high, low, close, delta):
    row = [0.0] * 7
    row[TS] = day * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_,
        high,
        low,
        close,
        delta,
        1,
    )
    return row


class StopRunReversalTest(unittest.TestCase):
    def setUp(self):
        self.strategy = StopRunReversal()
        previous = [
            bar(10, minute, 100, 105 if minute == 600 else 101,
                95 if minute == 601 else 99, 100, 1)
            for minute in range(570, 750)
        ]
        self.prefix = previous

    def params(self, **overrides):
        params = self.strategy.all_params(
            {
                "penetration": 1.0,
                "rejection_minutes": 3,
                "delta_ratio": 0.25,
                "to_date": None,
            }
        )
        params.update(overrides)
        return params

    def test_upper_sweep_rejects_and_enters_short_next_minute(self):
        current = [
            bar(11, 600, 104, 107, 103, 106, 100),
            bar(11, 601, 106, 106, 103, 104, -30),
            bar(11, 602, 104, 104, 102, 103, -5),
        ]
        bars = self.prefix + current
        signals = self.strategy.signals(bars, None, "all", self.params())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "short")
        self.assertEqual(signals[0].index, len(self.prefix) + 2)

    def test_requires_outward_then_opposite_delta(self):
        current = [
            bar(11, 600, 104, 107, 103, 106, -100),
            bar(11, 601, 106, 106, 103, 104, -30),
            bar(11, 602, 104, 104, 102, 103, -5),
        ]
        bars = self.prefix + current
        signals = self.strategy.signals(bars, None, "all", self.params())
        self.assertEqual(signals, [])

    def test_rejection_after_window_is_not_reused(self):
        current = [
            bar(11, 600, 104, 107, 103, 106, 100),
            bar(11, 601, 106, 107, 105, 106, 10),
            bar(11, 602, 106, 107, 105, 106, 10),
            bar(11, 603, 106, 107, 105, 106, 10),
            bar(11, 604, 106, 106, 103, 104, -100),
            bar(11, 605, 104, 104, 102, 103, -5),
        ]
        bars = self.prefix + current
        signals = self.strategy.signals(
            bars, None, "all", self.params(rejection_minutes=3)
        )
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
