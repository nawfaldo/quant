"""Walk-forward search for a switch that turns weekend crypto on and off.

Every weekend is one observation. Each factor is known at the Friday close.
For test year Y the switch (factor, side, cut-off) is chosen on weekends
before Y only, then applied to Y. Units: % of account, summed, at 1.5% risk
per trade (R x 1.5).

    EXNESS_ENTRY_DAYS=sat,sun py -3 -m sandbox.research._weekend_switch <symbol>
"""
import platform
platform._wmi = None
import glob, json, math, sys
from collections import defaultdict, Counter
import numpy as np
from sandbox.research import cfd_families as cf

sym = sys.argv[1]
other = "btc" if sym == "ethusd" else "ethusd"
cands = [(d["family"], d["params"]) for d in
         (json.load(open(f)) for f in sorted(glob.glob(f"sandbox/results/exness/{sym}_*_30m.json")))]
cf.resolve(sym)
cf.resolve(other)
cf.install_fills(sym, 30, quiet=True)
bars, ctx = cf.context(sym, "validate", 30, {f for f, _ in cands})
obars = cf.all_bars(other, "validate", 30)
END = 2 ** 40
RISK = cf.RISK_FRACTION * 100


def sat_of(ts):
    d = ts // 86400
    return d - 1 if (d + 3) % 7 == 6 else d


# ---- weekend P&L per strategy, keyed by the Saturday's day number
pnl = {}
for fam, params in cands:
    s = cf.backtest(fam, bars, ctx, cf.rehydrate(params), lo=None, hi=END, include_trades=True)
    w = defaultdict(float)
    for t in s.get("trade_log", []):
        if t["distance"] > 0:
            w[sat_of(t["entry_ts"])] += t["points"] / t["distance"] * RISK
    pnl[fam] = w
names = sorted(pnl)


# ---- daily series from the bars (session hours, 7 days a week)
def daily(bs):
    D = {}
    prev = None
    for b in bs:
        d = b[cf.TS] // 86400
        r = D.setdefault(d, {"c": b[cf.C], "h": b[cf.H], "l": b[cf.L], "v": 0.0, "rv": 0.0})
        r["c"] = b[cf.C]
        r["h"] = max(r["h"], b[cf.H])
        r["l"] = min(r["l"], b[cf.L])
        r["v"] += b[cf.V]
        if prev and prev[0] == d and prev[1] > 0:
            r["rv"] += math.log(b[cf.C] / prev[1]) ** 2
        prev = (d, b[cf.C])
    lo, hi = min(D), max(D)
    days = range(lo, hi + 1)
    get = lambda k: np.array([D[d][k] if d in D else np.nan for d in days])
    c = get("c")
    for i in range(1, len(c)):
        if np.isnan(c[i]):
            c[i] = c[i - 1]
    return lo, c, get("h"), get("l"), np.nan_to_num(get("v")), get("rv")


lo, C, H, L, V, RV = daily(bars)
olo, OC, *_ = daily(obars)
WD = ((np.arange(len(C)) + lo + 3) % 7) < 5


def nanmean(x):
    x = x[~np.isnan(x)]
    return x.mean() if len(x) else np.nan


def other_close(d):
    i = d - olo
    return OC[i] if 0 <= i < len(OC) else np.nan


sats_all = sorted(d for d in range(lo, lo + len(C)) if (d + 3) % 7 == 5)
F = {}
for S in sats_all:
    f = S - 1 - lo  # Friday index
    if f < 370 or f >= len(C):
        continue
    c = C[f]
    lc = math.log
    x = {}
    for k in (1, 4, 13, 26):
        x[f"ret_{k}w"] = lc(c / C[f - 7 * k])
    for k in (4, 13):
        x[f"absret_{k}w"] = abs(x[f"ret_{k}w"])
    for n in (20, 50, 200):
        x[f"ma{n}_dist"] = lc(c / np.nanmean(C[f - n + 1:f + 1]))
    dl = np.abs(np.diff(np.log(C[f - 28:f + 1])))
    x["efficiency_4w"] = abs(lc(c / C[f - 28])) / dl.sum() if dl.sum() else np.nan
    rv, m = RV[:f + 1], WD[:f + 1]
    v1, v4, v13 = (math.sqrt(nanmean(rv[-7 * k:][m[-7 * k:]])) for k in (1, 4, 13))
    x["vol_1w"], x["vol_4w"], x["vol_13w"] = v1, v4, v13
    x["vol_1w/13w"] = v1 / v13 if v13 else np.nan
    hist = [math.sqrt(nanmean(rv[j - 28:j][m[j - 28:j]])) for j in range(len(rv) - 364, len(rv), 7)]
    x["vol_4w_pct52w"] = float(np.mean([h <= v4 for h in hist]))
    we4 = math.sqrt(nanmean(rv[-28:][~m[-28:]]))
    x["we_vol_4w"] = we4
    x["we/wd_vol_4w"] = we4 / v4 if v4 else np.nan
    vv = V[:f + 1]
    base = vv[-182:][m[-182:]].mean()
    x["volume_4w/26w"] = vv[-28:][m[-28:]].mean() / base if base else np.nan
    x["we_volume_share_4w"] = vv[-28:][~m[-28:]].sum() / vv[-28:].sum() if vv[-28:].sum() else np.nan
    x["last_we_ret"] = lc(C[f - 5] / C[f - 7])
    x["last_we_absret"] = abs(x["last_we_ret"])
    x["fri_ret"] = lc(c / C[f - 1])
    wk_h, wk_l = np.nanmax(H[f - 4:f + 1]), np.nanmin(L[f - 4:f + 1])
    x["fri_close_loc"] = (c - wk_l) / (wk_h - wk_l) if wk_h > wk_l else np.nan
    o1, o4 = other_close(S - 1), other_close(S - 29)
    x[f"rel_4w_vs_{other}"] = x["ret_4w"] - (lc(o1 / o4) if o1 > 0 and o4 > 0 else np.nan)
    F[S] = x

