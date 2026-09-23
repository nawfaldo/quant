"""Stage 5 candidates for Deep OFI Momentum, judged fold by fold.

Two things are printed for every context variable:

  1. the bucket table -- what the unfiltered strategy earns in each band, which
     is a hypothesis and nothing more;
  2. the effect of actually *dropping* a bucket, re-resolved and re-sized, in
     each of the five walk-forward test windows separately.

The second is what decides. OPTIMIZATION_PLAN.md Stage 5 allows a filter only
when it has an a-priori economic rationale and helps in **every** fold, and
forbids any threshold that has to be tuned. A filter that improves the total
while hurting two folds is the search finding a lucky split, so the "improved"
column is the one to read, not the PnL column.

The base configuration is the **compiled** one, not a searched winner: a filter
study seeded from a cell that already won a sweep is measuring the two together.

Beyond the calendar and regime axes that `filters.py` runs for Hourly Delta
Reversal, this adds the order-flow columns the OFI bar already carries. Each is
a supporting-indicator hypothesis with its rationale stated before the number:

  * `trade_delta`      aggressive flow confirming quoted flow. Quoted book
                       pressure that no one lifts is an intention; pressure that
                       trades is a commitment.
  * `top1_imbalance`   the same confirmation at the touch, where deep OFI's
                       levels 2-10 are least informative.
  * `microprice`       the book's own fair value relative to the mid: if the
                       queue predicts the next tick, it should agree with the
                       side being taken.
  * `replenishment`    whether the side being hit is being refilled. Refilled
                       liquidity absorbs a move; pulled liquidity lets it run.
  * `trade_count`      an activity floor. A z-score of book pressure in a dead
                       minute is a z-score of nothing.
  * `price_change`     whether the move has already begun. Momentum entered
                       after the move is chasing; before it is anticipating.

Filtering happens on the `Signal` list rather than inside the strategy, so the
entry logic is untouched and sizing is recomputed from scratch for each variant.

Dropping signals also frees the occupancy the dropped trade would have held, and
that is deliberately *not* modelled here: a filter is scored as a pure removal so
the comparison is against the same trade population. A filter that survives this
is then worth re-running inside `signals()`.
"""
import sys
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0
BASE = "Deep OFI Momentum"
ATR_DAYS = 20
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def contexts(bars, features, signals):
    """Per-signal context, all of it known strictly before the entry bar.

    A signal fills at `index`, and was read off the bar *before* it -- every
    level-two column is an end-of-minute value -- so the order-flow row here is
    the one at `index - 1`, which is the same row the entry rule saw.
    """
    vix = data.vix_series(bars)
    atr = data.atr_by_day(bars, ATR_DAYS)
    daily = {}
    for bar, level in zip(bars, vix):
        if level > 0:
            daily[bar[data.TS] // 86_400] = level
    days = sorted(daily)
    previous = {day: daily[days[i - 1]] for i, day in enumerate(days) if i}

    out = []
    for signal in signals:
        bar = bars[signal.index]
        day = bar[data.TS] // 86_400
        level = vix[signal.index]
        before = previous.get(day)
        row = features.get(bars[signal.index - 1][data.TS], {})
        # +1 when the reading agrees with the side taken, -1 when it opposes.
        sign = 1.0 if signal.side == execution.LONG else -1.0
        mid = row.get("midprice") or 0.0
        micro = row.get("microprice") or 0.0
        out.append({
            "weekday": int((day + 3) % 7),
            "hour": int((bar[data.TS] % 86_400) // 3600),
            "side": signal.side,
            "vix": level,
            "vix_change": (level / before - 1.0) * 100 if before and level else None,
            "atr": atr.get(day),
            "delta": sign * row.get("trade_delta", 0.0),
            "top1": sign * row.get("top1_imbalance", 0.0),
            "micro": sign * (micro - mid) if mid and micro else None,
            # The stated rationale: for a long, the side being hit is the ask,
            # and an ask that is *not* refilled lets the move run. That is
            # `replenishment_score` positive, so signing it to the trade gives
            # "agrees" = the resting side is being pulled.
            "repl": sign * row.get("replenishment_score", 0.0),
            # The opposite reading, kept because it is what the first pass
            # actually measured and it scored better -- see the header note.
            "repl_refill": -sign * row.get("replenishment_score", 0.0),
            "trades": row.get("trade_count", 0.0),
            "move": sign * row.get("price_change", 0.0),
        })
    return out


def window_pnls(bars, signals, ex):
    """PnL in each fold's test window plus the full sample."""
    sized = execution.run(bars, signals, ex)
    out = []
    for _, _, test_lo, test_hi in wf.fold_windows():
        out.append(round(sum(p for _, p in metrics.segment(sized, test_lo, test_hi)), 2))
    return out, round(sum(p for _, p in sized), 2), sized


def try_drop(bars, ex, signals, ctx, base_windows, base_total, name, keep):
    """Re-run with `keep` applied and report per-fold deltas."""
    kept = [s for s, c in zip(signals, ctx) if keep(c)]
    if len(kept) == len(signals) or not kept:
        return None
    windows, total, _ = window_pnls(bars, kept, ex)
    deltas = [round(a - b, 2) for a, b in zip(windows, base_windows)]
    improved = sum(1 for d in deltas if d > 0)
    unhurt = sum(1 for d in deltas if d >= -0.01)
    print(f"    {name:<30} {len(signals) - len(kept):>5} "
          f"{total - base_total:>+9.1f}  "
          + " ".join(f"{d:>+7.1f}" for d in deltas)
          + f"   {improved}/5 {'*' if unhurt == 5 else ''}")
    sys.stdout.flush()
    return improved, unhurt, total - base_total


HEADER = (f"\n    {'filter':<30} {'cut':>5} {'total':>9}  "
          + " ".join(f"{'f' + str(i):>7}" for i in range(1, 6))
          + "   improved")


def attributed(sized, ctx, test):
    """PnL of the signals whose context satisfies `test`, and how many."""
    n = sum(1 for c in ctx if test(c))
    pnl = sum(p for (_, p), c in zip(sized, ctx) if test(c))
    return n, pnl


def main():
    strategy = strategies.get(BASE)
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    features = data.load_l2_features(strategy.symbol)
    context = strategy.context()

    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group,
                                        strategy.all_params(None)))
    signals.sort(key=lambda s: s.index)
    ctx = contexts(bars, features, signals)

    base_windows, base_total, base_sized = window_pnls(bars, signals, ex)
    base = metrics.stats(base_sized, initial=INITIAL)
    print(f"compiled Deep OFI Momentum, ${INITIAL:.0f} Forex NQ, cost {ex.entry_cost} (spread {ex.spread} + slippage {ex.slippage} + commission)")
    print(f"  baseline: pnl {base['pnl']} trades {base['trades']} pf {base['pf']} "
          f"mSharpe {base['msharpe']} pos_rate {base['pos_rate']}")
    print(f"  per-fold test PnL: {base_windows}")

    print("\n=== day of week " + "=" * 45)
    for day in range(5):
        n, pnl = attributed(base_sized, ctx, lambda c, d=day: c["weekday"] == d)
        print(f"  {WEEKDAYS[day]}: {n:>5} signals, attributed pnl {pnl:>8.2f}")
    print(HEADER)
    for day in range(5):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {WEEKDAYS[day]}", lambda c, d=day: c["weekday"] != d)

    print("\n=== entry hour " + "=" * 46)
    hours = sorted({c["hour"] for c in ctx})
    for hour in hours:
        n, pnl = attributed(base_sized, ctx, lambda c, h=hour: c["hour"] == h)
        print(f"  {hour:02d}:00 {n:>5} signals, attributed pnl {pnl:>8.2f}")
    print(HEADER)
    for hour in hours:
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {hour:02d}:00 entries", lambda c, h=hour: c["hour"] != h)

    print("\n=== side " + "=" * 51)
    print(HEADER)
    for side in ("long", "short"):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {side}s", lambda c, s=side: c["side"] != s)

    print("\n=== supporting indicators (sign cuts, not tuned) " + "=" * 12)
    print("    reading is signed to the trade: >0 agrees with the side taken")
    for key, label in (("delta", "trade_delta"), ("top1", "top1_imbalance"),
                       ("micro", "microprice-mid"), ("repl", "repl (pulled)"),
                       ("repl_refill", "repl (refilled)"), ("move", "price_change")):
        values = [c[key] for c in ctx if c[key] is not None]
        if not values:
            continue
        for name, test in (("agrees", lambda v: v > 0), ("opposes", lambda v: v < 0),
                           ("flat", lambda v: v == 0)):
            n, pnl = attributed(base_sized, ctx,
                                lambda c, k=key, t=test: c[k] is not None and t(c[k]))
            print(f"  {label:>16} {name:<8} {n:>5} signals, attributed pnl {pnl:>8.2f}")
    print(HEADER)
    for key, label in (("delta", "trade_delta"), ("top1", "top1_imbalance"),
                       ("micro", "microprice-mid"), ("repl", "repl (pulled)"),
                       ("repl_refill", "repl (refilled)"), ("move", "price_change")):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"require {label} agrees",
                 lambda c, k=key: c[k] is not None and c[k] > 0)
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"drop {label} opposes",
                 lambda c, k=key: c[k] is None or c[k] >= 0)

    print("\n=== activity floor (tuned threshold -- diagnostic only) " + "=" * 5)
    print(HEADER)
    for cut in (10, 25, 50, 100):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"require trade_count >= {cut}", lambda c, x=cut: c["trades"] >= x)

    print("\n=== VIX level (tuned threshold -- diagnostic only) " + "=" * 10)
    print(HEADER)
    for cut in (15, 18, 20, 22, 25, 28):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX >= {cut}", lambda c, x=cut: not c["vix"] or c["vix"] < x)
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX < {cut}", lambda c, x=cut: not c["vix"] or c["vix"] >= x)

    print("\n=== VIX day-over-day change (tuned threshold -- diagnostic) " + "=" * 1)
    print(HEADER)
    for cut in (5, 10, 15):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX up >{cut}%",
                 lambda c, x=cut: c["vix_change"] is None or c["vix_change"] <= x)
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX down >{cut}%",
                 lambda c, x=cut: c["vix_change"] is None or c["vix_change"] >= -x)

    print("\n=== trailing ATR level (tuned threshold -- diagnostic) " + "=" * 6)
    print(HEADER)
    for cut in (300, 400, 500, 600):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip ATR >= {cut}pt", lambda c, x=cut: c["atr"] is None or c["atr"] < x)
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip ATR < {cut}pt", lambda c, x=cut: c["atr"] is None or c["atr"] >= x)

    print("\n  * = helped or was neutral in all five folds")


if __name__ == "__main__":
    main()
