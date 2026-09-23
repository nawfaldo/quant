"""Which sealed cells survived the holdout, and the `why` commands to price them.

THE POINT OF SCOPING THE NULL THIS WAY. `why` re-runs a family's ENTIRE grid
with entry directions replaced by coin flips, three times. Run across every
family and symbol that is three times the cost of the original sweep, and most
of it is spent pricing candidates the holdout has already killed.

Scoping it to survivors is not a shortcut that weakens the control. `choose`
picks each family's winner independently, inside that family's own grid -- so
running `why --families xma_cross` reproduces exactly the search budget
`xma_cross`'s winner was drawn from. The null for a family is valid whether or
not its neighbours were also flipped. What would NOT be valid is narrowing the
grid within a family, and nothing here does that.

    python -m sandbox.research.exness_survivors            # the table
    python -m sandbox.research.exness_survivors --commands # the why plan
"""
from __future__ import annotations

import argparse
import glob
import json
import os

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")


def survivors(scope="new", bar="30m", threshold=0.0):
    """`{symbol: {family: (is_return, oos_return, oos_dd, n, pf)}}`.

    A survivor made money out of sample. That is a low bar on purpose -- it is
    a filter for what is worth spending a null on, not a verdict. The null is
    what decides, and a candidate that beat the holdout but not its own coin
    flips has still shown nothing.
    """
    out = {}
    pattern = os.path.join(RESULTS, f"exness_families_*_{bar}_{scope}.json")
    for path in sorted(glob.glob(pattern)):
        symbol = os.path.basename(path).split("_")[2]
        payload = json.load(open(path, encoding="utf-8"))
        validation = payload.get("validation") or {}
        for family, winner in payload["families"].items():
            if winner is None or family not in validation:
                continue
            oos = validation[family]["oos"]
            if oos["return_pct"] > threshold:
                out.setdefault(symbol, {})[family] = (
                    winner["in_sample"]["return_pct"], oos["return_pct"],
                    oos["max_dd_pct"], oos["trades"], oos["pf"])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="new")
    parser.add_argument("--bar", default="30m")
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--commands", action="store_true")
    parser.add_argument("--workers", type=int, default=15)
    args = parser.parse_args()

    found = survivors(args.scope, args.bar, args.threshold)
    if args.commands:
        for symbol, families in found.items():
            print(f"--symbols {symbol} --families {','.join(sorted(families))}")
        return

    print(f"{'symbol':9}{'family':22}{'IS %':>9}{'OOS %':>9}{'OOS dd':>8}"
          f"{'n':>6}{'PF':>6}")
    rows = []
    for symbol, families in found.items():
        for family, values in families.items():
            rows.append((symbol, family, *values))
    for row in sorted(rows, key=lambda r: -r[3]):
        print(f"{row[0]:9}{row[1]:22}{row[2]:>+9.1f}{row[3]:>+9.1f}"
              f"{row[4]:>8.1f}{row[5]:>6}{row[6]:>6.2f}")
    print(f"\n{len(rows)} survivors across {len(found)} symbols")


if __name__ == "__main__":
    main()
