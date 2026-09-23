"""Focused tests for the ORB optimisation grid."""

import unittest

from sandbox.research import zarattini_orb_optimization as opt


def bar(minute, open_price, high, low, close, volume=1.0):
    return (minute * 60, open_price, high, low, close, volume)


def session(bars, span=None, day=0):
    """A session from a sparse {offset: bar} map, padded to `span`."""
    span = span or (max(bars) + 1)
    padded = [None] * span
    for offset, value in bars.items():
        padded[offset] = value
    present = [b for b in padded if b is not None]
    return {"day": day, "ts": present[0][0], "bars": padded, "span": span,
            "open": present[0][1], "close": present[-1][4]}


def rising_session(day=0, base_price=100.0):
    """An up opening range followed by a slow drift higher."""
    bars = {0: bar(0, base_price, base_price + 10, base_price - 10, base_price + 8)}
    for offset in range(5, 40):
        price = base_price + 8 + (offset - 5) * 0.5
        bars[offset] = bar(offset, price, price + 1, price - 1, price)
    return session(bars, day=day)


class PathTests(unittest.TestCase):
    def test_doji_range_produces_no_path(self):
        bars = {0: bar(0, 100.0, 110.0, 90.0, 100.0)}
        bars.update({i: bar(i, 100.0, 100.0, 100.0, 100.0) for i in range(5, 10)})
        self.assertEqual(opt.build_paths([session(bars)], 5), [])

    def test_running_extremes_are_monotone(self):
        path = opt.build_paths([rising_session()], 5)[0]
        cummax = list(path["cummax"])
        self.assertEqual(cummax, sorted(cummax))
        neg = list(path["neg_cummin"])
        self.assertEqual(neg, sorted(neg))

    def test_entry_is_the_bar_after_the_range(self):
        path = opt.build_paths([rising_session()], 5)[0]
        self.assertAlmostEqual(path["entry"], 108.0)
        self.assertEqual(path["side"], "long")

    def test_range_minutes_moves_the_entry(self):
        path = opt.build_paths([rising_session()], 15)[0]
        self.assertAlmostEqual(path["entry"], 108.0 + 10 * 0.5)


class ExitAgreementTests(unittest.TestCase):
    """The binary-search path and the scanning path must agree.

    A trailing stop wider than the initial stop can never bind first, so the
    scan is obliged to return exactly what the search does. This is the test
    that lets the fast path be trusted for 100k cells.
    """

    def _sessions(self):
        return [rising_session(day=0), rising_session(day=1, base_price=200.0),
                falling_session(day=2)]

    def test_scan_matches_search_when_the_trail_never_binds(self):
        paths = opt.build_paths(self._sessions(), 5)
        for target_r in (2.0, 5.0, None):
            searched = opt.resolve(paths, 1.0, target_r, None)
            scanned = opt.resolve(paths, 1.0, target_r, 1e9)
            self.assertEqual([t["reason"] for t in searched],
                             [t["reason"] for t in scanned])
            for a, b in zip(searched, scanned):
                self.assertAlmostEqual(a["exit"], b["exit"])

    def test_tight_trail_exits_no_later_than_a_fixed_stop(self):
        paths = opt.build_paths(self._sessions(), 5)
        loose = opt.resolve(paths, 1.0, None, None)
        tight = opt.resolve(paths, 1.0, None, 0.1)
        for a, b in zip(loose, tight):
            self.assertLessEqual(b["index"], a["index"])


def falling_session(day=0, base_price=100.0):
    bars = {0: bar(0, base_price, base_price + 10, base_price - 10, base_price - 8)}
    for offset in range(5, 40):
        price = base_price - 8 - (offset - 5) * 0.5
        bars[offset] = bar(offset, price, price + 1, price - 1, price)
    return session(bars, day=day)


class StopAndTargetTests(unittest.TestCase):
    def test_stop_mult_scales_the_risk(self):
        paths = opt.build_paths([rising_session()], 5)
        wide = opt.resolve(paths, 1.0, None, None)[0]
        tight = opt.resolve(paths, 0.5, None, None)[0]
        self.assertAlmostEqual(tight["risk"], wide["risk"] / 2.0)

    def test_target_none_holds_to_the_session_close(self):
        trade = opt.resolve(opt.build_paths([rising_session()], 5),
                            1.0, None, None)[0]
        self.assertEqual(trade["reason"], "session_close")

    def test_reachable_target_is_taken(self):
        # Entry 108, range low 90, R = 18; the drift reaches 125, so a 0.5R
        # target at 117 is hit but a 5R target at 198 is not.
        paths = opt.build_paths([rising_session()], 5)
        self.assertEqual(opt.resolve(paths, 1.0, 0.5, None)[0]["reason"], "target")
        self.assertEqual(opt.resolve(paths, 1.0, 5.0, None)[0]["reason"],
                         "session_close")


