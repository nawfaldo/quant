"""Focused tests for the YouTube Drift VWAP Pullback replica."""

import unittest

from sandbox.research import drift_vwap_pullback as drift


DAY = 20_000 * 86_400


def ts(hour, minute):
    return DAY + (hour * 60 + minute) * 60


def bar(hour, minute, open_price, high=None, low=None, close=None, volume=1.0):
    close = open_price if close is None else close
    high = max(open_price, close) if high is None else high
    low = min(open_price, close) if low is None else low
    return drift.Bar(ts(hour, minute), open_price, high, low, close, volume)


class AggregationTests(unittest.TestCase):
    def test_aggregate_builds_clock_aligned_ohlcv(self):
        rows = [
            drift.Bar(ts(9, 30) + i * 60, 100 + i, 102 + i, 99 - i,
                      101 + i, i + 1)
            for i in range(5)
        ]
        result = drift.aggregate(rows, 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ts, ts(9, 30))
        self.assertEqual(result[0].open, 100)
        self.assertEqual(result[0].high, 106)
        self.assertEqual(result[0].low, 95)
        self.assertEqual(result[0].close, 105)
        self.assertEqual(result[0].volume, 15)

    def test_rth_filter_excludes_premarket(self):
        result = drift.aggregate([
            bar(9, 25, 100), bar(9, 30, 101), bar(15, 55, 102), bar(16, 0, 103)
        ], 5, rth_only=True)
        self.assertEqual([row.ts for row in result], [ts(9, 30), ts(15, 55)])


class TrendTests(unittest.TestCase):
    def test_long_state_uses_closed_15_minute_data(self):
        bars = [
            bar(8, 45, 100, close=100),
            bar(9, 0, 100, close=100),
            bar(9, 15, 100, close=100),
            bar(9, 30, 100, high=101, low=99, close=100, volume=10),
            bar(9, 45, 102, high=104, low=101, close=104, volume=10),
        ]
        states = drift.trend_states(bars)
        # The 09:45-10:00 bar cannot affect a 5m decision before 10:00.
        self.assertEqual(states[ts(10, 0)], "long")
        self.assertIsNone(states[ts(9, 45)])

    def test_short_state_is_symmetric(self):
        bars = [
            bar(8, 45, 100, close=100),
            bar(9, 0, 100, close=100),
            bar(9, 15, 100, close=100),
            bar(9, 30, 100, high=101, low=99, close=100, volume=10),
            bar(9, 45, 98, high=99, low=96, close=96, volume=10),
        ]
        self.assertEqual(drift.trend_states(bars)[ts(10, 0)], "short")


class EntryAndCostTests(unittest.TestCase):
    def test_pullback_enters_at_next_open_and_pays_entry_spread(self):
        bars = [
            bar(10, 25, 101, high=102, low=99, close=100),  # red trigger
            bar(10, 30, 100, high=100, low=100, close=100), # entry; no same-bar exit
            bar(10, 35, 100, high=141, low=100, close=140), # target at 140
            bar(15, 55, 140),
        ]
        result = drift.backtest(bars, {ts(10, 30): "long"})
        trade = result["trades"][0]
        self.assertEqual(trade["entry_ts"], ts(10, 30))
        self.assertEqual(trade["entry_price"], 100)
        self.assertEqual(trade["reason"], "target")
        self.assertAlmostEqual(trade["quantity"], 0.06)
        # Cost is spread + slippage + commission, all billed at entry. The
        # spread is 0.0 on this zero-spread account, so what is actually
        # subtracted is the 0.2 slippage allowance plus $1.40/lot of commission.
        cost = drift.Account().entry_cost
        self.assertAlmostEqual(cost, 0.0 + 0.2 + 1.40)
        self.assertAlmostEqual(trade["net_points"], 40 - cost)
        self.assertAlmostEqual(trade["pnl"], (40 - cost) * 0.06)

    def test_first_means_one_entry_per_contiguous_pullback(self):
        bars = [
            bar(10, 25, 102, close=101),  # trigger
            bar(10, 30, 100, close=99),   # entry, still red
            bar(10, 35, 100, high=141, close=99),  # win, still red
            bar(10, 40, 100, close=99),   # must not retrigger same red run
            bar(15, 55, 100),
        ]
        result = drift.backtest(bars, {ts(10, 30): "long"})
        self.assertEqual(len(result["trades"]), 1)


