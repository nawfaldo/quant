"""Write the off-hours (`--window off`) survivors into `results/exness/`.

Same three-step definition as `_barlb_export`: in-sample winner, holdout
ret > 0 / PF >= 1.05 / n >= 30, and beat the best coin-flip seed (or no seed
found an in-sample cell -- `margin_pts: None`, the "pass*" rows).

Covers both searches run in the off window: cfd_families (bar-counted
lookbacks, seals `exness_families_*-barlb_*_win-off.json`) and cfd_tt_families
(`exness_tt_families_*_win-off.json`).  Files are named
`<sym>_<family>_<bar>m-barlb-off.json` (cfd) and `<sym>_<family>_<bar>m-off.json`
(tt), so they never collide with an RTH survivor and never match the book
builder's `*_30m.json` glob.

    python -m sandbox.research._offhours_export [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from collections import Counter

from sandbox.research import cfd_families as ef
from sandbox.research._sixth_export import (INDEX, NULL_METHOD, SURVIVOR_DIR,
                                            index_row, session_block)

WAVE = "offhours_2026-10-12"
R = ef.RESULTS
KINDS = {"cfd": ("exness_families_", "-barlb-off",
                 "bar-counted (EXNESS_BAR_LOOKBACKS=1)"),
         "tt": ("exness_tt_families_", "-off", "bar-native (cfd_tt_families)")}


def load(kind):
    """{(sym, bar, family): (winner, oos, cost_sweep, payload, path)} plus nulls."""
    prefix = KINDS[kind][0]
    best, nulls = {}, {}
    for path in sorted(glob.glob(os.path.join(R, prefix + "*_win-off.json")),
                       key=os.path.getmtime):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if os.path.basename(path).startswith(prefix + "null_"):
            for family, seeds in payload["null_control"].items():
                nulls[(payload["symbol"], payload["bar_minutes"], family)] = seeds
            continue
        validation = payload.get("validation") or {}
        for family, winner in (payload.get("families") or {}).items():
            key = (payload["symbol"], payload["bar_minutes"], family)
            got = validation.get(family) or {}
            if key in best and best[key][1] is not None and got.get("oos") is None:
                continue  # keep the validated reading
            best[key] = (winner, got.get("oos"), got.get("cost_sweep_bp") or {},
                         payload, path)
    return best, nulls


def survivors(kind):
    best, nulls = load(kind)
    _, suffix, lookbacks = KINDS[kind]
    out = []
    for (symbol, bar, family), (winner, oos, sweep, payload, path) in best.items():
        if not winner or oos is None or (symbol, bar, family) not in nulls:
            continue
        if (oos["return_pct"] <= 0 or (oos.get("pf") or 0) < 1.05
                or oos["trades"] < 30):
            continue
        seeds = [r for r in nulls[(symbol, bar, family)] if r]
        top = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
        if top is not None and oos["return_pct"] <= top:
            continue
        ef.resolve(symbol, allow_stale=True)
        spec = ef.INSTRUMENTS[symbol]
        protocol = payload.get("protocol") or {}
        group = (protocol.get("family_groups") or {}).get(family)
        if group is None:
            group = ef.FAMILIES[family].group if family in ef.FAMILIES else "tt"
        out.append({
            "asset_class": spec["asset_class"], "bar_minutes": bar,
            "cost_sweep_bp": sweep, "family": family, "group": group,
            "holding": (protocol.get("holding") or {}).get(family),
            "in_sample": winner["in_sample"],
            "null_control": {
                "best_null_oos_return_pct": top,
                "margin_pts": None if top is None else round(oos["return_pct"] - top, 2),
                "method": NULL_METHOD,
                "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                         for r in seeds}},
            "out_of_sample": oos, "params": winner["params"],
            "lookbacks": lookbacks, "protocol": protocol,
            "provenance": {"seal_sha256": payload.get("seal_sha256"),
                           "select": os.path.basename(path),
                           "written": time.strftime("%Y-%m-%d")},
            "robust_neighbours": winner.get("robust_neighbours"),
            "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
            "session": session_block(symbol), "study_wave": WAVE,
            "symbol": symbol, "timeframe": f"{bar}m{suffix}",
            "window": "off (entries only while RTH is shut)"})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cells = survivors("cfd") + survivors("tt")
    tally = Counter((c["search"], "pass*" if c["null_control"]["margin_pts"] is None
                     else "pass") for c in cells)
    print(f"{len(cells)} survivors", dict(tally))
    if args.dry_run:
        return 0

    with open(INDEX, encoding="utf-8") as handle:
        index = json.load(handle)
    for c in cells:
        name = f"{c['symbol']}_{c['family']}_{c['timeframe']}.json"
        path = os.path.join(SURVIVOR_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                if json.load(handle).get("study_wave") != WAVE:
                    raise SystemExit(f"REFUSING to overwrite {name}")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True)
            handle.write("\n")
    keep = [r for r in index["survivors"] if r.get("study_wave") != WAVE]
    index["survivors"] = sorted(keep + [index_row(c) for c in cells],
                                key=lambda r: (r["symbol"], r["family"], r["timeframe"]))
    index["count"] = len(index["survivors"])
    index["waves"] = dict(Counter(r.get("study_wave") for r in index["survivors"]))
    index["written"] = time.strftime("%Y-%m-%d")
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(cells)} files; index now {index['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
