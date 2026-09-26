"""Rescore the saved btc/ethusd candidates (results/exness) with weekend-only entries.

    EXNESS_ENTRY_DAYS=sat,sun [EXNESS_FULL_DAY=1] py -3 -m sandbox.research._weekend_rescore <symbol> <tag>
"""
import platform
platform._wmi = None
import glob, json, os, sys
from sandbox.research import cfd_families as cf

sym, tag = sys.argv[1], sys.argv[2]
cands = []
for f in sorted(glob.glob(f"sandbox/results/exness/{sym}_*_30m.json")):
    d = json.load(open(f))
    cands.append((d["family"], d["params"]))
cf.resolve(sym)
cf.install_fills(sym, 30, quiet=True)
bars, ctx = cf.context(sym, "validate", 30, {f for f, _ in cands})
out = {}
for fam, params in cands:
    p = cf.rehydrate(params)
    r = {}
    for name, lo, hi in (("is", None, cf.IS_END), ("oos", cf.IS_END, cf.OOS_END), ("all", None, cf.OOS_END)):
        try:
            s = cf.backtest(fam, bars, ctx, p, lo=lo, hi=hi)
        except KeyError:
            s = None
            break
        r[name] = {k: s.get(k) for k in ("return_pct", "max_dd_pct", "trades", "pf")}
    if s is None:
        print(f"{sym} {fam:<22} skipped: setting not on this clock", flush=True)
        continue
    out[fam] = r
    print(f"{sym} {fam:<22} IS {r['is']['return_pct']:+8.1f}% dd {r['is']['max_dd_pct']:5.1f} n={r['is']['trades']:4} | "
          f"OOS {r['oos']['return_pct']:+7.1f}% dd {r['oos']['max_dd_pct']:5.1f} n={r['oos']['trades']:4}", flush=True)
json.dump(out, open(f"sandbox/results/_weekend_rescore_{sym}_{tag}.json", "w"), indent=1)
