import unittest

from sandbox.research import commodity_families_research as study
from sandbox.research import combined_book


class CommodityFamilyMetadataTests(unittest.TestCase):
    def test_combined_summary_uses_equity_carried_into_subwindow(self):
        run = {
            "curve": [(100, 110.0), (200, 120.0), (300, 90.0)],
            "trades": [],
        }
        result = combined_book.summarise(run, 100.0, 200, 400)
        self.assertEqual(result["return_pct"], -18.18)
        self.assertEqual(result["max_dd_pct"], 25.0)

    def test_mt5_multiplier_is_tick_value_over_tick_size(self):
        """Every registered instrument, not a subset.

        Asserting a fixed dict against the whole registry is deliberate: it
        fails when an instrument is added without a checked multiplier, which is
        the mistake worth catching. The USD ones below were re-verified against
        the live terminal on 2026-08-12 and against realised P&L on nine min-lot
        round trips.
        """
        expected = {
            "xagusd": 5000.0,
            "xcuusd": 1.0,
            "xngusd": 10000.0,
            "xpdusd": 100.0,
            "xptusd": 100.0,
            "xalusd": 1.0,
            "xniusd": 1.0,
            "xznusd": 1.0,
            "ukoil": 1000.0,
            # Metal crosses: tick_value is quoted in the CROSS currency, so
            # these are not round numbers the way the USD pairs are.
            "xauaud": 70.6,
            "xaueur": 115.5,
            "xaugbp": 134.9,
            "xagaud": 3531.1,
            "xageur": 5775.5,
            "xaggbp": 6744.2,
        }
        actual = {s: round(c["multiplier"], 1) for s, c in study.INSTRUMENTS.items()}
        self.assertEqual(expected, actual)

    def test_point_two_pips_is_instrument_specific(self):
        """The 0.2-pip charge now lives in SLIPPAGE_PIPS, not SPREAD_PIPS.

        `spread_price` reads the quoted spread, which is 0.0 on this
        zero-spread account, so the pip-scaled figure is asserted through the
        slippage knob that actually carries it.
        """
        for symbol, expected in (("xngusd", 0.00002), ("xagusd", 0.002),
                                 ("xptusd", 0.002)):
            cfg = study.INSTRUMENTS[symbol]
            self.assertAlmostEqual(study.spread_price(cfg), 0.0)
            self.assertAlmostEqual(
                study.spread_price(cfg, study.SLIPPAGE_PIPS), expected)

    def test_cost_price_adds_commission_to_slippage(self):
        """Commission is USD per lot and reaches price units via `multiplier`.

        XNGUSD's $70/lot over a 10,000 multiplier is 0.007 in price -- 350x the
        0.00002 the pip charge contributes, which is why omitting it mattered.
        """
        cfg = study.INSTRUMENTS["xngusd"]
        self.assertEqual(cfg["commission_per_lot"], 70.0)
        self.assertAlmostEqual(study.cost_price(cfg), 0.00002 + 0.007)
        # XALUSD: $4/lot over a multiplier of 1.
        xal = study.INSTRUMENTS["xalusd"]
        self.assertAlmostEqual(study.cost_price(xal), 0.2 * 0.01 + 4.0)

    def test_unmeasured_instruments_are_visibly_free(self):
        """Closed markets kept 0.0 rather than a guessed number."""
        for symbol in ("xcuusd", "xniusd", "xznusd"):
            cfg = study.INSTRUMENTS[symbol]
            self.assertEqual(cfg["commission_per_lot"], 0.0)
            self.assertIn("unmeasured", cfg["warning"])

    def test_available_in_sample_years(self):
        self.assertEqual(study.is_years("xagusd"), (2020, 2021, 2022, 2023, 2024))
        self.assertEqual(study.is_years("xpdusd"), (2022, 2023, 2024))
        self.assertEqual(study.is_years("xalusd"), (2023, 2024))
        self.assertEqual(study.min_positive_years("xalusd"), 2)

    def test_lot_rounding_uses_multiplier_and_margin(self):
        cfg = study.INSTRUMENTS["xngusd"]
        ctx = {"cfg": cfg, "vol_target": 0.5}
        # $15 risk / ($0.10 stop * $10,000 per price unit) = 0.015 lots,
        # floored to the broker's 0.01 lot step.  Margin allows that minimum.
        self.assertEqual(study.quantity(1000.0, 3.0, 0.1, 0.5, ctx), 0.01)

    def test_xcu_unit_mismatch_is_explicit(self):
        self.assertIn("differs", study.INSTRUMENTS["xcuusd"]["warning"])

    def test_combined_book_converts_lots_to_usd_per_lot(self):
        trades = [{"entry_ts": 1, "exit_ts": 2, "points": 1.5,
                   "quantity": 0.2, "pnl": 30.0}]
        row = combined_book.commodity_order_rows(
            "xpdusd", "momentum", trades, 1000.0)[0]
        self.assertEqual(row[:4], ("xpdusd:momentum", 1, 2, 150.0))
        self.assertAlmostEqual(row[4], 0.0002)
        self.assertEqual(row[5], 0.01)

    def test_combined_book_converts_drift_vwap_to_symbolic_order(self):
        trades = [{"entry_ts": 1, "exit_ts": 2, "entry_price": 20_000.0,
                   "net_points": 39.8, "quantity": 99.0}]
        row = combined_book.drift_vwap_order_rows(trades)[0]
        self.assertEqual(row[:4], ("nq:drift_vwap", 1, 2, 39.8))
        self.assertAlmostEqual(row[4], 0.01 / 80.0)
        self.assertEqual(row[5], 0.01)

    def test_combined_book_pins_requested_six_cells(self):
        self.assertEqual(combined_book.INITIAL, 400.0)
        self.assertEqual(combined_book.NQ_SLEEVES, {
            "ofi": "Deep OFI Momentum",
            "hdr": "Hourly Delta Reversal",
        })
        self.assertEqual(combined_book.DRIFT_VWAP_SLEEVE, "nq:drift_vwap")
        self.assertEqual(combined_book.DRIFT_VWAP_RISK, 0.01)
        # `nq:hdr` stands down at 0.0 and `aus200:momentum` runs at 0.45. Both
        # are deliberate risk decisions, not tuning: hdr produced 87% of the
        # book's worst drawdown from two winning trades, and aus200:momentum was
        # brought to parity with the rest of the book's per-sleeve drawdown.
        # Pinned here so any one of them silently reverting fails a test.
        #
        # The six live values are `sleeve weight x virtual-equity multiplier`;
        # the multiplier is what lifts a request over the broker's minimum lot
        # on a $400 account. These must equal `CANONICAL_STRATEGIES` and the
        # `*_EQUITY_MULTIPLIER` constants in `live/portfolio.rs` -- backtest and
        # live are one decision recorded twice.
        self.assertEqual(combined_book.SLEEVE_SCALE, {
            "nq:ofi": 1.4375,          # $1,000 shown: 0.575 x 2.5
            "nq:drift_vwap": 1.25,     # $500 shown
            "hk50:gap": 1.5,           # $600 shown
            "ethusd:vwap": 2.0,        # $800 shown
            "xngusd:donchian": 2.5,    # $1,000 shown
            "aus200:momentum": 0.675,  # $600 shown: 0.45 x 1.5
            "xalusd:pdr": 1.0,         # $400 shown
            "nq:hdr": 0.0,
            "ethusd:maroy": 0.0,
            "ethusd:drift_vwap": 0.0,
            "xalusd:ma_cross": 0.0,
            "xalusd:overnight": 0.0,
            "aus200:pdr": 0.0,
            "fr40:gap": 0.0,
            "xngusd:gap": 0.0,
            "xngusd:zscore": 0.0,
            "ethbtc:orb": 0.0,
            "ethbtc:gap": 0.0,
        })

    def test_live_and_backtest_sleeve_sets_agree(self):
        """The six live sleeves are exactly the six with a non-zero scale.

        `CANONICAL_STRATEGIES` in `live/portfolio.rs` uses underscores and this
        module uses `market:rule`, so the two lists cannot be compared directly
        and drift silently. This pins the Python half by name.
        """
        live = {"nq:ofi", "nq:drift_vwap", "ethusd:vwap", "xalusd:pdr",
                "xngusd:donchian", "aus200:momentum", "hk50:gap"}
        trading = {k for k, v in combined_book.SLEEVE_SCALE.items() if v > 0.0}
        self.assertEqual(trading, live)
        self.assertFalse(combined_book.MAROY_ENABLED)
        self.assertEqual(combined_book.CRYPTO_SLEEVES, (
            ("ethusd", "vwap"), ("ethbtc", "orb"), ("ethbtc", "gap")))
        self.assertEqual(combined_book.COMMODITY_SLEEVES, (
            ("xalusd", "ma_cross"), ("xngusd", "donchian"),
            ("xalusd", "pdr"), ("xalusd", "overnight"),
            ("xngusd", "gap"), ("xngusd", "zscore")))
        self.assertEqual(combined_book.SIZING_EQUITY_CAP, {
            "xngusd:gap": 400.0,
            "xngusd:zscore": 400.0,
        })


if __name__ == "__main__":
    unittest.main()
