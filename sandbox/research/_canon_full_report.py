"""Canon on live fills from $500: realised, per sleeve, every week, cold weeks.

Reads the `_mc_books` cache for the window named by BOOK_NAME / MC_START /
MC_END, so run `_mc_books run` for the same env first.

    realised   one full-window replay: totals, per sleeve, every week's % from
               the balance that week OPENED with
    cold       every Monday a fresh $500 account, seven days of trading
               (`lo` at the Monday, so the admission shadow resets as it does
               at `live_started_at`), plus a Monte Carlo of that opening week:
               seven one-day blocks drawn with replacement, chained from $500

    BOOK_KEYS=... BOOK_NAME=x MC_START=2022-01-01 py -m sandbox.research._canon_full_report realised
"""
import platform

platform._wmi = None

import bisect
import json
import multiprocessing
import os
import random
import sys
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import _mc_books as mb
from sandbox.research import exness_combined_montecarlo as mc

DAY = 86_400
WEEK = 7 * DAY
MC_PATHS = int(os.environ.get("OPEN_PATHS", "1000"))


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def monday_on_or_after(ts):
    d = ts // DAY
    return (d + (-(d + 3)) % 7) * DAY          # 1970-01-01 was a Thursday


def setup():
    mb.arm()
    state = mc.load()
    mc.pin_external_window()
    return state


