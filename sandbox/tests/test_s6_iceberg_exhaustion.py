import unittest

from sandbox.execution import LONG, SHORT

from sandbox.strategies.s6_iceberg_exhaustion import (
    IcebergExhaustionBreakout,
    zscore,
)


class IcebergExhaustionTests(unittest.TestCase):
    def setUp(self):
        self.strategy = IcebergExhaustionBreakout()
        self.params = self.strategy.all_params()

    def test_long_requires_ask_replenishment_aggression_and_break(self):
        row = {
            "ask_replenishment_z": 2.1,
            "bid_replenishment_z": 0.0,
            "ask_execution_share": 0.53,
            "price_change": 3.0,
        }
        self.assertEqual(self.strategy.side_for(row, self.params), LONG)
        row["ask_execution_share"] = 0.52
        self.assertIsNone(self.strategy.side_for(row, self.params))

    def test_short_is_symmetric(self):
        row = {
            "ask_replenishment_z": 0.0,
            "bid_replenishment_z": 2.1,
            "ask_execution_share": 0.47,
            "price_change": -3.0,
        }
        self.assertEqual(self.strategy.side_for(row, self.params), SHORT)

    def test_zscore_is_strictly_causal(self):
        history = list(range(20))
        before = list(history)
        self.assertGreater(zscore(30.0, history), 0.0)
        self.assertEqual(history, before)


if __name__ == "__main__":
    unittest.main()
