"""Write one TikTok creator group's survivors into `results/exness/`.

Generalised from `_luxalgo_export` (which stays as the record of that wave):
`--group <handle>` reads that group's own seal
(`exness_tt_families_<symbol>_<tf>_<group>.json`) and its `why --winners`
nulls, and labels every file `study_wave` "tiktok_<group>", `group` <group>,
`source_creator` "@<handle>". The admission rule below is unchanged.

Same admission rule the folder's README fixes (and `_seventh_export` obeys):
an in-sample winner, holdout return > 0 with PF >= 1.05 and n >= 30, and a
coin-flip null it beat -- PASS -- or a null that found no in-sample cell at
all -- PASS*. Only the source differs: the sweep is `cfd_tt_families`
(`exness_tt_families_<symbol>_<tf>.json`, Exness Pro, RTH only) and the nulls
are `why --winners` runs (`exness_tt_families_null_<symbol>_<tf>_*.json`).

Family names carry the creator's prefix (`qp_`, `tqb_`, `td_`), so the file
name `<symbol>_<family>_<tf>.json` says whose rule it is. Timeframes other than 30m
are written as they are, and like the 2026-09-24 study they are NOT picked up
by `candidates()`, which reads `_30m.json` only.

    py -m sandbox.research._tiktok_export --group quantpad --dry-run
    py -m sandbox.research._tiktok_export --group quantpad
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

BARS = (5, 15, 30, 60, 120, 240)
GROUP = WAVE = CREATOR = None

#: A run under a changed protocol (`EXNESS_TT_STOP_WIDEN`, `EXNESS_IS_FIRST_YEAR`)
#: writes seals under its own prefix; its survivors get their own wave and
#: file suffix (e.g. `_widestop_isethusd2021`) so they never mix with, or
#: overwrite, the standard run's.
VARIANT = ef.RESULT_PREFIX[len("exness_tt_families"):]
CHANGES = {k: os.environ[k] for k in ("EXNESS_TT_STOP_WIDEN", "EXNESS_IS_FIRST_YEAR")
           if os.environ.get(k)}


def _members():
    return set(tt.GROUPS[GROUP])


def sweep_for(symbol, bar):
    ef.resolve(symbol, allow_stale=True)      # `output_path` reads INSTRUMENTS
    path = os.path.join(ef.RESULTS,
                        os.path.basename(ef.output_path(symbol, bar, _members())))
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle), os.path.basename(path)


def null_for(symbol, bar):
    pattern = os.path.join(
        ef.RESULTS,
        f"{ef.RESULT_PREFIX}_null_{symbol}_{ef.label_bar(bar)}_*.json")
    merged = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)["null_control"]
        merged.update({f: r for f, r in rows.items() if f in _members()})
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
            "group": GROUP,
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
            "protocol_change": CHANGES or None,
            "provenance": {"seal_sha256": seal, "select": source,
                           "module": f"sandbox.research.tt_{GROUP.replace('.', '_')}",
                           "notes": f"results/tiktok/{GROUP}_strategy_notes.md",
                           "written": time.strftime("%Y-%m-%d")},
            "robust_neighbours": winner.get("robust_neighbours"),
            "session": session_block(symbol),
            "source_creator": CREATOR,
            "study_wave": WAVE,
            "symbol": symbol,
            "timeframe": ef.label_bar(bar),
        })
    return out, why


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    global GROUP, WAVE, CREATOR
    GROUP, CREATOR = args.group, f"@{args.group}"
    WAVE = f"tiktok_{args.group}{VARIANT}"
    if GROUP not in tt.GROUPS:
        raise SystemExit(f"unknown group {GROUP}; one of {sorted(tt.GROUPS)}")
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
        name = f"{c['symbol']}_{c['family']}_{c['timeframe']}{VARIANT}.json"
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
        r["source_creator"] = CREATOR
        r["file"] = f"{c['symbol']}_{c['family']}_{c['timeframe']}{VARIANT}.json"
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
