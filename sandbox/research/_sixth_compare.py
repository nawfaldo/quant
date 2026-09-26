"""In sample, the sixth wave against the families it was built from.

WHAT THIS CAN AND CANNOT SETTLE.

Both sides are the best cell of a grid, scored on the same symbol, timeframe,
cost model and selection gates. That makes the comparison fair in the only sense
a comparison of two searches can be. It is still a contest between two maxima,
and the bigger grid wins a maximum contest by construction -- so every row
carries its cell count, and a new family that beats an old one by less than its
grid advantage has shown nothing at all.

READ THE T-STATISTIC, NOT THE RETURN. `return_pct` is one compounded ordering of
one trade sequence and grows with the trade count; `edge_vs_drift_t_stat` is the
per-trade edge against the instrument's own drift, which is the only column here
that does not inflate just because a family traded more often. On this data a
coin-flip search has returned +622% at t=4.19
([[coin-flip-control-beats-real-signals]]), so an in-sample return is close to
uninformative on its own.

AND NONE OF IT IS OUT OF SAMPLE. `select` fits; `validate` scores the holdout
and `why` prices the search. This script answers exactly one question -- did the
new families find better in-sample cells than the old ones -- and that question
is a sanity check, not a result.

    python -m sandbox.research._sixth_compare
    python -m sandbox.research._sixth_compare --symbols ethusd,btc --detail
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

from sandbox.research import cfd_families as ef

SIXTH = set(ef.ALIASES["sixth"])


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_new(symbol, bar):
    return read(ef.output_path(symbol, bar, SIXTH))


def load_old(symbol, bar):
    """EVERY sealed file for this symbol except the sixth wave's own.

    THE FIRST DRAFT OF THIS COMPARISON WAS WRONG AND FLATTERED THE NEW WAVE.
    `output_path(symbol, bar, None)` is the UNRESTRICTED file, which on ethusd
    holds 27 families -- the original core study and nothing else. The second,
    third and fourth waves were each run with `--groups`, and a restricted
    `select` writes to its own file on purpose, so `_new` (33 families),
    `_combined` (13) and `_fourth` (10) sit beside it. Comparing 27 new families
    against 27 old ones while calling it "the old study" left three quarters of
    the incumbent field out of the contest -- including `confluence`,
    `level_confluence` and `nested`, which carry the largest returns ethusd has
    ever produced.

    So this globs the lot. A family appearing in two files keeps its FIRST
    reading, which is the earliest sealed one; none currently overlap.
    """
    prefix = f"exness_families_{symbol}_{ef.label_bar(bar)}"
    mine = os.path.basename(ef.output_path(symbol, bar, SIXTH))
    out, seen = [], set()
    for name in sorted(os.listdir(ef.RESULTS)):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        if name == mine or "_null_" in name:
            continue
        payload = read(os.path.join(ef.RESULTS, name))
        if payload is None or "families" not in payload:
            continue
        payload = dict(payload)
        payload["families"] = {k: v for k, v in payload["families"].items()
                               if k not in seen}
        seen.update(payload["families"])
        out.append(payload)
    return out


def rows(payload, wave):
    """One record per family that produced a winner, plus the dead ones."""
    if payload is None:
        return []
    groups = (payload.get("protocol") or {}).get("family_groups") or {}
    cells = (payload.get("protocol") or {}).get("candidate_counts") or {}
    out = []
    for family, winner in payload["families"].items():
        record = {"symbol": payload["symbol"], "family": family, "wave": wave,
                  "group": groups.get(family, "-"), "cells": cells.get(family)}
        if winner is None:
            out.append({**record, "dead": True})
            continue
        stat = winner["in_sample"]
        out.append({**record, "dead": False,
                    "ret": stat["return_pct"], "dd": stat["max_dd_pct"],
                    "t": stat.get("edge_vs_drift_t_stat"),
                    "edge": stat.get("edge_vs_drift_bp"),
                    "gross": stat.get("gross_bp_per_trade"),
                    "n": stat["trades"], "pf": stat.get("pf"),
                    "msharpe": stat.get("monthly_sharpe"),
                    "robust": winner.get("robust_neighbours")})
    return out


def median(values):
    live = [v for v in values if v is not None]
    return statistics.median(live) if live else float("nan")


def summarise(records, label):
    if not records:
        print(f"  {label:14} -- nothing")
        return
    ts = [r["t"] for r in records if r["t"] is not None]
    print(f"  {label:14}{len(records):>4} winners"
          f" | median t {median(ts):>5.2f}"
          f" | max t {max(ts) if ts else float('nan'):>5.2f}"
          f" | t>=2 {sum(1 for t in ts if t >= 2):>3}/{len(ts):<3}"
          f" | t>=3 {sum(1 for t in ts if t >= 3):>3}/{len(ts):<3}"
          f" | median IS {median([r['ret'] for r in records]):>+8.1f}%"
          f" | median edge {median([r['edge'] for r in records]):>6.1f} bp")


def table(records, title, limit=None, key="t"):
    print(f"\n{title}")
    print(f"  {'symbol':9}{'family':21}{'group':10}{'cells':>7}{'IS %':>10}"
          f"{'dd':>6}{'n':>7}{'t':>7}{'edge':>8}{'pf':>6}{'mSh':>6}{'robust':>8}")
    ordered = sorted(records, key=lambda r: -(r.get(key)
                                              if r.get(key) is not None
                                              else -1e9))
    for r in (ordered[:limit] if limit else ordered):
        print(f"  {r['symbol']:9}{r['family']:21}{r['group']:10}"
              f"{(r['cells'] or 0):>7,}{r['ret']:>+10.1f}{r['dd']:>6.1f}"
              f"{r['n']:>7,}{(r['t'] or 0):>7.2f}{(r['edge'] or 0):>8.1f}"
              f"{(r['pf'] or 0):>6.2f}{(r['msharpe'] or 0):>6.2f}"
              f"{str(r.get('robust') or '-'):>8}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--detail", action="store_true",
                        help="per-symbol breakdown as well as the pooled view")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    names = (args.symbols.replace(" ", "").split(",") if args.symbols
             else [s for s in ef.UNIVERSE if ef.CLASS[s] != "stock"])

    old, new, missing = [], [], []
    for symbol in names:
        fresh = load_new(symbol, args.bar_minutes)
        if fresh is None:
            missing.append(symbol)
            continue
        new.extend(rows(fresh, "new"))
        for payload in load_old(symbol, args.bar_minutes):
            old.extend(rows(payload, "old"))

    done = sorted({r["symbol"] for r in new})
    print(f"=== in sample, {args.bar_minutes}m, {len(done)} symbols ===")
    print(f"scored: {', '.join(done)}")
    if missing:
        print(f"not yet run ({len(missing)}): {', '.join(missing)}")

    old_live = [r for r in old if not r["dead"]]
    new_live = [r for r in new if not r["dead"]]
    old_cells = sum(r["cells"] or 0 for r in old)
    new_cells = sum(r["cells"] or 0 for r in new)
    print(f"\nPOOLED  (old {len(old)} family-symbol slots, {old_cells:,} cells"
          f" | new {len(new)} slots, {new_cells:,} cells)")
    summarise(old_live, "OLD")
    summarise(new_live, "NEW (sixth)")

    dead_new = [r for r in new if r["dead"]]
    dead_old = [r for r in old if r["dead"]]
    print(f"\n  no cell cleared the gates:  old {len(dead_old)}/{len(old)}"
          f"   new {len(dead_new)}/{len(new)}")

    # WHERE DOES THE NEW WAVE LAND IN THE OLD DISTRIBUTION? A rank is the
    # comparison that survives the grid-size objection better than a mean does:
    # it asks whether the new families are drawn from a better distribution, not
    # whether one lucky maximum beat another.
    old_ts = sorted((r["t"] for r in old_live if r["t"] is not None),
                    reverse=True)
    if old_ts and new_live:
        print("\n  percentile of each new family's best t within the old pool"
              " (0 = better than every old winner)")
        ranked = sorted(new_live, key=lambda r: -(r["t"] or -1e9))
        for r in ranked[:15]:
            above = sum(1 for t in old_ts if t > (r["t"] or -1e9))
            print(f"    {r['symbol']:9}{r['family']:21}t={r['t'] or 0:>5.2f}"
                  f"   beaten by {above:>4}/{len(old_ts)}"
                  f"  ({100.0 * above / len(old_ts):>4.0f}th)")

    print("\n  BY GROUP (new)")
    by_group = {}
    for r in new_live:
        by_group.setdefault(r["group"], []).append(r)
    for group in sorted(by_group):
        summarise(by_group[group], group)

    print("\n  BY FAMILY (new), median t across symbols")
    by_family = {}
    for r in new_live:
        by_family.setdefault(r["family"], []).append(r)
    print(f"  {'family':21}{'group':10}{'n sym':>6}{'med t':>8}{'max t':>8}"
          f"{'med IS %':>10}{'t>=2':>7}")
    for family in sorted(by_family,
                         key=lambda f: -median([r["t"] for r in by_family[f]])):
        rs = by_family[family]
        ts = [r["t"] for r in rs if r["t"] is not None]
        print(f"  {family:21}{rs[0]['group']:10}{len(rs):>6}"
              f"{median(ts):>8.2f}{(max(ts) if ts else 0):>8.2f}"
              f"{median([r['ret'] for r in rs]):>+10.1f}"
              f"{sum(1 for t in ts if t >= 2):>4}/{len(ts):<2}")

    table(new_live, "TOP 25 NEW CELLS, by t", limit=25)
    table(old_live, "TOP 25 OLD CELLS, by t", limit=25)
    if args.detail:
        for symbol in done:
            table([r for r in new_live if r["symbol"] == symbol],
                  f"--- {symbol}: the sixth wave ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
