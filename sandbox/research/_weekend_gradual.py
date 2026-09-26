"""Slow switches for weekend crypto: on once the record over the last N weekends
turns positive, or sized up gradually as that record strengthens.

Nothing is fitted -- every rule is fixed in advance -- so there is no training
window to overfit. Units: % of account per year for the AVERAGE strategy,
summed, at 1.5% risk per trade.

    EXNESS_ENTRY_DAYS=sat,sun py -3 -m sandbox.research._weekend_gradual <symbol>
"""
import platform
platform._wmi = None
import glob, json, math, os, sys
from collections import defaultdict
import numpy as np
from sandbox.research import cfd_families as cf

sym = sys.argv[1]
cache = f"sandbox/results/_weekend_pnl_{sym}.npz"
if not os.path.exists(cache):
    cands = [(d["family"], d["params"]) for d in
             (json.load(open(f)) for f in sorted(glob.glob(f"sandbox/results/exness/{sym}_*_30m.json")))]
    cf.resolve(sym)
    cf.install_fills(sym, 30, quiet=True)
    bars, ctx = cf.context(sym, "validate", 30, {f for f, _ in cands})
    risk = cf.RISK_FRACTION * 100
    pnl = {}
    for fam, params in cands:
        s = cf.backtest(fam, bars, ctx, cf.rehydrate(params), lo=None, hi=2 ** 40, include_trades=True)
        w = defaultdict(float)
        for t in s.get("trade_log", []):
            if t["distance"] > 0:
                d = t["entry_ts"] // 86400
                w[d - 1 if (d + 3) % 7 == 6 else d] += t["points"] / t["distance"] * risk
        pnl[fam] = w
    first = min(bars[0][cf.TS] // 86400 + i for i in range(7) if (bars[0][cf.TS] // 86400 + i + 3) % 7 == 5)
    sats = np.arange(first, bars[-1][cf.TS] // 86400 + 1, 7)
    names = sorted(pnl)
    P = np.array([[pnl[n].get(int(S), 0.0) for n in names] for S in sats])
    np.savez(cache, P=P, sats=sats, names=np.array(names))
z = np.load(cache)
P, sats, names = z["P"], z["sats"], list(z["names"])
# drop the weekends before the first trade of any strategy (no data yet)
start = int(np.argmax(np.abs(P).sum(1) > 0))
P, sats = P[start:], sats[start:]
Y = np.array([1970 + int((S + 0.5) // 365.2425) for S in sats])
n_w, n_s = P.shape
G = P.mean(1)


def trailing(x, k):
    """sum and t-stat over the k weekends BEFORE each weekend (NaN until warm)."""
    s = np.full(len(x), np.nan)
    t = np.full(len(x), np.nan)
    for i in range(k, len(x)):
        w = x[i - k:i]
        s[i] = w.sum()
        sd = w.std(ddof=1)
        t[i] = w.mean() / sd * math.sqrt(k) if sd > 0 else 0.0
    return s, t


NS = (26, 52, 78, 104)
warm = max(NS)
rows = {"always on": G}
warm_of = {"always on": min(NS)}
for k in NS:
    s, t = trailing(G, k)
    rows[f"group: on if last {k}w > 0"] = G * (s > 0)
    rows[f"group: ramp by last {k}w"] = G * np.clip(np.nan_to_num(t) / 2, 0, 1)
    warm_of[f"group: on if last {k}w > 0"] = warm_of[f"group: ramp by last {k}w"] = k
for k in NS:
    on = np.zeros_like(P)
    ramp = np.zeros_like(P)
    for j in range(n_s):
        s, t = trailing(P[:, j], k)
        on[:, j] = P[:, j] * (s > 0)
        ramp[:, j] = P[:, j] * np.clip(np.nan_to_num(t) / 2, 0, 1)
    rows[f"each: on if own last {k}w > 0"] = on.mean(1)
    rows[f"each: ramp by own last {k}w"] = ramp.mean(1)
    warm_of[f"each: on if own last {k}w > 0"] = warm_of[f"each: ramp by own last {k}w"] = k

years = sorted(set(Y[min(NS):]))
print(f"\n{sym}: {n_s} strategies, {n_w} weekends; test from {Y[warm]} (after {warm} warm-up weekends)")
print("% of account per year, average strategy, 1.5% risk per trade")
print(f"{'rule':<32}" + "".join(f"{y:>7}" for y in years) + f"{'total':>8}{'vs on':>9}")
for lab, x in rows.items():
    keep = np.arange(n_w) >= warm_of[lab]
    per = [x[keep & (Y == y)].sum() if (keep & (Y == y)).any() else float("nan") for y in years]
    base = sum(G[keep & (Y == y)].sum() for y in years)
    print(f"{lab:<32}" + "".join("      ." if v != v else f"{v:>+7.1f}" for v in per)
          + f"{np.nansum(per):>+8.1f}{np.nansum(per) - base:>+9.1f}")

# when does the slow group switch flip, and where is it today
print("\nlast flips of 'group: on if last 52w > 0':")
s52, _ = trailing(G, 52)
state = s52 > 0
flips = [i for i in range(warm, n_w) if state[i] != state[i - 1]]
for i in flips[-6:]:
    d = np.datetime64(int(sats[i]), "D")
    print(f"   {d}  -> {'ON' if state[i] else 'off'}")
print(f"   today ({np.datetime64(int(sats[-1]), 'D')}): {'ON' if state[-1] else 'off'}, "
      f"last 52w = {s52[-1]:+.1f}%")
