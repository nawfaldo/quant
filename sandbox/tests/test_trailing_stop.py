"""The optional trailing leg in `execution.resolve`."""
import unittest

from sandbox import execution
from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal

#: Costless, so the bracket arithmetic is read without subtracting anything.
#: All three components must be zeroed, not just `spread` -- slippage and
#: commission are separate fields and both default to non-zero.
EX = Execution(initial=1_000.0, spread=0.0, slippage=0.0, commission_per_lot=0.0,
               session_end_min=None)


def bar(minute, open_, high, low, close):
    row = [0.0] * 7
    row[TS] = 20_000 * 86_400 + minute * 60
    row[O], row[H], row[L], row[C], row[D], row[DE] = (
        open_, high, low, close, 0.0, 1,
    )
    return row


class TrailingStopTest(unittest.TestCase):
    def test_a_trail_tighter_than_the_stop_binds_from_entry(self):
        # Entry at 100, stop 10, trail 5. The best price is the entry itself,
        # so the exit is 95 from the first bar — and 5, not 10, is the risk
        # sizing must divide by.
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 100, 91, 95),
                bar(572, 95, 95, 89, 90)]
        signal = Signal(0, LONG, 10.0, 100.0, None, 5.0)
        fills = execution.resolve(bars, [signal], EX)
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0].points, -5.0)
        self.assertAlmostEqual(fills[0].stop, 5.0)

    def test_a_trail_wider_than_the_stop_leaves_the_stop_in_charge(self):
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 100, 89, 90)]
        signal = Signal(0, LONG, 10.0, 100.0, None, 25.0)
        fills = execution.resolve(bars, [signal], EX)
        self.assertAlmostEqual(fills[0].points, -10.0)
        self.assertAlmostEqual(fills[0].stop, 10.0)

    def test_trail_ratchets_behind_the_best_price(self):
        # Runs to 120 on bar 1, then gives back. The trail sits at 115.
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 120, 100, 119),
                bar(572, 119, 119, 110, 111)]
        signal = Signal(0, LONG, 10.0, 100.0, None, 5.0)
        fills = execution.resolve(bars, [signal], EX)
        self.assertAlmostEqual(fills[0].points, 15.0)

    def test_trail_uses_only_closed_bars(self):
        # The entry bar itself spikes to 120 and returns to 100. Because the
        # trail may only ratchet off bars that have closed, it is not yet at
        # 115 during that same minute, so no exit fires.
        bars = [bar(570, 100, 120, 100, 100),
                bar(571, 100, 101, 100, 101)]
        signal = Signal(0, LONG, 10.0, 100.0, None, 5.0)
        self.assertEqual(execution.resolve(bars, [signal], EX), [])

    def test_short_trail_mirrors(self):
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 100, 80, 81),
                bar(572, 81, 90, 81, 89)]
        signal = Signal(0, SHORT, 10.0, 100.0, None, 5.0)
        fills = execution.resolve(bars, [signal], EX)
        self.assertAlmostEqual(fills[0].points, 15.0)

    def test_zero_target_means_no_target(self):
        # A trailing-only position must not exit instantly on a 0.0 target.
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 110, 100, 109),
                bar(572, 109, 109, 104, 104)]
        signal = Signal(0, LONG, 10.0, 0.0, None, 5.0)
        fills = execution.resolve(bars, [signal], EX)
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0].points, 5.0)

    def test_absent_trail_is_the_unchanged_fixed_bracket(self):
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 120, 100, 119),
                bar(572, 119, 119, 89, 90)]
        signal = Signal(0, LONG, 10.0, 100.0, None, None)
        fills = execution.resolve(bars, [signal], EX)
        self.assertAlmostEqual(fills[0].points, -10.0)

    def test_every_cost_component_is_charged_once_at_entry(self):
        """Spread, slippage and commission all bill once, at the entry.

        Commission is quoted per LOT in dollars, so it reaches price points by
        dividing by `point_value` -- 1.0 here, which keeps the arithmetic
        readable. Exness bills the whole round trip at the open, so charging it
        with the other two is the broker's behaviour, not just a convention.
        """
        bars = [bar(570, 100, 100, 100, 100),
                bar(571, 100, 120, 100, 119),
                bar(572, 119, 119, 110, 111)]
        signal = Signal(0, LONG, 10.0, 100.0, None, 5.0)
        ex = Execution(spread=0.1, slippage=0.2, commission_per_lot=0.5,
                       point_value=1.0, session_end_min=None)
        self.assertAlmostEqual(ex.entry_cost, 0.8)
        charged = execution.resolve(bars, [signal], ex)
        self.assertAlmostEqual(charged[0].points, 15.0 - 0.8)

    def test_commission_per_lot_scales_by_point_value(self):
        """$/lot becomes price points via `point_value`.

        On XNGUSD one lot is 10,000 units, so its $70 round trip is 0.007 in
        price -- the same dollars, expressed where the bracket can subtract it.
        """
        ex = Execution(spread=0.0, slippage=0.0, commission_per_lot=70.0,
                       point_value=10_000.0)
        self.assertAlmostEqual(ex.entry_cost, 0.007)


if __name__ == "__main__":
    unittest.main()
