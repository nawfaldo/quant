"""Print a detailed per-family IS/OOS table for sealed runs: py -m ... sym:label ..."""
import json, sys
for arg in sys.argv[1:]:
    sym, lab = arg.split(":")
    d = json.load(open(f"sandbox/results/exness_families_{sym}_{lab}.json"))
    val = d.get("validation") or {}
    won = {k: v for k, v in d["families"].items() if v}
    rows = []
    for k, v in won.items():
        s = v["in_sample"]; o = (val.get(k) or {}).get("oos")
        if not o: continue
        p = v["params"]
        keep = " ".join(f"{a}={b}" for a, b in p.items() if a not in ("trend", "vol_mode", "last_entry_minute", "exit_mode", "stop_day", "direction"))
        rows.append((o["return_pct"], f"| {k} | {s['return_pct']:+.1f}% | {s['max_dd_pct']:.1f}% | {s['trades']} | {o['return_pct']:+.1f}% | {o['max_dd_pct']:.1f}% | {o['trades']} | {o['pf']:.2f} | {p['exit_mode']} stop={p['stop_day']} {p['direction']} {p['trend']} {p['vol_mode']} {keep} |"))
    rows.sort(key=lambda r: -r[0])
    pos = sum(r[0] > 0 for r in rows)
    print(f"\n### {sym.upper()} {lab}: {len(won)}/{len(d['families'])} passed IS, {pos}/{len(rows)} positive OOS\n")
    print("| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |\n|---|---|---|---|---|---|---|---|---|")
    for r in rows: print(r[1])
