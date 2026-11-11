"""The5ers High Stakes: every account rule, and a judge for a replayed book.

READ THIS FIRST IF YOU ARE AN AGENT: terminal output does not reach the user;
paste results into the reply.

WHAT THIS IS. `new_cfd_families --broker the5ers` searches families priced at
The5ers (spread off its own 1m table, commission, swap, daily break, leverage
-- `cfd_families.BROKERS['the5ers']`). Those are per-trade costs. The rules
below are per-ACCOUNT and judge a whole book, like `fundednext_challenge.judge`
does for FundedNext, on the same book format (`exness_combined_strategies.replay`
output: `curve` closed-balance steps, `marked` mark-to-market equity, `settled`
trades). Sources, read 2026-11-07: the5ers.com/high-stakes, its FAQ "general
rules for the High Stakes program" and "is news trading allowed in the High
Stakes program", the account itself (HS1-5K, 26823080, FivePercentOnline-Real).

THE RULES ($5,000, every number a fraction of the initial balance):

  step 1 target    +10%   ($500)        step 2 target   +5% ($250), a fresh $5,000
  Pro scale-up     +10%   on each Pro balance, then the account grows
  daily loss       5%     of the HIGHER of balance and equity at the server
                          rollover (00:00 server = 17:00 New York); breach =
                          terminated. Unlike FundedNext's balance-only
                          reference, open profit carried over the rollover
                          RAISES the floor here ([[fundednext-daily-loss-is-balance-based]]).
  max loss         10%    STATIC: equity may never touch $4,500
  profitable days  3      per step and per Pro scale-up. A day counts when
                          min(midnight balance, midnight equity) - previous
                          day's balance >= 0.5% of the initial ($25).
  time limit       none   but 30 days without activity expires an evaluation
                          account (counted from registration), 60 a Pro one
  news             no order may EXECUTE from 2 min before to 2 min after a
                          Forex Factory red-folder event in the related
                          currency or index (server clock). Opening only --
                          closes and SL/TP hits are fine. A breach is SOFT:
                          the trade's profit is deducted and does not count
                          toward the target; its loss stays.
  bracketing       banned (buy + sell pending orders around a release)
  hedging          the MT5 account is a hedge account; no rule found against it
  EAs              allowed when you own the source; HFT, tick scalping,
                          latency / reverse / hedge arbitrage, rollover
                          scalping, shared third-party EAs and excessive
                          server requests are not
  stop-loss        not required on High Stakes (it is on the Bootcamp)
  consistency      none found for High Stakes
  leverage         1:100 headline; measured FX 1:100, metals 1:25, indices
                          ~1:25, oil 1:5 (`cfd_families.BROKERS`)
  Pro payouts      every 14 days, 80% split to $150K (85/90/100% higher up),
                          crypto or RISE; caps $3,000 ($50K) / $4,000 ($100K)
                          per cycle, none listed at $5K; Pro is granted after
                          a review ("performance benchmarks, risk parameters")

    py -m sandbox.research.the5ers_rules selftest
    py -m sandbox.research.the5ers_rules fetch-news     # this week's red folders
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ACCOUNT = 5_000.0
#: Fractions of the initial balance (same at every size).
RULES = {
    "step_targets": (0.10, 0.05),
    "pro_target": 0.10,
    "daily_loss": 0.05,
    "max_loss": 0.10,
    "profitable_days": 3,
    "profitable_day_min": 0.005,
    "inactive_days_eval": 30,
    "inactive_days_pro": 60,
    "news_seconds": 120,
    "pro_split": 0.80,
    "payout_every_days": 14,
}

DAY = 86_400
#: Server clock = New York + 7, so the server day rolls at 17:00 New York.
#: Book stamps are New York wall clock stored as UTC (the repository rule).
SERVER_OFFSET = 7 * 3_600


def server_day(ts):
    return (int(ts) + SERVER_OFFSET) // DAY


def day_start(day):
    """NY-wall stamp at which server day `day` opens."""
    return day * DAY - SERVER_OFFSET


# --------------------------------------------------------------------------- #
# the news window
# --------------------------------------------------------------------------- #

NEWS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ff_calendar")
#: Forex Factory's own weekly export (the feed MT4/5 news EAs read).
FF_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

#: Which calendar currencies "relate" to a study symbol. FX pairs are their two
#: legs (read from the name). The rest are ASSUMED from the currency each one
#: is priced in -- the FAQ says only "the related currency or index" -- so ask
#: support before trusting the metals / index rows.
RELATED = {
    "xauusd": ("USD",), "xagusd": ("USD",), "xptusd": ("USD",), "xpdusd": ("USD",),
    "xaueur": ("EUR", "USD"), "ukoil": ("USD",), "usoil": ("USD",),
    "ustec": ("USD",), "us500": ("USD",), "us30": ("USD",),
    "de40": ("EUR",), "fr40": ("EUR",), "stoxx50": ("EUR",), "uk100": ("GBP",),
    "aus200": ("AUD",), "hk50": ("CNY", "HKD"),
    "btc": ("USD",), "ethusd": ("USD",), "ltcusd": ("USD",), "solusd": ("USD",),
    "xrpusd": ("USD",), "dogeusd": ("USD",), "btceur": ("EUR",), "etheur": ("EUR",),
}


def related_currencies(symbol):
    symbol = symbol.lower()
    if symbol in RELATED:
        return RELATED[symbol]
    if len(symbol) == 6 and symbol.isalpha():
        return (symbol[:3].upper(), symbol[3:].upper())
    return ()


def load_news(path=NEWS_DIR):
    """`[(ny_stamp, currency)]` of every red-folder event on disk, sorted.

    Reads every `*.json` in `path` in Forex Factory's export format
    (`[{title, country, date, impact, ...}]`, `date` ISO with offset). EMPTY
    when nothing has been collected: there is no 2024-2026 history in the
    repository yet, so a book judged without it books no news deductions --
    a floor on the cost, said out loud by `judge`'s `news_events` count."""
    ny = ZoneInfo("America/New_York")
    out = set()
    for name in sorted(glob.glob(os.path.join(path, "*.json"))):
        with open(name, encoding="utf-8") as handle:
            rows = json.load(handle)
        for row in rows:
            if str(row.get("impact", "")).lower() != "high":
                continue
            when = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
            wall = when.astimezone(ny).replace(tzinfo=timezone.utc)
            out.add((int(wall.timestamp()), str(row["country"]).upper()))
    return sorted(out)


