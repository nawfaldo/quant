"""Run the canon book's decisions against the BROKER's own bars.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the tables into the reply
([[paste-results-into-the-reply]]).

THE TABLES THIS READS WERE DELETED ON 2026-08-30, BY OPERATOR INSTRUCTION.

`exness_<broker>_1m` is gone from the store, so every command here now reports
`no exness_<broker>_1m -- run exness_import_1m.py` and produces nothing. That is
the correct failure and the recovery is exactly what it says: one run of
`tools/exness_import_1m.py` rebuilds all eleven from the terminal, which serves
M1 back to 1999.

Nothing in the canon book depends on them. `FILL_FEED` is False by default, so
`build --members canon` never asked for a broker bar; only `--broker-fills` and
`--broker-signals` did, and only this module. The tick tables that replaced them
(`exness_<broker>_ticks`) are NOT a substitute -- they start 2026-01-01, where
the bars ran from 2020, so a rebuilt comparison is the one to use rather than
re-deriving fills from ticks over an eighth of the window.

WHAT THIS ANSWERS.

Every canon sleeve signals off a vendor table -- `gbpjpy_1m` is Dukascopy,
`ethusd_1m` is Binance, `nq_1m` is Databento -- and every canon sleeve is
executed at Exness. Those are not the same price series. Different venue,
different aggregation, different gaps, and on the index CFDs a different
instrument entirely (`nq_1m` is the back-adjusted future, `USTEC` is a cash
CFD). Until now the book has simply assumed the vendor price is the fill price.

`data_manipulation/exness_import_1m.py` lands the broker's own one-minute bars
as `exness_<broker>_1m` on the same New York clock. This module holds the
DECISIONS fixed -- same signal, same bars, same context, same stop distance,
same timestamps -- and swaps only the prices that are paid and received. The
difference between the two runs is the cost of the assumption.

WHY THE SIGNAL FEED IS NOT ALSO SWAPPED.

Swapping both changes two things at once and the result cannot be read: a
different P&L could be a different fill or a different trade. Signal-on-vendor,
fill-on-broker isolates execution, which is the only leg the operator cannot
choose. `both` is available as a third run for the separate question of whether
the sleeves would have been FOUND on broker data, and it is not the comparison
this module is for.

WHAT A MISSING BAR MEANS, AND WHY IT IS NOT FILLED IN.

The two feeds do not agree on which minutes exist. Exness quotes a CFD on its
own schedule; Dukascopy and Binance quote on theirs. Where the study's bar has
no broker counterpart the vendor bar is carried through unchanged and COUNTED,
because interpolating a price the broker never quoted would hide exactly the
gap that matters. Read `matched_pct` before reading any P&L: a sleeve at 80%
match is a sleeve whose comparison is one fifth vendor prices.

    py -m sandbox.research.exness_broker_fills feeds
    py -m sandbox.research.exness_broker_fills sleeves
    py -m sandbox.research.exness_broker_fills sleeves --members nq:ofi,gbpjpy:trap
"""

import argparse
import json
import math
import os
import statistics

from sandbox import data
from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cs


TS, O, H, L, C, V = range(6)

OUT_PATH = os.path.join(os.path.dirname(__file__), "exness_broker_fills.json")

#: `exness_ustec_1m`, not `exness_nq_1m`. The table is named for the instrument
#: the broker actually quotes, so the name says which contract the fills came
#: from -- and `nq_1m` against `exness_ustec_1m` is a visible reminder that
#: those are a future and a cash CFD, not two views of one series.
TABLE_PREFIX = "exness_"

#: Mean signed close difference, in basis points, beyond which the two series
#: are treated as different INSTRUMENTS rather than two quotes of one. 50bp is
#: already an order of magnitude past what a venue difference produces on the
#: FX pairs (2-4bp) and comfortably clears the metals and indices; only the
#: back-adjusted-future-against-cash-CFD pairs breach it, which is exactly what
#: it is meant to catch.
LEVEL_SHIFT_LIMIT_BP = 50.0


def fill_table(symbol):
    return f"{TABLE_PREFIX}{ef.broker_symbol(symbol).lower()}_1m"


def has_fills(symbol):
    return fill_table(symbol) in ef.tables()


