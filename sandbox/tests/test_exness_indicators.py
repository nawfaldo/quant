"""Properties of `exness_indicators`, and one end-to-end run of every family.

WHAT THESE TESTS ARE FOR. Three of the four bugs found while writing the second
wave of families were invisible to a backtest -- they produced a plausible
number rather than an error:

  * `parabolic_sar` clamped its stop to the CURRENT bar's low, which made the
    flip test `bar[L] < sar` unsatisfiable. The family emitted zero signals, and
    `select` reported that as "no cell passed" -- which reads as a finding.
  * `swing_pivots` is only causal because a fractal is recorded `wing` bars
    after it happened. Move it one bar earlier and the level appears exactly in
    time to trade the break of it, and every result improves.
  * Four swing-held families tested an intraday entry cutoff their grid does not
    carry. That one is a `KeyError`, but only on the bars where the signal gets
    far enough to reach it.

So the tests below check CAUSALITY by construction rather than by reading the
code, check that every family fires at all, and check that every family runs.
"""
import math
import unittest
from datetime import datetime, timezone

from sandbox.research import cfd_families as ef
from sandbox.research import exness_indicators as ind

TS, O, H, L, C, V = range(6)

#: One synthetic instrument, quoted 24h in 30-minute buckets with a 09:30-16:00
#: session, so the tests need no QuestDB and no terminal.
SESSION = (9 * 60 + 30, 16 * 60)
BAR = 30
DAYS = 420
FIRST_DAY = int(datetime(2019, 1, 2, tzinfo=timezone.utc).timestamp()) // 86_400


def synthetic_bars(days=DAYS, seed=11, drift_period=40):
    """A deterministic walk that contains every SHAPE the families look for.

    A plain random walk is not enough, and finding that out is what this
    generator is for. Five families -- `climax`, `volume_thrust`, `wick`, `rvol`
    and `rel_momentum` -- read a feature a smooth walk simply does not contain,
    and on a smooth walk they emit nothing at all. That is indistinguishable
    from a broken rule, so the shapes are injected on a fixed schedule:

      * volume SPIKES, or nothing clears a "2x the average volume" test and both
        volume families are silent;
      * long-tailed REJECTION bars, or every body fills its range and `wick`
        never sees a tail;
      * a U-shaped intraday volume profile, which is what `rvol` exists to
        correct for and therefore what it has to be tested against.

    The drift flips sign every `drift_period` days and the step size breathes,
    which supplies the trends and ranges the channel and regime families need.
    """
    bars = []
    price = 100.0
    state = seed
    for day in range(days):
        drift = 0.02 if (day // drift_period) % 2 == 0 else -0.02
        for bucket in range(48):
            minute = bucket * 30
            state = (state * 1_103_515_245 + 12_345) % 2_147_483_648
            noise = (state / 2_147_483_648 - 0.5)
            index = day * 48 + bucket
            # The overnight buckets move less, as a real CFD's do.
            scale = 0.6 if SESSION[0] <= minute < SESSION[1] else 0.15
            step = scale * (0.4 + abs(math.sin(day / 7.0))) * noise + drift * scale
            open_ = price
            price = max(1.0, price + step)
            high = max(open_, price) + scale * abs(noise) * 0.5
            low = min(open_, price) - scale * abs(noise) * 0.5

            # A rejection bar every 29th bucket: the body stays where it is and
            # one side of the range is stretched well past it.
            if index % 29 == 0:
                tail = 4.0 * scale * (0.3 + abs(noise))
                if index % 58 == 0:
                    low -= tail
                else:
                    high += tail

            # The intraday U, then a spike every 37th bucket.
            edge = 1.8 if minute in (SESSION[0], SESSION[1] - 30) else 1.0
            volume = 500.0 * edge * (0.5 + abs(noise))
            if index % 37 == 0:
                volume *= 5.0
            bars.append(((FIRST_DAY + day) * 86_400 + minute * 60,
                         open_, high, low, price, volume))
    return bars


class WindowTests(unittest.TestCase):
    def test_inclusive_extreme_matches_brute_force(self):
        values = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0, 5.0, 3.0]
        for period in (1, 2, 3, 5):
            got = ind.rolling_extreme_inclusive(values, period, True)
            for index in range(len(values)):
                want = (max(values[index - period + 1:index + 1])
                        if index >= period - 1 else None)
                self.assertEqual(got[index], want, f"period={period} i={index}")

    def test_argextreme_reports_age_not_level(self):
        values = [5.0, 1.0, 1.0, 1.0]
        # The window high is bar 0, so by bar 3 it is three bars old.
        self.assertEqual(ind.rolling_argextreme(values, 4, True)[3], 3)


