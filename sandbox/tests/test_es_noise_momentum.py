import unittest

from sandbox.research import es_noise_momentum_research as strategy


class EsNoiseMomentumTests(unittest.TestCase):
    def test_quantity_uses_equity_risk_and_exness_floor(self):
        self.assertEqual(strategy.quantity(1_000.0, 5_000.0, 20.0, 0.01), 0.49)
        self.assertEqual(strategy.quantity(10.0, 5_000.0, 100.0, 0.01), 0.0)

    def test_side_filter(self):
        self.assertTrue(strategy.accepts_side(1, "both"))
        self.assertTrue(strategy.accepts_side(1, "long"))
        self.assertFalse(strategy.accepts_side(-1, "long"))


if __name__ == "__main__":
    unittest.main()
