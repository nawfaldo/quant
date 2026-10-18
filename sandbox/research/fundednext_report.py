"""THE FundedNext canon report at one risk: every window realised AND Monte
Carlo, and the challenge. The user does not want ANY per-sleeve section
(tables or strategy list), monthly or weekly parts here (2026-10-13).

    py -m sandbox.research.fundednext_report [--risk 0.05] [--balance 6000]
    py -m sandbox.research.fundednext_report --balances 6000,15000,25000,50000,100000,200000

Windows: 2024-2026, 2024-2025 (the search's in-sample years), 2026 (holdout).
Each starts from a fresh account of `--balance`.

  realised     `fundednext_combined_strategies.replay` -- live fills, FundedNext
               spreads and commission, forced minimum lots, one shared balance.
  Monte Carlo  the book's server days in random 10-day blocks, chained in
               sequence for as many days as the window has. CHECK line: the
               same chaining in the REAL order must give back the realised
               return.
  challenge    the two-step (+8% then +5%, 5% daily, 10% static max -- the same
               fractions at every FundedNext size): a challenge from every real
               start day, and by Monte Carlo (`fundednext_challenge._mc_cell`).

`--balances` runs the report for each size and adds a side-by-side comparison.

Reads the book from `fundednext_challenge_windows.pkl` (written by `build`).
Writes results/FUNDEDNEXT_CANON_REPORT_<risk>_<balance>.md (+ .json) and, for
--balances, results/FUNDEDNEXT_BALANCE_COMPARISON_<risk>.md.
"""
import argparse
import json
import multiprocessing
import os
import pickle
import random
from datetime import datetime, timezone

from sandbox.research import fundednext_combined_strategies as fn
from sandbox.research import fundednext_challenge as challenge

#: The risk every window shares -- the highest that passes the challenge
#: Monte Carlo >= 75% in every window ($6,000).
COMMON_RISK = 0.05
BALANCE = 6_000
PATHS = 5000
BLOCK_DAYS = challenge.MC_BLOCK_DAYS
WINDOW_ORDER = ("24-26", "24-25", "26")
_STATE = None


def _label(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _bounds(tag):
    label, lo, hi = challenge.WINDOW_SPEC[tag]
    return label, fn._stamp(lo), (fn.ef.OOS_END if hi is None else fn._stamp(hi))


def _money(v):
    return f"{v:,.0f}"


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #

def _init(balance):
    global _STATE
    with open(challenge.CACHE, "rb") as handle:
        _STATE = pickle.load(handle)
    challenge.set_balance(balance)


def _work(job):
    kind = job[0]
    if kind == "book":
        _, tag, risk = job
        _, lo, hi = _bounds(tag)
        book = fn.replay(_STATE, risk, initial=challenge.ACCOUNT, lo=lo, hi=hi)
        _, sharpe, positive, n_months = fn.base.monthly(book["settled"], challenge.ACCOUNT)
        days = challenge.daily_series({**_STATE, "start": lo, "end": hi}, risk)
        return {"kind": kind, "tag": tag,
                "head": {k: book[k] for k in ("final", "return_pct", "mtm_dd_pct",
                                              "mtm_dd_trough", "max_dd_pct", "trades",
                                              "below_broker_minimum")},
                "sharpe": sharpe, "positive": positive, "months_n": n_months,
                "days": days}
    if kind == "roll":
        _, tag, risk, start, hi = job
        return {"kind": kind, "tag": tag, **challenge.challenge(_STATE, risk, start, hi)}
    if kind == "mc_book":
        _, tag, days, paths, seed = job
        return {"kind": kind, "tag": tag, **book_montecarlo(days, paths, seed)}
    if kind == "mc_chal":
        _, tag, days, risk, paths, seed = job
        cell = challenge._mc_cell((days, risk, paths, seed))
        return {"kind": kind, "tag": tag, **cell}
    raise ValueError(kind)


# --------------------------------------------------------------------------- #
# Monte Carlo of the book's return and drawdown
# --------------------------------------------------------------------------- #

def _walk(seq):
    """Chain day returns in order -> (return %, worst marked drawdown %)."""
    equity, peak, worst = 1.0, 1.0, 0.0
    for ret, dip in seq:
        low = equity * (1 + dip)
        worst = max(worst, (peak - low) / peak)
        equity *= 1 + ret
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return 100.0 * (equity - 1.0), 100.0 * worst


def book_montecarlo(days, paths, seed):
    cut = [(r, dip) for r, dip, _, _ in days]
    n = len(cut)
    rng = random.Random(seed)
    rets, dds = [], []
    for _ in range(paths):
        seq = []
        while len(seq) < n:
            start = rng.randrange(0, n - BLOCK_DAYS + 1)
            seq.extend(cut[start:start + BLOCK_DAYS])
        ret, dd = _walk(seq[:n])
        rets.append(ret)
        dds.append(dd)
    real_ret, real_dd = _walk(cut)

    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(q * len(values)))]

    return {"real_order_return": real_ret, "real_order_dd": real_dd,
            "ret_p5": pct(rets, 0.05), "ret_p50": pct(rets, 0.50), "ret_p95": pct(rets, 0.95),
            "dd_p50": pct(dds, 0.50), "dd_p95": pct(dds, 0.95), "dd_max": max(dds),
            "p_loss": 100.0 * sum(1 for r in rets if r < 0) / paths,
            "p_dd_over_10": 100.0 * sum(1 for d in dds if d > 10) / paths}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def run(risk, paths, workers, balance):
    """Every number of one report, for one account size."""
    challenge.set_balance(balance)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers, initializer=_init, initargs=(balance,)) as pool:
        books = {r["tag"]: r for r in pool.map(_work, [("book", t, risk) for t in WINDOW_ORDER])}
        jobs = []
        for i, tag in enumerate(WINDOW_ORDER):
            _, lo, hi = _bounds(tag)
            jobs += [("roll", tag, risk, s, hi) for s in challenge.starts_of(lo, hi, 1)]
            days = books[tag]["days"]
            jobs.append(("mc_book", tag, days, paths, 100 * i))
            jobs.append(("mc_chal", tag, days, risk, paths, 5000 + 100 * i))
        done = pool.map(_work, jobs, chunksize=4)
    rolling = {tag: challenge.summarise([r for r in done if r["kind"] == "roll" and r["tag"] == tag],
                                        risk)
               for tag in WINDOW_ORDER}
    return {"risk": risk, "balance": balance, "paths": paths, "books": books,
            "rolling": rolling,
            "mc_book": {r["tag"]: r for r in done if r["kind"] == "mc_book"},
            "mc_chal": {r["tag"]: r for r in done if r["kind"] == "mc_chal"}}