class MovingAverageTests(unittest.TestCase):
    def test_every_average_reproduces_a_constant(self):
        """A flat series has one right answer and every member must give it.

        Catches a weighting or a warm-up that is off by one, which on real data
        shows up as a small bias nobody notices.
        """
        values = [42.0] * 400
        for kind in ind.MA_KINDS:
            series = ind.moving_average(kind, values, 20)
            tail = [v for v in series[-50:] if v is not None]
            self.assertTrue(tail, kind)
            for value in tail:
                self.assertAlmostEqual(value, 42.0, places=6, msg=kind)

    def test_lsma_leads_and_sma_lags_on_a_ramp(self):
        """The reason the zoo is not a set of synonyms, asserted.

        On a straight line the least-squares endpoint sits ON the line while a
        simple mean sits half a period behind it. If this ever stops being true,
        `xma_cross`'s `kind` axis has become a rename rather than a hypothesis.
        """
        values = [float(i) for i in range(300)]
        period = 20
        straight = ind.lsma(values, period)[-1]
        lagging = ind.sma(values, period)[-1]
        self.assertAlmostEqual(straight, values[-1], places=6)
        self.assertAlmostEqual(lagging, values[-1] - (period - 1) / 2, places=6)

    def test_efficiency_ratio_separates_a_line_from_a_round_trip(self):
        line = [float(i) for i in range(50)]
        self.assertAlmostEqual(ind.efficiency_ratio(line, 20)[-1], 1.0, places=6)
        saw = [float(i % 2) for i in range(50)]
        self.assertLess(ind.efficiency_ratio(saw, 20)[-1], 0.1)


class GeometryTests(unittest.TestCase):
    def test_sar_actually_flips(self):
        """Regression: the flip test was unsatisfiable and the family was mute.

        `sar` is clamped to the previous two bars' extremes. Clamping it to the
        current bar's low as well makes `bar[L] < sar` impossible, so the
        indicator returned a stop that never triggered and the family produced
        no signals at all on eleven thousand bars.
        """
        bars = synthetic_bars(days=40)
        _sar, flip = ind.parabolic_sar(bars, ef.SAR_STEP, ef.SAR_CAP)
        self.assertGreater(sum(1 for value in flip if value), 10)
        self.assertTrue(any(value == 1 for value in flip))
        self.assertTrue(any(value == -1 for value in flip))

    def test_swing_pivot_is_not_known_before_it_is_confirmed(self):
        """A fractal at bar `i` may not appear before bar `i + wing`.

        The cheapest possible lookahead: recording it against `i` puts the level
        in place exactly in time to trade the break of it.
        """
        wing = 3
        bars = synthetic_bars(days=20)
        highs, _prior, _lows, _prior_low = ind.swing_pivots(bars, wing)
        for index in range(2 * wing + 2, len(bars)):
            if highs[index] is None or highs[index] == highs[index - 1]:
                continue
            # A newly appearing level must belong to a bar at least `wing` back.
            centre = index - wing
            self.assertAlmostEqual(highs[index], bars[centre][H], places=9)


