"""Consolidate every sealed `cfd_families` run into one readable table.

`select`, `validate` and `why` each write JSON, and JSON is not readable by eye.
This turns the whole set into a markdown file and prints the same thing, so the
numbers can be pasted into a reply ([[paste-results-into-the-reply]]).

The verdict column is the only interpretation here, and it is deliberately
severe: a family is `PASS` only if it made money out of sample AND beat the best
of its own three coin-flip seeds on the same holdout. Everything else is
`fail`, `no-null` (the control has not been run yet) or `-` (no in-sample cell).

    python -m sandbox.research.exness_families_report --bar-minutes 30
"""
from __future__ import annotations

import argparse
import json
import os

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")


def label_bar(bar):
    return "1d" if bar >= 1440 else f"{bar}m"


def load(symbol, bar, scope=None):
    """The sealed selection and, if it exists, its coin-flip control.

    `scope` names a restricted run -- `--groups xma` writes
    `exness_families_tsla_30m_xma.json` so a one-group sweep cannot overwrite a
    full one. The report has to ask for the same scope it wants to read; falling
    back to the unrestricted file would silently report a different study than
    the one requested.
    """
    tail = f"_{scope}" if scope else ""
    path = os.path.join(
        RESULTS, f"exness_families_{symbol}_{label_bar(bar)}{tail}.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    null_path = os.path.join(
        RESULTS, f"exness_families_null_{symbol}_{label_bar(bar)}{tail}.json")
    null = None
    if os.path.exists(null_path):
        with open(null_path, encoding="utf-8") as handle:
            null = json.load(handle)["null_control"]
    return payload, null


def null_best(null, family):
    """`(best_holdout_return, seeds_that_found_a_cell)` for the coin-flip search.

    The best of the seeds rather than their mean: the real search selects the
    best of N draws by construction, so the bar it has to clear is what that
    same construction produces from noise at ITS luckiest. Averaging the seeds
    would be a softer bar than the one the candidate was chosen under.

    A seed can return nothing at all -- with directions randomised, no cell in
    the family clears the in-sample gates. That is NOT a missing measurement, it
    is the strongest form of the control passing, so it is reported as a count
    rather than folded into the maximum and lost. `family` absent from the file
    entirely is the missing case, and returns `(None, None)`.
    """
    if not null or family not in null:
        return None, None
    rows = [r for r in (null.get(family) or []) if r]
    if not rows:
        return None, 0
    return max(r["out_of_sample"]["return_pct"] for r in rows), len(rows)


def rows_for(symbol, bar, scope=None, groups=None):
    payload, null = load(symbol, bar, scope)
    if payload is None:
        return []
    validation = payload.get("validation") or {}
    # Written by `select` since the second wave; older sealed files predate it
    # and simply have no group labels rather than wrong ones.
    labels = (payload.get("protocol") or {}).get("family_groups") or {}
    out = []
    for family, winner in sorted(payload["families"].items()):
        group = labels.get(family, "-")
        if groups and group not in groups:
            continue
        if winner is None:
            out.append({"symbol": symbol, "family": family, "group": group,
                        "verdict": "-"})
            continue
        stat = winner["in_sample"]
        oos = (validation.get(family) or {}).get("oos")
        flip, seeds = null_best(null, family)
        row = {
            "symbol": symbol, "family": family, "group": group,
            "is_return": stat["return_pct"], "is_dd": stat["max_dd_pct"],
            "is_trades": stat["trades"], "is_t": stat.get("edge_vs_drift_t_stat"),
            "flip": flip, "null_seeds": seeds,
        }
        if oos is None:
            row["verdict"] = "no-oos"
        else:
            row.update(oos_return=oos["return_pct"], oos_dd=oos["max_dd_pct"],
                       oos_trades=oos["trades"], pf=oos["pf"],
                       breakeven=oos.get("breakeven_bp"))
            # Losing money out of sample settles it on its own -- the null is a
            # bar for candidates that made money, so it is never consulted to
            # condemn one that did not.
            if oos["return_pct"] <= 0:
                row["verdict"] = "fail"
            elif seeds is None:
                row["verdict"] = "not-run"
            elif seeds == 0:
                # No coin-flip seed found a cell that clears the gates at all.
                row["verdict"] = "PASS*"
            elif oos["return_pct"] > flip:
                row["verdict"] = "PASS"
            else:
                row["verdict"] = "fail"
        out.append(row)
    return out


def fmt(value, spec="+.1f"):
    return "-" if value is None else format(value, spec)


def render(rows, bar):
    lines = [f"# Exness family study -- {label_bar(bar)}", "",
             "$1,000 initial balance, Exness Pro 416209807, spread-only cost.",
             "IS is each symbol's first full year through 2024; OOS is",
             "2025-01-01 through 2026-08-16, untouched by selection.",
             "`flip` is the best holdout return of three coin-flip seeds running",
             "the identical search -- a candidate that does not beat it has shown",
             "nothing. `seeds` is how many of the three found any in-sample cell",
             "at all once directions were randomised.", "",
             "  PASS    positive OOS and above its own null",
             "  PASS*   positive OOS and no coin-flip seed found a cell to compare",
             "  fail    negative OOS, or positive but not above the null",
             "  not-run the null has not been run for this family", "",
             "| symbol | group | family | IS % | IS dd | IS n | IS t | OOS % "
             "| OOS dd | OOS n | PF | BE bp | flip % | seeds | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        if row["verdict"] == "-":
            continue
        seeds = row.get("null_seeds")
        lines.append(
            f"| {row['symbol']} | {row.get('group', '-')} | {row['family']} "
            f"| {fmt(row.get('is_return'))} "
            f"| {fmt(row.get('is_dd'), '.1f')} | {row.get('is_trades', '-')} "
            f"| {fmt(row.get('is_t'), '.2f')} | {fmt(row.get('oos_return'))} "
            f"| {fmt(row.get('oos_dd'), '.1f')} | {row.get('oos_trades', '-')} "
            f"| {fmt(row.get('pf'), '.2f')} | {fmt(row.get('breakeven'), '.1f')} "
            f"| {fmt(row.get('flip'))} "
            f"| {'-' if seeds is None else f'{seeds}/3'} | {row['verdict']} |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="all")
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--out", default=None)
    parser.add_argument("--passing-only", action="store_true")
    parser.add_argument("--scope", default=None,
                        help="read the file a restricted `select` wrote, e.g. "
                             "--scope xma for a --groups xma run")
    parser.add_argument("--groups", default=None,
                        help="show only these thesis groups, comma separated")
    args = parser.parse_args()

    from sandbox.research import cfd_families as ef
    symbols = ef.expand(args.symbols)
    groups = set(args.groups.replace(" ", "").split(",")) if args.groups else None
    if groups:
        unknown = groups - set(ef.GROUPS)
        if unknown:
            raise SystemExit(f"unknown groups {sorted(unknown)}; "
                             f"have {sorted(ef.GROUPS)}")

    rows = []
    for symbol in symbols:
        rows.extend(rows_for(symbol, args.bar_minutes, args.scope, groups))
    if args.passing_only:
        rows = [r for r in rows if r["verdict"].startswith("PASS")]
    rows.sort(key=lambda r: -(r.get("oos_return") or -1e9))

    text = render(rows, args.bar_minutes)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
