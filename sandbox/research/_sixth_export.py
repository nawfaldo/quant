"""Write the sixth wave's survivors into `results/exness/`, tagged as their own wave.

WHAT A SURVIVOR IS, AND IT IS NOT THE SAME AS A `PASS`. The folder's README
fixes the definition and this module obeys it rather than inventing one:

    1. `select`   -- won its family's in-sample search and cleared the shared gates
    2. `validate` -- holdout return > 0, PF >= 1.05, n >= 30
    3. `why`      -- beat the best of its coin-flip seeds, OR no seed found any
                     in-sample cell at all

Step 2's `n >= 30` and `PF >= 1.05` are extra bars that `_sixth_verdict`'s PASS
does not apply, so a handful of PASS rows are correctly refused here -- `nq/runs`
holds 21 trades and cannot support a correlation estimate, let alone an edge.

`null_seeds_run: 0` IS ADMITTED, and that is the existing convention rather than
a concession: 52 of the 164 incumbent survivors have it. It means the null ran
and no coin-flip seed cleared the in-sample gates at all, which is the strongest
form of the control passing -- there was no random cell to beat. `margin_pts` is
`None` for those, exactly as the incumbents record it.

WRITING HERE HAS A CONSEQUENCE. `exness_combined_strategies.candidates()` globs
every `*_30m.json` in this folder, so a file dropped here is immediately
eligible for the live book. It applies its own harder gates on top
(`MIN_OOS_RETURN` 3.0, `MIN_OOS_TRADES` 40, `MIN_IS_T` 1.50, or a
buy-and-hold-beating drawdown ratio), so this is not an automatic promotion --
but it does mean these rows now sit in the pool the book is chosen from.

`study_wave: "sixth"` is how they are told apart from the three incumbent waves,
and the book builder already reads that field into its `wave` column.

    python -m sandbox.research._sixth_export --dry-run
    python -m sandbox.research._sixth_export
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter

from sandbox.research import cfd_families as ef
from sandbox.research import _sixth_compare as sc
from sandbox.research._sixth_verdict import null_for

SURVIVOR_DIR = os.path.join(ef.RESULTS, "exness")
INDEX = os.path.join(SURVIVOR_DIR, "SURVIVORS.json")
WAVE = "sixth"

#: The folder's own admission thresholds, from its README. NOT the verdict
#: script's PASS rule, which is looser.
MIN_OOS_RETURN = 0.0
MIN_OOS_PF = 1.05
MIN_OOS_TRADES = 30

NULL_METHOD = ("same search, entry direction replaced by a coin flip; "
               "the real holdout return must beat the best seed")


def session_block(symbol):
    """The traded window, in the shape the incumbent files record it."""
    spec = ef.INSTRUMENTS[symbol]
    opened, closed = spec["session"]

    def clock(minutes):
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    pinned = symbol in getattr(ef, "SESSION", {})
    return {"open_ny": clock(opened), "close_ny": clock(closed),
            "hours": round((closed - opened) / 60.0, 2),
            "shift_hours": spec.get("shift_hours", 0),
            "source": ("pinned in SESSION" if pinned
                       else "derived from the traded volume profile")}


def survivors_for(symbol, bar=30):
    payload = sc.load_new(symbol, bar)
    if payload is None:
        return [], Counter()
    validation = payload.get("validation") or {}
    null = null_for(symbol, bar) or {}
    protocol = payload.get("protocol") or {}
    groups = protocol.get("family_groups") or {}
    holdings = protocol.get("holding") or {}
    seal = payload.get("seal_sha256")
    source = os.path.basename(ef.output_path(symbol, bar,
                                             set(ef.ALIASES["sixth"])))
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


def index_row(cell):
    null = cell["null_control"]
    oos = cell["out_of_sample"]
    return {
        "asset_class": cell["asset_class"],
        "best_null_oos_return_pct": null["best_null_oos_return_pct"],
        "family": cell["family"],
        "file": f"{cell['symbol']}_{cell['family']}_{cell['timeframe']}.json",
        "group": cell["group"],
        "margin_pts": null["margin_pts"],
        "null_seeds_run": len(null["seeds_oos_return_pct"]),
        "oos_breakeven_bp": oos.get("breakeven_bp"),
        "oos_edge_vs_drift_t_stat": oos.get("edge_vs_drift_t_stat"),
        "oos_max_dd_pct": oos["max_dd_pct"],
        "oos_monthly_sharpe": oos.get("monthly_sharpe"),
        "oos_pf": oos.get("pf"),
        "oos_return_pct": oos["return_pct"],
        "oos_trades": oos["trades"],
        "study_wave": cell["study_wave"],
        "symbol": cell["symbol"],
        "timeframe": cell["timeframe"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    names = [s for s in ef.UNIVERSE if ef.CLASS[s] != "stock"]
    cells, why = [], Counter()
    for symbol in names:
        rows, reasons = survivors_for(symbol, args.bar_minutes)
        cells.extend(rows)
        why.update(reasons)

    print(f"sixth wave: {len(cells)} survivors")
    print(f"{'symbol':9}{'family':21}{'OOS %':>8}{'null %':>8}{'margin':>8}"
          f"{'n':>6}{'pf':>6}{'seeds':>7}")
    for cell in sorted(cells, key=lambda c: -c["out_of_sample"]["return_pct"]):
        null, oos = cell["null_control"], cell["out_of_sample"]
        best = null["best_null_oos_return_pct"]
        print(f"{cell['symbol']:9}{cell['family']:21}"
              f"{oos['return_pct']:>+8.1f}"
              f"{('-' if best is None else format(best, '+.1f')):>8}"
              f"{('-' if null['margin_pts'] is None else format(null['margin_pts'], '+.1f')):>8}"
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
        # A sixth-wave family name cannot collide with an incumbent -- all 27
        # are new -- but assert it rather than trust it, because a silent
        # overwrite here would destroy a sealed survivor from another wave.
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