class CausalityTests(unittest.TestCase):
    """No indicator may change a value once later bars arrive.

    Computed on a prefix and on the whole series, the shared part must be
    identical. This is the property every one of these functions claims and the
    one a backtest cannot check: lookahead does not raise, it just improves the
    result.
    """

    def _assert_prefix_stable(self, build, bars, name):
        cut = len(bars) // 2
        whole = build(bars)
        part = build(bars[:cut])
        for index in range(cut):
            self.assertEqual(_round(part[index]), _round(whole[index]),
                             f"{name} changed at index {index} when later bars "
                             "were appended")

    def test_indicators_are_causal(self):
        bars = synthetic_bars(days=60)
        closes = [bar[C] for bar in bars]
        atr = [1.0] * len(bars)
        cases = {
            "sma": lambda b: ind.sma([x[C] for x in b], 20),
            "wma": lambda b: ind.wma([x[C] for x in b], 20),
            "hma": lambda b: ind.hma([x[C] for x in b], 20),
            "tema": lambda b: ind.tema([x[C] for x in b], 20),
            "lsma": lambda b: ind.lsma([x[C] for x in b], 20),
            "kama": lambda b: ind.kama([x[C] for x in b], 20),
            "efficiency_ratio": lambda b: ind.efficiency_ratio(
                [x[C] for x in b], 20),
            "linreg_slope": lambda b: ind.linreg([x[C] for x in b], 20)[0],
            "linreg_fit": lambda b: ind.linreg([x[C] for x in b], 20)[1],
            "variance_ratio": lambda b: ind.variance_ratio(
                [x[C] for x in b], 5, 100),
            "rolling_skew": lambda b: ind.rolling_skew([x[C] for x in b], 50),
            "stochastic_k": lambda b: ind.stochastic(b, 20, 3)[0],
            "stochastic_d": lambda b: ind.stochastic(b, 20, 3)[1],
            "cci": lambda b: ind.cci(b, 20),
            "macd_hist": lambda b: ind.macd([x[C] for x in b], 12, 26, 9)[2],
            "adx": lambda b: ind.adx_dmi(b, 14)[2],
            "plus_di": lambda b: ind.adx_dmi(b, 14)[0],
            "aroon_up": lambda b: ind.aroon(b, 25)[0],
            "mfi": lambda b: ind.money_flow_index(b, 14),
            "cmf": lambda b: ind.chaikin_money_flow(b, 20),
            "obv": ind.on_balance_volume,
            "relative_volume": ind.relative_volume,
            "supertrend": lambda b: ind.supertrend(b, atr[:len(b)], 2.0)[0],
            "sar_flip": lambda b: ind.parabolic_sar(b, 0.02, 0.2)[1],
            "swing_high": lambda b: ind.swing_pivots(b, 3)[0],
            "gaps": lambda b: [len(z) for z in ind.fair_value_gaps(b)],
            "avwap_week": lambda b: ind.anchored_vwap(b, "week"),
            "wick_lower": lambda b: ind.wick_ratios(b)[1],
        }
        for name, build in cases.items():
            with self.subTest(indicator=name):
                self._assert_prefix_stable(build, bars, name)
        self.assertEqual(len(closes), len(ind.sma(closes, 20)))

    def test_align_never_reads_a_future_benchmark_bar(self):
        bars = synthetic_bars(days=10)
        other = [(bar[TS] + 60, 0.0, 0.0, 0.0, float(index), 0.0)
                 for index, bar in enumerate(bars)]
        # Every benchmark bar is stamped one minute LATER than its symbol bar,
        # so the aligned value must be the previous one, never the same index.
        carried = ind.align(bars, other)
        self.assertIsNone(carried[0])
        for index in range(1, len(bars)):
            self.assertEqual(carried[index], float(index - 1))


def _round(value):
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, tuple):
        return tuple(_round(item) for item in value)
    return value