def realised(state, out):
    lo, hi = ecs.ef.IS_END, ecs.ef.OOS_END
    book = mc.run_book(state, lo=lo, hi=hi, initial=ecs.CANON_INITIAL)
    init = book["initial"]
    print(f"\nCANON {len(state['members'])} sleeves, ${init:.0f}, live fills, "
          f"realised {day(lo)} .. {day(hi - 1)}")
    print(f"return {book['return_pct']:+,.1f}%   final ${book['final']:,.0f}   "
          f"MTM dd {book['mtm_dd_pct']:.2f}% (trough {book['mtm_dd_trough']})   "
          f"closed dd {book['max_dd_pct']:.2f}%   trades {book['trades']}   "
          f"below min {sum(book['below_broker_minimum'].values())}")

    rows = sorted(book["by_sleeve"].items(), key=lambda kv: -kv[1]["pnl"])
    print(f"\n{'sleeve':<26}{'pnl $':>12}{'trades':>8}{'mtm dd $':>11}{'dd share':>10}")
    for name, r in rows:
        print(f"{name:<26}{r['pnl']:>+12,.0f}{r['trades']:>8}"
              f"{r['mtm_dd_usd']:>11,.0f}{100 * r['dd_event_share']:>9.1f}%")

    curve = sorted(book["curve"])
    stamps = [s for s, _ in curve]

    def eq_at(ts):
        i = bisect.bisect_right(stamps, ts) - 1
        return curve[i][1] if i >= 0 else init

    weeks = []
    start = lo - ((lo // DAY + 3) % 7) * DAY          # Monday of the first week
    while start < hi:
        s = init if start <= lo else eq_at(start - 1)
        e = eq_at(min(start + WEEK, hi) - 1)
        weeks.append({"week": day(start), "open": s, "close": e,
                      "pct": 100.0 * (e / s - 1)})
        start += WEEK
    up = sum(w["pct"] > 0 for w in weeks)
    down = sum(w["pct"] < 0 for w in weeks)
    pcts = sorted(w["pct"] for w in weeks)
    print(f"\nweeks {len(weeks)}: up {up}, down {down}, flat {len(weeks) - up - down}; "
          f"worst {pcts[0]:+.2f}%  median {mc.quantile(pcts, 50):+.2f}%  best {pcts[-1]:+.2f}%")
    print(f"\n{'week of':<12}{'open $':>12}{'close $':>12}{'week %':>9}")
    for w in weeks:
        print(f"{w['week']:<12}{w['open']:>12,.0f}{w['close']:>12,.0f}{w['pct']:>+8.2f}%")

    out["realised"] = {k: book[k] for k in ("initial", "final", "return_pct",
                       "mtm_dd_pct", "mtm_dd_trough", "max_dd_pct", "trades")}
    out["by_sleeve"] = book["by_sleeve"]
    out["weeks"] = weeks


# ---- opening-week Monte Carlo workers ------------------------------------- #
_STATE = None
_DAYS = None


def _init():
    global _STATE, _DAYS
    _STATE = setup()
    _DAYS = mc.blocks_of(1)


def _open_path(seed):
    rng = random.Random(seed)
    order = [rng.choice(_DAYS) for _ in range(7)]
    book, _returns, curve = mc.chain(_STATE, order, ecs.CANON_INITIAL)
    low = min([v for _, v in curve] + [ecs.CANON_INITIAL])
    peak, dd = ecs.CANON_INITIAL, 0.0
    for _, v in sorted(curve):
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak if peak > 0 else 0.0)
    return {"pct": 100.0 * (book["final"] / ecs.CANON_INITIAL - 1),
            "mtm_dd": 100.0 * dd, "low": low}


def dist(label, values):
    q = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print(f"{label:<22}worst {min(values):+8.2f}  "
          + "  ".join(f"p{p} {mc.quantile(values, p):+7.2f}" for p in q)
          + f"  best {max(values):+8.2f}")


def cold(state, out):
    lo, hi = ecs.ef.IS_END, ecs.ef.OOS_END
    rows = []
    start = monday_on_or_after(lo)
    while start + WEEK <= hi:
        b = mc.run_book(state, lo=start, hi=start + WEEK, initial=ecs.CANON_INITIAL)
        rows.append({"week": day(start), "pct": b["return_pct"],
                     "mtm_dd": b["mtm_dd_pct"], "trades": b["trades"],
                     "final": b["final"]})
        start += WEEK
    print(f"\nOPENING WEEK, REALISED: a fresh ${ecs.CANON_INITIAL:.0f} every Monday, "
          f"7 days, {len(rows)} starts {rows[0]['week']} .. {rows[-1]['week']}")
    print(f"{'go-live week':<14}{'week %':>9}{'MTM dd %':>10}{'trades':>8}{'final $':>10}")
    for r in rows:
        print(f"{r['week']:<14}{r['pct']:>+8.2f}%{r['mtm_dd']:>9.2f}%"
              f"{r['trades']:>8}{r['final']:>10,.0f}")
    up = sum(r["pct"] > 0 for r in rows)
    print(f"\nup {up} / down {sum(r['pct'] < 0 for r in rows)} of {len(rows)}")
    dist("realised week %", [r["pct"] for r in rows])
    dist("realised MTM dd %", [r["mtm_dd"] for r in rows])
    by_year = {}
    for r in rows:
        by_year.setdefault(r["week"][:4], []).append(r["pct"])
    for y, v in sorted(by_year.items()):
        print(f"  {y}: n {len(v):>3}  mean {sum(v) / len(v):+6.2f}%  "
              f"median {mc.quantile(v, 50):+6.2f}%  worst {min(v):+7.2f}%  "
              f"down {sum(x < 0 for x in v)}")
    out["cold_weeks"] = rows

    workers = int(os.environ.get("MC_WORKERS") or max(1, (os.cpu_count() or 4) - 4))
    print(f"\nOPENING WEEK, MONTE CARLO: {MC_PATHS} paths of 7 one-day blocks "
          f"drawn with replacement from {day(lo)} .. {day(hi - 1)}, chained "
          f"from ${ecs.CANON_INITIAL:.0f}, {workers} workers", flush=True)
    with multiprocessing.Pool(workers, _init) as pool:
        paths = list(pool.imap_unordered(_open_path, range(1, MC_PATHS + 1),
                                         chunksize=4))
    dist("MC week %", [p["pct"] for p in paths])
    dist("MC MTM dd %", [p["mtm_dd"] for p in paths])
    for cut in (-5, -10, -15, -20):
        print(f"  P(week < {cut}%) {100 * sum(p['pct'] < cut for p in paths) / len(paths):5.1f}%")
    out["open_week_mc"] = paths


WINDOWS = (("2226", "2022-2026"), ("2224", "2022-2024"),
           ("2526", "2025-2026"), ("26", "2026"))


def markdown(tag):
    """THE canon result: every window's realised + Monte Carlo, per sleeve,
    every week, and the opening week realised + Monte Carlo, in one file."""
    import glob

    def mc_json(name):
        hits = glob.glob(os.path.join(mc.RESULTS, f"mc_books_{name}.json")) + \
               glob.glob(os.path.join(mc.RESULTS, f"mc_books_{name}_20??.json"))
        return json.load(open(hits[0], encoding="utf-8"))

    def pct(d, m, q):
        return d["runs"]["boot"]["metrics"][m]["percentiles"][str(q)]

    real = {t: json.load(open(os.path.join(
        mc.RESULTS, f"canon_report_{tag}_{w}_realised.json"), encoding="utf-8"))
        for w, t in WINDOWS}
    mcs = {t: mc_json(f"{tag}_{w}") for w, t in WINDOWS}
    cold_rows = json.load(open(os.path.join(
        mc.RESULTS, f"canon_report_{tag}_2226_cold.json"), encoding="utf-8"))
    first = real["2022-2026"]
    L = [f"# Canon report `{tag}` ({len(first['members'])} sleeves)", "",
         f"Exness Pro, live fills, ${ecs.CANON_INITIAL:.0f} cold per window, "
         f"risk {ecs.CANON_RISK_SCALE}, data through {first['window'][1]}. "
         "Monte Carlo: 14-day block bootstrap, 1,000 paths.", "",
         "## Realised vs Monte Carlo", "",
         "| window | realised | final $ | MTM dd | MC p5 | MC median | MC p95 "
         "| MC dd p50 / p95 / p99 | P(dd>20) | P(dd>30) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for _w, t in WINDOWS:
        r, d = real[t]["realised"], mcs[t]
        tail = d["runs"]["boot"]["tail"]
        L.append(f"| {t} | {r['return_pct']:+,.1f}% | {r['final']:,.0f} | "
                 f"{r['mtm_dd_pct']:.2f}% | {pct(d, 'return_pct', 5):+,.0f}% | "
                 f"{pct(d, 'return_pct', 50):+,.0f}% | {pct(d, 'return_pct', 95):+,.0f}% | "
                 f"{pct(d, 'mtm_dd_pct', 50):.2f} / {pct(d, 'mtm_dd_pct', 95):.2f} / "
                 f"{pct(d, 'mtm_dd_pct', 99):.2f} | {tail['p_mtm_dd_over_20']:.1f}% | "
                 f"{tail['p_mtm_dd_over_30']:.1f}% |")
    names = sorted(first["by_sleeve"], key=lambda k: -first["by_sleeve"][k]["pnl"])
    L += ["", "## Per sleeve, realised P&L $ (trades)", "",
          "| sleeve | " + " | ".join(t for _, t in WINDOWS) + " | MC P(<0) 22-26 |",
          "|---|" + "---|" * (len(WINDOWS) + 1)]
    for k in names:
        cells = []
        for _w, t in WINDOWS:
            row = real[t]["by_sleeve"].get(k)
            cells.append(f"{row['pnl']:+,.0f} ({row['trades']})" if row else "-")
        neg = mcs["2022-2026"]["runs"]["boot"].get("by_sleeve", {}).get(k, {})
        p_neg = neg.get("p_negative") if isinstance(neg, dict) else None
        L.append(f"| {k} | " + " | ".join(cells) + " | "
                 + (f"{p_neg:.0f}%" if p_neg is not None else "") + " |")
    c = cold_rows["cold_weeks"]
    cp = [r["pct"] for r in c]
    cd = [r["mtm_dd"] for r in c]
    mp = [p["pct"] for p in cold_rows["open_week_mc"]]
    L += ["", f"## Opening week: fresh ${ecs.CANON_INITIAL:.0f} every Monday, 7 days "
          f"({len(c)} starts)", "",
          "| | worst | p1 | p5 | p50 | p95 | p99 | best |", "|---|---|---|---|---|---|---|---|"]
    for label, v in (("realised week %", cp), ("realised MTM dd %", cd),
                     ("MC week % (7 random days)", mp)):
        lo_is_worst = "dd" not in label
        worst = min(v) if lo_is_worst else max(v)
        best = max(v) if lo_is_worst else min(v)
        L.append(f"| {label} | {worst:+.2f} | " + " | ".join(
            f"{mc.quantile(v, q):+.2f}" for q in (1, 5, 50, 95, 99)) + f" | {best:+.2f} |")
    L.append(f"\nup {sum(x > 0 for x in cp)} / down {sum(x < 0 for x in cp)}; "
             + ", ".join(f"P(MC week < {t}%) {100 * sum(x < t for x in mp) / len(mp):.1f}%"
                         for t in (-5, -10, -15)))
    for _w, t in WINDOWS:
        wk = real[t]["weeks"]
        L += ["", f"## Weekly % from week-open balance, {t}", "", "```"]
        L += ["  ".join(f"{x['week']} {x['pct']:+6.2f}%" for x in wk[i:i + 5])
              for i in range(0, len(wk), 5)]
        L.append("```")
    L += ["", "## Opening week, realised (week %, MTM dd %)", "", "```"]
    L += ["  ".join(f"{r['week']} {r['pct']:+6.2f}% dd{r['mtm_dd']:5.2f}" for r in c[i:i + 4])
          for i in range(0, len(c), 4)]
    L.append("```")
    path = os.path.join(mc.RESULTS, f"CANON_REPORT_{tag}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(L) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "markdown":
        markdown(sys.argv[2] if len(sys.argv) > 2 else "canon")
        sys.exit(0)
    state = setup()
    out = {"members": [f"{m['symbol']}:{m['family']}" for m in state["members"]],
           "window": [day(ecs.ef.IS_END), day(ecs.ef.OOS_END - 1)]}
    (realised if mode == "realised" else cold)(state, out)
    path = os.path.join(mc.RESULTS, f"canon_report_{mb.NAME}_{mode}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, default=str)
    print(f"\nwrote {path}")
