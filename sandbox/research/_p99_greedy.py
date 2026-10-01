"""Greedy drop on the PRIMARY window (2025-26), re-measured after every drop.

Each step tries dropping every remaining sleeve (paired paths) and removes the one
with the best  p99_relief - LAMBDA * log_return_cost ; stops once the primary p99
is under TARGET. The book after each step is also scored on 2022-24 (secondary).
"""
import json
import math
import os

from sandbox.research import _p99_lab as lab

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "..", "results", "p99_greedy.json")
PATHS = int(os.environ.get("PATHS", "300"))
TARGET = float(os.environ.get("TARGET", "25"))
LAMBDA = float(os.environ.get("LAMBDA", "20"))


def lr(r):
    return math.log1p(max(r["med_ret"] / 100.0, -0.9999))


if __name__ == "__main__":
    book = lab.canon_keys()
    base = lab.evaluate("2526", [(book, 0.13, None)], paths=PATHS)[0]
    sec = lab.evaluate("2224", [(book, 0.13, None)], paths=PATHS)[0]
    path = [{"step": 0, "drop": None, "n": len(book), "p99": base["dd99"], "med": base["med_ret"],
             "p99_2224": sec["dd99"], "med_2224": sec["med_ret"]}]
    print(f"step 0  n {len(book)}  2526 p99 {base['dd99']:.2f} med {base['med_ret']:,.0f}%   "
          f"2224 p99 {sec['dd99']:.2f} med {sec['med_ret']:,.0f}%", flush=True)
    step = 0
    while base["dd99"] >= TARGET and len(book) > 4:
        step += 1
        configs = [([k for k in book if k != d], 0.13, None) for d in book]
        rows = lab.evaluate("2526", configs, paths=PATHS, realised=False)
        scored = []
        for d, r in zip(book, rows):
            scored.append(((base["dd99"] - r["dd99"]) - LAMBDA * (lr(base) - lr(r)), d, r))
        scored.sort(key=lambda x: -x[0])
        _s, drop, best = scored[0]
        book = [k for k in book if k != drop]
        sec = lab.evaluate("2224", [(book, 0.13, None)], paths=PATHS, realised=False)[0]
        base = best
        path.append({"step": step, "drop": drop, "n": len(book), "p99": best["dd99"],
                     "med": best["med_ret"], "p99_2224": sec["dd99"], "med_2224": sec["med_ret"],
                     "book": book})
        print(f"step {step}  -{drop:36s} n {len(book)}  2526 p99 {best['dd99']:.2f} "
              f"med {best['med_ret']:,.0f}%   2224 p99 {sec['dd99']:.2f} med {sec['med_ret']:,.0f}%",
              flush=True)
        with open(OUT, "w") as handle:
            json.dump(path, handle, indent=1)
