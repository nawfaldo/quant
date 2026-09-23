"""Fixed vs ATR-scaled vs VIX-scaled brackets, at matched settings.

One question only: does making the bracket proportional to volatility help,
holding the entry signal and everything else constant? `k` is chosen so the
average stop lands near the fixed 50 points rather than by searching, so this
is a comparison of *structure*, not of tuning.
"""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0
FIELDS = ("pnl", "trades", "pf", "max_dd", "msharpe", "pos_rate",
          "max_loss_streak", "worst_quarter", "top_month_share", "worst_month")


def show(label, stat, extra=""):
    print(f"\n{label}{extra}")
    print("  " + "  ".join(f"{f}={stat[f]}" for f in FIELDS))
    return stat


def stop_profile(strategy, params, bars, context):
    """Mean/min/max stop the configuration actually used, in points."""
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group,
                                        strategy.all_params(params)))
    stops = [s.stop for s in signals]
    return (f"   stops: mean {sum(stops) / len(stops):.0f} "
            f"min {min(stops):.0f} max {max(stops):.0f} pts, n={len(stops)}")


def main():
    tied = strategies.get("Hourly Delta Reversal (tied)")
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(tied.execution, initial=INITIAL)
    bars = data.load_bars(tied.bars, tied.symbol)
    tied_ctx, vol_ctx = tied.context(), vol.context()

    print(f"initial ${INITIAL:.0f}, Forex NQ, cost {ex.entry_cost}, "
          f"{len(bars)} bars\nall rows: rr=1.5, sell_delta=225, "
          f"short_trend_days=35, risk 0.005")

    stat, _ = search.backtest(tied, {"stop": 50, "rr": 1.5, "sell_delta": 225},
                              ex=ex, bars=bars, context=tied_ctx)
    show("fixed 50-point stop", stat,
         "\n" + stop_profile(tied, {"stop": 50, "rr": 1.5, "sell_delta": 225},
                             bars, tied_ctx))

    for source, ks in (("atr", (0.10, 0.125, 0.15, 0.20)),
                       ("vix", (0.15, 0.20, 0.25, 0.30))):
        for k in ks:
            params = {"k": k, "rr": 1.5, "sell_delta": 225, "vol_source": source}
            stat, _ = search.backtest(vol, params, ex=ex, bars=bars,
                                      context=vol_ctx)
            show(f"{source}-scaled, k={k}", stat,
                 "\n" + stop_profile(vol, params, bars, vol_ctx))


if __name__ == "__main__":
    main()
