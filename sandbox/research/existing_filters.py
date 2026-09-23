"""Re-validate the filters already compiled in, under the ATR-scaled bracket.

Both were chosen against fixed point stops. Widening the bracket in violent
regimes changes which trades survive, so neither carries over for free -- the
Thursday skip and the short-side trend gate are re-measured here per fold, on
the same rule the new candidates were held to.
"""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0
PARAMS = {"k": 0.2, "rr": 2.0, "sell_delta": 225, "vol_source": "atr"}
FIELDS = ("pnl", "trades", "pf", "msharpe", "pos_rate", "max_dd", "worst_quarter")


def main():
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(vol.execution, initial=INITIAL)
    bars = data.load_bars(vol.bars, vol.symbol)
    context = vol.context()
    folds = wf.fold_windows()

    def measure(overrides):
        stat, sized = search.backtest(vol, {**PARAMS, **overrides}, ex=ex,
                                      bars=bars, context=context)
        windows = [round(sum(p for _, p in metrics.segment(sized, lo, hi)), 2)
                   for _, _, lo, hi in folds]
        return stat, windows

    base, base_windows = measure({})

    def show(label, overrides):
        stat, windows = measure(overrides)
        deltas = [round(a - b, 2) for a, b in zip(windows, base_windows)]
        improved = sum(1 for d in deltas if d > 0)
        print(f"  {label:<26} " + "  ".join(f"{f}={stat[f]}" for f in FIELDS[:4]))
        print(f"  {'':<26} per-fold delta " + " ".join(f"{d:>+7.1f}" for d in deltas)
              + f"   improved {improved}/5")

    print(f"ATR-scaled {PARAMS}, ${INITIAL:.0f} Forex NQ\n")
    print("baseline (both filters on)")
    print("  " + "  ".join(f"{f}={base[f]}" for f in FIELDS))
    print(f"  per-fold test PnL {base_windows}\n")

    print("Thursday skip")
    show("off (trade Thursdays)", {"skip_thursday": False})

    print("\nshort-side SMA trend gate")
    for days in (0, 20, 30, 35, 45, 60, 90):
        show(f"short_trend_days={days}", {"short_trend_days": days})

    print("\nbook spread gate")
    for cut in (1.0, 1.25, 1.5, 2.0, 3.0, 99.0):
        show(f"max_spread={cut}", {"max_spread": cut})


if __name__ == "__main__":
    main()
