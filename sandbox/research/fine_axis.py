"""Fine-resolution walk of the stop axis, fixed-point against VIX-scaled.

The plan's own criterion: "if a parameter needs 5-unit resolution to work, it
does not work." The coarse grid said stop=50 sat among profitable neighbours;
the equal-width control hinted it is a spike between them. This resolves it, and
runs the VIX-scaled axis alongside so the two parameterizations can be compared
on smoothness rather than on peak.
"""
from dataclasses import replace

from sandbox import data
from sandbox import search
from sandbox import strategies

INITIAL = 1000.0
RR, SELL_DELTA = 1.5, 225


def walk(strategy, ex, bars, context, axis, values, extra, label):
    print(f"\n{label}")
    print(f"  {axis:>8} {'pnl':>8} {'tr':>4} {'pf':>5} {'mSh':>6} {'dd':>6} "
          f"{'pos':>5} {'wQtr':>8}")
    out = []
    for value in values:
        params = {axis: value, "rr": RR, "sell_delta": SELL_DELTA, **extra}
        stat, _ = search.backtest(strategy, params, ex=ex, bars=bars,
                                  context=context)
        out.append(stat["pnl"])
        print(f"  {value:>8} {stat['pnl']:>8.1f} {stat['trades']:>4} "
              f"{stat['pf']:>5.2f} {stat['msharpe']:>6.2f} {stat['max_dd']:>6.1f} "
              f"{stat['pos_rate']:>5.2f} {stat['worst_quarter']:>8.1f}")
    span = max(out) - min(out)
    swings = sum(1 for a, b, c in zip(out, out[1:], out[2:])
                 if (b - a) * (c - b) < 0)
    print(f"  range {min(out):.0f}..{max(out):.0f} (span {span:.0f}), "
          f"direction changes {swings}/{len(out) - 2}")
    return out


def main():
    tied = strategies.get("Hourly Delta Reversal (tied)")
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(tied.execution, initial=INITIAL)
    bars = data.load_bars(tied.bars, tied.symbol)
    print(f"rr={RR}, sell_delta={SELL_DELTA}, ${INITIAL:.0f} Forex NQ, "
          f"cost {ex.entry_cost} (spread {ex.spread} + slippage {ex.slippage} + commission)")

    walk(tied, ex, bars, tied.context(), "stop", list(range(36, 79, 2)), {},
         "fixed point stop, 2-point resolution")
    walk(vol, ex, bars, vol.context(), "k",
         [round(0.10 + 0.0125 * i, 4) for i in range(17)],
         {"vol_source": "vix"}, "VIX-scaled stop, k axis")


if __name__ == "__main__":
    main()
