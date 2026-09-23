"""The surface around the cell that won every fold, on two windows.

A plateau is only a plateau if it is one on data that did not choose it, so the
same neighbourhood is printed over the earliest train window (Feb-Sep 2025, the
only window fold 1 could see) and over the full sample.
"""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

CENTRE = {"stop": 50, "rr": 1.5, "sell_delta": 225}

strategy = strategies.get("Hourly Delta Reversal (tied)")
ex = replace(strategy.execution, initial=wf.INITIAL)
bars = data.load_bars(strategy.bars, strategy.symbol)
cells = wf.cell_fills(strategy, bars, strategy.context(), ex, progress=False)

WINDOWS = [("fold-1 train  (.. 2025-09-30)", None, wf.fold_windows()[0][1]),
           ("full sample", None, None)]

for label, lo, hi in WINDOWS:
    print(f"\n{label}")
    print(f"  {'stop':>5} {'rr':>5} {'sell_delta':>11}  {'pnl':>8} {'tr':>4} "
          f"{'pf':>5} {'mSh':>6} {'pos':>5} {'wQtr':>8}")
    keys = [search.freeze(CENTRE)] + [
        k for k in search.neighbours(strategy, search.freeze(CENTRE)) if k in cells]
    for key in keys:
        combo = dict(key)
        stat, _ = wf.window_stats(cells[key], ex, lo, hi, wf.INITIAL)
        mark = " *" if combo == CENTRE else ""
        print(f"  {combo['stop']:>5} {combo['rr']:>5} {combo['sell_delta']:>11}"
              f"{mark:<2}{stat['pnl']:>8.1f} {stat['trades']:>4} {stat['pf']:>5.2f} "
              f"{stat['msharpe']:>6.2f} {stat['pos_rate']:>5.2f} "
              f"{stat['worst_quarter']:>8.1f}")
