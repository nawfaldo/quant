"""Why 2025 is flat and 2026 is not.

The dollar PnL of a compounding account cannot answer this: equity grows, so a
2026 trade is sized larger than an identical 2025 one and earns more for the
same edge. `execution.resolve` returns per-unit `points`, which is the same
trade measured without that confound -- so the first table separates the two.

If points per trade are equal across the years, the split is sizing and there is
nothing to explain. If they differ, the second half asks what changed: the
market (volatility, trend), or the data feed itself (spread, book validity,
activity). The third possibility -- that the parameters were fit on 2026 -- is
what OPTIMIZATION_PLAN.md Stage 1 documents, and it is partly controlled for
here because these constants came out of an anchored walk-forward whose train
windows all start in 2025-02.
"""
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import strategies
from sandbox.data import DE, TS

INITIAL = 1000.0
MEMBERS = ["Hourly Delta Reversal", "Deep OFI Momentum"]


def year(ts):
    return metrics.month_key(ts)[:4]


def member_fills(name, bars, ex):
    strategy = strategies.get(name)
    context = strategy.context()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, strategy.all_params(None)))
    return execution.resolve(bars, signals, ex), signals


def edge_table(name, fills):
    """Per-unit edge by year -- the number position sizing cannot inflate."""
    print(f"\n  {name}")
    print(f"    {'year':>6} {'trades':>7} {'pts/trade':>10} {'total pts':>10} "
          f"{'win%':>6} {'pf(pts)':>8} {'mean win':>9} {'mean loss':>10}")
    for y in ("2025", "2026"):
        rows = [f for f in fills if year(f.entry_ts) == y]
        if not rows:
            continue
        pts = [f.points for f in rows]
        wins = [p for p in pts if p > 0]
        losses = [-p for p in pts if p < 0]
        pf = sum(wins) / sum(losses) if losses else float("inf")
        print(f"    {y:>6} {len(rows):>7} {sum(pts) / len(pts):>10.3f} "
              f"{sum(pts):>10.1f} {100 * len(wins) / len(pts):>6.1f} {pf:>8.3f} "
              f"{(sum(wins) / len(wins) if wins else 0):>9.2f} "
              f"{(sum(losses) / len(losses) if losses else 0):>10.2f}")


def dollar_table(name, fills, ex):
    sized = execution.size(fills, ex)
    print(f"\n  {name} -- dollars on a compounding $1000 account")
    print(f"    {'year':>6} {'trades':>7} {'pnl':>10} {'$/trade':>9}")
    for y in ("2025", "2026"):
        rows = [(ts, p) for ts, p in sized if year(ts) == y]
        if rows:
            total = sum(p for _, p in rows)
            print(f"    {y:>6} {len(rows):>7} {total:>10.2f} "
                  f"{total / len(rows):>9.4f}")


def market_table(bars, features):
    """What the data itself looks like in each year.

    A change here is a change in the market or in the feed, and the two are not
    the same thing: a wider spread is a cost, a lower `book_valid` rate is a
    gap in the recording.
    """
    atr = data.atr_by_day(bars, 20)
    vix = data.vix_series(bars)
    print(f"\n  {'year':>6} {'sessions':>9} {'bars':>8} {'ATR':>8} {'VIX':>7} "
          f"{'spread':>8} {'book_ok':>8} {'trades/m':>9} {'depth/m':>9} {'|OFI|':>9}")
    for y in ("2025", "2026"):
        rows = [b for b in bars if year(b[TS]) == y]
        if not rows:
            continue
        days = {b[TS] // 86_400 for b in rows}
        spreads, valid, counts, ofis = [], 0, [], []
        for bar in rows:
            row = features.get(bar[TS])
            if row is None:
                continue
            if row["book_valid"]:
                valid += 1
                spreads.append(row["spread"])
            counts.append(row["trade_count"])
            ofis.append(abs((row["bid_add_volume"] - row["bid_cancel_volume"])
                            - (row["ask_add_volume"] - row["ask_cancel_volume"])))
        atrs = [atr[d] for d in days if d in atr]
        vixs = [v for b, v in zip(bars, vix) if year(b[TS]) == y and v > 0]
        depth = [b[DE] for b in rows]
        print(f"  {y:>6} {len(days):>9} {len(rows):>8} "
              f"{(sum(atrs) / len(atrs) if atrs else 0):>8.1f} "
              f"{(sum(vixs) / len(vixs) if vixs else 0):>7.2f} "
              f"{(sum(spreads) / len(spreads) if spreads else 0):>8.3f} "
              f"{100 * valid / max(1, len(rows)):>7.1f}% "
              f"{(sum(counts) / len(counts) if counts else 0):>9.1f} "
              f"{(sum(depth) / len(depth) if depth else 0):>9.1f} "
              f"{(sum(ofis) / len(ofis) if ofis else 0):>9.1f}")


def trend_table(bars):
    """Directional character of each year, from the traded bars themselves.

    Both strategies are conditioned on direction -- Hourly Delta Reversal fades
    an hourly candle and gates shorts on a trend filter, deep OFI trades with
    book pressure -- so how much the market actually travelled per unit of
    range is the regime variable that matters to them.
    """
    sessions = data.session_ranges(bars)
    print(f"\n  {'year':>6} {'sessions':>9} {'net move':>10} {'mean |chg|':>11} "
          f"{'up days':>8} {'mean range':>11}")
    for y in ("2025", "2026"):
        rows = [s for s in sessions if year(s[0] * 86_400) == y]
        if len(rows) < 2:
            continue
        closes = [s[3] for s in rows]
        changes = [b - a for a, b in zip(closes, closes[1:])]
        ranges = [s[1] - s[2] for s in rows]
        print(f"  {y:>6} {len(rows):>9} {closes[-1] - closes[0]:>10.1f} "
              f"{sum(abs(c) for c in changes) / len(changes):>11.1f} "
              f"{100 * sum(1 for c in changes if c > 0) / len(changes):>7.1f}% "
              f"{sum(ranges) / len(ranges):>11.1f}")


def main():
    bars = data.load_bars("level_two", "nq")
    features = data.load_l2_features("nq")
    ex = replace(strategies.get(MEMBERS[0]).execution, initial=INITIAL)

    print(f"2025 vs 2026 -- {len(bars)} bars, {data.bar_range('level_two', 'nq')}")

    print("\n" + "=" * 78)
    print("=== 1. edge per unit (position sizing removed)")
    print("=" * 78)
    pooled = []
    for name in MEMBERS:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL)
        fills, _ = member_fills(name, bars, member_ex)
        pooled.extend(fills)
        edge_table(name, fills)
    edge_table("COMBINED", pooled)

    print("\n" + "=" * 78)
    print("=== 2. the same trades in dollars (position sizing restored)")
    print("=" * 78)
    for name in MEMBERS:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL)
        fills, _ = member_fills(name, bars, member_ex)
        dollar_table(name, fills, member_ex)
    dollar_table("COMBINED", pooled, ex)

    print("\n" + "=" * 78)
    print("=== 3. what the data looks like in each year")
    print("=" * 78)
    market_table(bars, features)

    print("\n" + "=" * 78)
    print("=== 4. directional character of each year")
    print("=" * 78)
    trend_table(bars)


if __name__ == "__main__":
    main()
