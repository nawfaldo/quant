"""Every subset of the weekend candidates on top of the base book, 2025-26.

Stage 1 replays all 2^N subsets on the realised path; stage 2 runs a short
block-bootstrap Monte Carlo on the best of them.

    BOOK_KEYS=<base>,<cand@we>,... py -3 -m sandbox.research._we_combos <n_base> <mc_top> <paths>
"""
import platform
platform._wmi = None
import itertools, json, multiprocessing, os, pickle, random, sys, time

from sandbox.research import _mc_books as mb
from sandbox.research import exness_combined_montecarlo as mc

CACHE = os.path.join(mb.ecs.CACHE_DIR, "we_combos.pkl")
_S = None
_B = None


def _init():
    global _S, _B
    mb.arm()
    with open(CACHE, "rb") as h:
        _S = pickle.load(h)
    mc.pin_external_window()
    _B = mc.blocks_of(mc.BLOCK_DAYS)


def _sub(keys):
    return {"members": [m for m in _S["members"] if f"{m['symbol']}:{m['family']}" in keys],
            "logs": _S["logs"], "bars_by": _S["bars_by"], "ctx_by": _S["ctx_by"]}


def _real(keys):
    r = mc.reference(_sub(set(keys)), _B, mc.BLOCK_DAYS)
    return keys, r["return_pct"], r["mtm_dd_pct"]


def _boot(job):
    keys, seed = job
    rng = random.Random(seed)
    order = [rng.choice(_B) for _ in _B]
    book, returns, curve = mc.chain(_sub(set(keys)), order, mb.ecs.CANON_INITIAL)
    m = mc.metrics(book, returns, curve, mb.ecs.CANON_INITIAL, mc.BLOCK_DAYS)
    return keys, m["return_pct"], m["mtm_dd_pct"]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


if __name__ == "__main__":
    n_base, top, paths = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    mb.arm()
    t0 = time.time()
    state = mb.prepare()
    with open(CACHE, "wb") as h:
        pickle.dump(state, h, protocol=5)
    keys = [f"{m['symbol']}:{m['family']}" for m in state["members"]]
    base, cands = keys[:n_base], keys[n_base:]
    print(f"prepared in {time.time() - t0:.0f}s; {len(cands)} candidates -> {2 ** len(cands)} books", flush=True)
    subsets = [tuple(base + list(c)) for r in range(len(cands) + 1)
               for c in itertools.combinations(cands, r)]
    workers = 12
    t0 = time.time()
    with multiprocessing.Pool(workers, _init) as pool:
        real = list(pool.imap_unordered(_real, subsets, chunksize=4))
        print(f"realised: {len(real)} books in {time.time() - t0:.0f}s", flush=True)
        short = lambda ks: "+".join(k.split(":")[1] for k in ks[n_base:]) or "BASE 18"
        by = {k: (r, d) for k, r, d in real}
        br, bd = by[tuple(base)]
        # best return at each drawdown ceiling, plus the overall best
        picks = sorted(real, key=lambda x: -x[1])[:top]
        for cap in (bd, bd + 1, bd + 2):
            ok = [x for x in real if x[2] <= cap]
            if ok:
                picks.append(max(ok, key=lambda x: x[1]))
        forced = []
        for spec in filter(None, os.environ.get("FORCE", "").split(";")):
            want = set(spec.split("+"))
            forced += [k for k in by if set(k[n_base:]) == want]
        picks = list(dict.fromkeys([tuple(base)] + forced + [p[0] for p in picks]))
        print(f"\nMonte Carlo, {paths} paths each, on {len(picks)} books", flush=True)
        t0 = time.time()
        jobs = [(k, s) for k in picks for s in range(1, paths + 1)]
        out = {}
        for k, r, d in pool.imap_unordered(_boot, jobs, chunksize=8):
            out.setdefault(k, []).append((r, d))
        print(f"done in {time.time() - t0:.0f}s\n")
    rows = []
    for k in picks:
        rs, ds = [x[0] for x in out[k]], [x[1] for x in out[k]]
        rows.append((short(k), len(k) - n_base, by[k][0], by[k][1], q(rs, 5), q(rs, 50),
                     q(ds, 50), q(ds, 95), q(ds, 99), 100 * sum(d > 20 for d in ds) / len(ds)))
    rows.sort(key=lambda x: -x[5])
    print(f"{'adds':<58}{'n':>3}{'real ret':>10}{'dd':>7} |{'MC p5':>9}{'p50':>9} |{'DD p50':>8}{'p95':>7}{'p99':>7}{'dd>20':>7}")
    for r in rows:
        print(f"{r[0][:58]:<58}{r[1]:>3}{r[2]:>9,.0f}%{r[3]:>6.2f}% |{r[4]:>8,.0f}%{r[5]:>8,.0f}% |"
              f"{r[6]:>7.2f}%{r[7]:>6.2f}%{r[8]:>6.2f}%{r[9]:>6.1f}%")
    json.dump({"realised": [[list(k), r, d] for k, r, d in real], "mc": rows},
              open(os.path.join(mc.RESULTS, "we_combos_2025.json"), "w"), indent=1)
    print("\nrealised top 15 by return:")
    for k, r, d in sorted(real, key=lambda x: -x[1])[:15]:
        print(f"   {r:>8,.0f}%  dd {d:5.2f}%  {short(k)}")
