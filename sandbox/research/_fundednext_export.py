"""Write the FundedNext IS 2024-2025 / OOS 2026 survivors into `results/fundednext/`.

The 2026-10-13 search (`sandbox/results/logs/run_fns.sh`, `CFD_SPLIT=2024-2025`):
cfd_families (59 session families, bar-counted lookbacks) and cfd_tt_families,
RTH and off-hours windows, 30m / 15m / 1h, priced at FundedNext.

A survivor did all three, in order:
  1. `select`   won its family's 2024-2025 search and passed the shared gates
  2. `validate` cleared 2026-01-01..2026-10-12 at return > 0, PF >= 1.05, n >= 20
  3. `why`      beat the best coin-flip seed -- "pass" -- or no seed found an
                in-sample cell at all -- "pass*" (`margin_pts: None`)

Files are named `<sym>_<family>-<bar>m-<rth|offhour>.json`, the
new_cfd_families tokens; `timeframe` inside keeps `<bar>m[-barlb][-off]`.
; `SURVIVORS.json`
indexes them.

`--is-only` writes step 1 alone -- every 2024-2025 winner, no 2026 field --
to `results/fundednext_is/`, the pool `fundednext_combined_strategies` picks
from.

    python -m sandbox.research._fundednext_export [--dry-run] [--is-only]
"""
from __future__ import annotations

import os

os.environ["CFD_BROKER"] = "fundednext"
os.environ.setdefault("CFD_SPLIT", "2024-2025")
os.environ.setdefault("CFD_OOS_END", "2026-10-13")

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402

from sandbox.research import cfd_families as ef  # noqa: E402
from sandbox.research._sixth_export import (NULL_METHOD, index_row,  # noqa: E402
                                            session_block)

WAVE = "fundednext_is2024-2025_2026-10-13"
R = ef.RESULTS
SURVIVOR_DIR = os.path.join(R, "fundednext")
INDEX = os.path.join(SURVIVOR_DIR, "SURVIVORS.json")
#: `--is-only`: every 2024-2025 winner, the challenge book's pool.
IS_DIR = os.path.join(R, "fundednext_is")
MIN_TRADES = 20
#: Never exported: es and nq are futures-data twins of us500 / ustec and trade
#: the same FundedNext SPX500 / NDX100 -- the user does not want them.
EXCLUDE_SYMBOLS = {"es", "nq", "jp225"}
KINDS = {"cfd": ("fundednext_families_is2024-2025_", "-barlb",
                 "bar-counted (EXNESS_BAR_LOOKBACKS=1)"),
         "tt": ("fundednext_tt_families_is2024-2025_", "",
                "bar-native (cfd_tt_families)")}


def load(kind):
    """{(sym, bar, window, family): (winner, oos, sweep, payload, path)}, nulls."""
    prefix = KINDS[kind][0]
    best, nulls = {}, {}
    for path in sorted(glob.glob(os.path.join(R, prefix + "*.json")),
                       key=os.path.getmtime):
        window = "off" if path.endswith("_win-off.json") else "rth"
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if os.path.basename(path).startswith(prefix + "null_"):
            for family, seeds in payload["null_control"].items():
                nulls[(payload["symbol"], payload["bar_minutes"], window, family)] = seeds
            continue
        validation = payload.get("validation") or {}
        for family, winner in (payload.get("families") or {}).items():
            key = (payload["symbol"], payload["bar_minutes"], window, family)
            got = validation.get(family) or {}
            if key in best and best[key][1] is not None and got.get("oos") is None:
                continue  # keep the validated reading
            best[key] = (winner, got.get("oos"), got.get("cost_sweep_bp") or {},
                         payload, path)
    return best, nulls


