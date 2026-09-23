"""Focused tests for the Zarattini & Aziz VWAP trend trading port."""

import unittest

from sandbox.research import zarattini_vwap_trend as vwap


def bar(minute, open_price, high, low, close, volume=1.0):
    """One session bar; `minute` is an offset, the absolute epoch is irrelevant."""
    return (minute * 60, open_price, high, low, close, volume)


def session(bars):
    return {"day": 0, "ts": bars[0][0], "bars": bars,
            "open": bars[0][1], "close": bars[-1][4]}


class VwapSeriesTests(unittest.TestCase):
    def test_first_level_is_the_first_bar_typical_price(self):
        levels = vwap.vwap_series([bar(0, 100.0, 106.0, 100.0, 104.0)])
        self.assertAlmostEqual(levels[0], (106.0 + 100.0 + 104.0) / 3.0)

    def test_level_is_volume_weighted_and_includes_the_current_bar(self):
        bars = [bar(0, 100.0, 100.0, 100.0, 100.0, volume=1.0),
                bar(1, 100.0, 200.0, 200.0, 200.0, volume=3.0)]
        levels = vwap.vwap_series(bars)
        self.assertAlmostEqual(levels[0], 100.0)
        self.assertAlmostEqual(levels[1], (100.0 * 1 + 200.0 * 3) / 4)

    def test_zero_volume_bar_falls_back_to_typical_price(self):
        levels = vwap.vwap_series([bar(0, 10.0, 12.0, 8.0, 10.0, volume=0.0)])
        self.assertAlmostEqual(levels[0], 10.0)


class EntryTests(unittest.TestCase):
    """The side comes from the first bar's close versus VWAP, filled next bar."""

    def _first_trade(self, first_bar, fill="next_open"):
        bars = [first_bar,
                bar(1, 100.0, 100.0, 100.0, 100.0),
                bar(2, 100.0, 100.0, 100.0, 100.0)]
        result = vwap.run([session(bars)], "rth", fill=fill, sizing="flat")
        return result["trades"][0]

    def test_close_above_vwap_goes_long(self):
        # HLC/3 of (110, 90, 108) is 102.67, so the close is above VWAP.
        self.assertEqual(self._first_trade(bar(0, 100.0, 110.0, 90.0, 108.0))["side"],
                         "long")

    def test_close_below_vwap_goes_short(self):
        # HLC/3 of (110, 90, 92) is 97.33, so the close is below VWAP.
        self.assertEqual(self._first_trade(bar(0, 100.0, 110.0, 90.0, 92.0))["side"],
                         "short")

    def test_next_open_fill_uses_the_following_bar_open(self):
        trade = self._first_trade(bar(0, 100.0, 110.0, 90.0, 108.0))
        self.assertAlmostEqual(trade["entry_price"], 100.0)

    def test_close_fill_uses_the_deciding_bar_close(self):
        trade = self._first_trade(bar(0, 100.0, 110.0, 90.0, 108.0), fill="close")
        self.assertAlmostEqual(trade["entry_price"], 108.0)


class ExitTests(unittest.TestCase):
    def test_intrabar_cross_without_a_close_through_does_not_flip(self):
        """Section 3.5: only a *close* on the other side exits."""
        bars = [
            bar(0, 100.0, 110.0, 90.0, 108.0),           # long: close > VWAP
            bar(1, 108.0, 108.0, 108.0, 108.0),
            # Trades far below VWAP intrabar but closes back above it.
            bar(2, 108.0, 109.0, 50.0, 108.0, volume=0.0),
            bar(3, 108.0, 108.0, 108.0, 108.0),
        ]
        result = vwap.run([session(bars)], "rth", sizing="flat")
        self.assertEqual([t["reason"] for t in result["trades"]], ["session_close"])

    def test_close_through_vwap_flips_the_side(self):
        bars = [
            bar(0, 100.0, 110.0, 90.0, 108.0),           # long
            bar(1, 108.0, 108.0, 108.0, 108.0),
            bar(2, 108.0, 108.0, 60.0, 60.0),            # closes below VWAP
            bar(3, 60.0, 60.0, 60.0, 60.0),
            bar(4, 60.0, 60.0, 60.0, 60.0),
        ]
        result = vwap.run([session(bars)], "rth", sizing="flat")
        sides = [t["side"] for t in result["trades"]]
        self.assertEqual(sides, ["long", "short"])
        self.assertEqual([t["reason"] for t in result["trades"]],
                         ["vwap_flip", "session_close"])

    def test_position_is_flat_at_the_session_close(self):
        bars = [bar(0, 100.0, 110.0, 90.0, 108.0)] + [
            bar(i, 108.0, 108.0, 108.0, 108.0) for i in range(1, 5)]
        result = vwap.run([session(bars)], "rth", sizing="flat")
        self.assertEqual(result["trades"][-1]["reason"], "session_close")
        self.assertEqual(sum(t["quantity"] for t in result["trades"]
                             if t["side"] == "long"),
                         sum(t["quantity"] for t in result["trades"]))


class CostTests(unittest.TestCase):
    def test_spread_is_charged_wholly_at_entry_on_both_sides(self):
        self.assertAlmostEqual(vwap._pnl("long", 100.0, 110.0, 1.0),
                               10.0 - vwap.SPREAD)
        self.assertAlmostEqual(vwap._pnl("short", 100.0, 90.0, 1.0),
                               10.0 - vwap.SPREAD)

    def test_cost_bps_replaces_the_fixed_spread(self):
        vwap.COST_BPS = 10.0
        try:
            self.assertAlmostEqual(vwap._cost(20_000.0), 20.0)
        finally:
            vwap.COST_BPS = 0.0

    def test_points_are_gross_of_cost(self):
        """`points` must not move with the spread, or the edge diagnostic lies."""
        bars = [bar(0, 100.0, 110.0, 90.0, 108.0),
                bar(1, 100.0, 100.0, 100.0, 100.0),
                bar(2, 120.0, 120.0, 120.0, 120.0)]
        result = vwap.run([session(bars)], "rth", sizing="flat")
        self.assertAlmostEqual(result["trades"][0]["points"], 20.0)


class SizingTests(unittest.TestCase):
    def test_equity_sizing_rounds_down_to_a_whole_lot(self):
        # $1,000 at $60,000 buys 0.0167 BTC, which floors to a single 0.01 step.
        self.assertAlmostEqual(vwap._quantity(1_000.0, 60_000.0, 1.0, "equity"), 0.01)

    def test_equity_sizing_returns_nothing_below_one_step(self):
        self.assertAlmostEqual(vwap._quantity(100.0, 60_000.0, 1.0, "equity"), 0.0)

    def test_flat_sizing_ignores_equity(self):
        self.assertAlmostEqual(vwap._quantity(1.0, 60_000.0, 1.0, "flat"),
                               vwap.QUANTITY_STEP)


class EdgeTests(unittest.TestCase):
    def test_gross_edge_is_scaled_by_entry_price(self):
        trades = [{"points": 1.0, "entry_price": 10_000.0},
                  {"points": 3.0, "entry_price": 10_000.0}]
        edge = vwap._gross_edge(trades)
        self.assertAlmostEqual(edge["gross_bps_per_trade"], 2.0)

    def test_gross_edge_is_empty_without_trades(self):
        self.assertIsNone(vwap._gross_edge([])["gross_bps_per_trade"])


if __name__ == "__main__":
    unittest.main()
