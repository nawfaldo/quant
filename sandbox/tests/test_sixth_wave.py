"""The sixth wave: its arithmetic, and the one bug class that would void it.

THREE THINGS ARE TESTED HERE AND THEY ARE NOT INTERCHANGEABLE.

`IndicatorTests` check that each new reading MEASURES WHAT IT CLAIMS TO, against
series whose answer is known before the code runs -- a Hurst exponent must read
0.5 on a random walk and must be monotone in the autocorrelation of returns, an
entropy must collapse on a repeating pattern, a runs statistic must go strongly
positive on an alternating one. A test that only checked shapes and lengths
would pass on an indicator that returned the wrong quantity with the right
dimensions, which is the failure that survives review.

`CausalityTests` are the important ones. Every family here is scored on a
holdout and then, if it survives, considered for a live book, so a reading that
can see one bar into the future produces a beautiful result and a losing sleeve.
The test truncates the bar series, rebuilds the whole context from the shorter
one, and requires every signal on the overlap to be identical -- which is the
only form of the question that catches a lookahead hiding in a context block
rather than in a signal.

`FamilyTests` check the wiring: that every family builds a grid, fires, and
survives the engine. A family that returns `None` on every bar is not a negative
result, it is a dead grid, and `select` reports it as "no cell passed" -- which
reads like a finding about the market and is a fact about the code.

Two bugs were found by this file and are pinned by the tests named after them:
`cusum` was thresholding one-bar returns against an ANNUALISED volatility and
fired 23 times in seven years, and `ou_half_life` was clamping a
non-reverting series to its ceiling instead of refusing it.
"""
import math
import random
import unittest

from sandbox.research import cfd_families as ef
from sandbox.research import exness_indicators as ind

SIXTH = ef.ALIASES["sixth"]


def walk(n=4000, drift=0.0, sigma=0.004, seed=7):
    generator = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= math.exp(generator.gauss(drift, sigma))
        out.append(price)
    return out


def ar1(phi, n=4000, sigma=0.004, seed=3):
    """A series whose RETURNS are autocorrelated at `phi`.

    This, and not a drift, is what a Hurst exponent measures. A trending series
    is a random walk plus a constant: demeaning removes the drift and the k-bar
    moves still scale as k^0.5, so a pure trend must read 0.5. Testing Hurst
    against a drift is the mistake this function exists to prevent.
    """
    generator = random.Random(seed)
    price, r, out = 100.0, 0.0, []
    for _ in range(n):
        r = phi * r + generator.gauss(0, sigma)
        price *= math.exp(r)
        out.append(price)
    return out


def reverting(n=4000, sigma=0.004, kappa=0.15, seed=5):
    generator = random.Random(seed)
    level = math.log(100.0)
    x, out = level, []
    for _ in range(n):
        x += kappa * (level - x) + generator.gauss(0, sigma)
        out.append(math.exp(x))
    return out


def bars_from(closes, spread=0.002, volume=1000.0):
    out = []
    for index, close in enumerate(closes):
        opening = closes[index - 1] if index else close
        out.append((1_600_000_000 + index * 1800, opening,
                    max(opening, close) * (1 + spread),
                    min(opening, close) * (1 - spread), close, volume))
    return out


def tail_mean(values, count=500):
    live = [v for v in values if v is not None][-count:]
    return sum(live) / len(live)


