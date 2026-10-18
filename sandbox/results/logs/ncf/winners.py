"""OOS winners of one job folder, comma-separated: return > 0, PF >= 1.05, >= 20 trades."""
import glob, json, os, sys
out = []
for p in glob.glob(os.path.join(sys.argv[1], "*.json")):
    if p.endswith("_coinflip.json") or "_null_" in p:
        continue
    d = json.load(open(p, encoding="utf-8"))
    v = d.get("validation") or {}
    for f, cell in (d.get("families") or {}).items():
        o = (v.get(f) or {}).get("oos") or {}
        if cell and o.get("return_pct", 0) > 0 and (o.get("pf") or 0) >= 1.05 and o.get("trades", 0) >= 20:
            out.append(f)
print(",".join(sorted(set(out))))