class RegimeTests(unittest.TestCase):
    def test_features_never_read_the_session_being_judged(self):
        """The SMA state for day i must not move when day i's close changes."""
        sessions = [rising_session(day=d, base_price=100.0 + d) for d in range(60)]
        before = opt.regime_features(sessions)[sessions[-1]["day"]]
        sessions[-1] = dict(sessions[-1], close=99_999.0)
        after = opt.regime_features(sessions)[sessions[-1]["day"]]
        self.assertEqual(before["sma50"], after["sma50"])
        self.assertEqual(before["vol"], after["vol"])

    def test_short_history_leaves_features_undefined(self):
        features = opt.regime_features([rising_session(day=0)])
        self.assertIsNone(features[0]["sma50"])
        self.assertIsNone(features[0]["vol_band"])


class FilterTests(unittest.TestCase):
    def _trades(self):
        return [{"day": 0, "side": "long", "ts": 0},
                {"day": 1, "side": "short", "ts": 1}]

    def test_no_filter_returns_the_input_untouched(self):
        trades = self._trades()
        self.assertIs(opt.apply_filters(trades, {}, None, None, None), trades)

    def test_trend_filter_keeps_only_aligned_sides(self):
        features = {0: {"sma50": 1, "vol_band": None, "weekday": 0},
                    1: {"sma50": 1, "vol_band": None, "weekday": 0}}
        kept = opt.apply_filters(self._trades(), features, "sma50", None, None)
        self.assertEqual([t["side"] for t in kept], ["long"])

    def test_absent_feature_blocks_rather_than_waves_through(self):
        features = {0: {"sma50": None, "vol_band": None, "weekday": 0},
                    1: {"sma50": None, "vol_band": None, "weekday": 0}}
        self.assertEqual(opt.apply_filters(self._trades(), features,
                                           "sma50", None, None), [])

    def test_weekday_filter_drops_the_weekend(self):
        features = {0: {"sma50": 1, "vol_band": None, "weekday": 5},
                    1: {"sma50": 1, "vol_band": None, "weekday": 2}}
        kept = opt.apply_filters(self._trades(), features, None, None, "mon_fri")
        self.assertEqual([t["day"] for t in kept], [1])


class SizingTests(unittest.TestCase):
    """Every mode must scale with equity -- that is the requirement."""

    def test_every_mode_scales_with_the_account(self):
        for sizing in ({"mode": "risk_leverage", "risk": 0.01, "cap": 4.0},
                       {"mode": "vol_target", "vol_target_annual": 0.6, "cap": 4.0},
                       {"mode": "fixed_fraction", "fraction": 1.0}):
            small = opt._quantity(10_000.0, 1_000.0, 10.0, sizing, 0.03)
            large = opt._quantity(100_000.0, 1_000.0, 10.0, sizing, 0.03)
            # Ten times the account buys ten times the size, up to the lot step
            # that the smaller account's floor() threw away.
            self.assertLessEqual(abs(large - small * 10.0), 10 * opt.QUANTITY_STEP,
                                 msg=f"{sizing['mode']} is not equity-based")
            self.assertGreater(small, 0.0)

    def test_risk_leverage_respects_the_cap(self):
        sizing = {"mode": "risk_leverage", "risk": 0.01, "cap": 4.0}
        # A 1-point stop asks for 1,000 units; the 4x cap allows 40.
        self.assertAlmostEqual(
            opt._quantity(10_000.0, 1_000.0, 1.0, sizing, 0.0), 40.0)

    def test_vol_target_lowers_size_as_volatility_rises(self):
        sizing = {"mode": "vol_target", "vol_target_annual": 0.6, "cap": 4.0}
        calm = opt._quantity(100_000.0, 1_000.0, 10.0, sizing, 0.02)
        wild = opt._quantity(100_000.0, 1_000.0, 10.0, sizing, 0.08)
        self.assertLess(wild, calm)

    def test_fixed_fraction_ignores_risk(self):
        sizing = {"mode": "fixed_fraction", "fraction": 1.0}
        self.assertAlmostEqual(
            opt._quantity(10_000.0, 1_000.0, 1.0, sizing, 0.0),
            opt._quantity(10_000.0, 1_000.0, 99.0, sizing, 0.0))


class WindowTests(unittest.TestCase):
    def test_in_sample_and_out_of_sample_do_not_overlap(self):
        self.assertEqual(opt._epoch(opt.IS_END), opt._epoch(opt.OOS_START))

    def test_split_is_half_open(self):
        edge = opt._epoch(opt.IS_END)
        trades = [{"ts": edge - 1}, {"ts": edge}, {"ts": edge + 1}]
        in_sample = opt._split(trades, opt._epoch(opt.IS_START), edge)
        out = opt._split(trades, edge, opt._epoch(opt.OOS_END))
        self.assertEqual(len(in_sample), 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(in_sample) + len(out), len(trades))


if __name__ == "__main__":
    unittest.main()
