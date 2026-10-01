"""Full return / drawdown percentiles for one book on 1,000 paired paths.

    BOOK=a,b,c py -m sandbox.research._p99_pct 2224 2526 2226
"""
import multiprocessing
import os
import sys

from sandbox.research import _p99_lab as lab

if __name__ == "__main__":
    book = tuple(k for k in os.environ["BOOK"].split(",") if k)
    paths = int(os.environ.get("PATHS", "1000"))
    for window in sys.argv[1:]:
        ret, dd = [], []
        with multiprocessing.Pool(lab.WORKERS, lab._init, (window,)) as pool:
            tasks = [(0, book, 0.13, None, s) for s in range(1, paths + 1)]
            for _i, _s, r, d, _b in pool.imap_unordered(lab._task, tasks, chunksize=4):
                ret.append(r)
                dd.append(d)
        pcts = (5, 25, 50, 75, 95, 99)
        print(f"{window}  return  " + "  ".join(f"p{p} {lab.q(ret, p):+,.0f}%" for p in pcts), flush=True)
        print(f"{window}  MTM dd  " + "  ".join(f"p{p} {lab.q(dd, p):.2f}" for p in pcts), flush=True)
