"""Export the ncf FundedNext run's `why` PASS / PASS* survivors into `results/fundednext/`.

The 2026-10-16 run (`sandbox/results/logs/ncf/run.sh`): new_cfd_families'
86 families, FundedNext, $6,000, IS 2024-2025 / OOS 2026-01-01..2026-10-14,
vendor signals in FundedNext's quoted hours (CFD_BROKER_HOURS=1, FILLS_ONLY).
Results live in `sandbox/results/new_cfd_families/fundednext/<cfd|tt>/<sym>-<tf>-<window>/`.

A survivor: OOS return > 0, PF >= 1.05, >= 20 trades, and `why` PASS (beats
the best coin-flip seed's OOS) or PASS* (no seed found an IS cell).

Files are `<sym>_<family>-<tf>-<window>.json`; a name already held by an
earlier wave gets `-6k` so nothing older is overwritten. SURVIVORS.json is
merged: rows of this wave are replaced, every other row is kept.

`--is-only` writes every 2024-2025 winner instead -- no 2026 filter, no 2026
field -- to `results/fundednext_is/`, the pool `fundednext_combined_strategies`
picks from.

    python -m sandbox.research._ncf_fundednext_export [--dry-run] [--is-only]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from collections import Counter

WAVE = "ncf_fundednext_6000_2026-10-16"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "results", "new_cfd_families", "fundednext")
DST = os.path.join(HERE, "..", "results", "fundednext")
INDEX = os.path.join(DST, "SURVIVORS.json")
#: `--is-only`: every 2024-2025 winner, the challenge book's pool.
IS_DIR = os.path.join(HERE, "..", "results", "fundednext_is")
MIN_TRADES = 20


def survivors(is_only=False):
    """`is_only`: every 2024-2025 winner, with no 2026 field -- the pool
    `fundednext_combined_strategies` chooses from."""
    out = []
    for kind in ("cfd", "tt"):
        for job in sorted(glob.glob(os.path.join(SRC, kind, "*"))):
            symbol, tf, window = os.path.basename(job).split("-", 2)
            nulls = {}
            for q in glob.glob(os.path.join(job, "*_null_*.json")):
                nulls.update(json.load(open(q, encoding="utf-8")).get("null_control") or {})
            for path in glob.glob(os.path.join(job, "*.json")):
                if "_null_" in path or path.endswith("_coinflip.json"):
                    continue
                payload = json.load(open(path, encoding="utf-8"))
                validation = payload.get("validation") or {}
                for family, winner in (payload.get("families") or {}).items():
                    oos = (validation.get(family) or {}).get("oos")
                    if not winner:
                        continue
                    if is_only:
                        out.append(_cell(symbol, family, tf, window, kind, winner,
                                         payload, path))
                        continue
                    if not oos or family not in nulls:
                        continue
                    if (oos["return_pct"] <= 0 or (oos.get("pf") or 0) < 1.05
                            or oos["trades"] < MIN_TRADES):
                        continue
                    seeds = [r for r in nulls[family] if r]
                    top = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
                    if top is not None and oos["return_pct"] <= top:
                        continue
                    out.append({
                        **_cell(symbol, family, tf, window, kind, winner, payload, path),
                        "holdout": "2026-01-01 through 2026-10-14",
                        "out_of_sample": oos,
                        "cost_sweep_bp": (validation.get(family) or {}).get("cost_sweep_bp") or {},
                        "null_control": {
                            "method": "why: search re-run x3 with random sides",
                            "best_null_oos_return_pct": top,
                            "margin_pts": None if top is None else round(oos["return_pct"] - top, 2),
                            "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                                     for r in seeds}},
                        "null_verdict": "PASS*" if top is None else "PASS"})
    return out


def _cell(symbol, family, tf, window, kind, winner, payload, path):
    """The 2024-2025 half of a cell: what the search chose, nothing from 2026."""
    return {"symbol": symbol, "family": family, "timeframe": tf,
            "window": window, "broker": "fundednext", "initial_balance": 6000,
            "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
            "in_sample": winner["in_sample"], "in_sample_years": [2024, 2025],
            "params": winner["params"],
            "robust_neighbours": winner.get("robust_neighbours"),
            "protocol": payload.get("protocol"), "study_wave": WAVE,
            "provenance": {"seal_sha256": payload.get("seal_sha256"),
                           "select": os.path.relpath(path, SRC),
                           "written": time.strftime("%Y-%m-%d")}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--is-only", action="store_true",
                        help="every 2024-2025 winner, no 2026 filter -> results/fundednext_is/")
    args = parser.parse_args()
    cells = survivors(args.is_only)
    if args.is_only:
        print(f"{len(cells)} 2024-2025 winners")
    else:
        print(f"{len(cells)} survivors", dict(Counter(c["null_verdict"] for c in cells)))
    if args.dry_run:
        return 0
    if args.is_only:
        # `-ncf` keeps these apart from `_fundednext_export --is-only`'s names
        os.makedirs(IS_DIR, exist_ok=True)
        for c in cells:
            name = f"{c['symbol']}_{c['family']}-{c['timeframe']}-{c['window']}-ncf.json"
            with open(os.path.join(IS_DIR, name), "w", encoding="utf-8") as handle:
                json.dump(c, handle, indent=2, sort_keys=True)
                handle.write("\n")
        print(f"wrote {len(cells)} files -> {IS_DIR}")
        return 0
    os.makedirs(DST, exist_ok=True)
    index = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else {"survivors": []}
    keep = [r for r in index.get("survivors", []) if r.get("study_wave") != WAVE]
    rows = []
    for c in cells:
        name = f"{c['symbol']}_{c['family']}-{c['timeframe']}-{c['window']}.json"
        path = os.path.join(DST, name)
        if os.path.exists(path) and json.load(open(path, encoding="utf-8")).get("study_wave") != WAVE:
            name = name[:-5] + "-6k.json"
            path = os.path.join(DST, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True)
            handle.write("\n")
        rows.append({"file": name, "symbol": c["symbol"], "family": c["family"],
                     "timeframe": c["timeframe"], "window": c["window"],
                     "oos_return_pct": c["out_of_sample"]["return_pct"],
                     "oos_max_dd_pct": c["out_of_sample"]["max_dd_pct"],
                     "null_verdict": c["null_verdict"], "study_wave": WAVE})
    index["survivors"] = keep + rows
    index["count"] = len(index["survivors"])
    index["written"] = time.strftime("%Y-%m-%d")
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(cells)} files; SURVIVORS.json now {index['count']} rows -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
