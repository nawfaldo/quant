"""Side-by-side of results/broker_compare_{exness,fundednext}.jsonl."""
import json
import os
import statistics
from collections import defaultdict

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")


def load(broker):
    rows = {}
    with open(os.path.join(RESULTS, f"broker_compare_{broker}.jsonl"), encoding="utf-8") as h:
        for line in h:
            if line.strip():
                r = json.loads(line)
                rows[r["file"]] = r
    return rows


ex, fn = load("exness"), load("fundednext")
both = sorted(set(ex) & set(fn))
errors = [(f, ex[f].get("error"), fn[f].get("error")) for f in both
          if "error" in ex[f] or "error" in fn[f]]
good = [f for f in both if "oos" in ex[f] and "oos" in fn[f]]

lines = ["| symbol | tf | family | EX OOS % | EX dd | EX PF | FN OOS % | FN dd | FN PF | n | FN 2024 % |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
per_symbol = defaultdict(list)
for f in sorted(good, key=lambda f: -fn[f]["oos"]["return_pct"]):
    e, n = ex[f]["oos"], fn[f]["oos"]
    y = fn[f]["y2024"]
    per_symbol[ex[f]["symbol"]].append((e["return_pct"], n["return_pct"]))
    lines.append(f"| {ex[f]['symbol']} | {ex[f]['bar']}m | {ex[f]['family']} | "
                 f"{e['return_pct']:+.1f} | {e['max_dd_pct']:.1f} | {e['pf']:.2f} | "
                 f"{n['return_pct']:+.1f} | {n['max_dd_pct']:.1f} | {n['pf']:.2f} | "
                 f"{n['trades']} | {y['return_pct']:+.1f} |")

ex_ret = [ex[f]["oos"]["return_pct"] for f in good]
fn_ret = [fn[f]["oos"]["return_pct"] for f in good]
fn_24 = [fn[f]["y2024"]["return_pct"] for f in good]
summary = [
    f"strategies compared: {len(good)} (errors: {len(errors)})",
    f"holdout profitable:  Exness {sum(r > 0 for r in ex_ret)}/{len(good)}   "
    f"FundedNext {sum(r > 0 for r in fn_ret)}/{len(good)}",
    f"holdout PF >= 1.05:  Exness {sum(ex[f]['oos']['pf'] >= 1.05 for f in good)}   "
    f"FundedNext {sum(fn[f]['oos']['pf'] >= 1.05 for f in good)}",
    f"median holdout return: Exness {statistics.median(ex_ret):+.1f}%   "
    f"FundedNext {statistics.median(fn_ret):+.1f}%",
    f"median FundedNext minus Exness: {statistics.median([b - a for a, b in zip(ex_ret, fn_ret)]):+.1f} pts",
    f"FundedNext 2024 profitable: {sum(r > 0 for r in fn_24)}/{len(good)}",
]
sym_lines = ["| symbol | strategies | EX median OOS % | FN median OOS % | FN profitable |",
             "|---|---|---|---|---|"]
for s, v in sorted(per_symbol.items(), key=lambda kv: -statistics.median(b for _, b in kv[1])):
    sym_lines.append(f"| {s} | {len(v)} | {statistics.median(a for a, _ in v):+.1f} | "
                     f"{statistics.median(b for _, b in v):+.1f} | {sum(b > 0 for _, b in v)}/{len(v)} |")

out = os.path.join(RESULTS, "broker_compare.md")
with open(out, "w", encoding="utf-8") as h:
    h.write("# Exness strategies re-run at FundedNext\n\nParams fixed as sealed; holdout "
            "2025-01-01..2026-08-16; both brokers on today's engine with live fills.\n\n")
    h.write("\n".join("- " + s for s in summary) + "\n\n## By symbol\n\n" + "\n".join(sym_lines)
            + "\n\n## Every strategy\n\n" + "\n".join(lines) + "\n")
    if errors:
        h.write("\n## Errors\n\n" + "\n".join(f"- {f}: EX {a} / FN {b}" for f, a, b in errors) + "\n")
print("\n".join(summary))
print("\n".join(sym_lines))
print("\nerrors:", errors[:10])
print("wrote", out)
