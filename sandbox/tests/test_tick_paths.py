import unittest

from sandbox import execution


class IntrabarResolutionTests(unittest.TestCase):
    def setUp(self):
        self.bars = [
            [0, 100.0, 101.0, 99.0, 100.0, 0.0, 0],
            [60, 100.0, 110.0, 90.0, 100.0, 0.0, 0],
        ]
        # Costless: which bracket leg trades first is the subject here, so all
        # three cost components are zeroed rather than only `spread`.
        self.ex = execution.Execution(spread=0.0, slippage=0.0,
                                      commission_per_lot=0.0,
                                      session_end_min=None)

    def test_missing_path_keeps_conservative_stop_first(self):
        signal = execution.Signal(0, execution.LONG, 5.0, 5.0)
        fills = execution.resolve(self.bars, [signal], self.ex)
        self.assertEqual(fills[0].points, -5.0)

    def test_long_uses_first_traded_bracket(self):
        signal = execution.Signal(0, execution.LONG, 5.0, 5.0)
        fills = execution.resolve(
            self.bars,
            [signal],
            self.ex,
            intrabar_prices={60: [101.0, 106.0, 94.0]},
        )
        self.assertEqual(fills[0].points, 6.0)

    def test_short_uses_first_traded_bracket(self):
        signal = execution.Signal(0, execution.SHORT, 5.0, 5.0)
        fills = execution.resolve(
            self.bars,
            [signal],
            self.ex,
            intrabar_prices={60: [99.0, 94.0, 106.0]},
        )
        self.assertEqual(fills[0].points, 6.0)


if __name__ == "__main__":
    unittest.main()
