"""Focused tests for the Zarattini & Aziz 5-minute ORB port."""

import unittest

from sandbox.research import zarattini_orb as orb


def bar(minute, open_price, high, low, close, volume=1.0):
    """One session bar; `minute` is an offset, the absolute epoch is irrelevant."""
    return (minute * 60, open_price, high, low, close, volume)


def session(bars, span=None):
    """A session from a sparse {offset: bar} map, padded to `span`."""
    span = span or (max(bars) + 1)
    padded = [None] * span
    for offset, value in bars.items():
        padded[offset] = value
    present = [b for b in padded if b is not None]
    return {"day": 0, "ts": present[0][0], "bars": padded, "span": span,
            "open": present[0][1], "close": present[-1][4]}


def flat_session(offsets):
    """A session where every listed offset holds a quiet bar at 100.0."""
    return {offset: bar(offset, 100.0, 100.0, 100.0, 100.0) for offset in offsets}


class OpeningRangeTests(unittest.TestCase):
    def test_range_spans_the_first_five_minutes_only(self):
        bars = [bar(0, 100.0, 105.0, 99.0, 104.0),
                bar(1, 104.0, 106.0, 98.0, 102.0),
                bar(2, 102.0, 102.0, 102.0, 102.0),
                bar(3, 102.0, 102.0, 102.0, 102.0),
                bar(4, 102.0, 102.0, 102.0, 103.0),
                bar(5, 103.0, 999.0, 1.0, 103.0)]   # must not affect the range
        self.assertEqual(orb.opening_range(bars), (100.0, 106.0, 98.0, 103.0))

    def test_gap_inside_the_range_shrinks_it_rather_than_borrowing_later_bars(self):
        bars = [bar(0, 100.0, 105.0, 99.0, 104.0), None, None, None, None,
                bar(5, 200.0, 300.0, 100.0, 250.0)]
        self.assertEqual(orb.opening_range(bars), (100.0, 105.0, 99.0, 104.0))

    def test_missing_range_returns_none(self):
        self.assertIsNone(orb.opening_range([None] * 5))


class DirectionTests(unittest.TestCase):
    def _trades(self, first, rest_offsets=(5, 6, 7)):
        bars = {0: first}
        bars.update(flat_session(rest_offsets))
        return orb.run([session(bars)], "rth", sizing="flat")["trades"]

    def test_up_candle_goes_long(self):
        trades = self._trades(bar(0, 100.0, 110.0, 90.0, 108.0))
        self.assertEqual(trades[0]["side"], "long")

    def test_down_candle_goes_short(self):
        trades = self._trades(bar(0, 100.0, 110.0, 90.0, 92.0))
        self.assertEqual(trades[0]["side"], "short")

    def test_doji_is_skipped_entirely(self):
        self.assertEqual(self._trades(bar(0, 100.0, 110.0, 90.0, 100.0)), [])


class EntryAndStopTests(unittest.TestCase):
    def test_entry_is_the_open_of_the_sixth_minute(self):
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0)}
        bars.update({5: bar(5, 107.0, 107.0, 107.0, 107.0)})
        bars.update(flat_session((6, 7)))
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertAlmostEqual(trade["entry_price"], 107.0)

    def test_long_stop_is_the_range_low(self):
        # Entry 107, range low 90, so R = 17 and a stop is exactly -1R.
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                5: bar(5, 107.0, 107.0, 89.0, 89.0),
                6: bar(6, 89.0, 89.0, 89.0, 89.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "stop")
        self.assertAlmostEqual(trade["points"], -17.0)
        self.assertAlmostEqual(trade["r_multiple"], -1.0)

    def test_short_stop_is_the_range_high(self):
        bars = {0: bar(0, 100.0, 110.0, 90.0, 92.0),
                5: bar(5, 93.0, 111.0, 93.0, 111.0),
                6: bar(6, 111.0, 111.0, 111.0, 111.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "stop")
        self.assertAlmostEqual(trade["r_multiple"], -1.0)

    def test_entry_bar_itself_can_stop_out(self):
        """The fill is the entry bar's open, so the rest of that minute is live."""
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                5: bar(5, 107.0, 107.0, 80.0, 85.0),
                6: bar(6, 85.0, 85.0, 85.0, 85.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "stop")


class TargetTests(unittest.TestCase):
    def test_target_is_ten_r(self):
        # Entry 107, R = 17, so the target is 107 + 170 = 277.
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                5: bar(5, 107.0, 107.0, 107.0, 107.0),
                6: bar(6, 107.0, 280.0, 107.0, 280.0),
                7: bar(7, 280.0, 280.0, 280.0, 280.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "target")
        self.assertAlmostEqual(trade["points"], 170.0)
        self.assertAlmostEqual(trade["r_multiple"], 10.0)

    def test_stop_wins_when_one_bar_touches_both(self):
        """OHLC cannot order them; awarding the 10R would invent the edge."""
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                5: bar(5, 107.0, 107.0, 107.0, 107.0),
                6: bar(6, 107.0, 300.0, 80.0, 107.0),
                7: bar(7, 107.0, 107.0, 107.0, 107.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "stop")

    def test_unresolved_position_liquidates_at_the_session_close(self):
        bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                5: bar(5, 107.0, 107.0, 107.0, 107.0),
                6: bar(6, 107.0, 112.0, 106.0, 111.0)}
        trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
        self.assertEqual(trade["reason"], "session_close")
        self.assertAlmostEqual(trade["points"], 4.0)


