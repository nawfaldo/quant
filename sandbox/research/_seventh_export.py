"""Write the ustec/usoil sweeps' survivors into `results/exness/`.

Same admission rule the folder's README fixes and `_sixth_export` obeys --
in-sample winner, holdout return > 0 with PF >= 1.05 and n >= 30, and a coin-flip
null it beat (or a null that found no in-sample cell at all). The only thing that
differs is where the sweep came from: these two symbols were swept at `--scope
all` into `exness_families_<symbol>_30m.json` rather than into a wave-suffixed
file, and their nulls are written under the long `+`-joined family names the
runner produces, so both are globbed rather than looked up by one path.

`ustec` is the Nasdaq 100 CFD Exness actually quotes. It is NOT `nq`, whose table
is the back-adjusted futures continuum ([[nq-has-two-incompatible-price-series]]);
the two are different price series and a book may hold one or the other, not both
on the assumption that they are the same instrument.

    py -m sandbox.research._seventh_export --dry-run
    py -m sandbox.research._seventh_export
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from collections import Counter

from sandbox.research import cfd_families as ef
from sandbox.research._sixth_export import (MIN_OOS_PF, MIN_OOS_RETURN,
                                            MIN_OOS_TRADES, NULL_METHOD,
                                            SURVIVOR_DIR, INDEX, index_row,
                                            session_block)

WAVE = "seventh"
SYMBOLS = ("ustec", "usoil")


def sweep_for(symbol, bar):
    path = os.path.join(ef.RESULTS,
                        f"exness_families_{symbol}_{ef.label_bar(bar)}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def null_for(symbol, bar):
    """Every null seed on file for this symbol, merged across runner files.

    The runner names a null file after the families it covered, joined with
    `+` and truncated, so one symbol can have several and none of them is
    findable by a fixed path.
    """
    pattern = os.path.join(
        ef.RESULTS,
        f"exness_families_null_{symbol}_{ef.label_bar(bar)}*.json")
    merged = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            merged.update(json.load(handle)["null_control"])
    return merged


def survivors_for(symbol, bar=30):
    payload = sweep_for(symbol, bar)
    if payload is None:
        return [], Counter({"no sweep on file": 1})
    validation = payload.get("validation") or {}
    null = null_for(symbol, bar)
    protocol = payload.get("protocol") or {}
    groups = protocol.get("family_groups") or {}
    holdings = protocol.get("holding") or {}
    seal = payload.get("seal_sha256")
    source = f"exness_families_{symbol}_{ef.label_bar(bar)}.json"
    ef.resolve(symbol, allow_stale=True)
    spec = ef.INSTRUMENTS[symbol]

    out, why = [], Counter()
    for family, winner in payload["families"].items():
        if not winner:
            why["no in-sample winner"] += 1
            continue
        entry = validation.get(family) or {}
        oos = entry.get("oos")
        if oos is None:
            why["never validated"] += 1
            continue
        if oos["return_pct"] <= MIN_OOS_RETURN:
            why["lost on the holdout"] += 1
            continue
        if (oos.get("pf") or 0) < MIN_OOS_PF:
            why["holdout PF below 1.05"] += 1
            continue
        if oos["trades"] < MIN_OOS_TRADES:
            why["fewer than 30 holdout trades"] += 1
            continue
        if family not in null:
            why["no null on file"] += 1
            continue
        seeds = [r for r in (null.get(family) or []) if r]
        best = max((r["out_of_sample"]["return_pct"] for r in seeds),
                   default=None)
        if best is not None and oos["return_pct"] <= best:
            why["lost to their own null"] += 1
            continue
        out.append({
            "asset_class": spec["asset_class"],
            "bar_minutes": bar,
            "cost_sweep_bp": entry.get("cost_sweep_bp") or {},
            "family": family,
            "group": groups.get(family, ef.FAMILIES[family].group),
            "holding": holdings.get(family, ef.family_hold(family)),
            "in_sample": winner["in_sample"],
            "null_control": {
                "best_null_oos_return_pct": best,
                "margin_pts": (None if best is None
                               else round(oos["return_pct"] - best, 2)),
                "method": NULL_METHOD,
                "seeds_oos_return_pct": {
                    str(r["seed"]): r["out_of_sample"]["return_pct"]
                    for r in seeds},
            },
            "out_of_sample": oos,
            "params": winner["params"],
            "protocol": protocol,
            "provenance": {"seal_sha256": seal, "select": source,
                           "written": time.strftime("%Y-%m-%d")},
            "robust_neighbours": winner.get("robust_neighbours"),
            "session": session_block(symbol),
            "study_wave": WAVE,
            "symbol": symbol,
            "timeframe": ef.label_bar(bar),
        })
    return out, why


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cells, why = [], Counter()
    for symbol in SYMBOLS:
        rows, reasons = survivors_for(symbol, args.bar_minutes)
        cells.extend(rows)
        why.update(reasons)

    print(f"{WAVE} wave: {len(cells)} survivors")
    print(f"{'symbol':9}{'family':21}{'OOS %':>8}{'null %':>8}{'margin':>8}"
          f"{'n':>6}{'pf':>6}{'seeds':>7}")
    for cell in sorted(cells, key=lambda c: -c["out_of_sample"]["return_pct"]):
        null, oos = cell["null_control"], cell["out_of_sample"]
        best = null["best_null_oos_return_pct"]
        margin = null["margin_pts"]
        print(f"{cell['symbol']:9}{cell['family']:21}"
              f"{oos['return_pct']:>+8.1f}"
              f"{('-' if best is None else format(best, '+.1f')):>8}"
              f"{('-' if margin is None else format(margin, '+.1f')):>8}"
              f"{oos['trades']:>6}{(oos.get('pf') or 0):>6.2f}"
              f"{len(null['seeds_oos_return_pct']):>7}")
    print("\nrefused:")
    for reason, count in why.most_common():
        print(f"  {reason:34}{count:>5}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    os.makedirs(SURVIVOR_DIR, exist_ok=True)
    written = []
    for cell in cells:
        name = f"{cell['symbol']}_{cell['family']}_{cell['timeframe']}.json"
        path = os.path.join(SURVIVOR_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                existing = json.load(handle)
            if existing.get("study_wave") != WAVE:
                raise SystemExit(
                    f"REFUSING to overwrite {name}: it belongs to wave "
                    f"{existing.get('study_wave')!r}, not {WAVE!r}")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cell, handle, indent=2, sort_keys=True)
            handle.write("\n")
        written.append(name)

    with open(INDEX, encoding="utf-8") as handle:
        index = json.load(handle)
    keep = [r for r in index["survivors"] if r.get("study_wave") != WAVE]
    index["survivors"] = sorted(
        keep + [index_row(c) for c in cells],
        key=lambda r: (r["symbol"], r["family"], r["timeframe"]))
    index["count"] = len(index["survivors"])
    index["waves"] = dict(Counter(r.get("study_wave")
                                  for r in index["survivors"]))
    index["written"] = time.strftime("%Y-%m-%d")
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"\nwrote {len(written)} files to {SURVIVOR_DIR}")
    print(f"index now {index['count']} survivors: {index['waves']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