def this_week_file(path=NEWS_DIR):
    """`path/<sunday>.json` for the current Forex Factory week (it starts on
    Sunday, New York)."""
    from datetime import timedelta  # noqa: PLC0415
    today = datetime.now(ZoneInfo("America/New_York")).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return os.path.join(path, f"{sunday:%Y-%m-%d}.json")


def fetch_news(path=NEWS_DIR, force=False):
    """Save this week's Forex Factory export to `path/<sunday>.json`. Run it
    weekly to build the history forward; it never reaches back in time.
    Skipped (no download) when this week's file is already on disk, unless
    `force`. Returns `(file, events, red folders, downloaded)`."""
    existing = this_week_file(path)
    if not force and os.path.exists(existing):
        with open(existing, encoding="utf-8") as handle:
            rows = json.load(handle)
        high = sum(str(r.get("impact", "")).lower() == "high" for r in rows)
        return existing, len(rows), high, False
    request = urllib.request.Request(FF_WEEK_URL, headers={"User-Agent": "quant-research"})
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.loads(response.read().decode("utf-8"))
    os.makedirs(path, exist_ok=True)
    first = min(datetime.fromisoformat(r["date"].replace("Z", "+00:00")) for r in rows)
    name = os.path.join(path, f"{first:%Y-%m-%d}.json")
    with open(name, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)
    high = sum(str(r.get("impact", "")).lower() == "high" for r in rows)
    return name, len(rows), high, True


FF_MONTH_URL = "https://www.forexfactory.com/calendar?month={mon}.{year}"
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _month_events(html):
    """Every event in one Forex Factory month page: the page embeds its own
    state as JSON (`window.calendarComponentStates[1] = {days: [...]}`), each
    event with `dateline` (UTC epoch), `currency`, `impactName`, `name`."""
    out, decoder, at = [], json.JSONDecoder(), 0
    while True:
        at = html.find("days: [", at)
        if at < 0:
            return out
        try:
            days, end = decoder.raw_decode(html, at + len("days: "))
        except ValueError:
            at += 1
            continue
        for day in days:
            for e in day.get("events", ()):
                if "dateline" in e and "currency" in e:
                    out.append(e)
        at = end


