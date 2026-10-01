"""Replay the sealed survivors in other trading windows.

    python -m sandbox.research.exness_window_replay --window rth,sessions
    python -m sandbox.research.exness_window_replay --compare

Every survivor in `results/exness/SURVIVORS.json` was selected, held out and
nulled INSIDE its market's cash session. This runs the same rule -- same family,
same sealed parameters -- in another window (`cfd_families` "WHICH HOURS A RUN
MAY TRADE"), so nothing is searched and both halves of each result are
unselected for the new hours. Replaying is minutes; re-selecting every family in
every window is days, and adds a search budget this adds none of
([[use-existing-survivors-before-sweeping]]).

"THE SAME RULE" NEEDS TWO TRANSLATIONS, AND NOTHING ELSE IS TOUCHED.

    clock     `last_entry_minute`, `signal_minute` and `entry_minute` are minutes
              on the old session clock. `rth` keeps them; any other window gets
              the same REAL New York time if it falls inside the window, else
              the window's own cutoff -- the cash session's 14:00 cutoff means
              nothing to a 03:00-06:00 window.
    lookback  lookbacks are sessions converted to bars (`periods`), and a window
              holds a different number of bars than the cash session. Each
              sealed integer is mapped through the two `periods` tables, entry
              for entry, so "10 sessions" stays ten windows AND lands on a
              length the context actually builds. `fb_`/`sb_` count in BARS by
              design and keep theirs.

Writes `results/exness_window_replay_<window>.json`, one per window; `rth` is
the like-for-like baseline under today's fill model.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import platform
import time

platform._wmi = None

from sandbox.research import cfd_families as ef

ROOT = os.path.join(os.path.dirname(__file__), "..", "results")
SURVIVORS = os.path.join(ROOT, "exness", "SURVIVORS.json")
CLOCK_KEYS = ("last_entry_minute", "signal_minute", "entry_minute")
BARS = {"30m": 30, "60m": 60}


def output(window):
    return os.path.join(ROOT, f"exness_window_replay_{window}.json")


def _integers(value):
    """Every integer in a `periods` table, in a fixed order."""
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _integers(value[key])
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _integers(item)
    elif isinstance(value, int) and not isinstance(value, bool):
        yield value


def lookback_map(symbol, bar, old_session):
    """`{old bars: new bars}` from the rth `periods` table to this window's."""
    spec = ef.INSTRUMENTS[symbol]
    new = ef.periods(symbol, bar)
    kept = spec["session"]
    spec["session"] = old_session
    try:
        old = ef.periods(symbol, bar)
    finally:
        spec["session"] = kept
    votes = collections.defaultdict(collections.Counter)
    for before, after in zip(_integers(old), _integers(new)):
        votes[before][after] += 1
    return {before: count.most_common(1)[0][0] for before, count in votes.items()}


def translate(family, params, symbol, bar, mapping, old_shift):
    """The sealed cell, moved onto the window's clock and day length."""
    spec = ef.INSTRUMENTS[symbol]
    if ef.window_of(spec) == "rth":
        return dict(params)
    opened, closed = spec["session"]
    cutoff = closed - 60
    out = {}
    for key, value in params.items():
        if key in CLOCK_KEYS and isinstance(value, int) and value < ef.DAILY:
            minute = ((value - old_shift * 60) % 1440
                      + spec["shift_hours"] * 60) % 1440
            out[key] = (minute if opened <= minute < closed
                        else cutoff if key == "last_entry_minute" else opened)
        elif family.startswith(("fb_", "sb_")) or "minute" in key:
            out[key] = value
        else:
            out[key] = _map(value, mapping)
    return out


def _map(value, mapping):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return mapping.get(value, value)
    if isinstance(value, (tuple, list)):
        return type(value)(_map(item, mapping) for item in value)
    return value


def _stat(result):
    return {k: result.get(k) for k in ("return_pct", "max_dd_pct", "pf",
                                       "trades", "edge_vs_drift_t_stat")}


def replay(window):
    rows = [s for s in json.load(open(SURVIVORS, encoding="utf-8"))["survivors"]
            if s["timeframe"] in BARS]
    groups = {}
    for row in rows:
        groups.setdefault((row["symbol"], BARS[row["timeframe"]]), []).append(row)
    results, skipped = [], []
    started = time.time()
    for (symbol, bar), items in sorted(groups.items()):
        try:
            ef.resolve(symbol, allow_stale=True, window="rth")
            old_session = ef.INSTRUMENTS[symbol]["session"]
            ef.resolve(symbol, allow_stale=True, window=window)
        except SystemExit as error:
            skipped.append(f"{symbol}: {error}"[:160])
            continue
        ef.BAR_MINUTES = bar
        ef.install_fills(symbol, bar, quiet=True)
        spec = ef.INSTRUMENTS[symbol]
        # The broker table can start after the last in-sample year, and then
        # there is no selection window for the fills to be priced in.
        if not ef.is_years(symbol):
            skipped.append(f"{symbol}: broker fills start "
                           f"{spec['first_full_year']}, after the in-sample years")
            continue
        families = {row["family"] for row in items if row["family"] in ef.FAMILIES}
        bars, ctx = ef.context(symbol, "validate", bar, families)
        mapping = lookback_map(symbol, bar, old_session)
        old_shift = ef.SHIFT_HOURS.get(symbol, 0)
        for row in items:
            if row["family"] not in families:
                continue
            path = os.path.join(ROOT, "exness", row["file"])
            sealed = ef.rehydrate(json.load(open(path, encoding="utf-8"))["params"])
            params = translate(row["family"], sealed, symbol, bar, mapping,
                               old_shift)
            try:
                ins = ef.backtest(row["family"], bars, ctx, params, hi=ef.IS_END)
                oos = ef.backtest(row["family"], bars, ctx, params,
                                  lo=ef.IS_END, hi=ef.OOS_END)
            except (KeyError, IndexError) as error:
                skipped.append(f"{symbol}:{row['family']}: "
                               f"{type(error).__name__} {error}")
                continue
            results.append({
                "symbol": symbol, "family": row["family"], "bar": bar,
                "wave": row["study_wave"], "params": params,
                "in_sample": _stat(ins), "oos": _stat(oos),
                "sealed_oos_return_pct": row["oos_return_pct"]})
        print(f"{window} {symbol} {bar}m: {len(items)} survivors "
              f"({time.time() - started:.0f}s)", flush=True)
    payload = {"window": window, "results": results, "skipped": skipped}
    with open(output(window), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, default=str)
    print(f"wrote {output(window)}: {len(results)} replayed, "
          f"{len(skipped)} skipped")


def compare():
    runs = {}
    for path in sorted(glob.glob(output("*"))):
        payload = json.load(open(path, encoding="utf-8"))
        runs[payload["window"]] = {(r["symbol"], r["family"], r["bar"]): r
                                   for r in payload["results"]}
    base = runs.get("rth", {})
    print(f"{'window':14}{'replayed':>9}{'OOS>0':>7}{'beats rth':>10}"
          f"{'median OOS%':>12}")
    for window, rows in runs.items():
        oos = sorted(r["oos"]["return_pct"] for r in rows.values())
        better = sum(1 for key, r in rows.items() if key in base
                     and r["oos"]["return_pct"] > base[key]["oos"]["return_pct"])
        print(f"{window:14}{len(rows):>9}{sum(v > 0 for v in oos):>7}"
              f"{better:>10}{oos[len(oos) // 2] if oos else 0:>12.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="rth",
                        help="windows to replay in, as `cfd_families --window`")
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    if args.compare:
        compare()
    else:
        for name in ef.expand_windows(args.window):
            replay(name)
