"""Does *scaling* the bracket help, or only the average width it happened to pick?

The ATR-scaled form is the only Deep OFI variant with a positive full-sample
number, which is exactly the situation the README warns about: bucketing trades
by stop-as-a-fraction-of-ATR measures the regime, not the bracket.

So each volatility-scaled configuration is matched against a fixed stop set to
the mean point distance that configuration actually used. If the fixed twin does
as well, the scaling contributes nothing.
"""
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import search
from sandbox import strategies

INITIAL = 1000.0
RR = 1.5
Z = 2.0


def mean_stop(strategy, params, bars, context):
    """Average point stop the scaled configuration actually placed."""
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group,
                                        strategy.all_params(params)))
    return sum(s.stop for s in signals) / len(signals) if signals else 0.0


def main():
    tied = strategies.get("Deep OFI Momentum (tied)")
    ex = replace(tied.execution, initial=INITIAL)
    bars = data.load_bars(tied.bars, tied.symbol)
    tied_ctx = tied.context()

    print(f"rr={RR}, ofi_z={Z}, ${INITIAL:.0f} Forex NQ, cost {ex.entry_cost}\n")
    print(f"  {'scaled':>16} {'pnl':>8} {'pf':>5} {'mSh':>6} | "
          f"{'fixed twin':>13} {'pnl':>8} {'pf':>5} {'mSh':>6} | {'scaling adds':>13}")

    for name, ks in (("Deep OFI Momentum (atr)", (0.05, 0.075, 0.10, 0.15)),
                     ("Deep OFI Momentum (vix)", (0.06, 0.10, 0.15, 0.20))):
        vol = strategies.get(name)
        vol_ctx = vol.context()
        source = vol.defaults["vol_source"]
        for k in ks:
            params = {"k": k, "rr": RR, "ofi_z": Z}
            width = round(mean_stop(vol, params, bars, vol_ctx))
            scaled, _ = search.backtest(vol, params, ex=ex, bars=bars, context=vol_ctx)
            fixed, _ = search.backtest(tied, {"stop": width, "rr": RR, "ofi_z": Z},
                                       ex=ex, bars=bars, context=tied_ctx)
            print(f"  {source + ' k=' + str(k):>16} {scaled['pnl']:>8.1f} "
                  f"{scaled['pf']:>5.2f} {scaled['msharpe']:>6.2f} | "
                  f"{'stop ' + str(width) + 'pt':>13} {fixed['pnl']:>8.1f} "
                  f"{fixed['pf']:>5.2f} {fixed['msharpe']:>6.2f} | "
                  f"{scaled['pnl'] - fixed['pnl']:>+13.1f}")


if __name__ == "__main__":
    main()
