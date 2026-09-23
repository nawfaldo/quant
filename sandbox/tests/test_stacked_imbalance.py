"""Causality and trigger tests for the stacked-imbalance continuation."""
import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.stacked_imbalance import StackedImbalance

DAY = 20_000


def bar(minute, open_, high, low, close, delta=0.0):
    row = [0.0] * 7
    row[TS] = DAY * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, delta, 1,
    )
    return row


def printed(buy=0, sell=0):
    return {"stacked_buy": buy, "stacked_sell": sell, "unfinished_high": False,
            "unfinished_low": False, "poc": 100.0, "delta_at_high": 0.0,
            "delta_at_low": 0.0, "levels": 8}


class StackedImbalanceTest(unittest.TestCase):
    def setUp(self):
        self.strategy = StackedImbalance()

    def params(self, **overrides):
        merged = self.strategy.all_params()
        merged.update(overrides)
        return merged

    def run_signals(self, bars, footprint, params):
        return self.strategy.signals(bars, {"footprint": footprint}, "all", params)

    def test_three_stacked_buys_enter_long_on_the_next_minute(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        entry = bar(601, 105, 107, 104, 106)
        bars = [signal_bar, entry]
        signals = self.run_signals(bars, {signal_bar[TS]: printed(buy=3)},
                                   self.params())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, 1)
        self.assertEqual(signals[0].side, "long")

    def test_a_shorter_run_does_not_trigger(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        bars = [signal_bar, bar(601, 105, 107, 104, 106)]
        self.assertEqual(
            self.run_signals(bars, {signal_bar[TS]: printed(buy=2)}, self.params()), [])
        self.assertEqual(
            len(self.run_signals(bars, {signal_bar[TS]: printed(buy=2)},
                                 self.params(min_stack=2))), 1)

    def test_stacks_on_both_sides_are_a_fight_not_a_signal(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        bars = [signal_bar, bar(601, 105, 107, 104, 106)]
        self.assertEqual(
            self.run_signals(bars, {signal_bar[TS]: printed(buy=4, sell=4)},
                             self.params()), [])

    def test_delta_must_agree_when_required(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=-40.0)
        bars = [signal_bar, bar(601, 105, 107, 104, 106)]
        footprint = {signal_bar[TS]: printed(buy=3)}
        self.assertEqual(self.run_signals(bars, footprint, self.params()), [])
        self.assertEqual(
            len(self.run_signals(bars, footprint,
                                 self.params(require_delta=False))), 1)

    def test_stop_sits_beyond_the_stacking_bar_low(self):
        # Close 105, low 99, buffer 1.0 -> 7.0 points of risk.
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        bars = [signal_bar, bar(601, 105, 107, 104, 106)]
        signals = self.run_signals(bars, {signal_bar[TS]: printed(buy=3)},
                                   self.params(stop=20.0, rr=2.0))
        self.assertAlmostEqual(signals[0].stop, 7.0)
        self.assertAlmostEqual(signals[0].target, 14.0)

    def test_the_extreme_stop_is_capped_and_floored(self):
        wide = bar(600, 100, 106, 50, 105, delta=40.0)
        bars = [wide, bar(601, 105, 107, 104, 106)]
        capped = self.run_signals(bars, {wide[TS]: printed(buy=3)},
                                  self.params(stop=20.0))
        self.assertAlmostEqual(capped[0].stop, 20.0)

        narrow = bar(600, 100, 106, 104.9, 105, delta=40.0)
        bars = [narrow, bar(601, 105, 107, 104, 106)]
        floored = self.run_signals(bars, {narrow[TS]: printed(buy=3)},
                                   self.params(min_stop=4.0))
        self.assertAlmostEqual(floored[0].stop, 4.0)

    def test_short_side_mirrors(self):
        signal_bar = bar(600, 105, 106, 99, 100, delta=-40.0)
        bars = [signal_bar, bar(601, 100, 101, 98, 99)]
        signals = self.run_signals(bars, {signal_bar[TS]: printed(sell=3)},
                                   self.params(stop=20.0))
        self.assertEqual(signals[0].side, "short")
        self.assertAlmostEqual(signals[0].stop, 7.0)

    def test_minutes_without_a_footprint_row_are_skipped(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        bars = [signal_bar, bar(601, 105, 107, 104, 106)]
        self.assertEqual(self.run_signals(bars, {}, self.params()), [])

    def test_max_trades_caps_the_session_and_resets_the_next_day(self):
        bars, footprint = [], {}
        for minute in range(600, 610):
            row = bar(minute, 100, 106, 99, 105, delta=40.0)
            bars.append(row)
            footprint[row[TS]] = printed(buy=3)
        self.assertEqual(len(self.run_signals(bars, footprint, self.params())), 3)

        second = []
        for row in bars:
            copy = list(row)
            copy[TS] += 86_400
            second.append(copy)
            footprint[copy[TS]] = printed(buy=3)
        self.assertEqual(
            len(self.run_signals(bars + second, footprint, self.params())), 6)

    def test_no_entry_without_a_next_minute_in_session(self):
        signal_bar = bar(600, 100, 106, 99, 105, delta=40.0)
        self.assertEqual(
            self.run_signals([signal_bar], {signal_bar[TS]: printed(buy=3)},
                             self.params()), [])


if __name__ == "__main__":
    unittest.main()
