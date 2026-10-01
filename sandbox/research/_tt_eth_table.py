"""ethusd wide-stop / IS-from-2021 table: symbol, tf, IS/OOS ret dd trades, widen, null."""
import glob, json, os
P = "exness_tt_families_widestop_isethusd2021"
here = os.path.join(os.path.dirname(__file__), "..", "results")
nulls = {}
for f in glob.glob(os.path.join(here, f"{P}_null_ethusd_*.json")):
    d = json.load(open(f, encoding="utf-8"))
    for fam, rows in d["null_control"].items():
        seeds = [r["out_of_sample"]["return_pct"] for r in rows if r]
        nulls[(d["bar_minutes"], fam)] = max(seeds) if seeds else None
rows = []
for f in glob.glob(os.path.join(here, f"{P}_ethusd_*.json")):
    d = json.load(open(f, encoding="utf-8"))
    grp = os.path.basename(f).rsplit("_", 1)[1][:-5]
    for fam, w in d["families"].items():
        if not w:
            continue
        o = ((d.get("validation") or {}).get(fam) or {}).get("oos")
        i = w["in_sample"]
        rows.append((grp, d["bar_minutes"], fam, w["params"].get("stop_widen"), i, o))
rows.sort(key=lambda r: -(r[5]["return_pct"] if r[5] else -1e9))
tf = lambda m: f"{m // 60}h" if m >= 60 else f"{m}m"
print("| group | tf | family | stop x | IS ret | IS dd | IS tr | OOS ret | OOS dd | OOS tr | OOS pf | null best | verdict |")
print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
for g, b, fam, k, i, o in rows:
    if o is None:
        print(f"| {g} | {tf(b)} | {fam} | {k} | {i['return_pct']:+.1f}% | {i['max_dd_pct']:.1f}% | {i['trades']} | - | - | - | - | - | not validated |")
        continue
    key = (b, fam)
    n = nulls.get(key, "none") if key in nulls else "none"
    ok = o["return_pct"] > 0 and (o.get("pf") or 0) >= 1.05 and o["trades"] >= 30
    if not ok:
        v = "fails holdout"
    elif key not in nulls:
        v = "no null run"
    elif n is None:
        v = "PASS*"
    else:
        v = "PASS" if o["return_pct"] > n else "lost to null"
    ns = "-" if not isinstance(n, float) else f"{n:+.1f}%"
    print(f"| {g} | {tf(b)} | {fam} | {k} | {i['return_pct']:+.1f}% | {i['max_dd_pct']:.1f}% | {i['trades']} | "
          f"{o['return_pct']:+.1f}% | {o['max_dd_pct']:.1f}% | {o['trades']} | {o.get('pf') or 0:.2f} | {ns} | {v} |")
print(f"\n{len(rows)} IS winners, {sum(1 for r in rows if r[5] and r[5]['return_pct'] > 0)} positive OOS")
