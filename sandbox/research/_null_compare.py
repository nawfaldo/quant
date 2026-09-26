"""Winner vs its own coin-flip null, per family: PASS only if real OOS beats the best seed."""
import glob
import json
import os

from sandbox.research.run_null_winners import RUNS

LABEL = {5: "5m_fastbar", 60: "60m_swingbar", 240: "240m_swingbar", 1440: "1d_swingbar"}
BAR = {5: "5m", 60: "60m", 240: "240m", 1440: "1d"}
rows, pending = [], []
for symbol, bar, families in RUNS:
    prefix = "fb_" if bar == 5 else "sb_"
    names = [prefix + f for f in families.split()]
    real = json.load(open(f"sandbox/results/exness_families_{symbol}_{LABEL[bar]}.json"))
    nulls = glob.glob(f"sandbox/results/exness_families_null_{symbol}_{BAR[bar]}_*.json")
    null = None
    for path in nulls:
        rows_ = json.load(open(path))["null_control"]
        if set(names) <= set(rows_):
            null = rows_
    for name in names:
        oos = real["validation"][name]["oos"]
        if null is None:
            pending.append(f"{symbol} {BAR[bar]} {name}")
            continue
        seeds = [r for r in null[name] if r]
        best = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
        verdict = ("PASS*" if not seeds else "PASS" if oos["return_pct"] > best else "fail")
        flips = " / ".join("none" if r is None else f"{r['out_of_sample']['return_pct']:+.1f}%"
                           for r in null[name])
        rows.append((verdict != "fail", oos["return_pct"],
                     f"| {symbol} | {BAR[bar]} | {name} | **{oos['return_pct']:+.1f}%** | {oos['max_dd_pct']:.1f}% "
                     f"| {oos['trades']} | {flips} | {'-' if best is None else f'{best:+.1f}%'} | **{verdict}** |"))
rows.sort(key=lambda r: (not r[0], -r[1]))
print("| symbol | bar | family | real OOS | OOS dd | OOS n | coin-flip seeds OOS | best flip | verdict |")
print("|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(r[2])
print(f"\npassed {sum(r[0] for r in rows)} of {len(rows)} checked; {len(pending)} still running")