def report_text(res):
    risk, balance, paths = res["risk"], res["balance"], res["paths"]
    books, rolling, mc_book, mc_chal = res["books"], res["rolling"], res["mc_book"], res["mc_chal"]
    p1, p2 = (challenge.RULE_FRACTIONS["p1"] * balance, challenge.RULE_FRACTIONS["p2"] * balance)
    daily, floor = challenge.RULE_FRACTIONS["daily"] * balance, (1 - challenge.RULE_FRACTIONS["max"]) * balance
    L = [f"# FundedNext canon report -- risk {risk}, ${_money(balance)} per window", "",
         f"{len(_book_members())} sleeves, FundedNext live fills, spreads and commission, "
         f"forced minimum lot. Every window starts from a fresh ${_money(balance)}. Monte Carlo: "
         f"{paths:,} paths, {BLOCK_DAYS}-day blocks of the book's own days, chained in order.", ""]

    L += ["## 1. Summary, every window", "",
          "| window | realised return | final $ | realised MTM drawdown | realised closed drawdown "
          "| trades | monthly Sharpe | positive months |",
          "|---|---|---|---|---|---|---|---|"]
    for tag in WINDOW_ORDER:
        label, lo, hi = _bounds(tag)
        b = books[tag]
        h = b["head"]
        L.append(f"| {label} ({_label(lo)} to {_label(hi)}) | {h['return_pct']:+.1f}% | "
                 f"{_money(h['final'])} | {h['mtm_dd_pct']:.1f}% (low on {h['mtm_dd_trough']}) | "
                 f"{h['max_dd_pct']:.1f}% | {h['trades']:,} | {b['sharpe']:.2f} | "
                 f"{b['positive']}/{b['months_n']} |")

    L += ["", "## 2. Monte Carlo of the book (return and drawdown over the window's length)", "",
          "| window | return: bad case (5th pct) | return: typical (median) "
          "| return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) "
          "| drawdown: worst path | chance of losing money | chance drawdown > 10% "
          "| CHECK: real order gives |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for tag in WINDOW_ORDER:
        m = mc_book[tag]
        L.append(f"| {_bounds(tag)[0]} | {m['ret_p5']:+,.1f}% | "
                 f"{m['ret_p50']:+,.1f}% | {m['ret_p95']:+,.1f}% | {m['dd_p50']:.1f}% | "
                 f"{m['dd_p95']:.1f}% | {m['dd_max']:.1f}% | {m['p_loss']:.1f}% | "
                 f"{m['p_dd_over_10']:.1f}% | {m['real_order_return']:+,.1f}%, "
                 f"dd {m['real_order_dd']:.1f}% |")
    L += ["", "CHECK: chaining the real days in their real order should give the "
          "realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks)."]

    L += ["", f"## 3. The ${_money(balance)} two-step challenge at risk {risk}", "",
          f"Pass = +${_money(p1)} (phase 1) then +${_money(p2)} (phase 2), never -${_money(daily)} "
          f"in a day, never below ${_money(floor)}.", "",
          "**A. Real history: a challenge started on every past day** "
          "(calendar days, median of the starts that passed)",
          "", "| window | start days | passed both | failed daily-loss | failed max-loss "
          "| days to pass phase 1 | days to pass phase 2 | days to pass both |",
          "|---|---|---|---|---|---|---|---|"]
    for tag in WINDOW_ORDER:
        row = rolling[tag]
        L.append(f"| {_bounds(tag)[0]} | {row['starts']} | {row['pass_both_pct']:.1f}% | "
                 f"{row['fail_daily']} | {row['fail_max']} | {row['p1_median_days']} | "
                 f"{row['p2_median_days']} | {row['both_median_days']} |")
    L += ["", "**B. Monte Carlo** (trading days, median of the paths that passed)", "",
          "| window | passed both | failed max-loss | failed daily-loss "
          "| not finished in 500 days | trading days to pass both |",
          "|---|---|---|---|---|---|"]
    for tag in WINDOW_ORDER:
        c = mc_chal[tag]
        L.append(f"| {_bounds(tag)[0]} | {c['pass']:.1f}% (±{c['pass_se']:.1f}) | "
                 f"{c['max']:.1f}% | {c['daily']:.1f}% | {c['stuck']:.1f}% | "
                 f"{c['median_trading_days'] or '-'} |")
    return "\n".join(L) + "\n"


def comparison_text(results):
    risk = results[0]["risk"]
    L = [f"# FundedNext canon by account size -- risk {risk}", "",
         "Same book, same risk, rules scaled to each size (+8% / +5%, 5% daily, 10% max).", ""]
    for tag in WINDOW_ORDER:
        L += [f"## {_bounds(tag)[0]}", "",
              "| account size | realised return | realised MTM drawdown | trades "
              "| challenge passed (real history) | days to pass both (real history, calendar) "
              "| challenge passed (Monte Carlo) | trading days to pass both (Monte Carlo) |",
              "|---|---|---|---|---|---|---|---|"]
        for res in results:
            h = res["books"][tag]["head"]
            roll = res["rolling"][tag]
            mc = res["mc_chal"][tag]
            L.append(f"| ${_money(res['balance'])} | {h['return_pct']:+.1f}% | {h['mtm_dd_pct']:.1f}% | "
                     f"{h['trades']:,} | {roll['pass_both_pct']:.1f}% | {roll['both_median_days']} | "
                     f"{mc['pass']:.1f}% | {mc['median_trading_days'] or '-'} |")
        L.append("")
    return "\n".join(L)


_MEMBERS = None


def _book_members():
    global _MEMBERS
    if _MEMBERS is None:
        with open(challenge.CACHE, "rb") as handle:
            _MEMBERS = pickle.load(handle)["members"]
    return _MEMBERS


def write(res):
    text = report_text(res)
    tag = f"{res['risk']}_{int(res['balance'])}"
    path = os.path.join(fn.ef.RESULTS, f"FUNDEDNEXT_CANON_REPORT_{tag}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    dump = {"risk": res["risk"], "balance": res["balance"], "paths": res["paths"],
            "windows": {t: {k: v for k, v in res["books"][t].items() if k != "days"}
                        for t in WINDOW_ORDER},
            "rolling": res["rolling"],
            "mc_book": res["mc_book"], "mc_challenge": res["mc_chal"]}
    with open(os.path.join(fn.ef.RESULTS, f"fundednext_canon_report_{tag}.json"), "w",
              encoding="utf-8") as handle:
        json.dump(dump, handle, indent=1, default=str)
    return text, path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=COMMON_RISK)
    parser.add_argument("--balance", type=float, default=BALANCE)
    parser.add_argument("--balances", default=None,
                        help="comma list of account sizes: one report each plus a comparison")
    parser.add_argument("--paths", type=int, default=PATHS)
    parser.add_argument("--workers", type=int, default=fn.default_workers())
    args = parser.parse_args()
    sizes = ([float(x) for x in args.balances.split(",")] if args.balances else [args.balance])
    results = []
    for size in sizes:
        res = run(args.risk, args.paths, args.workers, size)
        text, path = write(res)
        print(text)
        print(f"wrote {path}", flush=True)
        results.append(res)
    if len(results) > 1:
        text = comparison_text(results)
        path = os.path.join(fn.ef.RESULTS, f"FUNDEDNEXT_BALANCE_COMPARISON_{args.risk}.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(text)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
