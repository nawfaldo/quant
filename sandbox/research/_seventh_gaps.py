"""Data coverage and gaps, per canon symbol, on the series each sleeve reads.

A sleeve is only as good as the bars it decides on. This measures, for every
symbol in the book, how complete the 30m series actually is: sessions present
against sessions expected, bars per session against the session length, and the
longest runs of missing trading days.

TWO SERIES PER SYMBOL AND THEY ANSWER DIFFERENT QUESTIONS. The DECIDING series
is what `cfd_families` reads to generate signals; the BROKER 1m series is
what `fill_models.exness` prices fills from. A hole in the first changes the
strategy; a hole in the second only means that bar keeps the constant spread and
the correction is a lower bound ([[exness-tick-tables-have-daily-holes]]).

Weekends and the symbol's own non-session hours are not gaps and are excluded --
counting them would make every instrument look broken.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_gaps
"""
from __future__ import annotations

import argparse
import statistics
from collections import Counter
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import cfd_families as ef
from sandbox.research.fill_models import exness as le


def day_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def weekday(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).weekday()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="2022-01-01")
    args = parser.parse_args()
    lo = int(datetime.fromisoformat(args.start)
             .replace(tzinfo=timezone.utc).timestamp())

    symbols = sorted({k.split(":")[0] for k in ecs.BOOK})
    print(f"DECIDING SERIES, 30m bars, from {args.start}")
    print(f"{'symbol':9}{'table':16}{'session':>9}{'expect':>8}"
          f"{'bars/day':>10}{'days':>7}{'missing':>9}{'worst gap':>11}"
          f"  longest hole")
    rows = {}
    for symbol in symbols:
        ef.resolve(symbol, allow_stale=True)
        spec = ef.INSTRUMENTS[symbol]
        bars, _ctx = ecs._context(symbol)
        bars = [b for b in bars if b[ef.TS] >= lo]
        if not bars:
            print(f"{symbol:9}no bars in window")
            continue
        opened, closed = spec["session"]
        expected = max(1, (closed - opened) // ecs.BAR)
        per_day = Counter(day_of(b[ef.TS]) for b in bars)
        days = sorted(per_day)
        # Weekdays between the first and last bar that hold NO bar at all.
        first = datetime.fromisoformat(days[0]).replace(tzinfo=timezone.utc)
        last = datetime.fromisoformat(days[-1]).replace(tzinfo=timezone.utc)
        want, cursor = [], first
        while cursor <= last:
            if cursor.weekday() < 5:
                want.append(cursor.strftime("%Y-%m-%d"))
            cursor = datetime.fromtimestamp(cursor.timestamp() + 86_400,
                                            tz=timezone.utc)
        missing = [d for d in want if d not in per_day]
        run = best = 0
        best_at = "-"
        for d in want:
            if d in per_day:
                run = 0
            else:
                run += 1
                if run > best:
                    best, best_at = run, d
        median_bars = statistics.median(per_day.values())
        rows[symbol] = {"missing": len(missing), "want": len(want),
                        "median": median_bars, "expected": expected}
        print(f"{symbol:9}{spec['table'][:15]:16}"
              f"{(closed - opened) // 60:>7}h{expected:>8}"
              f"{median_bars:>10.0f}{len(want):>7,}"
              f"{len(missing):>8,} ({100 * len(missing) / len(want):>3.0f}%)"
              f"{best:>6} days  {best_at if best else '-'}")

    print(f"\nBROKER 1m MAPS (what live execution prices fills from)")
    print(f"{'symbol':9}{'has map':>9}{'bars priced':>13}{'of total':>10}"
          f"{'coverage':>10}")
    import json
    with open(le.maps_path("bars"), encoding="utf-8") as handle:
        maps = json.load(handle)
    for symbol in symbols:
        row = maps.get(symbol)
        if row is None:
            print(f"{symbol:9}{'NO':>9}{'-':>13}{'-':>10}"
                  f"{'constant spread':>18}")
            continue
        priced, total = len(row["spread_bp"]), row["bars"]
        print(f"{symbol:9}{'yes':>9}{priced:>13,}{total:>10,}"
              f"{100 * priced / total:>9.0f}%")


if __name__ == "__main__":
    main()
