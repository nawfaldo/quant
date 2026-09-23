"""Focused tests for the Creamer Context/Location/Confirmation port."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies import creamer_orderflow as co
from sandbox.strategies.creamer_orderflow import CreamerOrderflow, fib_levels

DAY = 20_000
MINUTE = 600                    # 10:00, inside the default 09:30-11:00 window
START = DAY * 86_400 + MINUTE * 60


def bar(ts, open_, high, low, close):
    return [ts, open_, high, low, close, 0.0, 0]


def row(overrides=None):
    """A base row that passes every gate at the published defaults.

    Overrides are keyed by the `R_*` field positions, which are integers and so
    cannot be keyword arguments.
    """
    values = {
        co.R_ATR: 20.0,
        co.R_VOL5: 1_000.0,
        co.R_VOL_REF: 500.0,
        co.R_SPREAD: 0.5,
        co.R_REGIME: 1.2,
        co.R_PRIOR_VAL: 19_900.0,
        co.R_PRIOR_VAH: 20_000.0,
        co.R_PRIOR_POC: 19_950.0,
        co.R_DEV_VAL: 20_020.0,
        co.R_DEV_VAH: 20_090.0,
        co.R_DEV_POC: 20_050.0,
        co.R_LEG_SIDE: "up",
        co.R_LEG_FROM: 20_000.0,
        co.R_LEG_TO: 20_100.0,
        co.R_DELTA: 50.0,
    }
    values.update(overrides or {})
    return tuple(values[key] for key in sorted(values))


def scenario(row_overrides=None):
    """Twelve contiguous bars whose bar 10 is a textbook long setup.

    The leg runs 20,000 -> 20,100, so 0.705 sits at 20,029.5 and the 0.886
    invalidation at 20,011.4. Bar 10 tags the zone, closes above the
    invalidation and closes up, and bar 9 carries the absorption.
    """
    bars = [bar(START + i * 60, 20_030.0, 20_040.0, 20_025.0, 20_035.0)
            for i in range(12)]
    bars[11] = bar(START + 11 * 60, 20_036.0, 20_050.0, 20_030.0, 20_045.0)
    base = [None] * 12
    base[10] = row(row_overrides)
    aggression = [None] * 12
    aggression[9] = (-2.0, 1.0)     # sellers hit hard, price did not move
    aggression[10] = (0.0, 0.0)
    return bars, {"base": base, "aggression": {3: aggression}}


class FibLevelTests(unittest.TestCase):
    def test_up_leg_retraces_downward(self):
        entry, invalidation = fib_levels(20_000.0, 20_100.0)
        self.assertAlmostEqual(entry, 20_029.5)
        self.assertAlmostEqual(invalidation, 20_011.4)

    def test_down_leg_retraces_upward(self):
        entry, invalidation = fib_levels(20_100.0, 20_000.0)
        self.assertAlmostEqual(entry, 20_070.5)
        self.assertAlmostEqual(invalidation, 20_088.6)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.params = CreamerOrderflow().all_params()

    def test_location_readings_are_opposites(self):
        # 20,029.5 is above the prior VAH (20,000) and below the developing VAL
        # (20,020)... which is impossible, so the two readings must disagree on
        # any single level. Two levels, one each way.
        above = 20_029.5
        holds = dict(self.params, location="holds_prior")
        below = dict(self.params, location="below_developing")
        self.assertTrue(CreamerOrderflow._location(row(), above, 1, holds))
        self.assertFalse(CreamerOrderflow._location(row(), above, 1, below))
        self.assertTrue(CreamerOrderflow._location(row(), 19_990.0, 1, below))
        self.assertFalse(CreamerOrderflow._location(row(), 19_990.0, 1, holds))
        self.assertTrue(CreamerOrderflow._location(
            row(), above, 1, dict(self.params, location="none")))

    def test_flip_modes_read_delta_and_candle_separately(self):
        up = bar(START, 10.0, 12.0, 9.0, 11.0)
        down = bar(START, 11.0, 12.0, 9.0, 10.0)
        buying, selling = row(), row({co.R_DELTA: -50.0})
        cases = {
            ("delta", True): (buying, down),
            ("candle", True): (selling, up),
            ("both", False): (selling, up),
            ("none", True): (selling, down),
        }
        for (mode, expected), (state, candle) in cases.items():
            self.assertEqual(
                CreamerOrderflow._flip(state, candle, 1,
                                       dict(self.params, flip=mode)),
                expected, mode)

    def test_participation_needs_a_reference_before_it_can_pass(self):
        params = dict(self.params, participation_mult=1.0)
        self.assertTrue(CreamerOrderflow._participation(row(), params))
        self.assertFalse(CreamerOrderflow._participation(
            row({co.R_VOL_REF: None}), params))
        self.assertFalse(CreamerOrderflow._participation(
            row({co.R_VOL5: 400.0}), params))
        # A raw contract floor is independent of the relative gate.
        self.assertFalse(CreamerOrderflow._participation(
            row(), dict(params, participation_mult=0, participation_min=2_000)))

    def test_environment_reads_developing_poc_against_prior_value(self):
        # dev POC 20,050 is above the prior VAH 20,000 -- value up, so longs
        # pass and shorts do not.
        va = dict(self.params, structure="va")
        self.assertTrue(CreamerOrderflow._environment(row(), 1, va))
        self.assertFalse(CreamerOrderflow._environment(row(), -1, va))
        self.assertTrue(CreamerOrderflow._environment(
            row(), -1, dict(self.params, structure="none")))

    def test_regime_gate_splits_on_the_trailing_median(self):
        calm, fast = row({co.R_REGIME: 0.8}), row({co.R_REGIME: 1.4})
        amplified = dict(self.params, regime="amplified")
        dampened = dict(self.params, regime="dampened")
        self.assertTrue(CreamerOrderflow._environment(fast, 1, amplified))
        self.assertFalse(CreamerOrderflow._environment(calm, 1, amplified))
        self.assertTrue(CreamerOrderflow._environment(calm, 1, dampened))
        # A session with no regime reading cannot satisfy a regime filter.
        self.assertFalse(CreamerOrderflow._environment(
            row({co.R_REGIME: None}), 1, amplified))

    def test_target_modes_fall_back_when_the_level_is_behind_entry(self):
        params = dict(self.params, rr=1.5)
        self.assertAlmostEqual(
            CreamerOrderflow._target(row(), 20_036.0, 1, 20.0,
                                     dict(params, target_mode="rr")), 30.0)
        # developing POC 20,050 is 14 points above a 20,036 entry
        self.assertAlmostEqual(
            CreamerOrderflow._target(row(), 20_036.0, 1, 20.0,
                                     dict(params, target_mode="poc")), 14.0)
        # ...and behind a 20,060 entry, so the R-multiple takes over
        self.assertAlmostEqual(
            CreamerOrderflow._target(row(), 20_060.0, 1, 20.0,
                                     dict(params, target_mode="poc")), 30.0)
        # swing targets the leg's end, 20,100
        self.assertAlmostEqual(
            CreamerOrderflow._target(row(), 20_036.0, 1, 20.0,
                                     dict(params, target_mode="swing")), 64.0)

    def test_contiguous_stops_at_a_minute_gap(self):
        bars = [bar(START + i * 60, 1.0, 1.0, 1.0, 1.0) for i in range(6)]
        self.assertEqual(list(CreamerOrderflow._contiguous(bars, 5, 4)),
                         [2, 3, 4, 5])
        # The gap now sits between bars 3 and 4, so the walk back stops at 4.
        bars[3] = bar(START + 9 * 60, 1.0, 1.0, 1.0, 1.0)
        self.assertEqual(list(CreamerOrderflow._contiguous(bars, 5, 4)), [4, 5])


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.strategy = CreamerOrderflow()
        self.params = self.strategy.all_params()

    def signals(self, bars, context, **overrides):
        return self.strategy.signals(bars, context, "all",
                                     self.strategy.all_params(overrides))

    def test_textbook_setup_enters_at_the_next_bar_open(self):
        bars, context = scenario()
        signals = self.signals(bars, context)
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        # The signal bar is 10; the fill is bar 11's open. A signal built from
        # bar 10's close that filled on bar 10 would be reading its own outcome.
        self.assertEqual(signal.index, 11)
        self.assertEqual(signal.side, LONG)
        # stop = 0.886 level (20,011.4) - 2.0 buffer, from a 20,036 entry
        self.assertAlmostEqual(signal.stop, 26.6)
        self.assertAlmostEqual(signal.target, 1.5 * 26.6)
        self.assertEqual(signal.max_minutes, 45)
        self.assertIsNone(signal.trail)

    def test_down_leg_produces_a_short(self):
        bars, context = scenario({
            co.R_LEG_SIDE: "down", co.R_LEG_FROM: 20_100.0, co.R_LEG_TO: 20_000.0,
            # mirror the value structure so the short's environment passes
            co.R_PRIOR_VAL: 20_100.0, co.R_PRIOR_VAH: 20_200.0,
            co.R_DEV_POC: 20_050.0, co.R_DELTA: -50.0,
        })
        # 0.705 of a down leg sits at 20,070.5; bar 10's high must tag it and
        # its close must stay under the 20,088.6 invalidation.
        bars[10] = bar(bars[10][0], 20_075.0, 20_080.0, 20_060.0, 20_065.0)
        # The fill bar has to sit near the setup: entered 54 points under a stop
        # pinned at the 20,088.6 invalidation, the trade exceeds `max_stop` and
        # is refused -- correctly, but it would test the wrong thing.
        bars[11] = bar(bars[11][0], 20_060.0, 20_065.0, 20_040.0, 20_045.0)
        context["aggression"][3][9] = (2.0, 1.0)    # buyers hit hard, absorbed
        signals = self.signals(bars, context)
        self.assertEqual([s.side for s in signals], [SHORT])

    def test_each_gate_alone_blocks_the_setup(self):
        blocks = {
            "leg too small": {co.R_LEG_TO: 20_030.0},
            "wide spread": {co.R_SPREAD: 5.0},
            "no book": {co.R_SPREAD: None},
            "thin participation": {co.R_VOL5: 100.0},
            "selling on the flip": {co.R_DELTA: -50.0},
            "value down": {co.R_DEV_POC: 19_800.0},
            "zone inside prior value": {co.R_PRIOR_VAH: 20_090.0},
        }
        for name, overrides in blocks.items():
            bars, context = scenario(overrides)
            self.assertEqual(self.signals(bars, context), [], name)

    def test_absorption_needs_aggression_that_failed_to_move_price(self):
        bars, context = scenario()
        # Sellers hit hard AND price fell: that is progress, not absorption.
        context["aggression"][3][9] = (-2.0, 30.0)
        self.assertEqual(self.signals(bars, context), [])
        # Price held, but nobody was selling: nothing was absorbed.
        context["aggression"][3][9] = (-0.2, 1.0)
        self.assertEqual(self.signals(bars, context), [])

    def test_absorption_is_read_before_the_flip_bar(self):
        """The flip bar's own delta must not be what satisfies the absorption."""
        bars, context = scenario()
        context["aggression"][3][9] = (0.0, 1.0)    # no absorption before
        context["aggression"][3][10] = (-2.0, 1.0)  # ...only on the flip bar
        self.assertEqual(self.signals(bars, context), [])

    def test_entry_window_and_daily_cap_bind(self):
        bars, context = scenario()
        self.assertEqual(self.signals(bars, context, entry_to=MINUTE - 1), [])
        self.assertEqual(self.signals(bars, context, max_entries=0), [])

    def test_trail_is_a_multiple_of_the_measured_risk(self):
        bars, context = scenario()
        signal = self.signals(bars, context, trail_r=0.5)[0]
        self.assertAlmostEqual(signal.trail, 0.5 * signal.stop)

    def test_funnel_counts_the_real_code_path(self):
        bars, context = scenario()
        funnel = dict(self.strategy.funnel(bars, context, self.params))
        self.assertEqual(funnel["bar"], len(bars))
        self.assertEqual(funnel["candidate"], 1)
        self.assertEqual(funnel["signal"], 1)
        # A blocked setup stops the funnel exactly where the rule sits.
        bars, context = scenario({co.R_VOL5: 100.0})
        funnel = dict(self.strategy.funnel(bars, context, self.params))
        self.assertEqual(funnel["flip"], 1)
        self.assertEqual(funnel["participation"], 0)


if __name__ == "__main__":
    unittest.main()
