"""Write the bar-lookback (EXNESS_BAR_LOOKBACKS=1) survivors into `results/exness/`.

Same three-step definition as the folder README and `_sixth_export`: in-sample
winner, holdout ret > 0 / PF >= 1.05 / n >= 30, and beat the best coin-flip seed
(or no seed found an in-sample cell -- `margin_pts: None`, the "pass*" rows).

Files are named `<sym>_<family>_<bar>m-barlb.json`, so they never collide with a
day-scaled survivor and never match the book builder's `*_30m.json` glob.
Also writes `barlb_vs_30m.md`: each survivor beside the same symbol/family at
30m (day-scaled lookbacks) from the 30m seals.

    python -m sandbox.research._barlb_export [--dry-run]
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

WAVE = "barlb_2026-10-11"
BARS = (5, 15, 60, 240)
R = ef.RESULTS
TF = {5: "5m", 15: "15m", 60: "1h", 240: "4h"}


def seals(symbol, bar):
    for path in sorted(glob.glob(os.path.join(
            R, f"exness_families_{symbol}_{bar}m-barlb_*.json"))):
        if "_win-" in path:  # another trading window, not this study
            continue
        with open(path, encoding="utf-8") as handle:
            yield path, json.load(handle)


def nulls(symbol, bar):
    out = {}
    for path in glob.glob(os.path.join(
            R, f"exness_families_null_{symbol}_{bar}m-barlb_*.json")):
        if "_win-" in path:
            continue
        with open(path, encoding="utf-8") as handle:
            out.update(json.load(handle)["null_control"])
    return out


def survivors(symbol, bar):
    null = nulls(symbol, bar)
    ef.resolve(symbol, allow_stale=True)
    out = []
    for path, payload in seals(symbol, bar):
        validation = payload.get("validation") or {}
        protocol = payload.get("protocol") or {}
        for family, winner in (payload.get("families") or {}).items():
            oos = (validation.get(family) or {}).get("oos")
            if not winner or oos is None or family not in null:
                continue
            if (oos["return_pct"] <= 0 or (oos.get("pf") or 0) < 1.05
                    or oos["trades"] < 30):
                continue
            seeds = [r for r in null[family] if r]
            best = max((r["out_of_sample"]["return_pct"] for r in seeds),
                       default=None)
            if best is not None and oos["return_pct"] <= best:
                continue
            spec = ef.INSTRUMENTS[symbol]
            out.append({
                "asset_class": spec["asset_class"], "bar_minutes": bar,
                "cost_sweep_bp": validation[family].get("cost_sweep_bp") or {},
                "family": family,
                "group": (protocol.get("family_groups") or {}).get(
                    family, ef.FAMILIES[family].group),
                "holding": (protocol.get("holding") or {}).get(
                    family, ef.family_hold(family)),
                "in_sample": winner["in_sample"],
                "null_control": {
                    "best_null_oos_return_pct": best,
                    "margin_pts": (None if best is None
                                   else round(oos["return_pct"] - best, 2)),
                    "method": NULL_METHOD,
                    "seeds_oos_return_pct": {
                        str(r["seed"]): r["out_of_sample"]["return_pct"]
                        for r in seeds}},
                "out_of_sample": oos, "params": winner["params"],
                "lookbacks": "bar-counted (EXNESS_BAR_LOOKBACKS=1)",
                "protocol": protocol,
                "provenance": {"seal_sha256": payload.get("seal_sha256"),
                               "select": os.path.basename(path),
                               "written": time.strftime("%Y-%m-%d")},
                "robust_neighbours": winner.get("robust_neighbours"),
                "session": session_block(symbol), "study_wave": WAVE,
                "symbol": symbol, "timeframe": f"{bar}m-barlb"})
    return out


def thirty(symbol, family):
    """Best-informed 30m reading of symbol/family: a validated seal first."""
    best = None
    for path in glob.glob(os.path.join(R, f"exness_families_{symbol}_30m*.json")):
        if "_null_" in path:
            continue
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        winner = (payload.get("families") or {}).get(family, "absent")
        if winner == "absent":
            continue
        oos = ((payload.get("validation") or {}).get(family) or {}).get("oos")
        rank = (oos is not None, winner is not None, os.path.getmtime(path))
        if best is None or rank > best[0]:
            best = (rank, winner, oos)
    return best


def pct(x):
    return "-" if x is None else f"{x:+.1f}%"


def dd(x):
    return "-" if x is None else f"{x:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with open(INDEX, encoding="utf-8") as handle:
        index = json.load(handle)
    surv30 = {(r["symbol"], r["family"]) for r in index["survivors"]
              if r["timeframe"] == "30m"}

    names = {os.path.basename(p).split("_")[2] for p in glob.glob(
        os.path.join(R, "exness_families_*m-barlb_*.json"))} - {"null"}
    cells = [c for s in sorted(names) for b in BARS for c in survivors(s, b)]
    star = sum(c["null_control"]["best_null_oos_return_pct"] is None
               for c in cells)
    print(f"{len(cells)} survivors ({len(cells) - star} pass, {star} pass*)")

    rows = ["| TF | Symbol | Family | | IS ret | IS dd | OOS ret | OOS dd | flip "
            "| 30m IS ret | 30m IS dd | 30m OOS ret | 30m OOS dd | 30m survivor |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tally = Counter()
    for c in sorted(cells, key=lambda c: (c["bar_minutes"],
                                          -c["out_of_sample"]["return_pct"])):
        s, f = c["symbol"], c["family"]
        got = thirty(s, f)
        win, oos30 = (got[1], got[2]) if got else (None, None)
        isr = (win or {}).get("in_sample") or {}
        o = c["out_of_sample"]
        if oos30 is not None:
            tally["beats 30m OOS" if o["return_pct"] > oos30["return_pct"]
                  else "loses to 30m OOS"] += 1
        elif got is None:
            tally["family never run at 30m"] += 1
        elif win is None:
            tally["30m had no IS winner"] += 1
        else:
            tally["30m IS winner never validated"] += 1
        null = c["null_control"]["best_null_oos_return_pct"]
        rows.append("| " + " | ".join([
            TF[c["bar_minutes"]], s, f, "pass*" if null is None else "pass",
            pct(c["in_sample"]["return_pct"]), dd(c["in_sample"]["max_dd_pct"]),
            pct(o["return_pct"]), dd(o["max_dd_pct"]),
            "no IS cell" if null is None else pct(null),
            ("not run" if got is None else "no winner" if win is None
             else pct(isr.get("return_pct"))),
            dd(isr.get("max_dd_pct")),
            pct(oos30["return_pct"]) if oos30 else "-",
            dd(oos30["max_dd_pct"]) if oos30 else "-",
            "yes" if (s, f) in surv30 else ""]) + " |")
    with open(os.path.join(SURVIVOR_DIR, "barlb_vs_30m.md"), "w",
              encoding="utf-8") as handle:
        handle.write("# barlb survivors vs the same symbol/family at 30m\n\n"
                     "30m columns are day-scaled lookbacks from the 30m seals "
                     "(the validated seal when one exists).\n\n")
        handle.write("\n".join(f"- {k}: {v}" for k, v in tally.most_common()))
        handle.write("\n\n" + "\n".join(rows) + "\n")
    print(dict(tally))
    if args.dry_run:
        return 0

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
                                key=lambda r: (r["symbol"], r["family"],
                                               r["timeframe"]))
    index["count"] = len(index["survivors"])
    index["waves"] = dict(Counter(r.get("study_wave")
                                  for r in index["survivors"]))
    index["written"] = time.strftime("%Y-%m-%d")
    with open(INDEX, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(cells)} files; index now {index['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
