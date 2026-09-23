"""Causality, confirmation and invalidation tests for the Fabervaale models."""
import unittest

from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.strategies.fabervaale_orderflow import (
    FabervaaleOrderflow, profile, value_area,
)

DAY = 20_000


def bar(minute, open_, high, low, close, delta=0.0):
    row = [0.0] * 7
    row[TS] = DAY * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, delta, 1,
    )
    return row


def feature(volume=100.0, imbalance=0.3, valid=True):
    return {"aggressive_buy_volume": volume / 2, "aggressive_sell_volume": volume / 2,
            "top5_imbalance": imbalance, "book_valid": valid}


class ValueAreaTest(unittest.TestCase):
    def test_area_brackets_the_busiest_shelf(self):
        rows = [(bar(570, 100, 100.5, 100, 100.5), 900.0),
                (bar(571, 100, 103, 102, 103), 100.0)]
        bins = profile(rows, 1.0)
        # The heavy minute sits entirely in the 100 bin; the light one splits.
        self.assertEqual(max(bins, key=lambda index: bins[index]), 100)
        low, high = value_area(bins, 0.70, 1.0)
        self.assertEqual((low, high), (100.0, 101.0))

    def test_empty_profile_has_no_area(self):
        self.assertIsNone(value_area({}, 0.70, 1.0))


