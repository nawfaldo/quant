"""Causality and direction tests for NQ Large Print Continuation."""

import unittest

from sandbox.execution import LONG, SHORT
from sandbox.strategies.large_print_continuation import (
    LargePrintContinuation,
    candidate_rows,
)


DAY = 20_000 * 86_400


def bar(minute, open_, high, low, close):
    return [DAY + minute * 60, open_, high, low, close, 0.0, 1]


def print_(minute, second, side, price, size=120, imbalance=0.0, **overrides):
    row = {
        "source": "dbento",
        "event_ns": (DAY + minute * 60 + second) * 1_000_000_000,
        "sequence": 1,
        "side": side,
        "price": price,
        "size": size,
        "best_bid": price - 0.25 if side == "BUY" else price,
        "best_ask": price if side == "BUY" else price + 0.25,
        "book_imbalance": imbalance,
        "book_valid": True,
    }
    row.update(overrides)
    return row


class LargePrintContinuationTests(unittest.TestCase):
    def setUp(self):
        self.strategy = LargePrintContinuation()
        self.params = self.strategy.all_params()

    def candidates(self, bars, prints, **params):
        configured = dict(self.params)
        configured.update(params)
        sources = {int(row[0]): "dbento" for row in bars}
        return candidate_rows(bars, prints, sources, configured)

    def test_red_candle_with_large_buy_still_goes_long_next_minute(self):
        decision = bar(600, 105, 107, 102, 103)
        entry = bar(601, 103, 104, 102, 103)
        rows = self.candidates(
            [decision, entry], [print_(600, 30, "BUY", 106)]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], LONG)
        self.assertEqual(rows[0]["index"], 1)
        self.assertEqual(rows[0]["source"], "dbento")

    def test_green_candle_with_large_sell_still_goes_short(self):
        decision = bar(600, 100, 103, 98, 102)
        entry = bar(601, 102, 103, 101, 102)
        rows = self.candidates(
            [decision, entry], [print_(600, 30, "SELL", 99)]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], SHORT)

    def test_body_print_and_unbalanced_book_are_rejected(self):
        decision = bar(600, 100, 107, 98, 105)
        entry = bar(601, 105, 106, 104, 105)
        body = print_(600, 20, "BUY", 104)
        unbalanced = print_(600, 30, "BUY", 106, imbalance=0.21)
        self.assertEqual(self.candidates([decision, entry], [body]), [])
        self.assertEqual(self.candidates([decision, entry], [unbalanced]), [])

    def test_signal_requires_a_contiguous_next_minute(self):
        decision = bar(600, 100, 107, 98, 105)
        gap = bar(602, 105, 106, 104, 105)
        rows = self.candidates(
            [decision, gap], [print_(600, 30, "BUY", 106)]
        )
        self.assertEqual(rows, [])

    def test_monster_print_is_ignored_and_190_is_inclusive(self):
        decision = bar(600, 100, 107, 98, 105)
        entry = bar(601, 105, 106, 104, 105)
        rows = self.candidates(
            [decision, entry],
            [print_(600, 20, "BUY", 106, size=200),
             print_(600, 30, "BUY", 106, size=190)],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["size"], 190)

    def test_equal_opposing_largest_prints_are_ambiguous(self):
        decision = bar(600, 100, 107, 98, 105)
        entry = bar(601, 105, 106, 104, 105)
        rows = self.candidates(
            [decision, entry],
            [print_(600, 20, "BUY", 106),
             print_(600, 30, "SELL", 99)],
        )
        self.assertEqual(rows, [])

    def test_account_is_explicitly_1000_dollar_forex(self):
        execution = self.strategy.execution
        self.assertEqual(execution.initial, 1_000.0)
        self.assertEqual(execution.step, 0.01)
        self.assertEqual(execution.point_value, 1.0)
        self.assertEqual(execution.margin, 0.25)


if __name__ == "__main__":
    unittest.main()