def broker_bars(symbol, bar=None):
    """`exness_<broker>_1m` bucketed exactly the way `all_bars` buckets a study.

    Same `SAMPLE BY`, same `FILL(NONE)`, same `ALIGN TO CALENDAR`, same
    `shift_hours`. It has to be the same or the two series land on different
    timestamps and nothing lines up -- which is the one failure mode that would
    look like a result rather than a bug.
    """
    spec = ef.INSTRUMENTS[symbol]
    bar = ef.BAR_MINUTES if bar is None else bar
    table, shift = fill_table(symbol), spec["shift_hours"]
    sql = ("SELECT cast(timestamp as long),first(open),max(high),min(low),"
           f"last(close),sum(volume) FROM {table} "
           f"WHERE timestamp >= '{spec['warmup']}' "
           f"SAMPLE BY {bar}m FILL(NONE) ALIGN TO CALENDAR")
    key = f"{sql}:{shift}:{data._table_fingerprint([table])}"

    def build():
        return [[int(row[0]) // 1_000_000 + shift * 3600,
                 *(float(value) for value in row[1:])]
                for row in data.query(sql)]

    return [tuple(row) for row in
            data._cached(f"brokerfill_{symbol}_{bar}m", key, build)]


def rescaled(broker, vendor):
    """`broker` put on `vendor`'s price level with a PREVIOUS-session ratio.

    WHY A RESCALE IS THE RIGHT FIX AND NOT A FUDGE. `nq_1m` is a ratio-back-
    adjusted futures continuum and `USTEC` is a cash CFD; the 2.6% between them
    is a real fact about the two instruments, not a data error. The strategy's
    economics are RELATIVE -- a stop is some fraction of the instrument -- but
    `backtest` is handed that stop as an absolute point distance taken off the
    vendor's daily range. Spend those points at a level 2.6% away and the stop
    is a different fraction, so the run stops being an execution test and
    becomes a different strategy. Rescaling restores the level and leaves
    everything the broker actually contributes: the intraday path, the wicks,
    the gaps, the spread, and which bars exist at all.

    THE RATIO IS FROM THE PREVIOUS SESSION AND HELD ALL DAY. A per-bar ratio
    would divide the broker's every move straight back out and hand back the
    vendor series exactly -- a test that can only ever report zero difference.
    A previous-session ratio cannot see the day it prices, so it carries no
    lookahead, and a stale ratio is itself part of what is being measured.
    """
    vendor_close, broker_close = {}, {}
    for row in vendor:
        vendor_close[row[TS] // 86_400] = row[C]
    for row in broker:
        broker_close[row[TS] // 86_400] = row[C]

    ratio_for, last = {}, None
    for day in sorted(set(vendor_close) | set(broker_close)):
        ratio_for[day] = last
        v, b = vendor_close.get(day), broker_close.get(day)
        if v and b and b > 0:
            last = v / b

    out = []
    for row in broker:
        ratio = ratio_for.get(row[TS] // 86_400)
        if not ratio:
            # No prior session to price off -- the first day, or a broker day
            # the vendor never quoted. Left alone rather than guessed at; the
            # caller counts these as unmatched.
            out.append(row)
            continue
        out.append((row[TS], row[O] * ratio, row[H] * ratio,
                    row[L] * ratio, row[C] * ratio, row[V]))
    return out


def auto_fills(symbol, bars, bar=None, window=None):
    """`aligned_fills`, rescaling ONLY the symbols that need it.

    Rescaling is not free: it prices every bar off the previous session's
    ratio, and on a symbol whose two feeds already agree that ratio is pure
    noise -- GBPJPY goes from 0.23bp of mean absolute difference to 15.2bp
    purely by being rescaled. So it is applied where the level shift says the
    two series are different instruments, and nowhere else.
    """
    fills, stats = aligned_fills(symbol, bars, bar, window)
    if stats["comparable"]:
        return fills, stats
    fills, rescale_stats = aligned_fills(symbol, bars, bar, window, rescale=True)
    rescale_stats["level_shift_before_bp"] = stats["level_shift_bp"]
    return fills, rescale_stats


def aligned_fills(symbol, bars, bar=None, window=None, rescale=False):
    """`(fill_bars, stats)` -- broker bars on `bars`' exact timestamps.

    `fill_bars[i]` is the broker's bar for `bars[i][TS]` where one exists and
    `bars[i]` itself where none does, so the list is always the same length and
    `backtest` can index it without a branch. `stats` reports how much of it is
    really broker data and how far the two feeds sit apart.

    THE STATS ARE SCOPED TO `window`, THE LIST IS NOT. `bars` runs from the
    symbol's warm-up year, and the broker tables begin in 2020 or 2022, so a
    match rate over the whole list mostly measures how much history Exness
    happens to serve -- GBPJPY read 57% that way while being complete over
    every bar the comparison actually trades. `fill_bars` still has to span the
    full list because `backtest` indexes it from bar zero.
    """
    lo, hi = window if window else (float("-inf"), float("inf"))
    series = broker_bars(symbol, bar)
    if rescale:
        series = rescaled(series, bars)
    broker = {row[TS]: row for row in series}
    fills, matched, scoped, diffs = [], 0, 0, []
    for row in bars:
        other = broker.get(row[TS])
        inside = lo <= row[TS] < hi
        scoped += inside
        if other is None:
            fills.append(row)
            continue
        fills.append(other)
        if not inside:
            continue
        matched += 1
        if row[C] > 0:
            diffs.append(1e4 * (other[C] - row[C]) / row[C])
    total = scoped or 1
    stats = {
        "symbol": symbol, "table": fill_table(symbol),
        "vendor_table": ef.INSTRUMENTS[symbol]["table"],
        "bars": scoped, "matched": matched,
        "matched_pct": round(100 * matched / total, 2),
        # Signed and absolute both, because they say different things. A signed
        # mean is a systematic quote offset -- the broker's mid sitting above or
        # below the vendor's -- and survives averaging. An absolute mean is
        # dispersion, which is what a stop actually trades against, and does not.
        "close_bias_bp": round(statistics.fmean(diffs), 4) if diffs else None,
        "close_abs_bp": round(statistics.fmean([abs(d) for d in diffs]), 4)
                        if diffs else None,
        "close_sd_bp": round(statistics.stdev(diffs), 4) if len(diffs) > 1 else None,
        "close_p99_bp": round(sorted(abs(d) for d in diffs)[int(0.99 * len(diffs))], 4)
                        if len(diffs) > 100 else None,
    }
    # A LEVEL SHIFT IS NOT AN EXECUTION DIFFERENCE AND MUST NOT BE READ AS ONE.
    # `nq_1m` is a ratio-back-adjusted futures continuum and `USTEC` is a cash
    # CFD ([[nq-has-two-incompatible-price-series]], [[es-bars-are-back-adjusted]]),
    # so they sit 7% apart in level. The stop distance handed to `backtest` is
    # in POINTS off the vendor's daily range; spend those points on a series
    # 7% away and the stop is a different fraction of the instrument, which is
    # a different strategy rather than a different fill. Flagged, not silently
    # priced.
    stats["rescaled"] = bool(rescale)
    stats["level_shift_bp"] = stats["close_bias_bp"]
    stats["comparable"] = (stats["close_bias_bp"] is not None
                           and abs(stats["close_bias_bp"]) <= LEVEL_SHIFT_LIMIT_BP)
    return fills, stats


# --------------------------------------------------------------------------- #
# reports
# --------------------------------------------------------------------------- #

def feeds(symbols=None, bar=None, window=None, rescale=False):
    """How far the broker's bars sit from the vendor's, before any strategy."""
    bar = cs.BAR if bar is None else bar
    ef.BAR_MINUTES = bar
    window = (ef.IS_END, ef.OOS_END) if window is None else window
    rows = []
    for symbol in symbols or sorted({m.split(":")[0] for m in cs.BOOK}):
        if not has_fills(symbol):
            rows.append({"symbol": symbol, "table": fill_table(symbol),
                         "error": "no broker table -- run exness_import_1m.py"})
            continue
        try:
            ef.resolve(symbol, allow_stale=True)
            bars, _ = cs._context(symbol)
            _, stats = aligned_fills(symbol, bars, bar, window, rescale)
        except Exception as error:  # noqa: BLE001 - one feed must not cost the rest
            stats = {"symbol": symbol, "error": f"{type(error).__name__}: {error}"}
        rows.append(stats)
    return rows


def _run(row, fill_bars, lo, hi):
    bars, ctx = cs._context(row["symbol"])
    return ef.backtest(row["family"], bars, ctx, row["params"], lo=lo, hi=hi,
                       initial=ef.INITIAL_BALANCE, include_trades=True,
                       fill_bars=fill_bars)


def sleeves(members=None, bar=None, window=None, rescale=False):
    """Each canon sleeve run on vendor fills and again on broker fills.

    The signal feed is the vendor table in BOTH runs, so the only thing that
    moves is the price paid. See the module docstring for why swapping the
    signal feed as well would make the difference unreadable.
    """
    bar = cs.BAR if bar is None else bar
    ef.BAR_MINUTES = bar
    members = list(members or cs.BOOK)
    lo, hi = (ef.IS_END, ef.OOS_END) if window is None else window
    rows = cs.candidate_rows_exact(members)

    out = []
    for key in members:
        symbol, family = key.split(":")
        row = rows.get(key)
        if row is None:
            out.append({"sleeve": key, "error": "not in the survivor pool"})
            continue
        if not has_fills(symbol):
            out.append({"sleeve": key,
                        "error": f"no {fill_table(symbol)} -- run exness_import_1m.py"})
            continue
        try:
            ef.resolve(symbol, allow_stale=True)
            bars, _ = cs._context(symbol)
            fills, stats = (aligned_fills(symbol, bars, bar, (lo, hi), True)
                            if rescale else auto_fills(symbol, bars, bar, (lo, hi)))
            vendor = _run(row, None, lo, hi)
            broker = _run(row, fills, lo, hi)
        except Exception as error:  # noqa: BLE001 - one sleeve must not cost 19
            out.append({"sleeve": key, "error": f"{type(error).__name__}: {error}"})
            continue

        same = _trade_overlap(vendor["trade_log"], broker["trade_log"])
        out.append({
            "sleeve": key, "symbol": symbol, "family": family,
            "matched_pct": stats["matched_pct"],
            "comparable": stats["comparable"],
            "rescaled": stats["rescaled"],
            "level_shift_bp": stats["level_shift_bp"],
            "close_bias_bp": stats["close_bias_bp"],
            "close_abs_bp": stats["close_abs_bp"],
            "vendor_return": round(vendor["return_pct"], 2),
            "broker_return": round(broker["return_pct"], 2),
            "return_delta": round(broker["return_pct"] - vendor["return_pct"], 2),
            "vendor_dd": round(vendor["max_dd_pct"], 2),
            "broker_dd": round(broker["max_dd_pct"], 2),
            "vendor_trades": vendor["trades"], "broker_trades": broker["trades"],
            "vendor_pf": round(vendor["pf"], 3) if vendor["pf"] == vendor["pf"] else None,
            "broker_pf": round(broker["pf"], 3) if broker["pf"] == broker["pf"] else None,
            "vendor_bp": vendor.get("gross_bp_per_trade"),
            "broker_bp": broker.get("gross_bp_per_trade"),
            # The number that says whether the two runs are even comparable.
            # Same entry timestamps means the same DECISIONS were taken and the
            # difference is purely price; a low share means the broker feed
            # moved the trades themselves, through a stop that fired on one
            # series and not the other.
            "shared_entry_pct": same["shared_pct"],
            "bp_on_shared": same["bp_delta"],
        })
    return out


def _trade_overlap(vendor, broker):
    """How much of the two trade lists is the same decision at a different price."""
    left = {t["entry_ts"]: t for t in vendor}
    right = {t["entry_ts"]: t for t in broker}
    shared = sorted(set(left) & set(right))
    total = max(len(left), 1)
    deltas = [1e4 * (right[ts]["gross"] - left[ts]["gross"]) / left[ts]["entry"]
              for ts in shared if left[ts]["entry"]]
    return {"shared": len(shared),
            "shared_pct": round(100 * len(shared) / total, 1),
            "bp_delta": round(statistics.fmean(deltas), 3) if deltas else None}


# --------------------------------------------------------------------------- #
# printing
# --------------------------------------------------------------------------- #

def print_feeds(rows):
    print(f"\n{'symbol':8s} {'broker table':22s} {'vendor table':16s} "
          f"{'bars':>8s} {'match%':>7s} {'bias bp':>9s} {'abs bp':>8s} {'p99 bp':>8s}")
    print("-" * 92)
    for row in rows:
        if "error" in row:
            print(f"{row['symbol']:8s} {row.get('table',''):22s} -- {row['error']}")
            continue
        def fmt(key, width=9):
            value = row.get(key)
            return f"{value:>{width}.3f}" if isinstance(value, float) else f"{'--':>{width}}"
        print(f"{row['symbol']:8s} {row['table']:22s} {row['vendor_table']:16s} "
              f"{row['bars']:>8,} {row['matched_pct']:>7.2f} "
              f"{fmt('close_bias_bp')} {fmt('close_abs_bp', 8)} {fmt('close_p99_bp', 8)}")


def print_sleeves(rows):
    print(f"\n{'sleeve':28s} {'match%':>7s} {'shared%':>8s} "
          f"{'vendor%':>9s} {'broker%':>9s} {'delta%':>9s} "
          f"{'v dd':>7s} {'b dd':>7s} {'v trd':>6s} {'b trd':>6s} {'dbp/trade':>10s}")
    print("-" * 118)
    ok = [r for r in rows if "error" not in r and r["comparable"]]
    for row in rows:
        if "error" in row:
            print(f"{row['sleeve']:28s} -- {row['error']}")
            continue
        delta_bp = row["bp_on_shared"]
        tail = f"{delta_bp:>10.3f}" if delta_bp is not None else f"{'--':>10}"
        if row["rescaled"]:
            tail += "   rescaled"
        if not row["comparable"]:
            tail += f"   LEVEL MISMATCH {row['level_shift_bp']:+.0f}bp"
        print(f"{row['sleeve']:28s} {row['matched_pct']:>7.2f} "
              f"{row['shared_entry_pct']:>8.1f} "
              f"{row['vendor_return']:>9.2f} {row['broker_return']:>9.2f} "
              f"{row['return_delta']:>9.2f} "
              f"{row['vendor_dd']:>7.2f} {row['broker_dd']:>7.2f} "
              f"{row['vendor_trades']:>6d} {row['broker_trades']:>6d} {tail}")
    if ok:
        print("-" * 118)
        worse = sum(r["return_delta"] < 0 for r in ok)
        print(f"{'TOTAL':28s} {'':7s} {'':8s} "
              f"{sum(r['vendor_return'] for r in ok):>9.2f} "
              f"{sum(r['broker_return'] for r in ok):>9.2f} "
              f"{sum(r['return_delta'] for r in ok):>9.2f}"
              f"   {worse}/{len(ok)} sleeves worse on broker fills")
        print("\nSums of STANDALONE returns, not a book. They do not compound "
              "together and are here to rank the damage, not to size anything.")
    excluded = [r for r in rows if "error" not in r and not r["comparable"]]
    if excluded:
        print("\nEXCLUDED FROM THE TOTAL. The vendor and broker series are "
              "different instruments,\nnot two quotes of one, so a "
              "point-denominated stop is a different fraction of\neach and the "
              "swap is not a fill test:")
        for row in excluded:
            print(f"  {row['sleeve']:28s} level shift "
                  f"{row['level_shift_bp']:+8.0f}bp   "
                  f"{row['vendor_return']:+8.2f}% -> {row['broker_return']:+8.2f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("feeds", "sleeves"))
    parser.add_argument("--members", help="comma-separated sleeves; default canon")
    parser.add_argument("--symbols", help="comma-separated symbols for `feeds`")
    parser.add_argument("--bar", type=int, default=None)
    parser.add_argument("--rescale", action="store_true",
                        help="put the broker series on the vendor's price "
                             "level with a previous-session ratio, so a "
                             "point-denominated stop is the same fraction of "
                             "both; required for nq and ukoil")
    parser.add_argument("--path", default=OUT_PATH)
    args = parser.parse_args()

    if args.command == "feeds":
        rows = feeds(args.symbols.split(",") if args.symbols else None,
                     args.bar, rescale=args.rescale)
        print("\nStats are scoped to the comparison window "
              f"({ef.IS_END} .. {ef.OOS_END}), not to the whole bar list.")
        print_feeds(rows)
    else:
        members = args.members.split(",") if args.members else None
        rows = sleeves(members, args.bar, rescale=args.rescale)
        print_sleeves(rows)

    with open(args.path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)
    print(f"\nwrote {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
