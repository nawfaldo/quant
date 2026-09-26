"""What turns weekend crypto on? Factor chosen on 2018-2022, tested 2023-2026-08.

    EXNESS_ENTRY_DAYS=sat,sun py -3 -m sandbox.research._weekend_factor <symbol>
"""
import platform
platform._wmi = None
import glob, json, math, sys
from collections import defaultdict
from datetime import datetime, timezone
from sandbox.research import cfd_families as cf

sym = sys.argv[1]
cands = [(d["family"], d["params"]) for d in
         (json.load(open(f)) for f in sorted(glob.glob(f"sandbox/results/exness/{sym}_*_30m.json")))]
cf.resolve(sym)
cf.install_fills(sym, 30, quiet=True)
bars, ctx = cf.context(sym, "validate", 30, {f for f, _ in cands})

def month(ts):
    d = datetime.fromtimestamp(ts, timezone.utc); return d.year * 12 + d.month - 1

# per-month trade bp, per strategy
per = {}
for fam, params in cands:
    s = cf.backtest(fam, bars, ctx, cf.rehydrate(params), lo=None, hi=cf.OOS_END, include_trades=True)
    m = defaultdict(float)
    for t in s.get("trade_log", []):
        m[month(t["entry_ts"])] += t["points"] / t["entry"] * 1e4
    per[fam] = dict(m)

# factors from the month's own bars (applied to the NEXT month)
wk_r, wd_r, close, vol_wk, vol_all = defaultdict(list), defaultdict(list), {}, defaultdict(float), defaultdict(float)
prev = None
for row in bars:
    ts = row[cf.TS]; mo = month(ts)
    if prev is not None and prev > 0:
        r = math.log(row[cf.C] / prev)
        dow = datetime.fromtimestamp(ts, timezone.utc).weekday()
        (wk_r if dow >= 5 else wd_r)[mo].append(r)
        if dow >= 5: vol_wk[mo] += row[cf.V]
        vol_all[mo] += row[cf.V]
    prev = row[cf.C]; close[mo] = row[cf.C]
sd = lambda xs: (sum(x*x for x in xs)/len(xs))**0.5 if len(xs) > 5 else float("nan")
months = sorted(close)
F = {}
for i, mo in enumerate(months):
    f = {"wkend_vol": sd(wk_r[mo]), "wkday_vol": sd(wd_r[mo]),
         "wk/wd_vol": sd(wk_r[mo]) / sd(wd_r[mo]) if sd(wd_r[mo]) else float("nan"),
         "wkend_vol_share": vol_wk[mo] / vol_all[mo] if vol_all[mo] else float("nan"),
         "ret_1m": math.log(close[mo] / close[months[i-1]]) if i else float("nan"),
         "ret_3m": math.log(close[mo] / close[months[i-3]]) if i >= 3 else float("nan"),
         "abs_ret_3m": abs(math.log(close[mo] / close[months[i-3]])) if i >= 3 else float("nan")}
    F[mo + 1] = f  # known at the start of the next month

split = 2023 * 12
json.dump({"per": {k: {str(a): b for a, b in v.items()} for k, v in per.items()},
           "F": {str(k): v for k, v in F.items()}}, open(f"sandbox/results/_weekend_factor_{sym}.json", "w"))
pool = defaultdict(float)
for m in per.values():
    for mo, v in m.items(): pool[mo] += v / len(per)
live = sorted(mo for mo in pool if mo in F and mo >= 2018*12)
print(f"\n{sym}: pool = average of {len(per)} strategies, weekend bp per month")
print(f"{'factor':<16}{'on-side':>8}{'fit on':>9}{'fit off':>9}{'test on':>9}{'test off':>9}{'test all':>9}")
for name in next(iter(F.values())):
    fit = [(F[mo][name], pool[mo]) for mo in live if mo < split and not math.isnan(F[mo][name])]
    test = [(F[mo][name], pool[mo]) for mo in live if mo >= split and not math.isnan(F[mo][name])]
    med = sorted(x for x, _ in fit)[len(fit)//2]
    hi = [p for x, p in fit if x > med]; lo = [p for x, p in fit if x <= med]
    side = "high" if sum(hi)/len(hi) > sum(lo)/len(lo) else "low"
    on = lambda x: (x > med) == (side == "high")
    fo = sum(p for x, p in fit if on(x)); ff = sum(p for x, p in fit if not on(x))
    to = sum(p for x, p in test if on(x)); tf = sum(p for x, p in test if not on(x))
    print(f"{name:<16}{side:>8}{fo:>9.0f}{ff:>9.0f}{to:>9.0f}{tf:>9.0f}{to+tf:>9.0f}")
yr = defaultdict(float)
for mo in live: yr[mo//12] += pool[mo]
print("pool by year:", {y: round(v) for y, v in sorted(yr.items())})
