"""Stage 2: from the stage-4 book, cut 2022-24 p99 while holding 2025-26.

Moves each step: drop any member, or add any shortlisted pool candidate (the
shortlist is a realised 2022-24 pre-screen). Moves are ranked on 2022-24
(p99 relief - LAMBDA * log return cost); the top few are checked on 2025-26 and
the best one that keeps 2025-26 p99 <= start + TOL and median >= KEEP x start is
taken. Stops at 2022-24 p99 < TARGET or when no move qualifies.
"""
import json
import math
import os

from sandbox.research import _p99_lab as lab

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "..", "results", "p99_greedy2.json")
PATHS = int(os.environ.get("PATHS", "300"))
TARGET = float(os.environ.get("TARGET", "25"))
LAMBDA = float(os.environ.get("LAMBDA", "20"))
TOL = float(os.environ.get("TOL", "1.0"))
KEEP = float(os.environ.get("KEEP", "0.9"))
SHORT = int(os.environ.get("SHORT", "30"))
CHECK = int(os.environ.get("CHECK", "4"))
STAGE = int(os.environ.get("STAGE", "4"))


def lr(r, key="med_ret"):
    return math.log1p(max(r[key] / 100.0, -0.9999))


if __name__ == "__main__":
    greedy = json.load(open(os.path.join(HERE, "..", "results", "p99_greedy.json")))
    book = list(greedy[STAGE]["book"])
    if os.environ.get("START_JSON"):
        book = list(json.load(open(os.environ["START_JSON"]))[-1]["book"])
    for k in [x for x in os.environ.get("START_DROP", "").split(",") if x]:
        book.remove(k)
    # realised pre-screen of the pool on 2022-24
    state, _lo, _hi = lab.load_state("2224")
    barred = tuple(x for x in os.environ.get("BAR", "es,nq").split(",") if x)
    pool = [k for k in state["by_key"] if k not in book and k.split(":")[0] not in barred]
    # 2026 MUST BE CLEARLY POSITIVE for an addition: its own 2026 trades (from
    # the 2025-start logs) at >= MIN_R26 R and positive dollars.
    import pickle
    from datetime import datetime, timezone
    y26 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    min_r = float(os.environ.get("MIN_R26", "5"))
    with open(os.path.join(lab.CACHE, "p99_extra_2526.pkl"), "rb") as handle:
        logs26 = pickle.load(handle)["logs"]

    with open(os.path.join(lab.CACHE, "p99_extra_2224.pkl"), "rb") as handle:
        logs22 = pickle.load(handle)["logs"]
    y25 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())

    def mean_r(ts):
        rs = [t["points"] / t["distance"] for t in ts if t["distance"]]
        return sum(rs) / len(rs) if rs else 0.0

    def good26(k):
        # 2026 clearly positive AND no decayed edge: R/trade in 2025-26 at
        # least half its 2022-24 rate.
        ts = [t for t in logs26.get(k, []) if t["entry_ts"] >= y26]
        r = sum(t["points"] / t["distance"] for t in ts if t["distance"])
        early = mean_r([t for t in logs22.get(k, []) if t["entry_ts"] < y25])
        late = mean_r(logs26.get(k, []))
        return (r >= min_r and sum(t["pnl"] for t in ts) > 0
                and (early <= 0 or late >= 0.5 * early))
    pool = [k for k in pool if good26(k)]
    print(f"pool after 2026 filter: {len(pool)}", flush=True)
    # HEDGE VALUE: what each candidate made on the book's worst days in
    # 2022-24 (daily R, by exit day). Those that make money when the book is
    # losing are what lowers the tail; the rest of the shortlist is the
    # realised-return leaders so the search can still buy return back.
    from collections import defaultdict
    all_logs = state["logs"]
    daily = defaultdict(float)
    for k in book:
        for t in all_logs[k]:
            if t["entry_ts"] < y25 and t["distance"]:
                daily[t["exit_ts"] // 86400] += t["points"] / t["distance"]
    worst = set(sorted(daily, key=daily.get)[:60])

    def hedge(k):
        return sum(t["points"] / t["distance"] for t in all_logs[k]
                   if t["distance"] and t["exit_ts"] // 86400 in worst)
    by_hedge = sorted(pool, key=hedge, reverse=True)
    rows = lab.evaluate("2224", [(book, 0.13, None)] + [(book + [k], 0.13, None) for k in pool],
                        paths=0, realised=True)
    base_real = rows[0]
    screen = sorted(((lr(r, "real_ret") - lr(base_real, "real_ret"))
                     - 0.03 * max(0.0, r["real_dd"] - base_real["real_dd"]), k)
                    for k, r in zip(pool, rows[1:]))[::-1]
    n_hedge = int(os.environ.get("N_HEDGE", "30"))
    short = list(dict.fromkeys(by_hedge[:n_hedge] + [k for _s, k in screen]))[:SHORT]
    print("hedge top:", [(k, round(hedge(k), 1)) for k in by_hedge[:10]], flush=True)
    print("add shortlist:", short, flush=True)

    b24 = lab.evaluate("2224", [(book, 0.13, None)], paths=PATHS, realised=False)[0]
    b26 = lab.evaluate("2526", [(book, 0.13, None)], paths=PATHS, realised=False)[0]
    start26 = b26
    log = [{"step": 0, "move": None, "book": book, "p99_2224": b24["dd99"], "med_2224": b24["med_ret"],
            "p99_2526": b26["dd99"], "med_2526": b26["med_ret"]}]
    print(f"step 0  n {len(book)}  2224 p99 {b24['dd99']:.2f} med {b24['med_ret']:,.0f}%   "
          f"2526 p99 {b26['dd99']:.2f} med {b26['med_ret']:,.0f}%", flush=True)
    step = 0
    while b24["dd99"] >= TARGET:
        step += 1
        moves = ([] if os.environ.get("NO_DROP")
                 else [(f"-{k}", [x for x in book if x != k]) for k in book])
        moves += [(f"+{k}", book + [k]) for k in short if k not in book]
        rows = lab.evaluate("2224", [(m, 0.13, None) for _l, m in moves], paths=PATHS, realised=False)
        ranked = sorted((((b24["dd99"] - r["dd99"]) - LAMBDA * (lr(b24) - lr(r)), lab_, m, r)
                         for (lab_, m), r in zip(moves, rows)), key=lambda x: -x[0])
        ranked = [x for x in ranked if x[3]["dd99"] < b24["dd99"]][:CHECK]
        if not ranked:
            print("no move lowers 2022-24 p99 -- stop", flush=True)
            break
        checks = lab.evaluate("2526", [(m, 0.13, None) for _s, _l, m, _r in ranked],
                              paths=PATHS, realised=False)
        taken = None
        for (s, label, m, r24), r26 in zip(ranked, checks):
            ok = r26["dd99"] <= start26["dd99"] + TOL and r26["med_ret"] >= KEEP * start26["med_ret"]
            print(f"   try {label:46s} 2224 p99 {r24['dd99']:.2f} med {r24['med_ret']:,.0f}%  "
                  f"2526 p99 {r26['dd99']:.2f} med {r26['med_ret']:,.0f}%  {'OK' if ok else 'x'}", flush=True)
            if ok and taken is None:
                taken = (label, m, r24, r26)
        if taken is None:
            print("no move holds 2025-26 -- stop", flush=True)
            break
        label, book, b24, b26 = taken
        log.append({"step": step, "move": label, "book": book, "p99_2224": b24["dd99"],
                    "med_2224": b24["med_ret"], "p99_2526": b26["dd99"], "med_2526": b26["med_ret"]})
        print(f"step {step}  {label:44s} n {len(book)}  2224 p99 {b24['dd99']:.2f} med {b24['med_ret']:,.0f}%   "
              f"2526 p99 {b26['dd99']:.2f} med {b26['med_ret']:,.0f}%", flush=True)
        with open(OUT, "w") as handle:
            json.dump(log, handle, indent=1)
