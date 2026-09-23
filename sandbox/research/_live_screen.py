"""Which candidates survive being executed the way the account actually fills.

THIS IS A FILTER, NOT A SELECTION, AND THE DIFFERENCE IS THE WHOLE POINT.

The pool was screened on 2025-2026 ([[exness-survivor-pool-is-oos-conditioned]]),
which is the same window the fills are measured over. RANKING these cells by
their live return would therefore be selection on top of selection -- picking
whichever cell best survives a cost model on the data it was already fitted to,
which is how the next `xniusd:cci` gets manufactured.

So nothing here is ranked into a book. Each cell is asked one question with a
yes/no answer -- does its edge survive paying for execution -- and the ones that
fail are removed. Removing fill-dependent cells cannot overfit: it only ever
shrinks the pool, and it shrinks it on a mechanical cost rather than on a
return.

THE TEST IS PAIRED. Same trade, two fills, so the per-trade cost is measured
against its own dispersion instead of the strategy's. Unpaired, every sleeve
looks insignificant because per-trade variance swamps a few basis points.

    py -m sandbox.research._live_screen
"""
import platform

platform._wmi = None

import contextlib
import io
import json
import math
import os
import statistics

from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_live_execution as lx

OUT_PATH = os.path.join(os.path.dirname(__file__), "live_screen.json")

#: A cell fails if its live return is not positive, or if the execution cost is
#: significantly negative. Both are absolute tests against the cell itself --
#: neither compares it with another cell, which is what keeps this a filter.
MIN_T = -2.0

#: How far short of the window's end a symbol's coverage may fall and still be
#: screened, in days.
#:
#: NOT ZERO, AND NOT GENEROUS EITHER. The exclusion exists so cells are not
#: compared over different windows, and a symbol whose deciding table stops
#: twelve hours early loses 0.1% of a twenty-month span -- that is not a
#: comparability problem, it is a rounding error. A symbol that stops one or two
#: WEEKS early is a different matter, and the observed gap is bimodal: de40 and
#: es fall half a day short while hk50, nq and the three gold crosses fall seven
#: to fourteen days short. Three days sits in the empty middle.
MAX_SHORT_DAYS = 3


def main():
    # The window is the BOOK's, deliberately: a candidate is being judged for
    # the book it would join, so it has to be priced over the same span the
    # book is. Symbols that cannot cover that span are excluded rather than
    # screened over a shorter one -- comparing cells measured on different
    # windows is the bias this whole exercise exists to avoid.
    lo = lx.tick_window_start(source="bars")
    maps = lx.load_maps(source="bars")
    spans = lx.map_spans(source="bars")
    with contextlib.redirect_stdout(io.StringIO()):
        pool = cs.candidates()

    pool_symbols = {r["symbol"] for r in pool}
    no_map = sorted(s for s in pool_symbols if s not in maps)
    # A stale VENDOR table, not a missing broker one: a map only holds stamps
    # the deciding series also has, so `nq`, `hk50` and the gold crosses come
    # up short here while `exness_<broker>_1m` runs to the window's end.
    short = sorted(s for s in pool_symbols
                   if s in spans
                   and spans[s][1] < ef.OOS_END - MAX_SHORT_DAYS * 86400)
    skipped = sorted(set(no_map) | set(short))
    rows = {}
    print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, LIVE fills")
    if no_map:
        print(f"no 1m table, cannot be screened: {', '.join(no_map)}")
    for sym in short:
        print(f"vendor table ends {lx._stamp(spans[sym][1])}, excluded: {sym}")
    print(flush=True)
    head = (f"{'candidate':32}{'in book':>8}{'trd':>5}{'sealed':>9}{'live':>9}"
            f"{'cost bp':>9}{'t':>7}  verdict")
    print(head, flush=True)
    print("-" * (len(head) + 10), flush=True)

    for row in pool:
        symbol, family = row["symbol"], row["family"]
        name = f"{symbol}:{family}"
        if symbol in skipped:
            continue
        spreads, entries, exits = maps[symbol]
        ef.resolve(symbol, allow_stale=True)
        bars, ctx = cs._context(symbol)
        common = dict(lo=lo, hi=ef.OOS_END, include_trades=True,
                      fill_bars=cs._fill_bars(symbol, bars))
        a = ef.backtest(family, bars, ctx, row["params"], **common)
        b = ef.backtest(family, bars, ctx, row["params"],
                        tick_spreads=spreads, entry_prices=entries,
                        exit_prices=exits, **common)
        A = {t["entry_ts"]: 1e4 * t["points"] / t["entry"]
             for t in a["trade_log"] if t["entry"]}
        B = {t["entry_ts"]: 1e4 * t["points"] / t["entry"]
             for t in b["trade_log"] if t["entry"]}
        both = sorted(set(A) & set(B))
        if len(both) < 20:
            continue
        delta = [B[k] - A[k] for k in both]
        cost = statistics.fmean(delta)
        sd = statistics.stdev(delta)
        t = cost / (sd / math.sqrt(len(delta))) if sd else 0.0
        ok = b["return_pct"] > 0 and t > MIN_T
        verdict = "survives" if ok else (
            "FAILS: unprofitable live" if b["return_pct"] <= 0
            else "FAILS: cost significant")
        rows[name] = {"in_book": name in cs.BOOK, "trades": a["trades"],
                      "sealed_return_pct": a["return_pct"],
                      "live_return_pct": b["return_pct"],
                      "cost_bp": cost, "t": t, "survives": ok}
        print(f"{name:32}{'yes' if name in cs.BOOK else '':>8}"
              f"{a['trades']:>5}{a['return_pct']:>8.1f}%{b['return_pct']:>8.1f}%"
              f"{cost:>9.2f}{t:>7.2f}  {verdict}", flush=True)

    live = [k for k, v in rows.items() if v["survives"]]
    dead = [k for k, v in rows.items() if not v["survives"]]
    print(f"\n{len(live)} survive, {len(dead)} fail, of {len(rows)} screened")
    print("\nIN BOOK AND FAILING -- remove these:")
    for k in dead:
        if rows[k]["in_book"]:
            print(f"  {k:32}live {rows[k]['live_return_pct']:+.1f}%  "
                  f"t {rows[k]['t']:.2f}")
    print("\nNOT IN BOOK AND SURVIVING -- eligible, NOT ranked:")
    for k in sorted(live, key=lambda k: -rows[k]["live_return_pct"]):
        if not rows[k]["in_book"]:
            print(f"  {k:32}live {rows[k]['live_return_pct']:+7.1f}%  "
                  f"cost {rows[k]['cost_bp']:+6.2f}bp  t {rows[k]['t']:5.2f}")
    print("\nEligibility is not a reason to seat one. These cells were chosen "
          "on this same\n  window, so their RETURNS here are conditioned; only "
          "the pass/fail is\n  mechanical. Seat one only after a drop test on "
          "the book it would join.")
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                   "min_t": MIN_T, "unscreenable": skipped,
                   "candidates": rows}, handle, indent=2)
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    raise SystemExit(main())