class FabervaaleOrderflowTest(unittest.TestCase):
    def setUp(self):
        self.strategy = FabervaaleOrderflow()
        # A 15-minute opening range of 99..101, with volume built at 100.
        self.opening = [bar(minute, 100, 101, 99, 100, delta=1.0)
                        for minute in range(570, 585)]
        # Same 99..101 extremes, but the volume shelf sits in the 100 bin: two
        # wide minutes set the range and thirteen narrow ones build the value
        # area. `ivb1` reads its invalidation off exactly this difference.
        self.concentrated = (
            [bar(570, 100, 101, 99, 100, delta=1.0),
             bar(571, 100, 101, 99, 100, delta=1.0)]
            + [bar(minute, 100, 100.5, 100, 100, delta=1.0)
               for minute in range(572, 585)]
        )

    def params(self, **overrides):
        merged = self.strategy.all_params({"opening_range": 15, "buffer": 0.0})
        merged.update(overrides)
        return merged

    def run_signals(self, bars, params, features=None, **context):
        if features is None:
            features = {row[TS]: feature() for row in bars}
        return self.strategy.signals(
            bars, {"features": features, **context}, "all", params)

    def broke(self):
        """A session that breaks up and confirms, for gate tests to suppress."""
        return self.opening + [bar(585, 100, 103, 101, 102, delta=50.0),
                               bar(586, 102, 104, 101, 103)]

    # -- ivb2: confirmation ------------------------------------------------

    def test_ivb2_enters_the_minute_after_confirmed_aggression(self):
        # Break closes above 101 and cumulative delta prints a new session high.
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103, delta=5.0)
        bars = self.opening + [decision, entry]
        signals = self.run_signals(bars, self.params())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, len(self.opening) + 1)
        self.assertEqual(signals[0].side, "long")
        self.assertEqual(signals[0].stop, 15.0)
        self.assertEqual(signals[0].target, 30.0)

    def test_ivb2_rejects_a_break_whose_delta_is_not_a_new_extreme(self):
        # Same break, but the minute sells: CVD falls instead of extending.
        decision = bar(585, 100, 103, 101, 102, delta=-50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        self.assertEqual(self.run_signals(bars, self.params()), [])

    def test_ivb2_rejects_a_break_that_does_not_hold_the_level(self):
        # Closes through, but the minute dipped back under the range high.
        decision = bar(585, 100, 103, 98, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        self.assertEqual(self.run_signals(bars, self.params()), [])

    def test_ivb2_confirms_on_a_later_minute_inside_the_window(self):
        # The break itself is absorbed-looking (no new CVD high); the next
        # minute holds above 101 and prints one, so entry is at 587.
        broke = bar(585, 100, 103, 101, 102, delta=-5.0)
        confirm = bar(586, 102, 103, 101.5, 103, delta=60.0)
        entry = bar(587, 103, 105, 102, 104)
        bars = self.opening + [broke, confirm, entry]
        signals = self.run_signals(bars, self.params())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].index, len(self.opening) + 2)

    def test_ivb2_arming_lapses_after_the_confirm_window(self):
        broke = bar(585, 100, 103, 101, 102, delta=-5.0)
        quiet = [bar(minute, 102, 103, 101.5, 102, delta=-1.0)
                 for minute in range(586, 592)]
        late = bar(592, 102, 103, 101.5, 103, delta=60.0)
        entry = bar(593, 103, 105, 102, 104)
        bars = self.opening + [broke] + quiet + [late, entry]
        # `late` would confirm, but it is 7 minutes after a 5-minute window and
        # is not itself a fresh break, so nothing is armed by then.
        self.assertEqual(self.run_signals(bars, self.params(confirm_window=5)), [])

    def test_ivb2_absorption_veto_rejects_heavy_delta_that_went_nowhere(self):
        decision = bar(585, 102, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        allowed = self.run_signals(bars, self.params())
        self.assertEqual(len(allowed), 1)
        vetoed = self.run_signals(
            bars, self.params(absorption_delta=40.0, absorption_move=1.0))
        self.assertEqual(vetoed, [])

    def test_ivb2_honours_the_book_gate(self):
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        against = {row[TS]: feature(imbalance=-0.4) for row in bars}
        self.assertEqual(
            self.run_signals(bars, self.params(require_book=True), against), [])
        self.assertEqual(
            len(self.run_signals(bars, self.params(require_book=True))), 1)

    def test_a_held_breakout_is_one_attempt_not_one_per_minute(self):
        # Price closes above 101 for three straight minutes. That is a single
        # breakout, so raising the cap cannot turn it into three entries.
        broke = bar(585, 100, 103, 101, 102, delta=50.0)
        held = bar(586, 102, 104, 102, 103, delta=60.0)
        still = bar(587, 103, 105, 103, 104, delta=70.0)
        entry = bar(588, 104, 106, 104, 105)
        bars = self.opening + [broke, held, still, entry]
        self.assertEqual(len(self.run_signals(bars, self.params(max_trades=3))), 1)

    def test_max_trades_caps_separate_attempts(self):
        # Break, fall back inside the range, then break again: two attempts.
        broke = bar(585, 100, 103, 101, 102, delta=50.0)
        back_inside = bar(586, 102, 102, 100, 100, delta=-5.0)
        again = bar(587, 100.5, 103, 101, 102, delta=60.0)
        entry = bar(588, 102, 104, 101, 103)
        bars = self.opening + [broke, back_inside, again, entry]
        self.assertEqual(len(self.run_signals(bars, self.params())), 1)
        self.assertEqual(
            len(self.run_signals(bars, self.params(max_trades=2))), 2)

    def test_no_signal_without_a_next_minute_to_fill(self):
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        bars = self.opening + [decision]
        self.assertEqual(self.run_signals(bars, self.params()), [])

    def test_entry_to_closes_the_window(self):
        decision = bar(899, 100, 103, 101, 102, delta=50.0)
        entry = bar(900, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        self.assertEqual(len(self.run_signals(bars, self.params())), 1)
        self.assertEqual(
            self.run_signals(bars, self.params(entry_to=899)), [])

    # -- ivb1: volume-profile invalidation ---------------------------------

    def test_ivb1_stops_at_the_value_area_edge(self):
        # The opening range spans 99..101 but nearly all of its volume traded
        # in the 100 bin, so the value area is [100, 101) and a break closing
        # at 102 is invalidated 2.0 points away — at the shelf, not the low.
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.concentrated + [decision, entry]
        features = {row[TS]: feature() for row in self.concentrated}
        params = self.params(model="ivb1", stop=20.0, min_stop_frac=0.05,
                             value_area=0.70, bin_size=1.0)
        signals = self.run_signals(bars, params, features)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "long")
        self.assertAlmostEqual(signals[0].stop, 2.0)
        self.assertAlmostEqual(signals[0].target, 4.0)

    def test_ivb1_clamps_the_derived_stop(self):
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.concentrated + [decision, entry]
        features = {row[TS]: feature() for row in self.concentrated}
        capped = self.run_signals(
            bars, self.params(model="ivb1", stop=1.0, min_stop_frac=0.5), features)
        self.assertAlmostEqual(capped[0].stop, 1.0)
        floored = self.run_signals(
            bars, self.params(model="ivb1", stop=20.0, min_stop_frac=0.5), features)
        self.assertAlmostEqual(floored[0].stop, 10.0)

    # -- exhaustion: delta divergence --------------------------------------

    def test_exhaustion_fades_a_new_low_that_delta_did_not_confirm(self):
        # Falling price, rising cumulative delta: sellers are exhausted.
        first = bar(585, 100, 100, 98, 98, delta=-40.0)
        diverging = bar(586, 98, 98, 96, 97, delta=30.0)
        entry = bar(587, 97, 99, 96, 98)
        bars = self.opening + [first, diverging, entry]
        params = self.params(model="exhaustion", min_break=0.5)
        signals = self.run_signals(bars, params)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "long")
        self.assertEqual(signals[0].index, len(self.opening) + 2)

    def test_exhaustion_ignores_a_new_low_delta_agrees_with(self):
        first = bar(585, 100, 100, 98, 98, delta=-40.0)
        confirming = bar(586, 98, 98, 96, 97, delta=-30.0)
        entry = bar(587, 97, 99, 96, 98)
        bars = self.opening + [first, confirming, entry]
        params = self.params(model="exhaustion", min_break=0.5)
        self.assertEqual(self.run_signals(bars, params), [])

    # -- shared framing ----------------------------------------------------

    def test_opening_range_minutes_never_trade(self):
        # A break of the *forming* range is not a break; nothing fires inside it.
        bars = [bar(minute, 100, 100 + minute - 569, 99, 100 + minute - 569,
                    delta=10.0) for minute in range(570, 586)]
        self.assertEqual(self.run_signals(bars, self.params()), [])

    def test_sessions_are_independent(self):
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        first = self.opening + [decision, entry]
        second = [list(row) for row in first]
        for row in second:
            row[TS] += 86_400
        signals = self.run_signals(first + second, self.params())
        self.assertEqual(len(signals), 2)

    # -- optimizer axes ----------------------------------------------------

    def test_entry_from_delays_the_first_allowed_entry(self):
        bars = self.broke()
        self.assertEqual(len(self.run_signals(bars, self.params())), 1)
        # The fill would land on minute 586, so a 09:47 floor rejects it.
        self.assertEqual(
            self.run_signals(bars, self.params(entry_from=587)), [])

    def test_skip_days_drops_a_weekday(self):
        bars = self.broke()
        # The epoch began on a Thursday, so day 20000 is a Friday.
        self.assertEqual(len(self.run_signals(bars, self.params())), 1)
        self.assertEqual(self.run_signals(bars, self.params(skip_days="fri")), [])
        self.assertEqual(len(self.run_signals(bars, self.params(skip_days="mon,wed"))), 1)

    def test_vix_band_gates_the_session(self):
        bars = self.broke()
        vix = [18.0] * len(bars)
        self.assertEqual(
            len(self.run_signals(bars, self.params(vix_min=15.0), vix=vix)), 1)
        self.assertEqual(
            self.run_signals(bars, self.params(vix_min=20.0), vix=vix), [])
        self.assertEqual(
            self.run_signals(bars, self.params(vix_max=17.0), vix=vix), [])

    def test_atr_band_gates_the_session_and_absence_is_not_zero(self):
        bars = self.broke()
        atr = {20_000: 50.0}
        self.assertEqual(
            len(self.run_signals(bars, self.params(atr_min=40.0), atr=atr)), 1)
        self.assertEqual(
            self.run_signals(bars, self.params(atr_min=60.0), atr=atr), [])
        # No reading at all must be skipped, not treated as the calmest day.
        self.assertEqual(
            self.run_signals(bars, self.params(atr_min=40.0), atr={}), [])

    def test_trend_gate_requires_the_side_to_agree(self):
        bars = self.broke()
        closes = [(20_000 - n, 90.0) for n in range(1, 6)]
        rising = self.run_signals(
            bars, self.params(trend_days=5), closes=closes)
        self.assertEqual(len(rising), 1)   # opens at 100, above the 90 average
        above = [(20_000 - n, 150.0) for n in range(1, 6)]
        self.assertEqual(
            self.run_signals(bars, self.params(trend_days=5), closes=above), [])

    def test_stop_atr_scales_the_bracket_with_volatility(self):
        bars = self.broke()
        atr = {20_000: 60.0}
        signals = self.run_signals(
            bars, self.params(stop_atr=0.25, rr=2.0), atr=atr)
        self.assertAlmostEqual(signals[0].stop, 15.0)
        self.assertAlmostEqual(signals[0].target, 30.0)
        # A day with no ATR reading cannot size a bracket, so it does not trade.
        self.assertEqual(
            self.run_signals(bars, self.params(stop_atr=0.25), atr={}), [])

    def test_trail_frac_attaches_a_trailing_leg(self):
        bars = self.broke()
        plain = self.run_signals(bars, self.params())
        self.assertIsNone(plain[0].trail)
        trailing = self.run_signals(bars, self.params(stop=15.0, trail_frac=0.5))
        self.assertAlmostEqual(trailing[0].trail, 7.5)

    def test_trailing_only_exit_is_a_valid_configuration(self):
        bars = self.broke()
        signals = self.run_signals(bars, self.params(rr=0.0, trail_frac=1.0))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].target, 0.0)
        # But neither a target nor a trail leaves nothing to exit on.
        self.assertEqual(
            self.run_signals(bars, self.params(rr=0.0, trail_frac=0.0)), [])

    def test_invalid_params_produce_nothing(self):
        decision = bar(585, 100, 103, 101, 102, delta=50.0)
        entry = bar(586, 102, 104, 101, 103)
        bars = self.opening + [decision, entry]
        self.assertEqual(self.run_signals(bars, self.params(model="nope")), [])
        self.assertEqual(self.run_signals(bars, self.params(rr=0.0)), [])


if __name__ == "__main__":
    unittest.main()
