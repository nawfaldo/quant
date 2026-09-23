"""Does *scaling* help, or only the average width the scaling happened to pick?

Each volatility-scaled configuration is matched against a fixed stop set to the
mean point distance that configuration actually used. If the fixed twin does as
well, the scaling contributes nothing and the earlier ATR bucket table was
measuring the regime, not the bracket.
"""
from dataclasses import replace

from sandbox import data
from sandbox import search
from sandbox import strategies

INITIAL = 1000.0
RR, SELL_DELTA = 1.5, 225
PAIRS = [("atr", 0.10, 44), ("atr", 0.125, 54), ("atr", 0.15, 64),
         ("atr", 0.20, 84), ("vix", 0.15, 46), ("vix", 0.20, 62),
         ("vix", 0.25, 77), ("vix", 0.30, 93)]


def main():
    tied = strategies.get("Hourly Delta Reversal (tied)")
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(tied.execution, initial=INITIAL)
    bars = data.load_bars(tied.bars, tied.symbol)
    tied_ctx, vol_ctx = tied.context(), vol.context()

    print(f"rr={RR}, sell_delta={SELL_DELTA}, ${INITIAL:.0f} Forex NQ, "
          f"cost {ex.entry_cost}\n")
    print(f"  {'scaled':>14} {'pnl':>8} {'pf':>5} {'mSh':>6} | "
          f"{'fixed twin':>12} {'pnl':>8} {'pf':>5} {'mSh':>6} | {'scaling adds':>13}")

    for source, k, mean_stop in PAIRS:
        scaled, _ = search.backtest(
            vol, {"k": k, "rr": RR, "sell_delta": SELL_DELTA, "vol_source": source},
            ex=ex, bars=bars, context=vol_ctx)
        fixed, _ = search.backtest(
            tied, {"stop": mean_stop, "rr": RR, "sell_delta": SELL_DELTA},
            ex=ex, bars=bars, context=tied_ctx)
        print(f"  {source + ' k=' + str(k):>14} {scaled['pnl']:>8.1f} "
              f"{scaled['pf']:>5.2f} {scaled['msharpe']:>6.2f} | "
              f"{'stop ' + str(mean_stop) + 'pt':>12} {fixed['pnl']:>8.1f} "
              f"{fixed['pf']:>5.2f} {fixed['msharpe']:>6.2f} | "
              f"{scaled['pnl'] - fixed['pnl']:>+13.1f}")


if __name__ == "__main__":
    main()
