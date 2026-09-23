"""Mark-to-market marking in `combined_book`, on hand-computable cases.

The MTM pass changed the book's headline risk figure from 15.88% to 28.43%, so
its arithmetic is pinned here rather than trusted. Price series are injected
directly into the module's cache, which keeps these tests free of QuestDB.
"""
import unittest

from sandbox.research import combined_book as cb


def inject(market, rows):
    """Register `[(ts, open, close)]` as `market`'s cached minute series.

    The cache is keyed by `(market, window_start, window_end)`, so the key has
    to be built from the module's current `FULL` exactly as `price_series` does.
    """
    key = (market, cb.FULL[0], cb.FULL[1])
    cb._SERIES[key] = ([r[0] for r in rows], [r[1] for r in rows],
                       [r[2] for r in rows])


class BrokerMinimumTests(unittest.TestCase):
    """`combine` must refuse orders below the broker's minimum ORDER size.

    The quantity STEP says which sizes are legal; `volume_min` says how small an
    order may be. Treating the step as the floor is what let the book fill
    ETHBTC orders 100x under the smallest one the broker accepts.
    """

    def order(self, sleeve, units, step=0.01):
        # (sleeve, entry_ts, exit_ts, points_per_unit, units_per_dollar, step)
        return (sleeve, 1_000, 2_000, 1.0, units, step, None)

    def run_one(self, sleeve, units, floor):
        return cb.combine(
            [self.order(sleeve, units)],
            initial=1_000.0,
            window=("1970-01-01", "2000-01-01"),
            scale={}, sizing_equity_cap={}, balance_gates={},
            initial_balance_gates={}, own_pnl_throttle={},
            lot_floor=floor,
        )

    def test_order_under_the_minimum_is_dropped_and_counted(self):
        # 0.005 units per dollar x $1,000 = 0.005 ETH, under ETHBTC's 1.0.
        run = self.run_one("ethbtc:orb", 0.000_005, {"ethbtc": 1.0})
        self.assertEqual(run["trades"], [])
        self.assertEqual(run["below_broker_minimum"], {"ethbtc:orb": 1})

    def test_order_at_the_minimum_is_kept(self):
        # 0.0012 x 1,000 = 1.2 ETH, above ETHBTC's 1.0 floor.
        run = self.run_one("ethbtc:orb", 0.0012, {"ethbtc": 1.0})
        self.assertEqual(len(run["trades"]), 1)
        self.assertEqual(run["below_broker_minimum"], {})

    def test_the_step_still_applies_when_no_floor_is_registered(self):
        """An unlisted market falls back to the step, the old behaviour."""
        run = self.run_one("unknown:rule", 0.000_001, {})
        self.assertEqual(run["trades"], [])
        # 0.000001 x 1,000 = 0.001, under the 0.01 step.
        self.assertEqual(run["below_broker_minimum"], {"unknown:rule": 1})

    def test_registered_floors_match_the_live_terminal(self):
        """`volume_min` x `contract_size`, read from MT5 on 2026-08-12."""
        self.assertEqual(cb.LOT_FLOOR_UNITS["ethbtc"], 1.0)    # 0.01 lot x 100
        self.assertEqual(cb.LOT_FLOOR_UNITS["ethusd"], 0.10)   # 0.10 lot x 1
        self.assertEqual(cb.LOT_FLOOR_UNITS["aus200"], 0.06)
        self.assertEqual(cb.LOT_FLOOR_UNITS["nq"], 0.05)


class SideSignTests(unittest.TestCase):
    def test_both_conventions_normalise(self):
        self.assertEqual(cb.side_sign("long"), 1)
        self.assertEqual(cb.side_sign("short"), -1)
        self.assertEqual(cb.side_sign(1), 1)
        self.assertEqual(cb.side_sign(-1), -1)

    def test_missing_side_propagates(self):
        self.assertIsNone(cb.side_sign(None))


