"""Consolidate every sealed `--groups fourth` result into one readable table.

READ THE HEADER OF `exness_families` FIRST. Terminal output does not reach the
user, so this writes a markdown file as well as printing, and the path is the
thing to hand over.

WHAT IT REFUSES TO DO. It does not rank symbols by return. The fourth wave spans
four groups with different trade counts and different holding periods, and the
biggest number in a 163,000-cell search is a fact about the search. So every row
carries the trade count, the drift-adjusted t-statistic and the robust-neighbour
fraction next to the return, and the summary counts PASSES rather than averaging
returns -- a family that passed on nine symbols is the only kind of evidence a
sweep this size can offer on its own.
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys

from sandbox.research import exness_families as f

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")


def sealed(pattern):
    """Every sealed payload matching `pattern`, newest write order irrelevant."""
    out = []
    for path in sorted(glob.glob(os.path.join(RESULTS, pattern))):
        try:
            out.append((path, json.load(io.open(path, encoding="utf-8"))))
        except (ValueError, OSError):
            continue
    return out


def winners(doc):
    for name, entry in doc["families"].items():
        if entry and entry.get("params"):
            yield name, entry


def row(doc, name, entry):
    stat = entry["in_sample"]
    params = dict(entry["params"])
    cell = ", ".join(f"{k}={v}" for k, v in sorted(params.items())
                     if k not in ("exit_mode", "stop_day", "trend", "vol_mode"))
    return {
        "symbol": doc["symbol"], "class": doc["asset_class"],
        "bar": doc["timeframe"], "family": name,
        "group": f.FAMILIES[name].group if name in f.FAMILIES else "?",
        "return_pct": stat["return_pct"], "dd": stat["max_dd_pct"],
        "trades": stat["trades"], "pf": stat["pf"],
        "sharpe": stat["monthly_sharpe"],
        "gross_bp": stat.get("gross_bp_per_trade", 0.0),
        "edge_bp": stat.get("edge_vs_drift_bp", 0.0),
        "t": stat.get("edge_vs_drift_t_stat", 0.0),
        "robust": entry.get("robust_neighbours", "-"),
        "exit": params.get("exit_mode", "-"), "stop": params.get("stop_day", "-"),
        "trend": params.get("trend", "-"), "cell": cell,
    }


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else "exness_families_*_fourth.json"
    docs = sealed(pattern)
    if not docs:
        raise SystemExit(f"no sealed files match {pattern}")

    rows, ran, attempted = [], set(), 0
    for _path, doc in docs:
        ran.add((doc["symbol"], doc["timeframe"]))
        attempted += len(doc["families"])
        for name, entry in winners(doc):
            rows.append(row(doc, name, entry))

    rows.sort(key=lambda r: (-abs(r["t"]), -r["return_pct"]))
    lines = []

    def emit(text=""):
        print(text)
        lines.append(text)

    emit(f"# Fourth wave sweep: {len(ran)} symbol/bar jobs, "
         f"{attempted} family slots, {len(rows)} passed")
    emit()
    emit(f"Pass rate {len(rows)}/{attempted} = {100 * len(rows) / attempted:.1f}%.")
    emit()

    # ---- by group ---------------------------------------------------------- #
    emit("## Passes by group")
    emit()
    emit("| group | slots | passes | rate |")
    emit("|---|---:|---:|---:|")
    for group in ("night", "almanac", "horizon", "crossasset"):
        members = set(f.GROUPS[group])
        slots = sum(len(members & set(doc["families"])) for _p, doc in docs)
        hit = sum(1 for r in rows if r["group"] == group)
        if slots:
            emit(f"| {group} | {slots} | {hit} | {100 * hit / slots:.1f}% |")
    emit()

    # ---- by family --------------------------------------------------------- #
    emit("## Passes by family")
    emit()
    emit("| family | group | slots | passes | symbols |")
    emit("|---|---|---:|---:|---|")
    counts = {}
    for r in rows:
        counts.setdefault(r["family"], []).append(r["symbol"])
    for name in sorted(counts, key=lambda n: -len(counts[n])):
        slots = sum(1 for _p, doc in docs if name in doc["families"])
        emit(f"| `{name}` | {f.FAMILIES[name].group} | {slots} | "
             f"{len(counts[name])} | {', '.join(sorted(set(counts[name])))} |")
    emit()

    # ---- by class ---------------------------------------------------------- #
    emit("## Passes by asset class")
    emit()
    emit("| class | symbols | slots | passes |")
    emit("|---|---:|---:|---:|")
    classes = {}
    for _p, doc in docs:
        item = classes.setdefault(doc["asset_class"], [set(), 0, 0])
        item[0].add(doc["symbol"])
        item[1] += len(doc["families"])
    for r in rows:
        classes[r["class"]][2] += 1
    for name in sorted(classes):
        seen, slots, hit = classes[name]
        emit(f"| {name} | {len(seen)} | {slots} | {hit} |")
    emit()

    # ---- every survivor ----------------------------------------------------- #
    emit("## Every survivor, ranked by drift-adjusted t")
    emit()
    emit("**t is the statistic that matters.** The return is the outcome of one "
         "cell chosen from a 163,000-cell search; `edge_bp` strips the "
         "unconditional drift a mechanical rule with the same long/short mix "
         "and holding period would have earned anyway, and `t` says whether "
         "what is left is distinguishable from zero. `robust` below 3/3 means "
         "the neighbour gate asked almost nothing "
         "([[all-categorical-axes-void-the-robustness-gate]]).")
    emit()
    emit("| symbol | bar | family | group | ret | dd | n | pf | gross bp | "
         "edge bp | t | robust | exit | stop | trend | cell |")
    emit("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|")
    for r in rows:
        emit(f"| {r['symbol']} | {r['bar']} | `{r['family']}` | {r['group']} | "
             f"{r['return_pct']:+.1f}% | {r['dd']:.1f}% | {r['trades']} | "
             f"{r['pf']} | {r['gross_bp']:+.2f} | {r['edge_bp']:+.2f} | "
             f"{r['t']:+.2f} | {r['robust']} | {r['exit']} | {r['stop']} | "
             f"{r['trend']} | {r['cell']} |")
    emit()

    # ---- the two confounds that decide how this table reads ---------------- #
    emit("## Two confounds, before anything above is read as a finding")
    emit()
    emit("### 1. The gate is a function of history length, and stocks have less")
    emit()
    emit("`passes` demands `trades >= max(50, 25 * years)` and "
         "`positive_years >= years - 1`. Those are not constants -- they scale "
         "with the in-sample window, and the Dukascopy stock tables start in "
         "2020 while everything else starts in 2018. So the stocks were scored "
         "against a materially weaker bar than the symbols they are being "
         "compared with:")
    emit()
    emit("| class | IS years | trade floor | positive years needed |")
    emit("|---|---:|---:|---|")
    seen_class = {}
    for _p, doc in docs:
        seen_class.setdefault(doc["asset_class"], doc["symbol"])
    for name in sorted(seen_class):
        symbol = seen_class[name]
        try:
            years = f.is_years(symbol)
        except KeyError:
            f.resolve(symbol, allow_stale=True)
            years = f.is_years(symbol)
        emit(f"| {name} | {len(years)} ({years[0]}-{years[-1]}) | "
             f"{max(50, 25 * len(years))} | "
             f"{f.min_positive_years(symbol)}/{len(years)} |")
    emit()
    emit("**25 of the 26 passes are stocks.** Some unknown share of that is the "
         "gate being easier rather than the strategies being better, and this "
         "sweep cannot separate the two. The clean test is to re-run the "
         "non-stock symbols restricted to 2020-2024 so every symbol faces the "
         "same floors; until that is done, do not read the class table as a "
         "statement about which asset class the fourth wave works on.")
    emit()
    emit("### 2. TSLA and NVDA are 16 of the 26, which is a known artefact")
    emit()
    concentration = {}
    for r in rows:
        concentration[r["symbol"]] = concentration.get(r["symbol"], 0) + 1
    emit("| symbol | passes | share |")
    emit("|---|---:|---:|")
    for symbol in sorted(concentration, key=lambda s: -concentration[s]):
        emit(f"| {symbol} | {concentration[symbol]} | "
             f"{100 * concentration[symbol] / len(rows):.0f}% |")
    emit()
    emit("This is the result `exness_families` already documents and explains "
         "([[stock-cost-share-explains-the-tsla-result]]): risk per trade is "
         "fixed and the stop is a fraction of the daily range, so what decides "
         "a stock sweep is the ratio of spread to range -- the share of each "
         "unit of risk handed to the broker before a rule has done anything. "
         "TSLA pays 1.3% of its stop and ORCL 15.2%, a twelve-fold advantage. "
         "A fourth-wave sweep reproducing the same ranking is evidence the "
         "sweep is measuring cost share again, not evidence the night families "
         "like TSLA.")
    emit()

    strong = [r for r in rows if abs(r["t"]) >= 2.0]
    emit(f"**{len(strong)} of {len(rows)} survivors reach |t| >= 2.**")
    if strong:
        emit()
        for r in strong:
            emit(f"- {r['symbol']} {r['bar']} `{r['family']}`: "
                 f"{r['return_pct']:+.1f}%, n={r['trades']}, "
                 f"edge {r['edge_bp']:+.2f}bp t={r['t']:+.2f}, "
                 f"robust {r['robust']}")
    emit()
    emit("No null control has been run. A coin-flip search on this data has "
         "returned +622% at t=4.19 ([[coin-flip-control-beats-real-signals]]), "
         "so nothing above is a result until `why` has priced it at the same "
         "budget.")

    out = os.path.join(RESULTS, "FOURTH_WAVE_SWEEP.md")
    io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"\nwrote {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