class IndicatorTests(unittest.TestCase):
    """Each reading against a series whose answer is known in advance."""

    def test_every_reading_is_aligned_with_its_input(self):
        """A short list is a silent off-by-N on every bar after it."""
        closes = walk()
        bars = bars_from(closes)
        for name, values in (
                ("hurst", ind.hurst_exponent(closes, 300)),
                ("entropy", ind.permutation_entropy(closes, 200)),
                ("kurtosis", ind.rolling_kurtosis(closes, 200)),
                ("autocorr", ind.rolling_autocorr(closes, 200, 1)),
                ("runs", ind.runs_z(closes, 100)),
                ("kendall", ind.mann_kendall_z(closes, 100)),
                ("tails", ind.tail_ratio(closes, 200)),
                ("jump", ind.bipower_jump(closes, 100)),
                ("semi", ind.signed_jump(closes, 100)),
                ("roofing", ind.roofing_filter(closes)),
                ("fracdiff", ind.frac_diff(closes, 0.4)),
                ("cycle", ind.dominant_cycle(closes)),
                ("halflife", ind.ou_half_life(closes, 200)),
                ("quantile", ind.rolling_quantile(closes, 100, 0.9)),
                ("amihud", ind.amihud(bars, 100)),
                ("bulk", ind.bulk_imbalance(bars, 100)),
                ("fisher", ind.fisher_transform(bars, 50)),
                ("kalman_level", ind.kalman_trend(closes)[0]),
                ("parkinson", ind.variance_estimators(bars, 100)[0]),
        ):
            with self.subTest(name):
                self.assertEqual(len(values), len(closes))

    def test_hurst_is_monotone_in_return_autocorrelation(self):
        ladder = [tail_mean(ind.hurst_exponent(ar1(phi), 300))
                  for phi in (-0.4, -0.2, 0.0, 0.2, 0.4)]
        for lower, higher in zip(ladder, ladder[1:]):
            self.assertLess(lower, higher, ladder)
        self.assertLess(ladder[0], 0.45, ladder)
        self.assertGreater(ladder[-1], 0.55, ladder)

    def test_hurst_reads_a_pure_drift_as_a_random_walk(self):
        """A drift is not persistence, and reporting it as such would make the
        family a slow momentum rule wearing a fractal name."""
        drift = tail_mean(ind.hurst_exponent(walk(drift=0.004, sigma=0.001), 300))
        self.assertTrue(0.40 < drift < 0.60, drift)

    def test_entropy_collapses_on_a_repeating_pattern(self):
        saw = [100.0 + (1.0 if index % 2 else -1.0) for index in range(4000)]
        self.assertGreater(tail_mean(ind.permutation_entropy(walk(), 200)), 0.90)
        self.assertLess(tail_mean(ind.permutation_entropy(saw, 200)), 0.60)

    def test_entropy_ignores_magnitude(self):
        """The property that makes it the complement of `efficiency_ratio`."""
        closes = walk()
        stretched = [100.0 * (value / 100.0) ** 3 for value in closes]
        a = ind.permutation_entropy(closes, 200)
        b = ind.permutation_entropy(stretched, 200)
        pairs = [(x, y) for x, y in zip(a, b) if x is not None]
        self.assertTrue(pairs)
        for x, y in pairs:
            self.assertAlmostEqual(x, y, places=9)

    def test_kurtosis_separates_fat_tails_from_high_volatility(self):
        generator = random.Random(21)
        price, fat = 100.0, []
        for _ in range(4000):
            shock = generator.gauss(0, 0.001)
            if generator.random() < 0.01:
                shock += generator.choice([-0.08, 0.08])
            price *= math.exp(shock)
            fat.append(price)
        self.assertLess(abs(tail_mean(ind.rolling_kurtosis(walk(), 300))), 1.0)
        self.assertGreater(tail_mean(ind.rolling_kurtosis(fat, 300)), 5.0)

    def test_autocorrelation_is_negative_on_a_reverting_series(self):
        self.assertLess(tail_mean(ind.rolling_autocorr(reverting(), 300, 1)),
                        -0.05)
        self.assertLess(abs(tail_mean(ind.rolling_autocorr(walk(), 300, 1))),
                        0.12)

    def test_runs_statistic_is_positive_when_signs_alternate(self):
        saw = [100.0 + (1.0 if index % 2 else -1.0) for index in range(4000)]
        self.assertGreater(tail_mean(ind.runs_z(saw, 100)), 5.0)
        self.assertLess(abs(tail_mean(ind.runs_z(walk(), 100))), 1.0)

    def test_mann_kendall_is_more_robust_than_the_regression(self):
        """The whole reason to carry it next to `linreg`, at its MEASURED size.

        A first draft asserted the regression collapsed. It does not: one
        outlier at the end of a 200-bar window has only moderate leverage, and
        the honest numbers are a 2.0% loss for Mann-Kendall against 6.9% for
        least squares. Three times more robust is the claim the code supports,
        and the docstring was corrected to match rather than this assertion
        being loosened until the original claim passed.
        """
        grind = [100.0 * (1.0 + 0.0005 * index) for index in range(300)]
        spiked = list(grind)
        spiked[-1] *= 0.80

        def shift(clean, damaged):
            return abs(damaged - clean) / abs(clean)

        kendall = shift(ind.mann_kendall_z(grind, 200)[-1],
                        ind.mann_kendall_z(spiked, 200)[-1])
        regression = shift(ind.linreg(grind, 200)[0][-1],
                           ind.linreg(spiked, 200)[0][-1])
        self.assertGreater(ind.mann_kendall_z(spiked, 200)[-1], 5.0)
        self.assertLess(kendall * 2.0, regression, (kendall, regression))

    def test_bipower_separates_a_jump_from_a_diffusion(self):
        generator = random.Random(31)
        price, jumpy = 100.0, []
        for _ in range(4000):
            shock = generator.gauss(0, 0.001)
            if generator.random() < 0.01:
                shock += generator.choice([-0.08, 0.08])
            price *= math.exp(shock)
            jumpy.append(price)
        quiet = tail_mean(ind.bipower_jump(walk(), 200))
        loud = tail_mean(ind.bipower_jump(jumpy, 200))
        self.assertGreater(loud, quiet)
        self.assertTrue(0.0 <= quiet <= 1.0 and 0.0 <= loud <= 1.0)

    def test_signed_jump_pins_to_one_on_a_one_sided_series(self):
        up = [100.0 * 1.001 ** index for index in range(500)]
        down = [100.0 * 0.999 ** index for index in range(500)]
        self.assertAlmostEqual(ind.signed_jump(up, 100)[-1], 1.0, places=9)
        self.assertAlmostEqual(ind.signed_jump(down, 100)[-1], -1.0, places=9)

    def test_parkinson_and_close_to_close_disagree_in_the_right_direction(self):
        """The disagreement IS the reading `estimator` trades."""
        closes = walk()
        gapping = bars_from(closes, spread=0.0)
        whipping = [(row[0], row[1], max(row[1], row[4]) * 1.02,
                     min(row[1], row[4]) * 0.98, row[4], row[5])
                    for row in bars_from(closes)]
        park, _gk, close = ind.variance_estimators(gapping, 200)
        self.assertLess(park[-1], close[-1])
        park, _gk, close = ind.variance_estimators(whipping, 200)
        self.assertGreater(park[-1], close[-1])

    def test_bulk_imbalance_is_continuous_where_obv_is_not(self):
        """A bar that closes mid-range is neither side; OBV cannot say that."""
        high = [(1_600_000_000 + i * 1800, 100.0, 101.0, 99.0, 101.0, 500.0)
                for i in range(300)]
        middle = [(1_600_000_000 + i * 1800, 100.0, 101.0, 99.0, 100.0, 500.0)
                  for i in range(300)]
        self.assertAlmostEqual(ind.bulk_imbalance(high, 50)[-1], 1.0, places=9)
        self.assertAlmostEqual(ind.bulk_imbalance(middle, 50)[-1], 0.0, places=9)

    def test_roofing_filter_removes_a_pure_trend(self):
        """No average-difference in this module can do this, which is the
        family's entire admission argument."""
        ramp = [100.0 + 0.05 * index for index in range(1500)]
        values = [v for v in ind.roofing_filter(ramp, 48, 10) if v is not None]
        self.assertTrue(values)
        self.assertLess(max(abs(v) for v in values[-200:]), 0.01)

    def test_fracdiff_spans_the_range_from_a_level_to_a_return(self):
        closes = walk()
        nearly_return = ind.frac_diff(closes, 0.99)[-1]
        nearly_level = ind.frac_diff(closes, 0.05)[-1]
        self.assertAlmostEqual(nearly_return,
                               math.log(closes[-1] / closes[-2]), places=1)
        self.assertGreater(abs(nearly_level), abs(nearly_return) * 5)

    def test_dominant_cycle_recovers_a_known_period(self):
        for period in (16, 30):
            with self.subTest(period=period):
                sine = [100.0 + 5.0 * math.sin(2 * math.pi * i / period)
                        for i in range(3000)]
                got = tail_mean(ind.dominant_cycle(sine), 300)
                self.assertLess(abs(got - period) / period, 0.35, got)

    def test_half_life_refuses_a_trend_rather_than_clamping_to_its_ceiling(self):
        """Regression. It used to clamp, which reported the ceiling as a
        measurement -- indistinguishable from a genuine slow reverter."""
        trend = ind.ou_half_life(walk(drift=0.004, sigma=0.001), 300)[-500:]
        live = [v for v in trend if v is not None]
        self.assertLess(len(live), 0.05 * len(trend), len(live))
        for value in ind.ou_half_life(reverting(), 300):
            if value is not None:
                self.assertLess(value, 300)

    def test_half_life_is_shorter_when_reversion_is_faster(self):
        fast = tail_mean(ind.ou_half_life(reverting(kappa=0.30, seed=8), 300))
        slow = tail_mean(ind.ou_half_life(reverting(kappa=0.03, seed=9), 300))
        self.assertLess(fast, slow, (fast, slow))

    def test_cusum_resets_and_scales_with_its_threshold(self):
        closes = walk()
        few = ind.cusum_events(closes, [0.02] * len(closes))
        many = ind.cusum_events(closes, [0.005] * len(closes))
        self.assertTrue(any(few))
        self.assertGreater(sum(1 for v in many if v), sum(1 for v in few if v))
        self.assertLessEqual(set(few), {-1, 0, 1})

    def test_the_quantile_at_one_is_exactly_the_donchian_channel(self):
        """The control that makes the robustness claim falsifiable.

        Asserted on `rolling_quantile`, which accepts 1.0. `quantile_channel`
        refuses it by design -- it builds BOTH edges from one sort and needs
        `1 - quantile` to be the other side -- so the closest it can come is
        0.999999, and the 5e-5 residual that leaves is exactly the
        interpolation weight rather than an error.
        """
        closes = walk()
        exact = ind.rolling_quantile(closes, 50, 1.0)
        extreme = ind.rolling_extreme_inclusive(closes, 50, True)
        seen = 0
        for value, top in zip(exact, extreme):
            if value is not None:
                seen += 1
                self.assertAlmostEqual(value, top, places=12)
        self.assertTrue(seen)

    def test_quantile_channel_is_inside_the_extremes(self):
        closes = walk()
        upper, lower = ind.quantile_channel(closes, 50, 0.9)
        top = ind.rolling_extreme_inclusive(closes, 50, True)
        bottom = ind.rolling_extreme_inclusive(closes, 50, False)
        seen = 0
        for high, low, a, b in zip(upper, lower, top, bottom):
            if high is None:
                continue
            seen += 1
            self.assertLessEqual(high, a + 1e-9)
            self.assertGreaterEqual(low, b - 1e-9)
        self.assertTrue(seen)

    def test_no_reading_can_see_the_future(self):
        """Appending bars must not change any earlier value."""
        closes = walk(n=3000)
        bars = bars_from(closes)
        cut = 1500
        cases = (
            ("hurst", lambda s, _b: ind.hurst_exponent(s, 200)),
            ("entropy", lambda s, _b: ind.permutation_entropy(s, 200)),
            ("kurtosis", lambda s, _b: ind.rolling_kurtosis(s, 200)),
            ("autocorr", lambda s, _b: ind.rolling_autocorr(s, 200, 1)),
            ("runs", lambda s, _b: ind.runs_z(s, 100)),
            ("kendall", lambda s, _b: ind.mann_kendall_z(s, 100)),
            ("tails", lambda s, _b: ind.tail_ratio(s, 200)),
            ("jump", lambda s, _b: ind.bipower_jump(s, 100)),
            ("semi", lambda s, _b: ind.signed_jump(s, 100)),
            ("roofing", lambda s, _b: ind.roofing_filter(s, 48, 10)),
            ("fracdiff", lambda s, _b: ind.frac_diff(s, 0.4)),
            ("cycle", lambda s, _b: ind.dominant_cycle(s)),
            ("halflife", lambda s, _b: ind.ou_half_life(s, 200)),
            ("quantile", lambda s, _b: ind.rolling_quantile(s, 50, 0.9)),
            ("kalman", lambda s, _b: ind.kalman_trend(s)[1]),
            ("cusum", lambda s, _b: ind.cusum_events(s, [0.01] * len(s))),
            ("amihud", lambda _s, b: ind.amihud(b, 100)),
            ("bulk", lambda _s, b: ind.bulk_imbalance(b, 100)),
            ("fisher", lambda _s, b: ind.fisher_transform(b, 50)),
            ("parkinson", lambda _s, b: ind.variance_estimators(b, 100)[0]),
        )
        for name, build in cases:
            with self.subTest(name):
                short = build(closes[:cut], bars[:cut])
                long = build(closes, bars)[:cut]
                for index, (a, b) in enumerate(zip(short, long)):
                    if a is None or b is None:
                        self.assertIs(a, b, f"{name} at {index}")
                    else:
                        self.assertAlmostEqual(a, b, places=9,
                                               msg=f"{name} at {index}")


