"""Signal-definition tests for the corrected whale-digestion proxy."""

import unittest

from sandbox.execution import LONG
from sandbox.strategies.whale_digestion import WhaleDigestion, whale_rows

DAY = 20_000 * 86_400


def bar(minute, open_, high, low, close):
    return [DAY + minute * 60, open_, high, low, close, 0.0, 1]


def print_(minute, second, side, price, size=120, **overrides):
    row = {
        "source": "dbento",
        "event_ns": (DAY + minute * 60 + second) * 1_000_000_000,
        "sequence": second,
        "side": side,
        "price": price,
        "size": size,
        "best_bid": price - 0.25 if side == "BUY" else price,
        "best_ask": price if side == "BUY" else price + 0.25,
        "book_imbalance": 0.0,
        "book_valid": True,
    }
    row.update(overrides)
    return row


class WhaleDigestionTests(unittest.TestCase):
    def setUp(self):
        self.strategy = WhaleDigestion()
        self.params = self.strategy.all_params()

    def rows(self, bars, prints):
        return whale_rows(
            bars, prints, {int(row[0]): "dbento" for row in bars}, self.params
        )

    def test_monster_is_selected_first_then_rejects_the_minute(self):
        bars = [bar(600, 100, 108, 98, 105), bar(601, 105, 106, 104, 105)]
        prints = [print_(600, 10, "BUY", 106, 120),
                  print_(600, 20, "BUY", 107, 200)]
        self.assertEqual(self.rows(bars, prints), [])

    def test_buy_in_lower_wick_is_valid_and_ignores_candle_colour(self):
        bars = [bar(600, 105, 107, 98, 103), bar(601, 103, 104, 102, 103)]
        rows = self.rows(bars, [print_(600, 20, "BUY", 99)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], LONG)
        self.assertEqual(rows[0]["index"], 1)

    def test_body_print_is_rejected(self):
        bars = [bar(600, 100, 108, 98, 105), bar(601, 105, 106, 104, 105)]
        self.assertEqual(self.rows(bars, [print_(600, 20, "BUY", 104)]), [])

    def test_book_snapshot_must_be_balanced(self):
        bars = [bar(600, 100, 108, 98, 105), bar(601, 105, 106, 104, 105)]
        self.assertEqual(self.rows(
            bars, [print_(600, 20, "BUY", 106, book_imbalance=0.21)]
        ), [])

    def test_registered_form_is_time_exit_without_profit_target(self):
        self.assertEqual(self.params["target"], 0.0)
        self.assertEqual(self.params["time_stop"], 15)
        self.assertEqual(self.strategy.execution.initial, 1_000.0)


if __name__ == "__main__":
    unittest.main()
