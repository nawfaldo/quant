"""The FundedNext combined book run through the $6,000 two-step challenge.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE: terminal output does
NOT reach the user -- paste the tables into the reply, per-sleeve one included.

WHAT IT IS. The challenge engine `fundednext_combined_strategies` judges its
book with: `challenge` starts a fresh $6,000 on one server day and replays the
book from there through the real `replay` engine (FundedNext fills and costs,
sized off the challenge's own $6,000); `_mc_cell` runs the same rules on
random 10-day blocks of the book's server days. Neither picks anything --
members and risk are chosen and sealed in `fundednext_combined_strategies`,
on 2024-2025 only.

THE RULES (FundedNext, $6,000 account):

  phase 1 target   +$480 (8%)       phase 2 target   +$300 (5%), fresh $6,000
  daily loss       $300  -- equity may not fall $300 under the day's reference
  max loss         $600  STATIC -- equity may never touch $5,400
  trading days     5 minimum per phase (server days with an entry)

  * A pass needs BOTH the closed balance and the marked equity at the target,
    so a phase cannot "pass" on a realised winner while open losers sit under
    it. Once the target and the 5 days are met, everything is flattened.
  * Server day = NY+7 (FundedNext-Server 3 since 2024-07-08,
    [[fundednext-server-clock-is-ny-plus-7]]), so the day rolls at 17:00 NY.
  * The daily floor is the CLOSED BALANCE at the rollover minus $300
    (FundedNext help centre, "how can i calculate the daily loss limit",
    checked 2026-10-13): realised profit during the day adds room, and open
    profit held over the rollover does NOT raise the floor. An earlier version
    used the higher of balance and equity and booked 38 false fails, all on
    2026-03-04, from open profit given back.
  * Breaches are checked on the 30-minute marks the book is marked on. A spike
    inside a bar is invisible, so the breach rate here is a floor, not exact --
    unless the state asks for `intrabar`: then every open position is marked at
    the worst price its bar touched (`worst_marks`), and the daily and max-loss
    rules are judged on that, a ceiling on the breach rate instead.
  * Phase 2 begins the server day after phase 1 passes. A phase still open at
    the end of the data is UNRESOLVED and is reported, not counted as a pass.

`detail` replays the oct-13 live book's cached state (`CACHE`) at one risk.

    py -m sandbox.research.fundednext_challenge detail --risk 0.05
"""
from __future__ import annotations

import argparse
import pickle
import statistics
from datetime import datetime, timezone

from sandbox.research import fundednext_combined_strategies as fn  # patches first
from sandbox.research import exness_combined_montecarlo as mc

ecs = fn.base
ef = fn.ef

ACCOUNT = 6_000.0
PHASE_TARGETS = (480.0, 300.0)
DAILY_LOSS = 300.0
MAX_LOSS = 600.0
#: Every FundedNext size uses the same rules as fractions of the balance.
RULE_FRACTIONS = {"p1": 0.08, "p2": 0.05, "daily": 0.05, "max": 0.10}


def set_balance(balance):
    """Re-scale the challenge to another account size ($15k ... $200k): the
    targets and loss limits are the same fractions of the balance at every
    size. Call it in every process that judges or replays (workers too)."""
    global ACCOUNT, PHASE_TARGETS, DAILY_LOSS, MAX_LOSS
    ACCOUNT = float(balance)
    PHASE_TARGETS = (RULE_FRACTIONS["p1"] * balance, RULE_FRACTIONS["p2"] * balance)
    DAILY_LOSS = RULE_FRACTIONS["daily"] * balance
    MAX_LOSS = RULE_FRACTIONS["max"] * balance
MIN_DAYS = 5
DEFAULT_RISKS = (0.02, 0.03, 0.04, 0.05, 0.075, 0.1)
SERVER_OFFSET = 7 * 3600          # NY wall clock -> FundedNext server clock
DAY = 86_400

CACHE = fn.WINDOW_STATE   # the oct-13 live book's 2024-2026 logs (`detail`, `fundednext_report`)


def server_day(ts):
    return (ts + SERVER_OFFSET) // DAY


