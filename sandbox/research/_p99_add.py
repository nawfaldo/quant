"""Add step: realised pre-screen of every pool candidate on the trimmed book,
then paired Monte Carlo of the shortlist on 2025-26 (primary) and 2022-24."""
import json
import math
import os

from sandbox.research import _p99_lab as lab

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "..", "results", "p99_add.json")
PATHS = int(os.environ.get("PATHS", "300"))
SHORT = int(os.environ.get("SHORT", "40"))

if __name__ == "__main__":
    greedy = json.load(open(os.path.join(HERE, "..", "results", "p99_greedy.json")))
    book = greedy[-1]["book"]
    state, _lo, _hi = lab.load_state("2526")
    pool = [k for k in state["by_key"] if k not in book and k not in lab.canon_keys()]
    configs = [(book, 0.13, None)] + [(book + [k], 0.13, None) for k in pool]
    rows = lab.evaluate("2526", configs, paths=0, realised=True)
    base = rows[0]
    screen = []
    for k, r in zip(pool, rows[1:]):
        gain = math.log1p(r["real_ret"] / 100) - math.log1p(base["real_ret"] / 100)
        screen.append((gain - 0.02 * max(0.0, r["real_dd"] - base["real_dd"]), k, r["real_ret"], r["real_dd"]))
    screen.sort(reverse=True)
    print(f"base 22: realised 2025-26 {base['real_ret']:,.0f}%  dd {base['real_dd']:.2f}", flush=True)
    for s, k, ret, dd in screen[:SHORT]:
        print(f"  +{k:44s} {ret:>8,.0f}%  dd {dd:5.2f}", flush=True)
    short = [k for _s, k, _r, _d in screen[:SHORT]]
    configs = [(book, 0.13, None)] + [(book + [k], 0.13, None) for k in short]
    res = {}
    for window in ("2526", "2224"):
        rows = lab.evaluate(window, configs, paths=PATHS)
        res[window] = rows
        print(f"\n=== {window} add-one ({PATHS} paths)   base p99 {rows[0]['dd99']:.2f} med {rows[0]['med_ret']:,.0f}%", flush=True)
        for k, r in zip(["(base)"] + short, rows):
            print(f"  {k:46s} med {r['med_ret']:>8,.0f}%  p5 {r['p5_ret']:>7,.0f}%  dd95 {r['dd95']:5.2f}  dd99 {r['dd99']:5.2f}", flush=True)
    json.dump({"book": book, "short": short,
               "rows": {w: [{k: v for k, v in r.items() if k != "by_sleeve"} for r in rs]
                        for w, rs in res.items()}},
              open(OUT, "w"), indent=1)
