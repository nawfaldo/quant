"""Winners table for one TikTok group: symbol, tf, family, IS ret/dd/trades,
OOS ret/dd/trades. Reads the group's own seals.

    py sandbox/research/_tt_table.py thequantbuilder
"""
import glob
import json
import os
import sys

group = sys.argv[1]
here = os.path.join(os.path.dirname(__file__), "..", "results")
rows = []
for path in glob.glob(os.path.join(here, f"exness_tt_families_*_{group}.json")):
    with open(path, encoding="utf-8") as handle:
        d = json.load(handle)
    val = d.get("validation") or {}
    for fam, w in d["families"].items():
        if not w:
            continue
        o = (val.get(fam) or {}).get("oos")
        i = w["in_sample"]
        rows.append((d["symbol"], d["bar_minutes"], fam, i["return_pct"], i["max_dd_pct"],
                     i["trades"], o and o["return_pct"], o and o["max_dd_pct"],
                     o and o["trades"], o and o.get("pf")))
rows.sort(key=lambda r: -(r[6] if r[6] is not None else -1e9))
tf = lambda m: f"{m // 60}h" if m >= 60 else f"{m}m"
print(f"| symbol | tf | family | IS ret | IS dd | IS tr | OOS ret | OOS dd | OOS tr | OOS pf |")
print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
for s, b, f, ir, idd, it, orr, odd, ot, pf in rows:
    oos = ("-", "-", "-", "-") if orr is None else (f"{orr:+.1f}%", f"{odd:.1f}%", ot, f"{pf:.2f}" if pf else "-")
    print(f"| {s} | {tf(b)} | {f} | {ir:+.1f}% | {idd:.1f}% | {it} | {oos[0]} | {oos[1]} | {oos[2]} | {oos[3]} |")
pos = sum(1 for r in rows if r[6] is not None and r[6] > 0)
print(f"\n{len(rows)} IS winners, {sum(1 for r in rows if r[6] is not None)} validated, {pos} positive OOS")
