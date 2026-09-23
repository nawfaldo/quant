"""Zone construction, causality and entry tests for the pullback setup."""
import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.volume_accumulation import VolumeAccumulation

DAY = 20_000


def bar(minute, open_, high, low, close, delta=10.0):
    row = [0.0] * 7
    row[TS] = DAY * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, delta, 1,
    )
    return row


class VolumeAccumulationTest(unittest.TestCase):
    def setUp(self):
        self.strategy = VolumeAccumulation()

    def params(self, **overrides):
        merged = self.strategy.all_params({
            "lookback": 10, "gap": 2, "zone_share": 0.6, "bin_size": 1.0,
            # The synthetic zones here are one bin wide; the production floor
            # of 4.0 points would swallow the width arithmetic under test.
            "min_stop": 1.0,
        })
        merged.update(overrides)
        return merged

    def run_signals(self, bars, params):
        return self.strategy.signals(bars, {"features": {}}, "all", params)

    def zone_bars(self):
        """Ten minutes accumulating in 100..101, so the zone is [100, 101)."""
        return [bar(600 + n, 100, 100.5, 100, 100.5) for n in range(10)]

    def test_pullback_to_the_upper_edge_after_a_departure_goes_long(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109),   # the departure
                 bar(611, 109, 112, 108, 111)]       # gap minutes
        bars += [bar(612, 111, 111, 100.5, 108),     # pullback touches the edge
                 bar(613, 108, 112, 107, 110)]
        signals = self.run_signals(bars, self.params(min_departure=5.0))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "long")
        self.assertEqual(signals[0].index, len(bars) - 1)

    def test_the_stop_is_the_zone_width_plus_the_buffer(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        bars += [bar(612, 111, 111, 100.5, 108), bar(613, 108, 112, 107, 110)]
        signals = self.run_signals(
            bars, self.params(min_departure=5.0, stop_buffer=2.0, rr=2.0))
        # Zone is one bin wide (1.0) plus the 2.0 buffer.
        self.assertAlmostEqual(signals[0].stop, 3.0)
        self.assertAlmostEqual(signals[0].target, 6.0)

    def test_no_entry_without_a_departure(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 101, 100, 100.5), bar(611, 100.5, 101, 100, 100.5)]
        bars += [bar(612, 100.5, 101, 100, 100.5), bar(613, 100.5, 101, 100, 100.5)]
        self.assertEqual(self.run_signals(bars, self.params(min_departure=5.0)), [])

    def test_a_close_back_inside_the_zone_is_acceptance_not_a_pullback(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        # Closes at 100.2, inside the zone, so the departure condition fails.
        bars += [bar(612, 111, 111, 100, 100.2), bar(613, 100, 102, 99, 101)]
        self.assertEqual(self.run_signals(bars, self.params(min_departure=5.0)), [])

    def test_price_that_never_returns_to_the_edge_does_not_trade(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        bars += [bar(612, 111, 113, 110, 112), bar(613, 112, 114, 111, 113)]
        self.assertEqual(self.run_signals(bars, self.params(min_departure=5.0)), [])

    def test_short_side_mirrors(self):
        bars = self.zone_bars()
        bars += [bar(610, 100, 100, 90, 91), bar(611, 91, 92, 88, 89)]
        bars += [bar(612, 89, 100, 89, 92), bar(613, 92, 93, 90, 91)]
        signals = self.run_signals(bars, self.params(min_departure=5.0))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "short")

    def test_the_window_must_be_complete_before_anything_trades(self):
        # Only five minutes of history against a ten-minute lookback.
        bars = [bar(600 + n, 100, 100.5, 100, 100.5) for n in range(5)]
        bars += [bar(605, 100.5, 110, 100.5, 109), bar(606, 109, 110, 100.5, 108),
                 bar(607, 108, 112, 107, 110)]
        self.assertEqual(self.run_signals(bars, self.params()), [])

    def test_the_gap_keeps_the_traded_move_out_of_its_own_zone(self):
        # Ten quiet minutes at 100, then two very heavy minutes up at 108.
        bars = self.zone_bars()
        bars += [bar(610, 108, 109, 108, 108.5, delta=500.0),
                 bar(611, 108, 109, 108, 108.5, delta=500.0)]
        bars += [bar(612, 108.5, 111, 100.5, 108), bar(613, 108, 112, 107, 110)]

        # With the gap, the zone is the 100 shelf and the pullback to it trades.
        with_gap = self.run_signals(bars, self.params(gap=2, min_departure=5.0))
        self.assertEqual(len(with_gap), 1)
        self.assertEqual(with_gap[0].side, "long")

        # Without it, the heavy minutes the trade is reacting to dominate the
        # profile, the zone relocates to 108, and there is no departure left.
        self.assertEqual(
            self.run_signals(bars, self.params(gap=0, min_departure=5.0)), [])

    def test_a_narrow_zone_is_widened_to_the_noise_floor(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        bars += [bar(612, 111, 111, 100.5, 108), bar(613, 108, 112, 107, 110)]
        signals = self.run_signals(
            bars, self.params(min_departure=5.0, min_stop=8.0, max_stop=40.0))
        self.assertAlmostEqual(signals[0].stop, 8.0)

    def test_max_stop_rejects_a_zone_too_wide_to_trade(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        bars += [bar(612, 111, 111, 100.5, 108), bar(613, 108, 112, 107, 110)]
        self.assertEqual(
            self.run_signals(bars, self.params(min_departure=5.0, min_stop=1.0,
                                               max_stop=2.0)), [])

    def test_sessions_are_independent(self):
        bars = self.zone_bars()
        bars += [bar(610, 100.5, 110, 100.5, 109), bar(611, 109, 112, 108, 111)]
        bars += [bar(612, 111, 111, 100.5, 108), bar(613, 108, 112, 107, 110)]
        second = []
        for row in bars:
            copy = list(row)
            copy[TS] += 86_400
            second.append(copy)
        self.assertEqual(
            len(self.run_signals(bars + second, self.params(min_departure=5.0))), 2)


if __name__ == "__main__":
    unittest.main()