class TaxonomyTests(unittest.TestCase):
    """The wiring that a sweep silently depends on."""

    def test_the_wave_is_partitioned_into_groups(self):
        groups = ("pathstat", "micro", "filter", "adaptive", "fusion")
        listed = [name for group in groups for name in ef.GROUPS[group]]
        self.assertEqual(sorted(listed), sorted(SIXTH))
        self.assertEqual(len(listed), len(set(listed)))

    def test_every_family_declares_the_blocks_it_reads(self):
        """A family reading a block it did not declare works in a full sweep and
        raises a KeyError in a `--groups` run, which is the worst possible
        place to find out."""
        for name in SIXTH:
            with self.subTest(name):
                blocks = ef.context_blocks({name})
                self.assertEqual(blocks, set(ef.FAMILIES[name].reads))

    def test_no_family_is_all_categorical(self):
        """A grid of nothing but labels reports `1/1` robust neighbours and has
        never been perturbed ([[all-categorical-axes-void-the-robustness-gate]])."""
        grid = _grid()
        for name in SIXTH:
            if name not in grid:
                continue
            with self.subTest(name):
                scales = [key for key in grid[name]
                          if key not in ef.CATEGORICAL
                          and len(grid[name][key]) > 1]
                self.assertGreaterEqual(len(scales), 2, sorted(grid[name]))

    def test_every_scale_axis_is_ordered(self):
        """`neighbours` calls `.index` on the axis, so an unordered numeric axis
        would step to a value that is not adjacent to anything."""
        grid = _grid()
        for name in SIXTH:
            if name not in grid:
                continue
            for key, values in grid[name].items():
                if key in ef.CATEGORICAL:
                    continue
                with self.subTest(f"{name}.{key}"):
                    self.assertEqual(list(values), sorted(values))


