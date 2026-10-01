"""Drop several together: nested subsets from the 2022-24 drop-one ranking."""
import json
import math
import os
import sys

from sandbox.research import _p99_lab as lab

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "..", "results", "p99_exp2.json")

if __name__ == "__main__":
    exp1 = json.load(open(os.path.join(HERE, "..", "results", "p99_exp1.json")))["2224"]
    base = exp1[0]
    drops = []
    for r in exp1[5:]:
        key = r["label"][1:]
        relief = base["dd99"] - r["dd99"]                  # p99 points saved
        cost = math.log(base["med_ret"] / 100 + 1) - math.log(r["med_ret"] / 100 + 1)
        drops.append((relief - 20.0 * cost, key, relief, cost))
    drops.sort(reverse=True)
    order = [k for _s, k, _r, _c in drops]
    print("drop order:", order)
    canon = lab.canon_keys()
    ks = [int(x) for x in os.environ.get("KS", "4,6,8,10,12,14,16").split(",")]
    configs = [([k for k in canon if k not in order[:n]], 0.13, None) for n in ks]
    paths = int(os.environ.get("PATHS", "500"))
    result = {"order": order}
    for window in sys.argv[1:] or ["2224", "2526", "2226"]:
        rows = lab.evaluate(window, configs, paths=paths)
        print(f"\n=== {window}  ({paths} paths)")
        print(f"{'drop':>5}{'n':>4}{'real%':>10}{'realDD':>8}{'med%':>10}{'p5%':>9}{'dd95':>7}{'dd99':>7}{'ruin':>5}")
        for n, r in zip(ks, rows):
            print(f"{n:>5}{r['n']:>4}{r['real_ret']:>10,.0f}{r['real_dd']:>8.2f}{r['med_ret']:>10,.0f}"
                  f"{r['p5_ret']:>9,.0f}{r['dd95']:>7.2f}{r['dd99']:>7.2f}{r['ruin']:>5}", flush=True)
        result[window] = [{k: v for k, v in r.items() if k != "by_sleeve"} for r in rows]
        with open(OUT, "w") as handle:
            json.dump(result, handle, indent=1)
