"""Fine walk of the ATR `k` axis before compiling it.

The fixed-point stop axis turned out to be a four-point ridge that a coarse grid
sampled at its top. Same check here, at 0.0125 resolution on both candidate `rr`
values, over the full sample and over the walk-forward's out-of-sample span.
"""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0
SELL_DELTA = 225


def main():
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(vol.execution, initial=INITIAL)
    bars = data.load_bars(vol.bars, vol.symbol)
    context = vol.context()
    lo, hi = metrics.split_ts("2025-10-01"), metrics.split_ts("2026-08-01")

    for rr in (1.5, 2.0):
        print(f"\nATR-scaled, rr={rr}, sell_delta={SELL_DELTA}")
        print(f"  {'k':>7} | {'full pnl':>9} {'pf':>5} {'mSh':>6} {'dd':>6} "
              f"| {'oos pnl':>8} {'pf':>5} {'mSh':>6} {'pos':>5} {'wQtr':>7}")
        full = []
        for step in range(13):
            k = round(0.10 + 0.0125 * step, 4)
            params = {"k": k, "rr": rr, "sell_delta": SELL_DELTA,
                      "vol_source": "atr"}
            stat, sized = search.backtest(vol, params, ex=ex, bars=bars,
                                          context=context)
            oos = metrics.stats(metrics.segment(sized, lo, hi), initial=INITIAL,
                                span=(lo, hi))
            full.append(stat["pnl"])
            print(f"  {k:>7} | {stat['pnl']:>9.1f} {stat['pf']:>5.2f} "
                  f"{stat['msharpe']:>6.2f} {stat['max_dd']:>6.1f} "
                  f"| {oos['pnl']:>8.1f} {oos['pf']:>5.2f} {oos['msharpe']:>6.2f} "
                  f"{oos['pos_rate']:>5.2f} {oos['worst_quarter']:>7.1f}")
        swings = sum(1 for a, b, c in zip(full, full[1:], full[2:])
                     if (b - a) * (c - b) < 0)
        print(f"  full-sample range {min(full):.0f}..{max(full):.0f}, "
              f"direction changes {swings}/{len(full) - 2}")


if __name__ == "__main__":
    main()