_GRID_CACHE = {}


def _grid():
    if "grid" not in _GRID_CACHE:
        _GRID_CACHE["grid"] = ef.axes(_SYMBOL, bar=30, only=set(SIXTH))
    return _GRID_CACHE["grid"]


#: A symbol with a long, clean, high-volume 30m table. The family tests are
#: about wiring rather than about this instrument, so the choice only has to be
#: one where every block can be built.
_SYMBOL = "ethusd"


def setUpModule():
    ef.resolve(_SYMBOL, allow_stale=True)


class FamilyTests(unittest.TestCase):
    """Grids build, signals fire, and the engine survives them."""

    @classmethod
    def setUpClass(cls):
        cls.grid = _grid()
        cls.runnable = [name for name in SIXTH if name in cls.grid]
        cls.bars, cls.ctx = ef.context(_SYMBOL, "is", bar=30,
                                       only=set(cls.runnable))

    def test_every_family_builds_a_grid_at_thirty_minutes(self):
        self.assertEqual(sorted(self.runnable), sorted(SIXTH))

    def test_only_session_bound_families_are_dropped_on_a_daily_bar(self):
        daily = ef.axes(_SYMBOL, bar=ef.DAILY, only=set(SIXTH))
        dropped = set(SIXTH) - set(daily)
        # `regime_breakout` anchors on the session open and `value_area` needs a
        # within-session volume profile; neither exists on a one-bar day.
        self.assertEqual(dropped, {"regime_breakout", "value_area"})

    def test_every_family_fires(self):
        """A family that never signals is a dead grid, and `select` reports it
        as 'no cell passed' -- which reads as a finding and is not one."""
        generator = random.Random(11)
        for name in self.runnable:
            with self.subTest(name):
                cells = ef.candidates(self.grid[name])
                sample = generator.sample(cells, min(6, len(cells)))
                signal = ef.SIGNALS[name]
                fired = sum(
                    1 for params in sample
                    for index in range(len(self.bars))
                    if signal(index, self.bars, self.ctx, params, {}))
                self.assertGreater(fired, 0)

    def test_every_family_survives_the_engine(self):
        generator = random.Random(13)
        for name in self.runnable:
            with self.subTest(name):
                cells = ef.candidates(self.grid[name])
                traded = 0
                for params in generator.sample(cells, min(6, len(cells))):
                    stat = ef.backtest(name, self.bars, self.ctx, params)
                    self.assertIn("trades", stat)
                    traded += stat["trades"]
                self.assertGreater(traded, 0)

    def test_cusum_fires_at_a_tradeable_rate(self):
        """Regression, and the reason it is worth a named test. The threshold
        was an ANNUALISED volatility applied to one-bar log returns -- out by
        about ninety at 30m -- so the filter produced 23 events in seven years.
        It was alive, which is what made it dangerous: a family that returns
        nothing is obvious, and one that returns a handful of trades at a
        flattering profit factor is not."""
        cells = [c for c in ef.candidates(self.grid["cusum"])
                 if c["multiple"] == min(ef.periods(_SYMBOL, 30)["cusum"])]
        signal = ef.SIGNALS["cusum"]
        fired = sum(1 for index in range(len(self.bars))
                    if signal(index, self.bars, self.ctx, cells[0], {}))
        sessions = len({bar[ef.TS] // 86_400 for bar in self.bars})
        self.assertGreater(fired, sessions / 20.0, (fired, sessions))

    def test_a_family_cannot_see_the_future(self):
        """THE TEST THAT MATTERS. The context is rebuilt from a TRUNCATED bar
        series and every signal on the overlap must be unchanged.

        Checking the indicators alone is not enough: a lookahead can live in the
        context assembly -- a level keyed by the wrong session, a series aligned
        off by one -- and only a rebuild catches that. `context` takes
        `full_bars` for exactly this kind of use.
        """
        full = ef.all_bars(_SYMBOL, "is", 30)
        cut = int(len(full) * 0.75)
        generator = random.Random(17)
        short_bars, short_ctx = ef.context(
            _SYMBOL, "is", bar=30, only=set(self.runnable),
            full_bars=full[:cut])
        # The truncated context has its own bar list; compare on the overlap,
        # and only after the longest warm-up either side could still be inside.
        overlap = len(short_bars)
        self.assertGreater(overlap, 1000)
        start = overlap // 2
        for name in self.runnable:
            with self.subTest(name):
                cells = ef.candidates(self.grid[name])
                params = generator.choice(cells)
                signal = ef.SIGNALS[name]
                for index in range(start, overlap):
                    self.assertEqual(
                        signal(index, short_bars, short_ctx, params, {}),
                        signal(index, self.bars, self.ctx, params, {}),
                        f"{name} disagrees at bar {index}")


if __name__ == "__main__":
    unittest.main()
