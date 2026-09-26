"""What fraction of the book's TRADES the live-execution correction reaches.

BAR COVERAGE IS THE WRONG DENOMINATOR. The maps price 25-57% of bars per symbol,
which sounds alarming, but a sleeve does not trade every bar -- it trades a few
hundred times over four years, and the only question that matters is whether
THOSE bars are priced. A symbol can be 40% covered on bars and 95% covered on
trades, or the reverse.

WHAT AN UNPRICED TRADE GETS. It keeps the constant spread and the vendor bar
open, which is exactly the SEALED treatment. So a partially covered book is a
BLEND of live and sealed, and the live-minus-sealed delta is a lower bound on
execution cost in magnitude -- not a conservative bound in direction, because
nothing says the uncovered trades would move the same way
([[exness-tick-tables-have-daily-holes]]).

SPLIT BY ERA, because the broker tables start 2022-08 at the earliest and the
holdout is the window the account will actually trade. Coverage on 2025-2026 is
the number that governs whether the OOS execution figures can be trusted.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_coverage
"""
from __future__ import annotations

import argparse
import json
import pickle
from collections import defaultdict
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research.fill_models import exness as le


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="bars")
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    with open(le.maps_path(args.source), encoding="utf-8") as handle:
        maps = json.load(handle)
    priced = {symbol: set(int(k) for k in row["entry"])
              for symbol, row in maps.items()}

    eras = (("IS 2022-2024", stamp("2022-01-01"), stamp("2025-01-01")),
            ("OOS 2025-2026", stamp("2025-01-01"), stamp("2026-08-21")))

    print(f"TRADE-LEVEL COVERAGE -- share of entries the live maps can reprice")
    print(f"{'sleeve':30}" + "".join(f"{name:>22}" for name, _a, _b in eras))
    print(f"{'':30}" + "".join(f"{'trades':>10}{'priced':>12}"
                               for _n, _a, _b in eras))
    totals = defaultdict(lambda: [0, 0])
    by_symbol = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for key in ecs.BOOK:
        symbol = key.split(":")[0]
        seen = priced.get(symbol, set())
        line = f"{key:30}"
        for name, lo, hi in eras:
            n = hit = 0
            for trade in state["logs"][key]:
                ts = trade["entry_ts"]
                if not (lo <= ts < hi):
                    continue
                n += 1
                if ts in seen:
                    hit += 1
            totals[name][0] += n
            totals[name][1] += hit
            by_symbol[symbol][name][0] += n
            by_symbol[symbol][name][1] += hit
            share = (100.0 * hit / n) if n else float("nan")
            line += f"{n:>10,}{share:>11.0f}%"
        print(line)

    print(f"\n{'BY SYMBOL':30}" + "".join(f"{name:>22}"
                                          for name, _a, _b in eras))
    for symbol in sorted(by_symbol):
        line = f"{symbol:30}"
        for name, _lo, _hi in eras:
            n, hit = by_symbol[symbol][name]
            share = (100.0 * hit / n) if n else float("nan")
            line += f"{n:>10,}{share:>11.0f}%"
        print(line)

    print(f"\n{'BOOK TOTAL':30}", end="")
    for name, _lo, _hi in eras:
        n, hit = totals[name]
        print(f"{n:>10,}{100.0 * hit / n:>11.0f}%", end="")
    print()


if __name__ == "__main__":
    main()
