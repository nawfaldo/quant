"""Why a walk-forward fold selects nothing.

`walkforward.select` prints "no cell cleared" and stops there, which is the
right behaviour for a selector and useless for a diagnosis. This prints, for
every fold's *train* window, the best cell on each of the floors separately --
so a fold that selects nothing because it has no profitable cell can be told
apart from one that has profitable cells and fails on consistency.

In-sample by construction. It decides nothing.
"""
import sys

from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0


def main(name):
    strategy = strategies.get(name)
    result_cells = None
    from sandbox import data
    from sandbox import search
    from dataclasses import replace

    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    cells = wf.cell_fills(strategy, bars, context, ex)
    data_lo = bars[0][wf.TS]
    print(f"{strategy.name}: {len(cells)} cells, initial ${INITIAL:.0f}")

    for index, (_, train_hi, test_lo, test_hi) in enumerate(wf.fold_windows(), 1):
        stats = {k: wf.window_stats(f, ex, None, train_hi, INITIAL)[0]
                 for k, f in cells.items()}
        floor = wf.MIN_TRADES_PER_MONTH * wf.months_between(data_lo, train_hi)
        profitable = [k for k, s in stats.items() if s["pnl"] > 0]
        volume = [k for k, s in stats.items() if s["trades"] >= floor]
        streak = [k for k, s in stats.items() if s["max_loss_streak"] <= wf.MAX_LOSS_STREAK]
        share = [k for k, s in stats.items()
                 if s["top_month_share"] <= wf.MAX_TOP_MONTH_SHARE]
        both = [k for k in profitable if k in streak and k in share and k in volume]
        best = max(stats.items(), key=lambda kv: kv[1]["pnl"])
        print(f"\n  fold {index} train -> {train_hi}")
        print(f"    cells passing: trades>={floor:.0f} {len(volume)}  pnl>0 "
              f"{len(profitable)}  streak<=3 {len(streak)}  top_share<=0.5 "
              f"{len(share)}  all {len(both)}")
        print(f"    best train pnl: {dict(best[0])} "
              f"pnl {best[1]['pnl']:.1f} pf {best[1]['pf']:.3f} "
              f"mSh {best[1]['msharpe']:.2f} streak {best[1]['max_loss_streak']} "
              f"trades {best[1]['trades']}")
        # What that best-in-train cell then did out of sample, for reference.
        test = wf.window_stats(cells[best[0]], ex, test_lo, test_hi, INITIAL)[0]
        print(f"    -> its test pnl {test['pnl']:.1f} on {test['trades']} trades")
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Deep OFI Momentum (tied)")