def day_start(day):
    """NY-wall stamp at which server day `day` opens."""
    return day * DAY - SERVER_OFFSET


def _stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def _label(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# one phase
# --------------------------------------------------------------------------- #

def run_book(state, risk, lo, hi):
    members = state["members"]
    return ecs.replay(members, state["logs"], state["bars_by"], state["ctx_by"],
                      scale=state.get("scale") or {},
                      sizing_cap=state.get("sizing_cap") or {}, risk_scale=risk,
                      gross_cap=None, fair_cap=False, initial=ACCOUNT,
                      lo=lo, hi=hi, force_minimum=True,
                      marking_cache=mc._MARKING)


_EXTREMES = {}


def worst_marks(state, book):
    """`book["marked"]` with every open position at the worst price its 30m bar
    touched -- a long at the bar's low, a short at its high -- as if each one hit
    its 1-minute extreme in the same instant. The bars' highs and lows are built
    from the 1-minute data, so a spike a 30m close never shows is in here; the
    true 1-minute equity lies between this and the close-marked one."""
    key = id(state["bars_by"])
    if key not in _EXTREMES:
        _EXTREMES.clear()
        _EXTREMES[key] = {sym: {b[ef.TS]: (b[ef.H], b[ef.L]) for b in bars}
                          for sym, bars in state["bars_by"].items()}
    extremes = _EXTREMES[key]
    positions = sorted(book["settled"], key=lambda p: p["entry_ts"])
    closes = sorted(book["curve"])
    out, active, pi, ci, balance = [], [], 0, 0, ACCOUNT
    for stamp, marked in book["marked"]:
        while ci < len(closes) and closes[ci][0] <= stamp:
            balance = closes[ci][1]
            ci += 1
        while pi < len(positions) and positions[pi]["entry_ts"] <= stamp:
            active.append(positions[pi])
            pi += 1
        active = [q for q in active if q["exit_ts"] > stamp]
        open_value = 0.0
        for q in active:
            bar = extremes[q["symbol"]].get(stamp)
            if bar is None:       # unmarked here, as in the replay
                continue
            price = bar[1] if q["side"] > 0 else bar[0]
            open_value += q["side"] * (price - q["entry"]) * q["lots"] * q["money_per_point"]
        out.append((stamp, min(marked, balance + open_value)))
    return out


def judge(book, target, worst=None):
    """Walk one phase's replay against the rules.

    Returns `(outcome, at, days, worst_daily, worst_total)` with outcome one of
    pass / daily / max / open. `worst` (from `worst_marks`) judges the loss
    limits on each bar's worst price; the target still needs the closed balance
    and the bar's close-marked equity.
    """
    # Closed-balance steps and entry days, both in time order.
    closes = sorted(book["curve"])
    entry_days = sorted({server_day(p["entry_ts"]) for p in book["settled"]})
    balance, ci = ACCOUNT, 0
    traded, ei = set(), 0
    day, ref = None, ACCOUNT
    worst_daily, worst_total = 0.0, 0.0
    floor = ACCOUNT - MAX_LOSS
    goal = ACCOUNT + target
    lows = worst or book["marked"]
    for (stamp, marked), (_, low) in zip(book["marked"], lows):
        while ci < len(closes) and closes[ci][0] <= stamp:
            balance = closes[ci][1]
            ci += 1
        while ei < len(entry_days) and day_start(entry_days[ei]) <= stamp:
            traded.add(entry_days[ei])
            ei += 1
        today = server_day(stamp)
        if today != day:
            day, ref = today, balance
        worst_daily = max(worst_daily, ref - low)
        worst_total = max(worst_total, ACCOUNT - low)
        if low <= floor:
            return "max", stamp, len(traded), worst_daily, worst_total
        if low <= ref - DAILY_LOSS:
            return "daily", stamp, len(traded), worst_daily, worst_total
        if balance >= goal and marked >= goal and len(traded) >= MIN_DAYS:
            return "pass", stamp, len(traded), worst_daily, worst_total
    return "open", None, len(traded), worst_daily, worst_total


def challenge(state, risk, start, end=None):
    """Phase 1 from `start`, phase 2 from the server day after it passes."""
    end = state["end"] if end is None else end
    out = {"start": start, "risk": risk}
    lo = start
    for phase, target in enumerate(PHASE_TARGETS, 1):
        book = run_book(state, risk, lo, end)
        outcome, at, days, wd, wt = judge(
            book, target, worst_marks(state, book) if state.get("intrabar") else None)
        out[f"p{phase}"] = {"outcome": outcome, "at": at, "days": days,
                            "calendar_days": None if at is None
                            else round((at - lo) / DAY, 1),
                            "worst_daily": round(wd, 2),
                            "worst_total": round(wt, 2)}
        if outcome != "pass":
            break
        lo = day_start(server_day(at) + 1)
        if lo >= end:
            out["p2"] = {"outcome": "open", "at": None, "days": 0,
                         "calendar_days": None, "worst_daily": 0.0,
                         "worst_total": 0.0}
            break
    return out


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #

_STATE = None


def _init_worker(path):
    global _STATE
    with open(path, "rb") as handle:
        _STATE = pickle.load(handle)


def _work(job):
    window, risk, start, end = job
    return {**challenge(_STATE, risk, start, end), "window": window}


def starts_of(lo, hi, step_days):
    """Every `step_days`-th server-day open in [lo, hi), weekdays only."""
    first = server_day(lo) + 1
    last = server_day(hi)
    out = []
    for day in range(first, last, step_days):
        stamp = day_start(day)
        # A Saturday/Sunday server day carries no FX session; a challenge
        # bought then simply starts on Monday's, which is already a start.
        if datetime.fromtimestamp(stamp + SERVER_OFFSET, tz=timezone.utc).weekday() < 5:
            out.append(stamp)
    return out


def summarise(rows, risk, window=None):
    rows = [r for r in rows if r["risk"] == risk
            and (window is None or r.get("window") == window)]
    n = len(rows)
    p1 = [r["p1"] for r in rows]
    resolved1 = [x for x in p1 if x["outcome"] != "open"]
    pass1 = [x for x in p1 if x["outcome"] == "pass"]
    p2 = [r["p2"] for r in rows if "p2" in r]
    pass2 = [x for x in p2 if x["outcome"] == "pass"]
    resolved = [r for r in rows if r["p1"]["outcome"] != "open"
                and (r["p1"]["outcome"] != "pass" or r["p2"]["outcome"] != "open")]
    both = [r for r in resolved if r["p1"]["outcome"] == "pass"
            and r["p2"]["outcome"] == "pass"]
    fail = [r for r in resolved if r not in both]

    def med(values):
        return round(statistics.median(values), 1) if values else None

    def count(items, outcome):
        return sum(1 for x in items if x["outcome"] == outcome)

    return {
        "risk": risk, "starts": n, "resolved": len(resolved),
        "pass_both_pct": round(100 * len(both) / len(resolved), 1) if resolved else None,
        "p1_pass_pct": round(100 * len(pass1) / len(resolved1), 1) if resolved1 else None,
        "p1_daily": count(p1, "daily"), "p1_max": count(p1, "max"),
        "p1_open": count(p1, "open"),
        "p2_pass_pct": (round(100 * len(pass2) / sum(1 for x in p2 if x["outcome"] != "open"), 1)
                        if any(x["outcome"] != "open" for x in p2) else None),
        "p2_daily": count(p2, "daily"), "p2_max": count(p2, "max"),
        "p2_open": count(p2, "open"),
        "fail_daily": sum(1 for r in fail if "daily" in (r["p1"]["outcome"], r.get("p2", {}).get("outcome"))),
        "fail_max": sum(1 for r in fail if "max" in (r["p1"]["outcome"], r.get("p2", {}).get("outcome"))),
        "p1_median_days": med([x["calendar_days"] for x in pass1]),
        "p2_median_days": med([x["calendar_days"] for x in pass2]),
        "both_median_days": med([r["p1"]["calendar_days"] + r["p2"]["calendar_days"]
                                 for r in both]),
        "worst_daily_seen": round(max(x["worst_daily"] for x in p1 + p2), 2),
    }


def detail(risk):
    """The whole window at one dial from a single $6,000 start: per-sleeve P&L,
    worst server day and worst equity, for the per-sleeve table."""
    with open(CACHE, "rb") as handle:
        state = pickle.load(handle)
    mc._MARKING.clear()
    book = run_book(state, risk, state["start"], state["end"])
    daily = {}
    day, ref = None, ACCOUNT
    balance, ci = ACCOUNT, 0
    closes = sorted(book["curve"])
    for stamp, marked in book["marked"]:
        while ci < len(closes) and closes[ci][0] <= stamp:
            balance = closes[ci][1]
            ci += 1
        today = server_day(stamp)
        if today != day:
            day, ref = today, balance
        daily[day] = max(daily.get(day, 0.0), ref - marked)
    worst = sorted(daily.items(), key=lambda kv: -kv[1])[:5]
    print(f"\nrisk {risk}: ${ACCOUNT:,.0f} -> ${book['final']:,.0f} "
          f"({book['return_pct']:+.1f}%), MTM dd {book['mtm_dd_pct']:.1f}%, "
          f"{book['trades']} trades")
    print("worst server days (equity under the day's reference): "
          + ", ".join(f"{_label(day_start(d) + SERVER_OFFSET)} ${v:,.0f}"
                      for d, v in worst))
    print(f"\n{'sleeve':28}{'P&L $':>10}{'trades':>8}{'MTM dd $':>10}{'min-lot':>9}")
    for name, row in sorted(book["by_sleeve"].items(), key=lambda kv: -kv[1]["pnl"]):
        print(f"{name:28}{row['pnl']:>10,.0f}{row['trades']:>8}"
              f"{row['mtm_dd_usd']:>10,.0f}"
              f"{book['below_broker_minimum'].get(name, 0):>9}")


# --------------------------------------------------------------------------- #
# Monte Carlo
# --------------------------------------------------------------------------- #

MC_BLOCK_DAYS = 10
MC_PATHS = 5000
MC_MAX_DAYS = 500      # trading days per phase before a path counts as "stuck"


def daily_series(state, risk, fixed=False):
    """Per server day of the replay: (day return, worst intraday dip, traded,
    reference gap) -- fractions of the equity the day opened with. The gap is
    how far the day's daily-loss reference sits ABOVE that equity: FundedNext
    measures the $300 from the closed balance at the rollover, so a position
    carried overnight at a loss has already used part of it (gap > 0 puts
    the floor closer), and one carried in profit has not raised it (gap < 0).

    `fixed`: every sleeve sized off at most the challenge's own $6,000 and every
    figure a fraction of $6,000 -- what one fresh challenge sees. Without it the
    long replay compounds, its minimum-lot positions shrink against the grown
    balance and the daily dips read far too small (2026-10-17: Monte Carlo 100%
    pass against 62.6% from the real 2026 start days, 55 of them daily fails)."""
    if fixed:
        state = {**state, "sizing_cap": {key: ACCOUNT for key in state["logs"]}}
    book = run_book(state, risk, state["start"], state["end"])
    if fixed:
        def frac(value, opened):
            return (value - opened) / ACCOUNT
    else:
        def frac(value, opened):
            return value / opened - 1
    traded = {server_day(p["entry_ts"]) for p in book["settled"]}
    closes = sorted(book["curve"])
    lows = worst_marks(state, book) if state.get("intrabar") else book["marked"]
    days, prev, balance, ci = [], ACCOUNT, ACCOUNT, 0
    current, opened, low, close, gap = None, None, None, None, 0.0
    for (stamp, marked), (_, worst) in zip(book["marked"], lows):
        day = server_day(stamp)
        if day != current:
            if current is not None:
                days.append((frac(close, opened), frac(min(low, opened), opened),
                             current in traded, gap))
                prev = close
            # closed balance as the day opens (exits up to the rollover)
            while ci < len(closes) and closes[ci][0] < day_start(day):
                balance = closes[ci][1]
                ci += 1
            current, opened, low = day, prev, worst
            gap = frac(balance, opened)
        low = min(low, worst)
        close = marked
    if current is not None:
        days.append((frac(close, opened), frac(min(low, opened), opened),
                     current in traded, gap))
    return days


def _phase(days, rng, target):
    """One phase on resampled blocks. Returns (outcome, trading days used, the
    phase's worst day below its daily-loss reference, its deepest point below
    $6,000), dollars."""
    equity, used, traded, worst_day, deepest = ACCOUNT, 0, 0, 0.0, 0.0
    goal, floor = ACCOUNT + target, ACCOUNT - MAX_LOSS
    n = len(days)
    while used < MC_MAX_DAYS:
        start = rng.randrange(0, n - MC_BLOCK_DAYS)
        for ret, dip, did, gap in days[start:start + MC_BLOCK_DAYS]:
            used += 1
            low = equity * (1 + dip)
            ref = equity * (1 + gap)
            worst_day, deepest = max(worst_day, ref - low), max(deepest, ACCOUNT - low)
            if low <= floor:
                return "max", used, worst_day, deepest
            if low <= ref - DAILY_LOSS:
                return "daily", used, worst_day, deepest
            equity *= 1 + ret
            traded += did
            if equity <= floor:
                return "max", used, worst_day, deepest
            if equity >= goal and traded >= MIN_DAYS:
                return "pass", used, worst_day, deepest
    return "stuck", used, worst_day, deepest


def mc_paths(days, paths, seed):
    """`paths` challenges (both phases) on resampled day blocks:
    [(outcome, trading days used, worst day $, deepest $)] per path."""
    import random
    rng = random.Random(seed)
    out = []
    for _ in range(paths):
        total, worst_day, deepest = 0, 0.0, 0.0
        for target in PHASE_TARGETS:
            outcome, used, day, deep = _phase(days, rng, target)
            total, worst_day, deepest = total + used, max(worst_day, day), max(deepest, deep)
            if outcome != "pass":
                break
        out.append((outcome, total, worst_day, deepest))
    return out


#: Trading-day horizons for "passed within N days".
MC_WITHIN = (20, 40, 60)


def mc_stats(rows):
    """The Monte Carlo summary of `mc_paths` rows. Drawdowns are over PASSING
    paths only (a failed path sits at its limit by definition; its rate is the
    daily / max columns), p50 / p95. `expected_days`: trading days until a pass
    when every failed attempt is restarted at once -- mean days per attempt over
    the pass rate."""
    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(q * len(values)))] if values else None

    n = len(rows)
    won = [r for r in rows if r[0] == "pass"]
    p = len(won) / n
    days = [r[1] for r in won]
    return {**{k: round(100 * sum(r[0] == k for r in rows) / n, 1)
               for k in ("pass", "daily", "max", "stuck")},
            "paths": n,
            # binomial standard error of the pass rate, in percentage points
            "pass_se": round(100 * (p * (1 - p) / n) ** 0.5, 2),
            "median_trading_days": pct(days, 0.5), "p90_trading_days": pct(days, 0.9),
            "within": {d: round(100 * sum(x <= d for x in days) / n, 1) for d in MC_WITHIN},
            "expected_days": round(sum(r[1] for r in rows) / n / p, 1) if p else None,
            **{f"{name}_{q}": (round(pct([r[i] for r in won], q / 100)) if won else None)
               for name, i in (("worst_day", 2), ("deepest", 3)) for q in (50, 95)}}


def _mc_cell(job):
    days, risk, paths, seed = job
    return {"risk": risk, **mc_stats(mc_paths(days, paths, seed))}


#: `fundednext_report`'s windows, for the oct-13 live book.
WINDOW_SPEC = {"24-25": fn.WINDOWS[0], "26": fn.WINDOWS[1], "24-26": fn.WINDOWS[2]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("detail",))
    parser.add_argument("--risk", type=float, default=DEFAULT_RISKS[0])
    args = parser.parse_args()
    detail(args.risk)


if __name__ == "__main__":
    main()