def survivors(kind, is_only=False):
    """`is_only`: every 2024-2025 winner (step 1 alone), with no 2026 field --
    the pool `fundednext_combined_strategies` chooses from."""
    best, nulls = load(kind)
    _, suffix, lookbacks = KINDS[kind]
    out, resolved = [], set()
    for (symbol, bar, window, family), (winner, oos, sweep, payload, path) in best.items():
        if symbol in EXCLUDE_SYMBOLS or not winner:
            continue
        if is_only:
            top = seeds = None
        else:
            if oos is None or (symbol, bar, window, family) not in nulls:
                continue
            if (oos["return_pct"] <= 0 or (oos.get("pf") or 0) < 1.05
                    or oos["trades"] < MIN_TRADES):
                continue
            seeds = [r for r in nulls[(symbol, bar, window, family)] if r]
            top = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
            if top is not None and oos["return_pct"] <= top:
                continue
        if symbol not in resolved:
            ef.resolve(symbol, allow_stale=True)
            resolved.add(symbol)
        spec = ef.INSTRUMENTS[symbol]
        protocol = payload.get("protocol") or {}
        group = (protocol.get("family_groups") or {}).get(family)
        if group is None:
            group = ef.FAMILIES[family].group if family in ef.FAMILIES else "tt"
        holdout = {} if is_only else {
            "cost_sweep_bp": sweep,
            "holdout": "2026-01-01 through 2026-10-12",
            "null_control": {
                "best_null_oos_return_pct": top,
                "margin_pts": None if top is None else round(oos["return_pct"] - top, 2),
                "method": NULL_METHOD,
                "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                         for r in seeds}},
            "null_verdict": "PASS*" if top is None else "PASS",
            "out_of_sample": oos}
        out.append({
            **holdout,
            "asset_class": spec["asset_class"], "bar_minutes": bar,
            "broker": "fundednext", "family": family,
            "group": group, "holding": (protocol.get("holding") or {}).get(family),
            "in_sample": winner["in_sample"],
            "in_sample_years": [2024, 2025],
            "params": winner["params"],
            "lookbacks": lookbacks, "protocol": protocol,
            "provenance": {"seal_sha256": payload.get("seal_sha256"),
                           "select": os.path.basename(path),
                           "written": time.strftime("%Y-%m-%d")},
            "robust_neighbours": winner.get("robust_neighbours"),
            "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
            "session": session_block(symbol), "study_wave": WAVE,
            "symbol": symbol,
            "timeframe": f"{bar}m{suffix}{'-off' if window == 'off' else ''}",
            "window": ("off (entries only while RTH is shut)" if window == "off"
                       else "rth")})
    return out


def file_name(cell):
    """`audjpy_amihud-60m-offhour.json`: new_cfd_families' `<family>-<tf>-<window>`."""
    bar = cell["timeframe"].split("-")[0]
    window = "offhour" if cell["timeframe"].endswith("-off") else "rth"
    return f"{cell['symbol']}_{cell['family']}-{bar}-{window}.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--is-only", action="store_true",
                        help="every 2024-2025 winner, no 2026 filter -> results/fundednext_is/")
    args = parser.parse_args()
    cells = survivors("cfd", args.is_only) + survivors("tt", args.is_only)
    if args.is_only:
        print(f"{len(cells)} 2024-2025 winners",
              dict(Counter(c["search"] for c in cells)))
    else:
        tally = Counter((c["search"], c["null_verdict"]) for c in cells)
        print(f"{len(cells)} survivors", dict(tally))
    if args.dry_run:
        return 0
    if args.is_only:
        os.makedirs(IS_DIR, exist_ok=True)
        for c in cells:
            with open(os.path.join(IS_DIR, file_name(c)), "w", encoding="utf-8") as handle:
                json.dump(c, handle, indent=2, sort_keys=True)
                handle.write("\n")
        print(f"wrote {len(cells)} files -> {IS_DIR}")
        return 0
    os.makedirs(SURVIVOR_DIR, exist_ok=True)
    for c in cells:
        name = file_name(c)
        with open(os.path.join(SURVIVOR_DIR, name), "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True)
            handle.write("\n")
    rows = sorted(({**index_row(c), "null_verdict": c["null_verdict"]} for c in cells),
                  key=lambda r: (r["symbol"], r["family"], r["timeframe"]))
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump({"count": len(rows), "study_wave": WAVE,
                   "written": time.strftime("%Y-%m-%d"), "survivors": rows},
                  handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(cells)} files + SURVIVORS.json -> {SURVIVOR_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
