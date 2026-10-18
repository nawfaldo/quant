"""Summarise the ncf FundedNext run (IS 2024-2025 / OOS 2026-01-01..2026-10-14).

One row per (family, symbol, timeframe, window) whose `select` sealed an IS
winner. OOS winner = return > 0, PF >= 1.05, >= 20 trades. `fdr` = passes
Benjamini-Hochberg at 10% over every sealed slot (a slot with no coin flip
counts as p = 1). Writes sandbox/results/new_cfd_families/fundednext/SUMMARY.md
and summary.json.
"""
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                    "new_cfd_families", "fundednext")
rows = []


def verdict(real, seeds):
    """The user's `why` rule: PASS if real OOS beats every seed's OOS, PASS* if
    no seed found an IS cell, fail otherwise; None if `why` has not run."""
    if seeds is None:
        return None
    found = [x for x in seeds if x]
    if not found:
        return "PASS*"
    return "PASS" if real > max(x["out_of_sample"]["return_pct"] for x in found) else "fail"


for kind in ("cfd", "tt"):
    for job in sorted(glob.glob(os.path.join(ROOT, kind, "*"))):
        symbol, tf, window = os.path.basename(job).split("-", 2)
        seals = [p for p in glob.glob(os.path.join(job, "*.json"))
                 if not p.endswith("_coinflip.json") and "_null_" not in p]
        null = {}
        for q in glob.glob(os.path.join(job, "*_null_*.json")):
            null.update(json.load(open(q, encoding="utf-8")).get("null_control") or {})
        for path in seals:
            data = json.load(open(path, encoding="utf-8"))
            flips = {}
            if os.path.exists(path[:-5] + "_coinflip.json"):
                flips = json.load(open(path[:-5] + "_coinflip.json",
                                       encoding="utf-8")).get("coinflip") or {}
            validation = data.get("validation") or {}
            for family, cell in (data.get("families") or {}).items():
                if not cell:
                    continue
                oos = (validation.get(family) or {}).get("oos") or {}
                ins = cell.get("in_sample") or {}
                flip = flips.get(family) or {}
                rows.append({
                    "family": family, "symbol": symbol, "tf": tf, "window": window,
                    "is_return": ins.get("return_pct"), "is_trades": ins.get("trades"),
                    "is_dd": ins.get("max_dd_pct"),
                    "oos_return": oos.get("return_pct"), "oos_pf": oos.get("pf"),
                    "oos_trades": oos.get("trades"), "oos_dd": oos.get("max_dd_pct"),
                    "p": flip.get("p_value"), "params": cell.get("params"),
                    "why": verdict(oos.get("return_pct"), null.get(family)),
                    "flips": [None if x is None else x["out_of_sample"]["return_pct"]
                              for x in (null.get(family) or [])],
                })
for r in rows:
    r["oos_winner"] = bool(r["oos_return"] and r["oos_return"] > 0
                           and (r["oos_pf"] or 0) >= 1.05
                           and (r["oos_trades"] or 0) >= 20)
# Benjamini-Hochberg at 10% over every sealed slot.
m = len(rows)
ranked = sorted(range(m), key=lambda i: rows[i]["p"] if rows[i]["p"] is not None else 1.0)
cut = 0
for k, i in enumerate(ranked, 1):
    p = rows[i]["p"] if rows[i]["p"] is not None else 1.0
    if p <= 0.10 * k / max(m, 1):
        cut = k
for k, i in enumerate(ranked, 1):
    rows[i]["fdr"] = k <= cut and rows[i]["oos_winner"]
for r in rows:
    r["p05"] = r["oos_winner"] and r["p"] is not None and r["p"] < 0.05

json.dump(rows, open(os.path.join(ROOT, "summary.json"), "w"), indent=1, default=str)
win = [r for r in rows if r["oos_winner"] and r["why"] in ("PASS", "PASS*")]
win.sort(key=lambda r: -(r["oos_return"] or 0))
done = sum(r["why"] is not None for r in rows if r["oos_winner"])
NL = chr(10)
out = ["# ncf FundedNext summary ($6,000)" + NL,
       f"IS 2024-2025, OOS 2026-01-01..2026-10-14. Sealed slots: {m}. "
       f"OOS winners: {sum(r['oos_winner'] for r in rows)} (why run on {done}). "
       f"why PASS: {sum(r['why'] == 'PASS' for r in win)}, PASS*: {sum(r['why'] == 'PASS*' for r in win)}." + NL,
       "| family | symbol | tf | window | IS ret % | IS dd % | OOS ret % | OOS dd % | PF | trades | flips OOS % | why |",
       "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
for r in win:
    flips = " / ".join("none" if f is None else f"{f:+.1f}" for f in r["flips"])
    out.append(f"| {r['family']} | {r['symbol']} | {r['tf']} | {r['window']} | "
               f"{r['is_return']:+.1f} | {r['is_dd']:.1f} | {r['oos_return']:+.1f} | "
               f"{r['oos_dd']:.1f} | {r['oos_pf']:.2f} | {r['oos_trades']} | {flips} | {r['why']} |")
open(os.path.join(ROOT, "SUMMARY.md"), "w", encoding="utf-8").write(NL.join(out) + NL)
print(NL.join(out[:2]))