class OpenValueTests(unittest.TestCase):
    def setUp(self):
        # Price walks 100 -> 103 over four minutes.
        inject("fake", [(60, 100.0, 100.0), (120, 100.0, 101.0),
                        (180, 101.0, 102.0), (240, 102.0, 103.0)])

    def tearDown(self):
        cb._SERIES.pop(("fake", cb.FULL[0], cb.FULL[1]), None)

    def test_the_cache_is_keyed_by_window_not_by_market(self):
        """Two windows in one process must not share a market's series.

        Keyed on market alone, the first window to load a market won and every
        later one silently reused it -- which moved a 2020-2026 run's
        mark-to-market drawdown by 0.43 points when a 2025-2026 run had warmed
        the cache first, while leaving return and trade count identical.
        """
        saved = cb.FULL
        try:
            cb.FULL = ("2020-01-01", "2021-01-01")
            inject("fake2", [(60, 1.0, 1.0)])
            cb.FULL = ("2025-01-01", "2026-01-01")
            inject("fake2", [(60, 2.0, 2.0)])
            self.assertEqual(cb.price_series("fake2")[1], [2.0])
            cb.FULL = ("2020-01-01", "2021-01-01")
            self.assertEqual(cb.price_series("fake2")[1], [1.0])
        finally:
            for window in (("2020-01-01", "2021-01-01"),
                           ("2025-01-01", "2026-01-01")):
                cb._SERIES.pop(("fake2", window[0], window[1]), None)
            cb.FULL = saved

    def test_long_marks_each_minute_it_is_open(self):
        trade = {"sleeve": "x", "entry_ts": 60, "exit_ts": 240, "pnl": 6.0,
                 "quantity": 2.0, "mark": ("fake", 1, 100.0, 1.0)}
        # Open across 60, 120, 180; the exit minute is excluded because the
        # trade's realised P&L is booked there instead.
        self.assertEqual(cb.open_value([trade]), {60: 0.0, 120: 2.0, 180: 4.0})

    def test_short_marks_with_the_opposite_sign(self):
        trade = {"sleeve": "x", "entry_ts": 60, "exit_ts": 240, "pnl": -6.0,
                 "quantity": 2.0, "mark": ("fake", -1, 100.0, 1.0)}
        self.assertEqual(cb.open_value([trade]), {60: 0.0, 120: -2.0, 180: -4.0})

    def test_point_value_scales_the_mark(self):
        trade = {"sleeve": "x", "entry_ts": 60, "exit_ts": 240, "pnl": 0.0,
                 "quantity": 1.0, "mark": ("fake", 1, 100.0, 10.0)}
        self.assertEqual(cb.open_value([trade])[180], 20.0)

    def test_unmarked_and_unsized_trades_contribute_nothing(self):
        rows = [{"sleeve": "x", "entry_ts": 60, "exit_ts": 240, "pnl": 1.0,
                 "quantity": 2.0, "mark": None},
                {"sleeve": "x", "entry_ts": 60, "exit_ts": 240, "pnl": 1.0,
                 "quantity": 0.0, "mark": ("fake", 1, 100.0, 1.0)}]
        self.assertEqual(cb.open_value(rows), {})

    def test_concurrent_positions_sum(self):
        rows = [{"sleeve": "a", "entry_ts": 60, "exit_ts": 240, "pnl": 0.0,
                 "quantity": 1.0, "mark": ("fake", 1, 100.0, 1.0)},
                {"sleeve": "b", "entry_ts": 60, "exit_ts": 240, "pnl": 0.0,
                 "quantity": 3.0, "mark": ("fake", 1, 100.0, 1.0)}]
        self.assertEqual(cb.open_value(rows)[180], 8.0)


class MtmDrawdownTests(unittest.TestCase):
    def test_open_excursion_deepens_a_flat_realised_curve(self):
        # Realised equity never falls: it sits at 100 and books +10 at the end.
        closes = [(240, 110.0)]
        # While open, the position is 20 underwater at its worst.
        opens = {60: 0.0, 120: -20.0, 180: -5.0}
        percent, dollars, _peak_at, _trough_at = cb.mtm_drawdown(
            closes, opens, 100.0, 0, 1_000)
        self.assertEqual(dollars, 20.0)
        self.assertEqual(percent, 20.0)

    def test_closed_only_curve_matches_the_realised_drawdown(self):
        closes = [(60, 80.0), (120, 100.0)]
        percent, dollars, _peak_at, _trough_at = cb.mtm_drawdown(
            closes, {}, 100.0, 0, 1_000)
        self.assertEqual(dollars, 20.0)
        self.assertEqual(percent, 20.0)

    def test_window_start_inherits_the_equity_standing_at_lo(self):
        # The 60s close is before the window; the window must not treat the
        # later, lower equity as a fresh peak it never earned.
        closes = [(60, 200.0), (120, 150.0)]
        percent, _dollars, _peak_at, _trough_at = cb.mtm_drawdown(
            closes, {}, 100.0, 100, 1_000)
        self.assertEqual(percent, 25.0)


if __name__ == "__main__":
    unittest.main()