class SizingTests(unittest.TestCase):
    def test_risk_leg_binds_when_the_stop_is_wide(self):
        # 1% of $100,000 is $1,000; over a $100 stop that is 10 units, and 4x
        # leverage would have allowed 40, so the risk leg is the binding one.
        self.assertAlmostEqual(
            orb._quantity(100_000.0, 10_000.0, 100.0, "equity"), 10.0)

    def test_leverage_leg_binds_when_the_stop_is_tight(self):
        # 1% over a $1 stop asks for 1,000 units; 4x caps it at 40.
        self.assertAlmostEqual(
            orb._quantity(100_000.0, 10_000.0, 1.0, "equity"), 40.0)

    def test_size_rounds_down_to_a_whole_lot(self):
        # 4x on $1,000 at $60,000 is 0.0667 BTC, which floors to 0.06.
        self.assertAlmostEqual(orb._quantity(1_000.0, 60_000.0, 1.0, "equity"), 0.06)

    def test_non_positive_risk_takes_no_position(self):
        self.assertAlmostEqual(orb._quantity(1_000.0, 60_000.0, 0.0, "equity"), 0.0)

    def test_flat_sizing_ignores_equity_and_risk(self):
        self.assertAlmostEqual(orb._quantity(1.0, 60_000.0, 999.0, "flat"),
                               orb.QUANTITY_STEP)


class CostTests(unittest.TestCase):
    def test_spread_is_charged_wholly_at_entry_on_both_sides(self):
        self.assertAlmostEqual(orb._pnl("long", 100.0, 110.0, 1.0), 10.0 - orb.SPREAD)
        self.assertAlmostEqual(orb._pnl("short", 100.0, 90.0, 1.0), 10.0 - orb.SPREAD)

    def test_cost_bps_replaces_the_fixed_spread(self):
        orb.COST_BPS = 10.0
        try:
            self.assertAlmostEqual(orb._cost(20_000.0), 20.0)
        finally:
            orb.COST_BPS = 0.0

    def test_points_are_gross_of_cost(self):
        """`points` must not move with the spread, or the edge diagnostic lies."""
        orb.SPREAD = 50.0
        try:
            bars = {0: bar(0, 100.0, 110.0, 90.0, 108.0),
                    5: bar(5, 107.0, 107.0, 107.0, 107.0),
                    6: bar(6, 107.0, 112.0, 106.0, 111.0)}
            trade = orb.run([session(bars)], "rth", sizing="flat")["trades"][0]
            self.assertAlmostEqual(trade["points"], 4.0)
        finally:
            orb.SPREAD = 0.2


class EdgeTests(unittest.TestCase):
    def test_gross_edge_is_scaled_by_entry_price(self):
        trades = [{"points": 1.0, "entry_price": 10_000.0, "r_multiple": 1.0},
                  {"points": 3.0, "entry_price": 10_000.0, "r_multiple": 1.0}]
        self.assertAlmostEqual(
            orb._gross_edge(trades)["gross_bps_per_trade"], 2.0)

    def test_gross_edge_is_empty_without_trades(self):
        self.assertIsNone(orb._gross_edge([])["gross_bps_per_trade"])


if __name__ == "__main__":
    unittest.main()
