"""The sixth wave's final scoreboard: holdout AND coin-flip null, every symbol.

A FAMILY PASSES ONLY IF IT MADE MONEY OUT OF SAMPLE **AND** BEAT THE BEST OF ITS
OWN THREE COIN-FLIP SEEDS ON THE SAME HOLDOUT. The best of the seeds rather than
their mean, because the real search selects the best of N draws by construction,
so the bar it has to clear is what that construction produces from noise at ITS
luckiest.

THREE THINGS THIS PRINTS THAT A PASS COUNT ALONE WOULD HIDE.

`clusters` -- how many DISTINCT signal sets the passes represent. Nine of the new
families share a direction engine, `side = sign(close[i] - close[i-session])`,
behind a state test the search is free to set permissively. On ethusd six of
them collapsed onto one rule with 91-100% signal agreement, and three of those
six passed -- so nine passes were seven rules
([[permissive-gates-collapse-families-onto-one-rule]]). A pass count that does
not divide by this is counting one hypothesis several times.

`margin` -- holdout return minus the null's. A family that returned +56% against
a null of +68% failed, and a family that returned +21% against a null of +10%
passed. The margin is the result; the return is not.

`share` -- the fraction of ALL bars in the window the winning cell fires on.
Anything above ~50% means the family's own gate is inert and it has degenerated
to its fallback rule, whatever its name says.

    python -m sandbox.research._sixth_verdict
    python -m sandbox.research._sixth_verdict --out results/SIXTH_VERDICT.md
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
from collections import Counter, defaultdict

from sandbox.research import cfd_families as ef
from sandbox.research import _sixth_compare as sc
from sandbox.research._sixth_overlap import jaccard, signals

SIXTH = set(ef.ALIASES["sixth"])


def null_for(symbol, bar):
    path = os.path.join(
        ef.RESULTS,
        f"exness_families_null_{symbol}_{ef.label_bar(bar)}_sixth.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["null_control"]


def verdicts(symbol, bar):
    payload = sc.load_new(symbol, bar)
    if payload is None:
        return []
    validation = payload.get("validation") or {}
    null = null_for(symbol, bar)
    out = []
    for family, winner in payload["families"].items():
        if not winner:
            continue
        oos = (validation.get(family) or {}).get("oos")
        if oos is None:
            continue
        rows = [r for r in ((null or {}).get(family) or []) if r]
        seeds = len(rows) if (null and family in null) else None
        flip = max((r["out_of_sample"]["return_pct"] for r in rows),
                   default=None)
        ret = oos["return_pct"]
        if ret <= 0:
            verdict = "fail-oos"
        elif seeds is None:
            verdict = "not-run"
        elif seeds == 0:
            verdict = "PASS*"
        elif flip is not None and ret > flip:
            verdict = "PASS"
        else:
            verdict = "fail-null"
        out.append({
            "symbol": symbol, "family": family, "verdict": verdict,
            "oos": ret, "flip": flip, "seeds": seeds,
            "margin": None if flip is None else ret - flip,
            "dd": oos["max_dd_pct"], "n": oos["trades"], "pf": oos.get("pf"),
            "is": winner["in_sample"]["return_pct"],
            "ist": winner["in_sample"].get("edge_vs_drift_t_stat"),
            "robust": winner.get("robust_neighbours"),
        })
    return out


def clusters_for(symbol, bar, families, threshold=0.70):
    """Distinct signal clusters among `families`, and each one's bar share."""
    payload = sc.load_new(symbol, bar)
    winners = {f: ef.rehydrate(w["params"])
               for f, w in payload["families"].items()
               if w and f in families}
    if len(winners) < 2:
        return {f: {f} for f in winners}, {}
    ef.resolve(symbol, allow_stale=True)
    names = sorted(winners)
    bars, ctx = ef.context(symbol, "validate", bar, set(names))
    fired = signals(symbol, bar, names, winners, bars, ctx,
                    ef.IS_END, ef.OOS_END)
    window = sum(1 for row in bars if ef.IS_END <= row[ef.TS] < ef.OOS_END)
    share = {f: len(fired[f]) / window for f in names} if window else {}
    linked = {f: {f} for f in names}
    for a, b in itertools.combinations(names, 2):
        if jaccard(fired[a], fired[b]) >= threshold:
            merged = linked[a] | linked[b]
            for name in merged:
                linked[name] = merged
    return linked, share


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--no-clusters", action="store_true",
                        help="skip the signal-overlap pass (it rebuilds every "
                             "symbol's context and is the slow part)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    names = (args.symbols.replace(" ", "").split(",") if args.symbols
             else [s for s in ef.UNIVERSE if ef.CLASS[s] != "stock"])
    lines = []

    def say(text=""):
        print(text, flush=True)
        lines.append(text)

    all_rows = []
    per_symbol = {}
    for symbol in names:
        rows = verdicts(symbol, args.bar_minutes)
        if rows:
            per_symbol[symbol] = rows
            all_rows.extend(rows)

    say(f"# Sixth wave -- final verdict, {ef.label_bar(args.bar_minutes)}")
    say()
    say("PASS = made money on the 2025-01-01 -> 2026-08-16 holdout AND beat the")
    say("best of its own three coin-flip seeds on that same holdout.")
    say()
    say(f"{len(per_symbol)} symbols scored: {', '.join(sorted(per_symbol))}")
    missing = [s for s in names if s not in per_symbol]
    if missing:
        say(f"not run ({len(missing)}): {', '.join(missing)}")
    say()

    counts = Counter(r["verdict"] for r in all_rows)
    passes = counts["PASS"] + counts["PASS*"]
    total = len(all_rows)
    say("## Pooled")
    say()
    say(f"    scored          {total}")
    say(f"    PASS            {passes}"
        + (f"  ({100.0 * passes / total:.0f}%)" if total else ""))
    say(f"    fail vs null    {counts['fail-null']}")
    say(f"    fail on holdout {counts['fail-oos']}")
    say(f"    null not run    {counts['not-run']}")
    say()

    say("## Per symbol")
    say()
    say("| symbol | selected | +holdout | PASS | fail-null | distinct clusters |")
    say("|---|---|---|---|---|---|")
    cluster_note = {}
    for symbol in sorted(per_symbol):
        rows = per_symbol[symbol]
        c = Counter(r["verdict"] for r in rows)
        won = [r["family"] for r in rows if r["verdict"].startswith("PASS")]
        distinct = "-"
        if won and not args.no_clusters:
            linked, share = clusters_for(symbol, args.bar_minutes, set(won))
            groups = {frozenset(v) for v in linked.values()}
            distinct = f"{len(groups)}/{len(won)}"
            big = [g for g in groups if len(g) > 1]
            if big:
                cluster_note[symbol] = big
            for row in rows:
                row["share"] = share.get(row["family"])
        say(f"| {symbol} | {len(rows)} | "
            f"{sum(1 for r in rows if r['oos'] > 0)} | "
            f"{c['PASS'] + c['PASS*']} | {c['fail-null']} | {distinct} |")
    say()

    if cluster_note:
        say("### Passes that are the same rule")
        say()
        for symbol, groups in sorted(cluster_note.items()):
            for group in groups:
                say(f"- **{symbol}**: {', '.join(sorted(group))} "
                    f"-- one signal set, {len(group)} names")
        say()

    say("## Every passing cell")
    say()
    say("| symbol | family | OOS % | null % | margin | dd | n | pf | IS t | bars fired |")
    say("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(all_rows, key=lambda r: -(r["margin"] or -1e9)):
        if not r["verdict"].startswith("PASS"):
            continue
        share = r.get("share")
        flip = "-" if r["flip"] is None else format(r["flip"], "+.1f")
        margin = "-" if r["margin"] is None else format(r["margin"], "+.1f")
        fired = "-" if share is None else f"{100 * share:.0f}%"
        say(f"| {r['symbol']} | {r['family']} | {r['oos']:+.1f} | {flip} | "
            f"{margin} | {r['dd']:.1f} | {r['n']} | {r['pf']:.2f} | "
            f"{r['ist'] or 0:.2f} | {fired} |")
    say()

    say("## Families ranked by how often they pass")
    say()
    by_family = defaultdict(list)
    for r in all_rows:
        by_family[r["family"]].append(r)
    say("| family | group | scored | PASS | rate | median margin |")
    say("|---|---|---|---|---|---|")
    def rate(f):
        rs = by_family[f]
        p = sum(1 for r in rs if r["verdict"].startswith("PASS"))
        return -(p / len(rs)), -p
    for family in sorted(by_family, key=rate):
        rs = by_family[family]
        p = sum(1 for r in rs if r["verdict"].startswith("PASS"))
        margins = sorted(r["margin"] for r in rs if r["margin"] is not None)
        med = margins[len(margins) // 2] if margins else float("nan")
        say(f"| {family} | {ef.FAMILIES[family].group} | {len(rs)} | {p} | "
            f"{100.0 * p / len(rs):.0f}% | {med:+.1f} |")

    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(
            os.path.dirname(ef.RESULTS), args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