class SizingTests(unittest.TestCase):
    def test_equity_risk_size_compounds_with_live_equity(self):
        sizing = drift.Sizing(mode="equity_risk", risk=0.01)
        small = drift._quantity(1_000, 1_000, drift.Account(), 80, sizing, 0.01)
        large = drift._quantity(2_000, 1_000, drift.Account(), 80, sizing, 0.01)
        self.assertLessEqual(
            abs(large - small * 2), drift.FOREX_QUANTITY_STEP + 1e-12
        )

    def test_vol_target_reduces_size_when_realized_volatility_rises(self):
        sizing = drift.Sizing(mode="vol_target", vol_target_annual=0.20)
        calm = drift._quantity(10_000, 1_000, drift.Account(), 80, sizing, 0.01)
        wild = drift._quantity(10_000, 1_000, drift.Account(), 80, sizing, 0.04)
        self.assertGreater(calm, wild)

    def test_vol_target_refuses_size_without_prior_history(self):
        sizing = drift.Sizing(mode="vol_target", vol_target_annual=0.20)
        self.assertEqual(
            drift._quantity(10_000, 1_000, drift.Account(), 80, sizing, None), 0.0
        )

    def test_session_volatility_does_not_read_current_close(self):
        rows = [
            drift.Bar(DAY + i * 86_400 + 15 * 3_600, 100, 101, 99,
                      100 * (1.01 ** i), 1)
            for i in range(25)
        ]
        changed = list(rows)
        last = changed[-1]
        changed[-1] = drift.Bar(
            last.ts, last.open, last.high, last.low, last.close * 10, last.volume
        )
        base_vol = drift.session_volatility(rows)
        changed_vol = drift.session_volatility(changed)
        self.assertEqual(base_vol[rows[-1].ts // 86_400],
                         changed_vol[rows[-1].ts // 86_400])

    def test_is_and_oos_windows_are_half_open_and_adjacent(self):
        self.assertEqual(drift.IS_TO, drift.OOS_FROM)
        self.assertEqual(drift.metrics.split_ts(drift.IS_TO),
                         drift.metrics.split_ts(drift.OOS_FROM))


class GuardrailTests(unittest.TestCase):
    def test_two_daily_losses_stop_new_entries(self):
        bars = [
            bar(10, 25, 101, close=100),  # pullback 1
            bar(10, 30, 100),             # entry 1
            bar(10, 35, 100, high=101, low=19, close=101),  # stop; reset pullback
            bar(10, 40, 101, close=100),  # pullback 2
            bar(10, 45, 100),             # entry 2
            bar(10, 50, 100, high=101, low=19, close=101),  # stop; reset pullback
            bar(10, 55, 101, close=100),  # would be pullback 3
            bar(11, 0, 100, high=141, close=140),
            bar(15, 55, 140),
        ]
        result = drift.backtest(bars, {ts(10, 30): "long"})
        self.assertEqual(len(result["trades"]), 2)
        self.assertTrue(all(trade["reason"] == "stop" for trade in result["trades"]))

    def test_maximum_four_trades(self):
        bars = []
        minute = 25
        hour = 10
        for _ in range(5):
            bars.extend([
                bar(hour, minute, 101, close=100),
                bar(hour, minute + 5, 100),
                bar(hour, minute + 10, 100, high=141, low=100, close=101),
            ])
            minute += 15
            if minute >= 60:
                hour += minute // 60
                minute %= 60
        bars.append(bar(15, 55, 100))
        result = drift.backtest(bars, {ts(10, 30): "long"})
        self.assertEqual(len(result["trades"]), 4)

    def test_no_entry_after_1530(self):
        bars = [
            bar(15, 30, 101, close=100),  # would enter at 15:35: forbidden
            bar(15, 35, 100, high=141),
            bar(15, 55, 100),
        ]
        result = drift.backtest(bars, {ts(15, 35): "long"})
        self.assertEqual(result["trades"], [])

    def test_open_position_flattens_at_1555_open(self):
        bars = [
            bar(15, 25, 101, close=100),
            bar(15, 30, 100),
            bar(15, 35, 100),
            bar(15, 55, 110, high=150, low=50, close=120),
        ]
        result = drift.backtest(bars, {ts(15, 30): "long"})
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "session_close")
        self.assertEqual(trade["exit_ts"], ts(15, 55))
        self.assertEqual(trade["exit_price"], 110)


if __name__ == "__main__":
    unittest.main()
