"""Write the LuxAlgo TikTok families' survivors into `results/exness/`.

Same admission rule the folder's README fixes (and `_seventh_export` obeys):
an in-sample winner, holdout return > 0 with PF >= 1.05 and n >= 30, and a
coin-flip null it beat -- PASS -- or a null that found no in-sample cell at
all -- PASS*. Only the source differs: the sweep is `cfd_tt_families`
(`exness_tt_families_<symbol>_<tf>.json`, Exness Pro, RTH only) and the nulls
are `why --winners` runs (`exness_tt_families_null_<symbol>_<tf>_*.json`).

Every file written is marked as LuxAlgo's: `study_wave` "tiktok_luxalgo",
`group` "luxalgo", `source_creator` "@luxalgo", and every family name starts
`lux_`/`luxalgo_` (so the file name carries it too). Timeframes other than 30m
are written as they are, and like the 2026-09-24 study they are NOT picked up
by `candidates()`, which reads `_30m.json` only.

    py -m sandbox.research._luxalgo_export --dry-run
    py -m sandbox.research._luxalgo_export
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

# The shared helpers first: `_sixth_export` reads the original registry at
# import, before `cfd_tt_families` swaps in its own.
from sandbox.research._sixth_export import (MIN_OOS_PF, MIN_OOS_RETURN,  # noqa: E402
                                            MIN_OOS_TRADES, NULL_METHOD,
                                            SURVIVOR_DIR, INDEX, index_row,
                                            session_block)
sys_argv = sys.argv
sys.argv = [sys.argv[0]]
from sandbox.research import cfd_tt_families as tt       # noqa: E402  (installs the registry)
sys.argv = sys_argv
from sandbox.research import cfd_families as ef          # noqa: E402

WAVE = "tiktok_luxalgo"
BARS = (5, 15, 30, 60, 120, 240)


def sweep_for(symbol, bar):
    path = os.path.join(ef.RESULTS,
                        f"exness_tt_families_{symbol}_{ef.label_bar(bar)}.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle), os.path.basename(path)


def null_for(symbol, bar):
    pattern = os.path.join(
        ef.RESULTS,
        f"exness_tt_families_null_{symbol}_{ef.label_bar(bar)}_*.json")
    merged = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            merged.update(json.load(handle)["null_control"])
    return merged


def survivors_for(symbol, bar):
    payload, source = sweep_for(symbol, bar)
    if payload is None:
        return [], Counter({"no sweep on file": 1})
    validation = payload.get("validation") or {}
    null = null_for(symbol, bar)
    protocol = payload.get("protocol") or {}
    seal = payload.get("seal_sha256")
    ef.resolve(symbol, allow_stale=True)
    spec = ef.INSTRUMENTS[symbol]
    out, why = [], Counter()
    for family, winner in payload["families"].items():
        if not winner:
            why["no in-sample winner"] += 1
            continue
        oos = (validation.get(family) or {}).get("oos")
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
        best = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
        if best is not None and oos["return_pct"] <= best:
            why["lost to their own null"] += 1
            continue
        out.append({
            "asset_class": spec["asset_class"],
            "bar_minutes": bar,
            "cost_sweep_bp": (validation.get(family) or {}).get("cost_sweep_bp") or {},
            "family": family,
            "group": "luxalgo",
            "holding": "session",
            "in_sample": winner["in_sample"],
            "null_control": {
                "best_null_oos_return_pct": best,
                "margin_pts": None if best is None else round(oos["return_pct"] - best, 2),
                "method": NULL_METHOD,
                "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                         for r in seeds},
                "verdict": "PASS*" if best is None else "PASS",
            },
            "out_of_sample": oos,
            "params": winner["params"],
            "protocol": protocol,
            "provenance": {"seal_sha256": seal, "select": source,
                           "module": "sandbox.research.tt_luxalgo",
                           "notes": "results/tiktok/luxalgo_strategy_notes.md",
                           "written": time.strftime("%Y-%m-%d")},
            "robust_neighbours": winner.get("robust_neighbours"),
            "session": session_block(symbol),
            "source_creator": "@luxalgo",
            "study_wave": WAVE,
            "symbol": symbol,
            "timeframe": ef.label_bar(bar),
        })
    return out, why


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cells, why = [], Counter()
    for bar in BARS:
        for symbol in tt.DEFAULT_SYMBOLS:
            rows, reasons = survivors_for(symbol, bar)
            cells.extend(rows)
            why.update(reasons)
    print(f"{WAVE}: {len(cells)} survivors")
    for c in sorted(cells, key=lambda c: -c["out_of_sample"]["return_pct"]):
        n, o = c["null_control"], c["out_of_sample"]
        print(f"{c['symbol']:8} {c['timeframe']:5} {c['family']:28} OOS {o['return_pct']:+7.1f} "
              f"n={o['trades']:4d} pf={o.get('pf') or 0:.2f} null="
              f"{'-' if n['best_null_oos_return_pct'] is None else format(n['best_null_oos_return_pct'], '+.1f')} "
              f"{n['verdict']}")
    print("\nrefused:")
    for reason, count in why.most_common():
        print(f"  {reason:34}{count:>5}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    os.makedirs(SURVIVOR_DIR, exist_ok=True)
    for c in cells:
        name = f"{c['symbol']}_{c['family']}_{c['timeframe']}.json"
        path = os.path.join(SURVIVOR_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                if json.load(handle).get("study_wave") != WAVE:
                    raise SystemExit(f"REFUSING to overwrite {name}: another wave's file")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True)
            handle.write("\n")
    with open(INDEX, encoding="utf-8") as handle:
        index = json.load(handle)
    keep = [r for r in index["survivors"] if r.get("study_wave") != WAVE]
    rows = []
    for c in cells:
        r = index_row(c)
        r["null_verdict"] = c["null_control"]["verdict"]
        r["source_creator"] = "@luxalgo"
        rows.append(r)
    index["survivors"] = sorted(keep + rows,
                                key=lambda r: (r["symbol"], r["family"], r["timeframe"]))
    index["count"] = len(index["survivors"])
    index["waves"] = dict(Counter(r.get("study_wave") for r in index["survivors"]))
    index["written"] = time.strftime("%Y-%m-%d")
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {len(cells)} files to {SURVIVOR_DIR}; index now {index['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
