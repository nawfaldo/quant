"""Candidate filters for the ATR-scaled configuration, judged fold by fold.

Two things are printed for every context variable:

  1. the bucket table -- what the unfiltered strategy earns in each band, which
     is a hypothesis and nothing more;
  2. the effect of actually *dropping* a bucket, re-resolved and re-sized, in
     each of the five walk-forward test windows separately.

The second is what decides. OPTIMIZATION_PLAN.md Stage 5 allows a filter only
when it has an a-priori economic rationale and helps in **every** fold, and
forbids any threshold that has to be tuned. A filter that improves the total
while hurting two folds is the search finding a lucky split, so the "folds
improved" column is the one to read, not the PnL column.

Filtering happens on the `Signal` list rather than inside the strategy, so the
entry logic is untouched and sizing is recomputed from scratch for each variant.
"""
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0
PARAMS = {"k": 0.2, "rr": 2.0, "sell_delta": 225, "vol_source": "atr"}
ATR_DAYS = 20
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def contexts(bars, signals):
    """Per-signal context, all of it known strictly before the entry bar."""
    vix = data.vix_series(bars)
    atr = data.atr_by_day(bars, ATR_DAYS)
    # Last VIX close of each session, for a day-over-day change.
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
        out.append({
            "weekday": int((day + 3) % 7),
            "hour": int((bar[data.TS] % 86_400) // 3600),
            "side": signal.side,
            "vix": level,
            "vix_change": (level / before - 1.0) * 100 if before and level else None,
            "atr": atr.get(day),
        })
    return out


def window_pnls(bars, signals, ex):
    """PnL in each fold's test window plus the full sample."""
    sized = execution.run(bars, signals, ex)
    out = []
    for _, _, test_lo, test_hi in wf.fold_windows():
        out.append(round(sum(p for _, p in metrics.segment(sized, test_lo, test_hi)), 2))
    return out, round(sum(p for _, p in sized), 2), sized


def bucket_table(signals, ctx, key, bands, label):
    print(f"\n  {label}")
    print(f"    {'band':>16} {'signals':>8}")
    for name, test in bands:
        n = sum(1 for c in ctx if c[key] is not None and test(c[key]))
        print(f"    {name:>16} {n:>8}")


def try_drop(bars, ex, signals, ctx, base_windows, base_total, name, keep):
    """Re-run with `keep` applied and report per-fold deltas."""
    kept = [s for s, c in zip(signals, ctx) if keep(c)]
    if len(kept) == len(signals) or not kept:
        return None
    windows, total, _ = window_pnls(bars, kept, ex)
    deltas = [round(a - b, 2) for a, b in zip(windows, base_windows)]
    improved = sum(1 for d in deltas if d > 0)
    unhurt = sum(1 for d in deltas if d >= -0.01)
    print(f"    {name:<28} {len(signals) - len(kept):>4} "
          f"{total - base_total:>+9.1f}  "
          + " ".join(f"{d:>+7.1f}" for d in deltas)
          + f"   {improved}/5 {'*' if unhurt == 5 else ''}")
    return improved, unhurt, total - base_total


def main():
    vol = strategies.get("Hourly Delta Reversal")
    ex = replace(vol.execution, initial=INITIAL)
    bars = data.load_bars(vol.bars, vol.symbol)
    context = vol.context()

    signals = []
    for group in vol.groups():
        signals.extend(vol.signals(bars, context, group, vol.all_params(PARAMS)))
    signals.sort(key=lambda s: s.index)
    ctx = contexts(bars, signals)

    base_windows, base_total, base_sized = window_pnls(bars, signals, ex)
    base = metrics.stats(base_sized, initial=INITIAL)
    print(f"ATR-scaled {PARAMS}, ${INITIAL:.0f} Forex NQ, cost {ex.entry_cost} (spread {ex.spread} + slippage {ex.slippage} + commission)")
    print(f"  baseline: pnl {base['pnl']} trades {base['trades']} "
          f"pf {base['pf']} mSharpe {base['msharpe']} pos_rate {base['pos_rate']}")
    print(f"  per-fold test PnL: {base_windows}")

    header = (f"\n    {'filter':<28} {'cut':>4} {'total':>9}  "
              + " ".join(f"{'f' + str(i):>7}" for i in range(1, 6))
              + "   improved")

    print("\n=== day of week " + "=" * 40)
    for day in range(5):
        n = sum(1 for c in ctx if c["weekday"] == day)
        pnl = sum(p for (_, p), c in zip(base_sized, ctx) if c["weekday"] == day)
        print(f"  {WEEKDAYS[day]}: {n:>4} signals, attributed pnl {pnl:>8.2f}")
    print(header)
    for day in range(5):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {WEEKDAYS[day]}", lambda c, d=day: c["weekday"] != d)

    print("\n=== entry hour " + "=" * 41)
    hours = sorted({c["hour"] for c in ctx})
    for hour in hours:
        n = sum(1 for c in ctx if c["hour"] == hour)
        pnl = sum(p for (_, p), c in zip(base_sized, ctx) if c["hour"] == hour)
        print(f"  {hour:02d}:00 {n:>4} signals, attributed pnl {pnl:>8.2f}")
    print(header)
    for hour in hours:
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {hour:02d}:00 entries", lambda c, h=hour: c["hour"] != h)

    print("\n=== side " + "=" * 46)
    print(header)
    for side in ("long", "short"):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip {side}s", lambda c, s=side: c["side"] != s)

    print("\n=== VIX level (tuned threshold -- diagnostic only) " + "=" * 5)
    print(header)
    for cut in (18, 20, 22, 25, 28):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX >= {cut}", lambda c, x=cut: not c["vix"] or c["vix"] < x)

    print("\n=== VIX day-over-day change (tuned threshold -- diagnostic) " + "=" * 1)
    print(header)
    for cut in (5, 10, 15):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX up >{cut}%",
                 lambda c, x=cut: c["vix_change"] is None or c["vix_change"] <= x)
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip VIX down >{cut}%",
                 lambda c, x=cut: c["vix_change"] is None or c["vix_change"] >= -x)

    print("\n=== trailing ATR level (tuned threshold -- diagnostic) " + "=" * 3)
    print(header)
    for cut in (400, 500, 600):
        try_drop(bars, ex, signals, ctx, base_windows, base_total,
                 f"skip ATR >= {cut}pt",
                 lambda c, x=cut: c["atr"] is None or c["atr"] < x)

    print("\n  * = helped or was neutral in all five folds")


if __name__ == "__main__":
    main()