def fetch_history(start="2024-01", end=None, path=NEWS_DIR, pause=3.0):
    """Save Forex Factory's red folders month by month to
    `path/ffmonth_<yyyy-mm>.json`, in the weekly export's format
    ({title, country, date, impact}) so `load_news` reads both. Re-fetches the
    current month (it is still being filled); keeps earlier months on disk."""
    import time  # noqa: PLC0415

    end = end or datetime.now(timezone.utc).strftime("%Y-%m")
    year, month = map(int, start.split("-"))
    last = tuple(map(int, end.split("-")))
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    os.makedirs(path, exist_ok=True)
    report = []
    while (year, month) <= last:
        tag = f"{year:04d}-{month:02d}"
        name = os.path.join(path, f"ffmonth_{tag}.json")
        if os.path.exists(name) and tag != current:
            year, month = (year + month // 12, month % 12 + 1)
            continue
        url = FF_MONTH_URL.format(mon=_MONTHS[month - 1], year=year)
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130 Safari/537.36"})
        with urllib.request.urlopen(request, timeout=60) as response:
            html = response.read().decode("cp1252", errors="ignore")
        seen, rows = set(), []
        for e in _month_events(html):
            when = datetime.fromtimestamp(int(e["dateline"]), tz=timezone.utc)
            if (when.year, when.month) != (year, month) or e["id"] in seen:
                continue
            seen.add(e["id"])
            rows.append({"title": e.get("name", ""), "country": e["currency"],
                         "date": when.isoformat(),
                         "impact": {"high": "High", "medium": "Medium", "low": "Low",
                                    "holiday": "Holiday"}.get(e.get("impactName"),
                                                              str(e.get("impactName")))})
        if not rows:
            raise SystemExit(f"{url}: no events parsed -- the page format changed?")
        with open(name, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=0)
        high = sum(r["impact"] == "High" for r in rows)
        report.append((tag, len(rows), high))
        print(f"  {tag}: {len(rows)} events, {high} red folder", flush=True)
        year, month = (year + month // 12, month % 12 + 1)
        time.sleep(pause)
    return report


def in_news_window(symbol, ts, events, seconds=RULES["news_seconds"]):
    """Whether an order on `symbol` executing at NY-wall `ts` falls inside a
    related red-folder event's +-`seconds` window. `events` from `load_news`."""
    import bisect  # noqa: PLC0415

    legs = set(related_currencies(symbol))
    if not legs or not events:
        return False
    lo = bisect.bisect_left(events, (int(ts) - seconds, ""))
    for stamp, currency in events[lo:]:
        if stamp > ts + seconds:
            break
        if currency in legs:
            return True
    return False


def news_deduction(settled, events):
    """Dollars The5ers would deduct: the profit of every winning trade whose
    ENTRY executed inside a related news window (losses are kept, closes are
    free). Returns `(total, [trade, ...])`."""
    hit = [t for t in settled
           if t.get("pnl", 0.0) > 0 and in_news_window(t["symbol"], t["entry_ts"], events)]
    return sum(t["pnl"] for t in hit), hit


# --------------------------------------------------------------------------- #
# the judge
# --------------------------------------------------------------------------- #

def judge(book, target, account=ACCOUNT, events=None):
    """Walk one step's replay against The5ers' rules.

    `book` is a replay over the step (`curve` = [(ts, closed balance)],
    `marked` = [(ts, equity)], `settled` = trades with `entry_ts`, `exit_ts`,
    `pnl`, `symbol`). `target` is the step's fraction (0.10 / 0.05 / Pro 0.10).
    Breaches are checked on the marks the book carries -- a spike inside a bar
    is invisible, so the breach rate is a floor unless the marks are worst-case.

    News: winning trades opened in a related window lose their profit from
    the balance AND the target (`news_deduction`), applied from their exit on.

    Returns `{outcome, at, profitable_days, worst_daily, worst_total,
    news_deducted, news_events}`; outcome is pass / daily / max / open.
    """
    events = events or []
    deducted, hits = news_deduction(book["settled"], events)
    # a deduction lands when its trade closes
    cuts = sorted((t["exit_ts"], t["pnl"]) for t in hits)
    closes = sorted(book["curve"])
    balance, ci, cut_i, cut = account, 0, 0, 0.0
    floor = account * (1 - RULES["max_loss"])
    goal = account * (1 + target)
    day, ref, prev_balance = None, account, account
    profitable, worst_daily, worst_total = 0, 0.0, 0.0
    last_marked = account
    for stamp, marked in book["marked"]:
        while ci < len(closes) and closes[ci][0] <= stamp:
            balance = closes[ci][1]
            ci += 1
        while cut_i < len(cuts) and cuts[cut_i][0] <= stamp:
            cut += cuts[cut_i][1]
            cut_i += 1
        bal, eq = balance - cut, marked - cut
        today = server_day(stamp)
        if today != day:
            if day is not None:
                # the day that just ended: min(midnight balance, equity) - the
                # balance it opened with (the5ers.com/high-stakes wording)
                if min(prev_close_bal, last_marked) - prev_balance >= \
                        RULES["profitable_day_min"] * account:
                    profitable += 1
                prev_balance = prev_close_bal
            # the HIGHER of balance and equity at the rollover
            day, ref = today, max(bal, last_marked if day is not None else bal)
        prev_close_bal, last_marked = bal, eq
        worst_daily = max(worst_daily, ref - eq)
        worst_total = max(worst_total, account - eq)
        if eq <= floor:
            return _out("max", stamp, profitable, worst_daily, worst_total, deducted, events)
        if eq <= ref - RULES["daily_loss"] * account:
            return _out("daily", stamp, profitable, worst_daily, worst_total, deducted, events)
        if bal >= goal and eq >= goal and profitable >= RULES["profitable_days"]:
            return _out("pass", stamp, profitable, worst_daily, worst_total, deducted, events)
    return _out("open", None, profitable, worst_daily, worst_total, deducted, events)


def _out(outcome, at, days, wd, wt, deducted, events):
    return {"outcome": outcome, "at": at, "profitable_days": days,
            "worst_daily": round(wd, 2), "worst_total": round(wt, 2),
            "news_deducted": round(deducted, 2), "news_events": len(events)}


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #

def _book(points, trades=()):
    """A book from [(ny_ts, closed_balance, equity)]."""
    return {"curve": [(t, b) for t, b, _ in points],
            "marked": [(t, e) for t, _, e in points], "settled": list(trades)}


def selftest():
    h = 3_600
    d0 = day_start(20_000) + h          # 18:00 NY, one hour into a server day
    D = DAY
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  {'ok ' if good else 'BAD'} {name}: {got} (want {want})")

    # 1. three +$200 days: a day counts at the midnight that closes it, so the
    #    pass lands on the first mark of day 4
    pts, bal = [], ACCOUNT
    for k in range(3):
        bal += 200
        pts.append((d0 + k * D, bal, bal))
    check("3 profitable days not yet closed -> open", judge(_book(pts), 0.10)["outcome"], "open")
    pts.append((d0 + 3 * D, bal, bal))
    got = judge(_book(pts), 0.10)
    check("pass after 3 profitable days", (got["outcome"], got["profitable_days"]), ("pass", 3))
    # 2. +$500 on day 1 alone: target met but 1 profitable day -> open
    check("target with 1 profitable day stays open",
          judge(_book([(d0, 5500, 5500), (d0 + h, 5500, 5500)]), 0.10)["outcome"], "open")
    # 3. open profit raises the floor: day 1 ends with equity 5,300 on a 5,000
    #    balance; day 2 equity falls to 5,040 -> 5,300 - 5,040 = 260 > 250
    pts = [(d0, 5000, 5300), (d0 + D, 5000, 5040)]
    check("daily floor from equity at rollover", judge(_book(pts), 0.10)["outcome"], "daily")
    #    ...the same path under a balance-only reference would survive
    #    (5,000 - 5,040 < 0): this is the rule that differs from FundedNext.
    # 4. static max loss: equity 4,500 -> max (daily reference 5,000 would
    #    also be 500 down, the max check comes first)
    check("max loss at $4,500", judge(_book([(d0, 5000, 4500)]), 0.10)["outcome"], "max")
    # 5. news: a +$300 winner opened 60 s after a USD red folder is deducted
    t_news = d0 + 2 * h
    events = [(t_news, "USD")]
    trade = {"symbol": "xauusd", "entry_ts": t_news + 60, "exit_ts": t_news + h, "pnl": 300.0}
    got, _ = news_deduction([trade], events)
    check("news deducts a winner opened in the window", got, 300.0)
    trade2 = dict(trade, entry_ts=t_news + 180)
    check("news ignores an entry 3 min after", news_deduction([trade2], events)[0], 0)
    check("news ignores an unrelated currency", in_news_window("eurgbp", t_news, events), False)
    print("selftest", "PASSED" if ok else "FAILED")
    return ok


def main():
    parser = argparse.ArgumentParser(description="The5ers High Stakes rules")
    parser.add_argument("command", choices=("selftest", "fetch-news", "fetch-history", "news"))
    parser.add_argument("--start", default="2024-01")
    parser.add_argument("--end")
    parser.add_argument("--force", action="store_true", help="fetch-news: download even if this week is on disk")
    args = parser.parse_args()
    if args.command == "selftest":
        raise SystemExit(0 if selftest() else 1)
    if args.command == "fetch-history":
        fetch_history(args.start, args.end)
        print(f"{len(load_news())} red-folder events on disk")
        return
    if args.command == "fetch-news":
        name, n, high, downloaded = fetch_news(force=args.force)
        verb = "wrote" if downloaded else "already have (no download; --force to refresh)"
        print(f"{verb} {name}: {n} events, {high} red folder")
        return
    events = load_news()
    print(f"{len(events)} red-folder events on disk in {os.path.abspath(NEWS_DIR)}")


if __name__ == "__main__":
    main()
