"""Stage 5 diagnostic: per-trade PnL against context known before the trade.

The plan allows exactly one kind of structural change -- dropping or rescaling a
whole bucket for an a-priori reason that holds in every fold -- and forbids any
threshold that has to be tuned. So this prints the buckets and nothing else; it
does not choose one.

Context is measured strictly before the entry bar: `vix_1h`'s last completed
hourly close (the step function `attach_hourly_vix` builds), and an ATR over the
20 sessions that closed before the entry's day.
"""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies

INITIAL = 1000.0
ATR_DAYS = 20


def buckets(rows, edges, label):
    """`rows` is [(context value, pnl)]; print PnL per context band."""
    print(f"\n{label}")
    print(f"  {'band':>16} {'trades':>7} {'pnl':>9} {'per trade':>10} "
          f"{'win':>6} {'pf':>6}")
    bands = list(zip([None] + list(edges), list(edges) + [None]))
    for lo, hi in bands:
        inside = [pnl for value, pnl in rows
                  if (lo is None or value >= lo) and (hi is None or value < hi)]
        if not inside:
            continue
        wins = sum(p for p in inside if p > 0)
        losses = -sum(p for p in inside if p < 0)
        name = (f"< {hi:g}" if lo is None else
                f">= {lo:g}" if hi is None else f"{lo:g} .. {hi:g}")
        print(f"  {name:>16} {len(inside):>7} {sum(inside):>9.2f} "
              f"{sum(inside) / len(inside):>10.3f} "
              f"{sum(1 for p in inside if p > 0) / len(inside):>6.2f} "
              f"{(wins / losses if losses else 999):>6.2f}")


def main():
    strategy = strategies.get("Hourly Delta Reversal")
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)

    vix = dict(zip((bar[data.TS] for bar in bars), data.vix_series(bars)))
    atr = data.atr_by_day(bars, ATR_DAYS)

    _stats, sized = search.backtest(strategy, ex=ex)
    print(f"compiled parameters, {len(sized)} trades, initial ${INITIAL:.0f}")

    vix_rows = [(vix.get(ts, 0.0), pnl) for ts, pnl in sized if vix.get(ts, 0.0) > 0]
    atr_rows = [(atr[ts // 86_400], pnl) for ts, pnl in sized if ts // 86_400 in atr]
    print(f"  VIX known for {len(vix_rows)}/{len(sized)} trades, "
          f"ATR({ATR_DAYS}) for {len(atr_rows)}/{len(sized)}")

    buckets(vix_rows, (15, 18, 22, 28), "PnL by VIX at entry (last completed hour)")
    buckets(atr_rows, (150, 250, 350, 500),
            f"PnL by trailing {ATR_DAYS}-session ATR, points")

    # The ratio the vol-scaled bracket is built on: a 50-point stop is a
    # different bet at ATR 150 than at ATR 500. If the edge is roughly flat in
    # stop/ATR terms, scaling the bracket is the right structure.
    ratio_rows = [(50.0 / atr[ts // 86_400], pnl) for ts, pnl in sized
                  if ts // 86_400 in atr]
    buckets(ratio_rows, (0.10, 0.15, 0.20, 0.30),
            "PnL by compiled 50-point stop as a fraction of ATR")

    print("\nVIX by calendar month (mean of the hourly closes seen by bars)")
    by_month = {}
    for bar in bars:
        by_month.setdefault(metrics.month_key(bar[data.TS]), []).append(
            vix.get(bar[data.TS], 0.0))
    monthly_pnl = metrics.monthly(sized)
    print(f"  {'month':>8} {'mean VIX':>9} {'max VIX':>9} {'pnl':>9}")
    for month, values in by_month.items():
        seen = [v for v in values if v > 0]
        if not seen:
            continue
        print(f"  {month:>8} {sum(seen) / len(seen):>9.2f} {max(seen):>9.2f} "
              f"{monthly_pnl.get(month, 0.0):>9.2f}")


if __name__ == "__main__":
    main()