sats = sorted(F)
fnames = list(F[sats[0]])
year = lambda S: 1970 + int((S + 0.5) // 365.2425)
X = np.array([[F[S][k] for k in fnames] for S in sats], float)
P = np.array([[pnl[n].get(S, 0.0) for n in names] for S in sats])  # weekends x strategies
Y = np.array([year(S) for S in sats])
pool = P.mean(1)

# the weekend strategies' own recent record, known before the weekend
cs = np.vstack([np.zeros(P.shape[1]), np.cumsum(P, 0)])
pcs = np.concatenate([[0.0], np.cumsum(pool)])
TRAIL = {}
for k in (4, 8, 13, 26):
    own = np.full_like(P, np.nan)
    pt = np.full(len(sats), np.nan)
    for i in range(k, len(sats)):
        own[i] = cs[i] - cs[i - k]
        pt[i] = pcs[i] - pcs[i - k]
    TRAIL[k] = (own, pt)
common = [(k, X[:, i]) for i, k in enumerate(fnames)]
common += [(f"group_trail_{k}w", TRAIL[k][1]) for k in TRAIL]

QS = (0.1, 0.2, 0.33, 0.5, 0.67, 0.8, 0.9)


def rules(train, cols):
    """Every factor/side/cut-off; cut-offs are quantiles of the TRAINING rows."""
    out = [("always on", lambda rows: np.ones(len(rows), bool))]
    for j, col in cols:
        v = col[train]
        v = v[~np.isnan(v)]
        if len(v) < 50:
            continue
        for q in QS:
            t = float(np.quantile(v, q))
            out.append((f"{j} > {t:.4g}", lambda rows, c=col, t=t: np.nan_to_num(c[rows], nan=-np.inf) > t))
            out.append((f"{j} <= {t:.4g}", lambda rows, c=col, t=t: np.nan_to_num(c[rows], nan=np.inf) <= t))
    return out


def tstat(x):
    s = x.std(ddof=1)
    return x.mean() / s * math.sqrt(len(x)) if s > 0 else 0.0


def best_rule(tr, series, cols):
    return max(((tstat(np.where(m(tr), series[tr], 0.0)), lab, m) for lab, m in rules(tr, cols)
                if m(tr).mean() >= 0.2), key=lambda z: z[0])


test_years = [y for y in sorted(set(Y)) if (Y < y).sum() >= 104]
print(f"\n{sym}: {len(names)} strategies, {len(sats)} weekends {Y.min()}-{Y.max()}, "
      f"{len(common) + 2} factors x {len(QS) * 2} cut-offs; % of account at {RISK:.1f}% risk", flush=True)

# A. one switch for the whole group (average strategy)
print("\nA. ONE SWITCH FOR THE WHOLE GROUP (average strategy, % per year)")
print(f"{'year':<6}{'always on':>10}{'switched':>10}   switch chosen from earlier years only")
ta = ts_ = 0.0
for y in test_years:
    tr, te = np.where(Y < y)[0], np.where(Y == y)[0]
    t, lab, m = best_rule(tr, pool, common)
    on = m(te) if t > 0 else np.zeros(len(te), bool)
    a, s = pool[te].sum(), pool[te][on].sum()
    ta += a
    ts_ += s
    print(f"{y:<6}{a:>+10.1f}{s:>+10.1f}   {lab if t > 0 else 'OFF'}  (train t={t:.2f}, on {on.mean():.0%})",
          flush=True)
print(f"{'total':<6}{ta:>+10.1f}{ts_:>+10.1f}")

# B. each strategy picks its own switch (incl. its own record) -- or stays off
print("\nB. EACH STRATEGY PICKS ITS OWN SWITCH, OR STAYS OFF (train t >= 2), sum of all strategies, % per year")
print(f"{'year':<6}{'all on':>10}{'switched':>10}{'strategies on':>15}")
picks = Counter()
tb_a = tb_s = 0.0
last = {}
for y in test_years:
    tr, te = np.where(Y < y)[0], np.where(Y == y)[0]
    ya = ys = 0.0
    n_on = 0
    for k, n in enumerate(names):
        cols = common + [(f"own_trail_{w}w", TRAIL[w][0][:, k]) for w in TRAIL]
        t, lab, m = best_rule(tr, P[:, k], cols)
        ya += P[te, k].sum()
        if t >= 2.0:
            ys += P[te, k][m(te)].sum()
            n_on += 1
            picks[lab.split(" ")[0]] += 1
            if y == test_years[-1]:
                last[n] = (lab, t)
    tb_a += ya
    tb_s += ys
    print(f"{y:<6}{ya:>+10.1f}{ys:>+10.1f}{n_on:>10}/{len(names)}", flush=True)
print(f"{'total':<6}{tb_a:>+10.1f}{tb_s:>+10.1f}")
print("\nfactors picked most (strategy-years):", ", ".join(f"{k} {v}" for k, v in picks.most_common(10)))
print(f"switches in force for {test_years[-1]}:")
for n, (lab, t) in sorted(last.items()):
    print(f"   {n:<22} {lab}  (train t={t:.2f})")
