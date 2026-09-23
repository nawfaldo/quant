"""Sealed against live execution on a CHOSEN window, not the tick window.

`exness_live_execution.book` opens its window where the last symbol's broker
history begins -- 2022-08 on this book, because `ukoil` starts there -- which is
the right default for measuring the execution correction on as much data as
exists, and the wrong one for asking what the correction is worth on the holdout
the account is about to trade.

This runs the same two books `_book_runs` runs, at the same canon settings,
with `ef.IS_END` moved to the requested start. It is `_book_runs` itself, not a
reimplementation, so the numbers are comparable to `book`'s by construction.

`ef.IS_END` is restored in a `finally`: leaving it moved would make a later
`build --members canon` silently report a different window.

    MC_LIVE_START= py -m sandbox.research._seventh_sealed_live --from 2025-01-01
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_families as ef
from sandbox.research import exness_live_execution as le


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="2025-01-01")
    parser.add_argument("--source", default=le.DEFAULT_FEED)
    args = parser.parse_args()

    lo = int(datetime.fromisoformat(args.start)
             .replace(tzinfo=timezone.utc).timestamp())
    cs.MAPS_OVERRIDE = le.maps_path(args.source)
    sealed_is_end = ef.IS_END
    try:
        ef.IS_END = lo
        print(f"WINDOW {args.start} .. {le._stamp(ef.OOS_END)} UTC on the "
              f"{args.source} feed\n", flush=True)
        runs = le._book_runs(cs)
    finally:
        ef.IS_END = sealed_is_end

    sealed = runs["sealed"]["book"]
    live = runs["live"]["book"]

    print(f"\n{'=' * 78}\nSEALED / LIVE   {args.start} .. "
          f"{le._stamp(ef.OOS_END)}\n{'=' * 78}")
    print(f"  {'':24}{'sealed':>13}{'live':>13}{'live - sealed':>15}")
    for field, label, unit in (("return_pct", "return", "%"),
                               ("mtm_dd_pct", "MTM dd  <- read this", "%"),
                               ("max_dd_pct", "closed dd", "%"),
                               ("final", "final equity", ""),
                               ("trades", "trades", "")):
        a, b = sealed.get(field), live.get(field)
        if a is None or b is None:
            continue
        print(f"  {label:24}{a:>12,.1f}{unit:1}{b:>12,.1f}{unit:1}"
              f"{b - a:>+14,.1f}{unit:1}")
    print(f"  {'below broker minimum':24}"
          f"{sum(sealed['below_broker_minimum'].values()):>13,}"
          f"{sum(live['below_broker_minimum'].values()):>13,}")

    print(f"\n  {'sleeve':30}{'bk seal $':>11}{'bk live $':>11}"
          f"{'delta $':>10}{'live ddEvt%':>13}")
    for name in cs.BOOK:
        a = sealed["by_sleeve"].get(name, {})
        b = live["by_sleeve"].get(name, {})
        print(f"  {name:30}{a.get('pnl', 0.0):>11,.0f}"
              f"{b.get('pnl', 0.0):>11,.0f}"
              f"{b.get('pnl', 0.0) - a.get('pnl', 0.0):>+10,.0f}"
              f"{100 * (b.get('dd_event_share') or 0.0):>12.1f}%")


if __name__ == "__main__":
    main()