class FamilyIntegrationTests(unittest.TestCase):
    """Every family, on synthetic bars, end to end.

    No QuestDB and no terminal: `all_bars` and the instrument registry are
    replaced, which is enough because everything below the loader is arithmetic.
    """

    SYMBOL = "_synthetic"
    BENCH = "_synthetic_bench"

    @classmethod
    def setUpClass(cls):
        cls.bars = synthetic_bars()
        cls._all_bars = ef.all_bars
        cls._instruments = dict(ef.INSTRUMENTS)
        cls._benchmark = dict(ef.BENCHMARK)
        cls._bar_minutes = ef.BAR_MINUTES

        # An INDEPENDENT walk on the same timestamps, not a rescaling of the
        # symbol. A benchmark of `0.9 * close + 3` leaves a ratio that barely
        # moves, so every `rel_*` family sits below its threshold on every bar
        # and the test cannot tell that from a broken rule -- which is exactly
        # what happened the first time this was written.
        cls.bench_bars = synthetic_bars(seed=977, drift_period=27)

        def loader(symbol, phase, bar=None):
            return cls.bench_bars if symbol == cls.BENCH else cls.bars

        ef.all_bars = loader
        spec = {
            "symbol": cls.SYMBOL, "asset_class": "stock", "broker": "SYN",
            "table": "syn_1m", "source": "1m", "warmup": "2019-01-01",
            "first_full_year": 2020, "first_row": "2019-01-02",
            "last_row": "2020-12-31", "session": SESSION, "shift_hours": 0,
            "calendar": 252.0, "multiplier": 100.0, "contract_size": 100.0,
            "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01,
            "volume_step": 0.01, "volume_max": 100.0,
            "currency_profit": "USD", "fx_to_usd": 1.0, "spread_bp": 3.0,
            "spread_quoted_at": "session", "warning": None,
        }
        ef.INSTRUMENTS[cls.SYMBOL] = spec
        ef.INSTRUMENTS[cls.BENCH] = {**spec, "symbol": cls.BENCH}
        ef.BENCHMARK[cls.SYMBOL] = cls.BENCH
        ef.BAR_MINUTES = BAR
        cls.bar_list, cls.ctx = ef.context(cls.SYMBOL, "select", BAR)

    @classmethod
    def tearDownClass(cls):
        ef.all_bars = cls._all_bars
        ef.INSTRUMENTS.clear()
        ef.INSTRUMENTS.update(cls._instruments)
        ef.BENCHMARK.clear()
        ef.BENCHMARK.update(cls._benchmark)
        ef.BAR_MINUTES = cls._bar_minutes

    def test_groups_partition_families(self):
        listed = [name for members in ef.GROUPS.values() for name in members]
        self.assertEqual(sorted(listed), sorted(ef.FAMILIES))
        self.assertEqual(len(listed), len(set(listed)))

    def test_every_family_runs_and_fires(self):
        """A family that never signals is not a result, it is a broken rule.

        `select` prints "no cell passed" for both, so the two are
        indistinguishable in a run and have to be separated here. Every cell of
        every family is scored, which is slow but is the only way to tell "this
        rule cannot fire" from "these six cells happened not to".
        """
        grid = ef.axes(self.SYMBOL, BAR)
        self.assertGreaterEqual(len(grid), 55)
        silent, raised = [], []
        for family, axis in grid.items():
            fired = False
            for params in ef.candidates(axis):
                try:
                    stat = ef.backtest(family, self.bar_list, self.ctx, params)
                except Exception as error:              # noqa: BLE001
                    raised.append(f"{family}: {type(error).__name__}: {error}")
                    break
                if stat["signals"]:
                    fired = True
                    break
            if not fired and family not in {f.split(":")[0] for f in raised}:
                silent.append(family)
        self.assertEqual(raised, [], "families raised")
        self.assertEqual(silent, [], "families never fired")

    def test_context_holds_every_array_a_cell_can_reach(self):
        """`reads` and the builders must agree, exactly.

        Two ways to get this wrong and neither raises until a specific cell runs
        deep into a sweep. Building too LITTLE is a `KeyError` hours in --
        `extra_context` narrowed the moving-average zoo to the reachable
        `(kind, period)` pairs, and if `xma_ribbon` ever sweeps a fourth average
        the omission surfaces only on that family's cells. Building too MUCH is
        silent and merely expensive, which is how fifteen workers came to hold
        4.8 GB.

        So this walks every cell of every family and asserts the context has the
        array it would index. Static, so it costs nothing.
        """
        grid = ef.axes(self.SYMBOL, BAR)
        missing = []
        for family, axis in grid.items():
            blocks = ef.context_blocks({family})
            ctx = dict(self.ctx)
            for params in ef.candidates(axis):
                if family.startswith("xma"):
                    kind = params["kind"]
                    table = ctx["xma"][kind]
                    wanted = ([params["fast"], params["slow"]]
                              if family == "xma_cross" else
                              [params["period"]] if family == "xma_slope"
                              else list(ctx["periods"]["ribbon"]))
                    for period in wanted:
                        if period not in table:
                            missing.append(f"{family}: xma[{kind}][{period}]")
            self.assertIsInstance(blocks, set)
        self.assertEqual(sorted(set(missing)), [])

    def test_scoping_a_run_actually_shrinks_the_context(self):
        """The memory fix, asserted rather than assumed.

        A `--groups original` run must allocate none of the second wave's
        blocks. If a new family ever loses its `reads` declaration this test
        fails here instead of the machine failing at 3 a.m.
        """
        original = ef.expand_families(None, "original")
        self.assertEqual(ef.context_blocks(original), set())
        self.assertEqual(ef.context_blocks(ef.expand_families(None, "xma")),
                         {"xma"})
        self.assertGreater(len(ef.context_blocks(None)), 15)

    def test_relative_families_disappear_without_a_benchmark(self):
        """A `rel_*` family with no benchmark would emit nothing on every cell,
        and `select` would print that as a failed search rather than as an
        absent input."""
        del ef.BENCHMARK[self.SYMBOL]
        try:
            grid = ef.axes(self.SYMBOL, BAR)
        finally:
            ef.BENCHMARK[self.SYMBOL] = self.BENCH
        for family in ("rel_momentum", "rel_zscore", "rel_break"):
            self.assertNotIn(family, grid)
        self.assertIn("rel_momentum", ef.axes(self.SYMBOL, BAR))

    def test_restricted_run_writes_its_own_file(self):
        """A one-group sweep must not overwrite a full one, which is sealed and
        would still look complete afterwards."""
        full = ef.output_path("tsla", 30, None)
        scoped = ef.output_path("tsla", 30, set(ef.GROUPS["xma"]))
        self.assertNotEqual(full, scoped)
        self.assertTrue(scoped.endswith("_xma.json"))
        self.assertIsNone(ef.scope_tag(None))
        self.assertEqual(ef.scope_tag(set(ef.GROUPS["relative"])), "relative")


if __name__ == "__main__":
    unittest.main()
