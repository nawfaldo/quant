"""Split a `/api/combine` result into per-strategy contribution and months.

`/api/combine` returns one trade list for the whole shared account and the
records carry no strategy field -- only entry/exit price and timestamp, quantity
and side. Attribution therefore has to come from outside the response.

Entry *timestamps* are the key that works. A signal fires from bars and
indicators, none of which depend on the account balance; only the quantity does.
So the set of `(entry_ts, side)` pairs a strategy produces is identical whether
it runs alone or in a shared account, and matching the combined list against
per-strategy standalone runs recovers which strategy opened each trade. Sizes
differ between the two runs and are deliberately not compared.

Collisions -- two strategies opening the same side in the same second -- are
counted and reported rather than silently assigned, since a collision means the
split is approximate for those trades.

    py -B -m sandbox.research.attribute_combine
"""
import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"
COMBINE = RESULTS / "_combine_api.json"
STANDALONE = RESULTS / "_standalone.json"


def month_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m")


def build_index(standalone):
    """`(entry_ts, side) -> [strategy, ...]` from the standalone runs."""
    index = defaultdict(list)
    for name, payload in standalone.items():
        for ts, side in payload["keys"]:
            index[(ts, side)].append(name)
    return index


def run(out_path=None):
    combined = json.loads(COMBINE.read_text())
    standalone = json.loads(STANDALONE.read_text())
    index = build_index(standalone)

    names = list(standalone)
    contribution = {n: {"pnl": 0.0, "trades": 0, "months": defaultdict(float)}
                    for n in names}
    unmatched = collisions = 0
    unmatched_pnl = 0.0
    joint_months = defaultdict(float)

    for trade in combined["trades"]:
        owners = index.get((trade["et"], trade["side"]), [])
        month = month_of(trade["xt"])
        joint_months[month] += trade["pnl"]
        if not owners:
            unmatched += 1
            unmatched_pnl += trade["pnl"]
            continue
        if len(owners) > 1:
            collisions += 1
        owner = owners[0]
        contribution[owner]["pnl"] += trade["pnl"]
        contribution[owner]["trades"] += 1
        contribution[owner]["months"][month] += trade["pnl"]

    net = combined["final_bal"] - combined["initial_bal"]
    print(f"\nFive strategies, one ${combined['initial_bal']:,.0f} account, "
          f"spread 0.2")
    print(f"  {combined['first_ts']} .. {combined['last_ts']}")
    print(f"  net {net:+,.2f}   trades {combined['num_trades']}   "
          f"pf {combined['profit_factor']}   "
          f"max drawdown ${combined['max_drawdown_dollars']:,.2f}")
    if unmatched or collisions:
        print(f"  [{unmatched} unmatched ({unmatched_pnl:+.2f}), "
              f"{collisions} ambiguous timestamps]")

    print(f"\n  contribution inside the joint run")
    print(f"    {'strategy':<26}{'trades':>8}{'pnl':>11}{'share':>8}"
          f"     standalone (pnl / dd / pf)")
    for name in names:
        c = contribution[name]
        s = standalone[name]
        share = c["pnl"] / net if net else 0.0
        print(f"    {name:<26}{c['trades']:>8}{c['pnl']:>11.2f}{share:>7.0%}"
              f"     {s['pnl']:>9.2f} / {s['dd']:>7.2f} / {s['pf']}")

    print(f"\n  monthly by strategy")
    head = "".join(f"{n.replace('NQ ','').replace('BTC Maroy ','M-')[:9]:>10}"
                   for n in names)
    print(f"    {'month':<9}{head}{'total':>11}{'equity':>12}")
    equity = combined["initial_bal"]
    for month in sorted(joint_months):
        cells = "".join(f"{contribution[n]['months'].get(month, 0.0):>10.2f}"
                        for n in names)
        total = joint_months[month]
        equity += total
        print(f"    {month:<9}{cells}{total:>11.2f}{equity:>12.2f}")

    payload = {
        "net": round(net, 2),
        "trades": combined["num_trades"],
        "profit_factor": combined["profit_factor"],
        "max_drawdown_dollars": combined["max_drawdown_dollars"],
        "unmatched": unmatched,
        "ambiguous": collisions,
        "contribution": {
            n: {"trades": c["trades"], "pnl": round(c["pnl"], 2),
                "months": {k: round(v, 2) for k, v in sorted(c["months"].items())}}
            for n, c in contribution.items()},
        "standalone": {n: {k: v for k, v in s.items() if k != "keys"}
                       for n, s in standalone.items()},
        "joint_months": {k: round(v, 2) for k, v in sorted(joint_months.items())},
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"\n  wrote {out_path}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="sandbox/results/combine_attribution.json")
    run(out_path=parser.parse_args().out)


if __name__ == "__main__":
    main()
