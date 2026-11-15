"""The The5ers combined book, judged ONLY on one $5,000 High Stakes challenge.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE: terminal output does
NOT reach the user -- paste the tables into the reply.

WHAT IT IS. `fundednext_combined_strategies`' canon pipeline (logs -> build ->
test -> final), rebuilt for The5ers High Stakes 2-step $5K: The5ers prices
(`cfd_families.BROKERS['the5ers']`: its spread table, commission, swap, break
hours, per-symbol leverage) and The5ers' account rules (`the5ers_rules`) in
the challenge Monte Carlo that picks and scores the book. Self-contained: the
FundedNext module pins `CFD_BROKER=fundednext` at import, so nothing of it is
imported here -- its pipeline is copied and every FundedNext rule swapped.

THE RULE IS FROZEN BEFORE 2026 IS SEEN, as FundedNext's: members and dial are
chosen on 2024-2025, scored once on 2026, then the SAME rule re-picks on
2024-2026 for the book that trades.

THE POOL. `results/filtered/leveraged_the5ers/` (1,323 cells: the FundedNext
filtered pool replayed on The5ers plus the The5ers-only symbol search, each with
a leverage ladder set on its 2024-2025 drawdown, `_replay_the5ers T5_MODE=lev`).
It was screened on 2026, so the 2026 test prints NOT CLEAN. Never es / nq /
jp225; nth25 / swi20 / us2000 are not quoted at The5ers.

THE CHALLENGE (`the5ers_rules.RULES`, every figure a fraction of $5,000):

  step 1 target     +10% ($500)       step 2 target  +5% ($250), a fresh $5,000
  daily loss        5% ($250) under the HIGHER of balance and equity at the
                    00:00 server rollover (17:00 NY): open profit carried over
                    the rollover RAISES the floor (FundedNext: balance only)
  max loss          10% STATIC: equity may never touch $4,500
  profitable days   3 per step; a server day counts when
                    min(midnight balance, midnight equity) - the previous
                    midnight's balance >= 0.5% ($25)
  pass              closed balance AND equity at the target, 3 profitable days
  time limit        none; 30 calendar days without a new trade = expired
                    ("inactive" in the tables)
  news              a WINNING trade opened +-2 min of a related Forex Factory
                    red folder loses its profit (balance and target); losses
                    stay. Applied from `data/ff_calendar` -- which holds no
                    2024-2026 history yet, so today it deducts nothing; the
                    tables print the event count so that floor is visible.
  leverage          the replay's margin ceiling per symbol at the broker's own
                    (FX 1:100, metals/indices 1:25, oil 1:5, crypto 1:2) or
                    the 4x book ceiling, whichever is tighter (`_size`)

  Loss limits are judged with every open position at the worst price its 30m
  bar touched (`worst_marks`), so a spike inside a bar breaks them as live
  would. A path ends on a pass of step 2 or any breach. A book with no edge
  still passes some share (a race to +10% before -10%) -- the line to clear.

THE BAN LAYER AND THE FUNDEDNEXT LESSONS (pre-registered 2026-11-08,
`results/the5ers_book_prereg.md`; constants under "PRE-REGISTERED" below).
The prohibited-practices page (2026-11-03) bans bulk trading, opposing
positions on correlated instruments, overexposure, rollover scalping and news
moves. The book refuses entries/defers exits in news windows (`_news_guard`,
at log time, from Forex Factory history), and `comply` skips entries that
would break the rest, inside every replay and Monte Carlo path. `compliance`
prints the check for each window. The selection drops the top third of the
pool by cost_R, caps sleeves per symbol/family, caps trade risk at 1%, and
picks the dial on a half-dead, half-edge Monte Carlo.

    py -m sandbox.research.the5ers_combined_strategies selftest
    py -m sandbox.research.the5ers_combined_strategies logs [--workers N] [--fresh]
    py -m sandbox.research.the5ers_combined_strategies build [--paths 5000]
    py -m sandbox.research.the5ers_combined_strategies test | final
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import pickle
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

# BEFORE ANY `cfd_families` IMPORT: broker, split, costs and the search
# protocol's env bind at import (as `_replay_the5ers`).
if os.environ.get("CFD_BROKER", "the5ers") != "the5ers":
    raise SystemExit(f"CFD_BROKER={os.environ['CFD_BROKER']}; this module prices The5ers only")
os.environ["NCF_BROKER"] = "the5ers"
os.environ["CFD_BROKER"] = "the5ers"
os.environ.setdefault("CFD_SPLIT", "2024-2025")
os.environ.setdefault("CFD_OOS_END", os.environ.get("T5_DATA_END", "2026-11-07"))
_argv = sys.argv
sys.argv = [sys.argv[0], "--broker", "the5ers"]
from sandbox.research import new_cfd_families as ncf  # noqa: E402
sys.argv = _argv
for _k, _v in ncf.ENV.items():
    os.environ.setdefault(_k, _v)

from sandbox.research import cfd_families as ef  # noqa: E402
from sandbox.research import the5ers_rules as rules  # noqa: E402
from sandbox.research import twohost  # noqa: E402

if os.environ.get("FN_TT") == "1":
    import platform
    platform._wmi = None
    from sandbox.research import cfd_tt_families  # noqa: E402,F401
    os.environ["CFD_BROKER"] = "the5ers"

if ef.BROKER != "the5ers":
    raise SystemExit("cfd_families was imported for another broker earlier in "
                     "this process; run this module in a fresh interpreter")

_SPLIT_WINDOW = (ef.IS_END, ef.OOS_END)
from sandbox.research import exness_combined_strategies as base  # noqa: E402
from sandbox.research import exness_combined_montecarlo as mc  # noqa: E402

# The Exness module moves `ef.OOS_END` to its own data end at import.
ef.IS_END, ef.OOS_END = _SPLIT_WINDOW
base._validate_es_transition = lambda: None

RESULTS = ef.RESULTS
#: `T5_POOL_DIR` / `T5_OUT_DIR` point a dry run at a few cells and keep every
#: file it writes out of the real ones.
POOL_DIR = (os.environ.get("T5_POOL_DIR")
            or os.path.join(RESULTS, "filtered", "leveraged_the5ers"))
_CACHE = os.environ.get("T5_OUT_DIR") or base.CACHE_DIR
_OUT = os.environ.get("T5_OUT_DIR") or RESULTS
GROUP_DIR = os.path.join(
    _CACHE, f"the5ers_pool_{os.path.basename(os.path.normpath(POOL_DIR))}")
SUMMARY = os.path.join(GROUP_DIR, "summary.pkl")
IS_STATE = os.path.join(_CACHE, "the5ers_challenge_is.pkl")
FINAL_STATE = os.path.join(_CACHE, "the5ers_challenge_final.pkl")
SEAL_PATH = os.path.join(_OUT, "the5ers_challenge_seal.json")
TEST_LOG = os.path.join(_OUT, "the5ers_challenge_tests.json")
FINAL_PATH = os.path.join(_OUT, "the5ers_challenge_final.json")
LOG_START = "2024-01-01"
#: Fixed before any run, as FundedNext's.
RISKS = (0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2)
SIZES = (1, 2, 3, 5, 8, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 150, 200,
         250, 300, 400, 500, 700, 1000)
PASS_TOLERANCE = 1.0
DUD_SHARE = 0.5
DRAWS = 10
LEV_CAP = 5.0
GATE_RHO = 0.50
GATE_LIFT = 1.60
WARMUP_DAYS = 180

# --------------------------------------------------------------------------- #
# PRE-REGISTERED 2026-11-08, before any book was built on this pool
# (`results/the5ers_book_prereg.md`). Two groups: what keeps the account inside
# The5ers' Terms (the ban layer), and what the FundedNext live book taught.
# --------------------------------------------------------------------------- #

#: --- BAN LAYER (the5ers.com/faqs/prohibited-trading-practices, 2026-11-03) ---
#: News: no order of any kind (open OR close -- sources disagree, so the
#: strict reading) executes within +-2 min of a related red folder. Entries
#: there are refused at the fill candle (the strategy may signal again);
#: exits and session flattens there are DEFERRED to the window's end plus
#: `NEWS_DEFER_SECONDS` and priced on The5ers' own minute bars.
NEWS_GUARD_SECONDS = rules.RULES["news_seconds"]
NEWS_DEFER_SECONDS = 5
#: "Bulk trading ... open multiple trades at the same time": no two entries
#: within this many seconds anywhere in the book (the later one is skipped).
BULK_GAP_SECONDS = 60
#: "multiple trades open simultaneously" / overexposure: at most this many
#: positions open at once, and never two on one instrument (no stacking, no
#: same-instrument hedge).
MAX_OPEN = 5
ONE_PER_INSTRUMENT = True
#: "holding opposing positions on the same or highly correlated instruments":
#: no entry against an open position in its group (sign = how the instrument
#: moves with the group), and at most `GROUP_MAX_SAME` open in one group --
#: "repeatedly concentrating risk in ... correlated instruments".
CORRELATED = (
    {"us500": 1, "ustec": 1, "us30": 1, "us2000": 1},
    {"de40": 1, "fr40": 1, "stoxx50": 1, "uk100": 1},
    {"hk50": 1, "aus200": 1, "jp225": 1},
    {"xauusd": 1, "xagusd": 1, "xaueur": 1, "xptusd": 1},
    {"ukoil": 1, "usoil": 1},
    {"btc": 1, "ethusd": 1, "ltcusd": 1, "solusd": 1, "xrpusd": 1, "dogeusd": 1,
     "btceur": 1, "etheur": 1},
    {"eurusd": 1, "gbpusd": 1, "usdchf": -1},
    {"audusd": 1, "nzdusd": 1},
    {"usdjpy": 1, "eurjpy": 1, "gbpjpy": 1, "audjpy": 1, "nzdjpy": 1, "cadjpy": 1,
     "chfjpy": 1},
)
GROUP_MAX_SAME = 2
#: "EAs that scalp during the rollover": no entry in these NY minutes.
ROLLOVER_BLOCK = (16 * 60 + 45, 18 * 60 + 15)
#: The user, 2026-11-08: "i dont want to trades on weekend" -- no entry from
#: Friday 17:00 to Sunday 17:00 NY (crypto only; everything else is shut then).
NO_WEEKEND = True


def in_weekend(ts):
    """Real NY `ts` in Friday 17:00 .. Sunday 17:00."""
    weekday = (ts // 86_400 + 3) % 7          # 1970-01-01 was a Thursday; Monday = 0
    minute = (ts // 60) % 1_440
    return (weekday == 4 and minute >= 17 * 60) or weekday == 5 or (weekday == 6 and minute < 17 * 60)
#: Overleveraging / "position sizes substantially larger than typical": every
#: trade risks at most this share of the $5,000 at its stop, enforced on the
#: final lot (`_size`), whatever the dial, leverage and vol throttle ask.
MAX_TRADE_RISK = 0.01
#: AMENDED 2026-11-08 (amendment 2, the user asked for FundedNext's speed): the
#: vol throttle is FundedNext's again -- it may size up to 3x on a calm market
#: (was capped at 1x); the hard 1% cap above still bounds every trade.
base.VOL_MAX_MULTIPLIER = 3.0

#: --- LESSONS OF THE FUNDEDNEXT LIVE BOOK ---
#: 1. Cost: drop the top third of the pool by cost_R (S1 of
#:    `fundednext_cost_screen_prereg.md`, the one pre-registered screen that
#:    beat random picks on unseen data).
COST_R_DROP_SHARE = 1 / 3
COST_R_MIN_TRADES = 20
#: 2. Minimum-lot pinning (38% of FN canon trades): a sleeve whose minimum lot
#:    risks more than 1.25x what the dial asks is out (FN: 1.5x).
OVERSHOOT_MAX = 1.25
#: 3. Concentration (FN: one sleeve 22% of risk, gold 33%): at most 2 sleeves
#:    per symbol and 3 per family.
MAX_PER_SYMBOL = 2
MAX_PER_FAMILY = 3
#: 4. Edge decay (search keeps 2-4% of in-sample edge; FN live sat at the 47th
#:    percentile of the 50%-edge Monte Carlo): the dial is chosen on the
#:    Monte Carlo where half the symbols have no edge AND every surviving
#:    sleeve keeps half of its own, not on the full backtest edge.
DIAL_ALIVE = 0.5
#: AMENDED 2026-11-08 after the first 2026 test (seal eaba5e0dbb31, 50 @ 0.1:
#: 53 server days at full edge vs FundedNext's ~28): the user asked for
#: FundedNext's speed, so the dial is back on FundedNext's own Monte Carlo --
#: half the symbols dead, every other sleeve at its FULL edge (was 0.5).
DIAL_KEEP = 1.0
#: 5. Mixed clocks: RTH sleeves are stamped New York, every other window on
#:    New York + 7 (`cfd_families.BROKER_DAY_SHIFT`), and the FN book replayed
#:    them on one timeline 7 h apart. Here every stamp is moved to real New
#:    York (`load_state`) before the book, the day split and the rules see it.

ACCOUNT = rules.ACCOUNT                     # $5,000
STEP_TARGETS = rules.RULES["step_targets"]  # (0.10, 0.05)
DAILY_LOSS = rules.RULES["daily_loss"]
MAX_LOSS = rules.RULES["max_loss"]
PROFITABLE_DAYS = rules.RULES["profitable_days"]
PROFITABLE_DAY_MIN = rules.RULES["profitable_day_min"]
INACTIVE_DAYS = rules.RULES["inactive_days_eval"]
server_day, day_start, DAY = rules.server_day, rules.day_start, rules.DAY


def default_workers():
    """Half the cores on this Windows PC (the user's gaming PC); all on the Mac."""
    cpus = os.cpu_count() or 4
    return max(1, cpus // 2) if sys.platform == "win32" else cpus


#: es/nq: futures twins of us500/ustec; jp225: vendor != broker market;
#: nth25/swi20/us2000: not quoted at The5ers (`_replay_the5ers.SKIP`).
EXCLUDE_SYMBOLS = {"es", "nq", "jp225", "nth25", "swi20", "us2000"}

PROTOCOL_VARS = ("FN_TT", "EXNESS_BAR_LOOKBACKS", "EXNESS_WINDOW",
                 "EXNESS_IS_FIRST_YEAR", "EXNESS_TT_STOP_WIDEN",
                 "CFD_BROKER_HOURS", "EXNESS_FILLS_ONLY")


# --------------------------------------------------------------------------- #
# leverage: the broker's own margin per symbol in the book replay
# --------------------------------------------------------------------------- #

_BASE_SIZE = base.size


def _size(sleeve, equity, trade, ctx, *args, **kwargs):
    """`base.size` with the margin ceiling at the tighter of the 4x book ceiling
    and The5ers' leverage for the symbol (`ef.margin_fraction`): base.size reads
    the flat `ef.MARGIN_FRACTION`, which would let a 1:2 crypto or 1:5 oil
    sleeve hold 4x notional the broker refuses."""
    spec = ctx["cfg"]
    fraction = max(ef.margin_fraction(spec.get("asset_class", "other"), spec.get("symbol")),
                   ef.margin_fraction(spec.get("asset_class", "other"), ctx.get("symbol")))
    kept = ef.MARGIN_FRACTION
    ef.MARGIN_FRACTION = fraction
    try:
        lots, money = _BASE_SIZE(sleeve, equity, trade, ctx, *args, **kwargs)
    finally:
        ef.MARGIN_FRACTION = kept
    # the hard per-trade cap: never more than MAX_TRADE_RISK of min(equity,
    # $5,000) at the stop; the minimum lot stays only within OVERSHOOT_MAX of it
    distance = trade.get("distance", 0.0)
    if lots > 0 and distance > 0 and money > 0:
        cap = MAX_TRADE_RISK * min(equity, ACCOUNT)
        if lots * money * distance > cap + 1e-9:
            step, least = spec["volume_step"], spec["volume_min"]
            capped = math.floor(cap / (money * distance) / step + 1e-10) * step
            if capped + 1e-10 >= least:
                lots = round(capped, 8)
            elif least * money * distance <= OVERSHOOT_MAX * cap:
                lots = least
            else:
                lots = 0.0
    return lots, money


base.size = _size


# --------------------------------------------------------------------------- #
# the pool
# --------------------------------------------------------------------------- #

def _protocol(name, cell):
    """The env a cell was searched under (`fundednext_combined_strategies._protocol`)."""
    env = {k: str(v) for k, v in (cell.get("protocol_change") or {}).items()}
    if (cell.get("search") == "cfd_tt_families"
            or str(cell.get("study_wave", "")).startswith("tiktok")):
        env["FN_TT"] = "1"
    stem = cell["timeframe"]
    if "-barlb" in stem:
        env["EXNESS_BAR_LOOKBACKS"] = "1"
    if stem.endswith("-off"):
        env["EXNESS_WINDOW"] = "off"
    env.update(CFD_BROKER_HOURS="1", EXNESS_FILLS_ONLY="1")
    if cell.get("ncf_window") not in (None, "rth"):
        env["EXNESS_WINDOW"] = cell["ncf_window"]
    return tuple(sorted(env.items()))


def _here():
    return tuple(sorted((k, os.environ[k]) for k in PROTOCOL_VARS if k in os.environ))


def _tf_of(name, cell):
    return f"{cell['timeframe']}-{cell['window']}" if "ncf_window" in cell else cell["timeframe"]


def _alias(symbol):
    window = os.environ.get("EXNESS_WINDOW") or "rth"
    return symbol if window == "rth" else f"{symbol}~{window}"


def _key(row):
    return f"{row['symbol']}:{row['family']}"


def _stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def _label(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _lev(cell):
    """The highest whole ladder k whose 2024-2025 drawdown stays on target (1x
    when none does)."""
    lev = cell.get("leverage") or {}
    ok = [s["k"] for s in lev.get("ladder", [])
          if s["2024-2025"]["max_dd_pct"] <= lev["target_dd_pct"]]
    return float(max(ok)) if ok else 1.0


def pool_cells():
    """`[(name, cell)]`: every cell in `POOL_DIR` but `EXCLUDE_SYMBOLS`."""
    out = []
    for name in sorted(os.listdir(POOL_DIR)):
        if not name.endswith(".json") or name in ("SURVIVORS.json", "LEV.json"):
            continue
        with open(os.path.join(POOL_DIR, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        if "symbol" not in cell or cell["symbol"] in EXCLUDE_SYMBOLS:
            continue
        if "bar_minutes" not in cell:
            cell["bar_minutes"] = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[cell["timeframe"]]
            cell["ncf_window"] = cell["protocol"]["execution"]["window"]
        out.append((name, cell))
    return out


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #

def _days(log):
    """Per exit day: day, sum of points/entry, P&L, winning P&L, losing P&L, trades."""
    by = {}
    for t in log:
        day = t["exit_ts"] // base.DAY
        row = by.setdefault(day, [day, 0.0, 0.0, 0.0, 0.0, 0])
        row[1] += t["points"] / t["entry"]
        row[2] += t["pnl"]
        row[3 if t["pnl"] > 0 else 4] += abs(t["pnl"])
        row[5] += 1
    return np.array(sorted(by.values()), dtype=float).reshape(-1, 6)


def _group_path(symbol, bar, protocol=None):
    protocol = _here() if protocol is None else tuple(
        (k, v) for k, v in protocol if k in PROTOCOL_VARS)
    window = dict(protocol).get("EXNESS_WINDOW") or "rth"
    alias = symbol if window == "rth" else f"{symbol}~{window}"
    tag = hashlib.sha1(repr(protocol).encode()).hexdigest()[:8]
    return os.path.join(GROUP_DIR, f"{alias}_{bar}m_{tag}.pkl")


def _cells_path(path):
    return path[:-len(".pkl")] + ".cells"


def _news_tag():
    """A fingerprint of the red folders on disk: new history regenerates logs."""
    events = news_events()
    return hashlib.sha1(repr(events).encode()).hexdigest()[:8] if events else "none"


def _log_rule():
    """Bumped whenever the trade path changes, so cached group logs regenerate
    (the red-folder history is part of it)."""
    return ("rule:the5ers-break-flatten:fills-only:broker-hours:"
            f"news{NEWS_GUARD_SECONDS}+{NEWS_DEFER_SECONDS}-open-and-close:{_news_tag()}")


def _defer(at, events):
    """`(hit, when)`: an order meant to execute at NY-wall `at` and a sorted list
    of related red-folder stamps -> whether it falls inside a +-guard window,
    and when it may execute instead (the last overlapping window's end plus
    `NEWS_DEFER_SECONDS`; chained windows push it on)."""
    import bisect
    hit = False
    while True:
        i = bisect.bisect_left(events, at - NEWS_GUARD_SECONDS)
        if i >= len(events) or events[i] > at + NEWS_GUARD_SECONDS:
            return hit, at
        hit = True
        j = bisect.bisect_right(events, at + NEWS_GUARD_SECONDS) - 1
        at = events[j] + NEWS_GUARD_SECONDS + NEWS_DEFER_SECONDS


def _related_events(symbol):
    legs = set(rules.related_currencies(symbol))
    return sorted({t for t, c in news_events() if c in legs})


def _news_guard(symbol, bar):
    """Apply the news rule to `symbol`'s installed fill maps, in place, before
    any backtest reads them. Every map is keyed by the bar the order belongs
    to and executes a known delay later (`cfd_families.fill_maps`):

      entry    bar + feed lag + queue        -> in a window: removed, so
               `EXNESS_FILLS_ONLY` refuses the entry at that candle
      exit     bar + one bar + lag + queue   -> in a window: re-priced at the
      flatten  bar + bar - lead + queue         deferred time, as the fill model
                                                prices a delay (ratio of The5ers
                                                minute opens)

    Returns counts for the log line."""
    events = _related_events(symbol)
    out = {"events": len(events), "entries_refused": 0, "exits_deferred": 0,
           "exits_unpriced": 0}
    if not events:
        return out
    # the5ers' fill model re-exports the Exness readers; the minute cache is theirs
    from sandbox.research.fill_models import exness as lx
    stamps, opens, _ = lx._minutes(symbol)
    shift = int(ef.INSTRUMENTS[symbol].get("shift_hours") or 0) * 3_600
    queue = ef.BRIDGE_QUEUE_SECONDS
    lag = ef.feed_lag_seconds(symbol) + queue

    def price_at(t):
        i = int(np.searchsorted(stamps, t, side="right")) - 1
        return float(opens[i]) if i >= 0 and t - stamps[i] < 60 and opens[i] > 0 else None

    entries = ef.ENTRY_PRICE.get(symbol)
    if entries:
        for ts in list(entries):
            if _defer(ts - shift + lag, events)[0]:
                del entries[ts]
                out["entries_refused"] += 1
    for maps, delay in ((ef.EXIT_PRICE, bar * 60 + lag),
                        (ef.FLATTEN_PRICE,
                         max(0, bar * 60 - ef.flatten_lead_seconds(symbol)) + queue)):
        prices = maps.get(symbol)
        for ts, price in list((prices or {}).items()):
            at = ts - shift + delay
            hit, later = _defer(at, events)
            if not hit:
                continue
            a, b = price_at(at), price_at(later)
            if a and b:
                prices[ts] = price * b / a
                out["exits_deferred"] += 1
            else:
                out["exits_unpriced"] += 1
    lx.release_minutes()
    return out


def _write_cells(path, names):
    with open(_cells_path(path), "w", encoding="utf-8") as handle:
        handle.write("\n".join([_log_rule(), *sorted(names)]))


def _is_current(path, names):
    try:
        with open(_cells_path(path), encoding="utf-8") as handle:
            return (os.path.exists(path)
                    and handle.read() == "\n".join([_log_rule(), *sorted(names)]))
    except OSError:
        return False


def _cost_r(log, shift, lo, hi):
    """Median modelled cost per trade (spread + commission + financing) as a
    share of the stop, `(gross - points) / distance`, over trades closed in
    [lo, hi) -- 2025, as `fundednext_cost_screen_prereg.md` -- or over every
    trade before `hi` when 2025 has fewer than `COST_R_MIN_TRADES`. No P&L."""
    def costs(a):
        return sorted((t["gross"] - t["points"]) / t["distance"] for t in log
                      if t["distance"] > 0 and a <= t["exit_ts"] - shift < hi)
    got = costs(lo)
    if len(got) < COST_R_MIN_TRADES:
        got = costs(-2 ** 62)
    return float(got[len(got) // 2]) if got else None


def _log_group(job):
    """One (symbol, bar) group's logs on $5,000 at The5ers -> its own file."""
    (symbol, bar), items = job
    path = _group_path(symbol, bar)
    lo, hi = int(os.environ["T5_LOG_LO"]), int(os.environ["T5_LOG_HI"])
    alias = _alias(symbol)
    ef.BAR_MINUTES = bar
    try:
        ef.resolve(symbol, allow_stale=True)
        ef.prewarm(symbol, "validate", bar)
        if os.environ.get("FN_TT") == "1":
            bars, ctx = ef.context(symbol, "validate", bar)
        else:
            bars, ctx = ef.context(symbol, "validate", bar,
                                   {cell["family"] for _, cell in items})
    except (Exception, SystemExit) as error:  # noqa: BLE001
        print(f"  skipped -- {symbol} {bar}m: {error}", flush=True)
        return
    guard = _news_guard(symbol, bar)
    print(f"  news guard {symbol} {bar}m {_alias(symbol)}: {guard}", flush=True)
    shift = int(ctx["cfg"].get("shift_hours") or 0) * 3_600
    y2025, y2026 = _stamp("2025-01-01"), _stamp("2026-01-01")
    logs, summary = {}, {}
    for name, cell in items:
        key = f"{symbol}:{cell['family']}@{_tf_of(name, cell)}"
        try:
            result = ef.backtest(cell["family"], bars, ctx,
                                 ef.rehydrate(cell["params"]), lo=lo, hi=hi,
                                 initial=ACCOUNT, include_trades=True)
        except (Exception, SystemExit) as error:  # noqa: BLE001
            print(f"  skipped -- {name}: {type(error).__name__}: {error}", flush=True)
            continue
        log = []
        for trade in result["trade_log"]:
            trade = dict(trade)
            trade["symbol"], trade["sleeve"] = alias, key
            log.append(trade)
        logs[key] = log
        summary[key] = {"symbol": symbol, "family": key.split(":", 1)[1],
                        "alias": alias, "file": name, "k": _lev(cell),
                        "saw_2026": "out_of_sample" in cell,
                        "group": os.path.basename(path), "days": _days(log),
                        "cost_r": _cost_r(log, shift, y2025, y2026)}
    ef.BAR_MINUTES = 30
    marks = [b for b in ef.all_bars(symbol, "validate", 30)
             if b[ef.TS] < hi and (alias != symbol or ef.in_session(symbol, b[ef.TS]))]
    with open(path + ".tmp", "wb") as handle:
        pickle.dump({"summary": summary, "logs": logs, "bars": {alias: marks},
                     "ctx": {alias: {"cfg": ctx["cfg"], "symbol": symbol}}},
                    handle, protocol=5)
    os.replace(path + ".tmp", path)
    _write_cells(path, [name for name, _ in items])


def pool_logs(workers, lo, hi, fresh=False):
    """Every pool cell's trade log over [lo, hi); each missing (protocol,
    symbol, bar) group in its own child under the cell's protocol."""
    os.makedirs(GROUP_DIR, exist_ok=True)
    if fresh:
        for name in os.listdir(GROUP_DIR):
            os.remove(os.path.join(GROUP_DIR, name))
    groups = {}
    for name, cell in pool_cells():
        key = (_protocol(name, cell), cell["symbol"], cell["bar_minutes"])
        groups.setdefault(key, []).append(name)
    keys = sorted(groups, key=lambda k: (k[2], -len(groups[k]), k))
    todo = [k for k in keys if not _is_current(_group_path(k[1], k[2], k[0]), groups[k])]
    if twohost.IS_MAC and twohost.enabled():
        todo = todo[::-1]          # the Mac starts at the light (60m) end
    print(f"{sum(len(v) for v in groups.values())} cells in {len(keys)} groups; "
          f"{len(todo)} groups to run on {workers} workers", flush=True)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def unit(key):
        return twohost.unit_id("t5logs", os.path.basename(_group_path(key[1], key[2], key[0])),
                               _log_rule(), sorted(groups[key]))

    def run(key):
        protocol, symbol, bar = key
        if twohost.enabled() and not twohost.claim(unit(key)):
            return "other host"
        env = {k: v for k, v in os.environ.items() if k not in PROTOCOL_VARS}
        env.update(dict(protocol))
        env.update(PYTHONIOENCODING="utf-8", EXNESS_HOLD_SKIP="1",
                   T5_LOG_LO=str(lo), T5_LOG_HI=str(hi))
        done = subprocess.run([sys.executable, "-m",
                               "sandbox.research.the5ers_combined_strategies",
                               "_group", "--symbol", symbol, "--bar", str(bar)],
                              env=env, cwd=root)
        path = _group_path(symbol, bar, protocol)
        if twohost.enabled():
            result = {}
            if twohost.IS_MAC and os.path.exists(path):
                with open(path, "rb") as handle:
                    result = {"group": os.path.basename(path), "data": handle.read()}
            twohost.finish(unit(key), result)
        return "done" if os.path.exists(path) else f"failed (exit {done.returncode})"

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max(1, workers)) as threads:
        futures = {threads.submit(run, k): k for k in todo}
        for i, future in enumerate(as_completed(futures), 1):
            _, symbol, bar = key = futures[future]
            print(f"  {i}/{len(todo)} {os.path.basename(_group_path(symbol, bar, key[0]))}: "
                  f"{future.result()}", flush=True)
    if twohost.enabled():
        if twohost.IS_MAC:
            print("twohost: Mac side finished; the PC collects and writes the summary", flush=True)
            return
        results = twohost.collect(unit(k) for k in todo)
        for k in todo:
            got = results[unit(k)]
            if "data" in got:      # a group the Mac ran: install it here
                path = os.path.join(GROUP_DIR, got["group"])
                with open(path + ".tmp", "wb") as handle:
                    handle.write(got["data"])
                os.replace(path + ".tmp", path)
                _write_cells(path, groups[k])
    summary, missing = {}, []
    for k in keys:
        path = _group_path(k[1], k[2], k[0])
        if not os.path.exists(path):
            missing.append(os.path.basename(path))
            continue
        with open(path, "rb") as handle:
            summary.update(pickle.load(handle)["summary"])
    if missing:
        print(f"  {len(missing)} groups have no file (failed above): {missing}", flush=True)
    with open(SUMMARY, "wb") as handle:
        pickle.dump({"start": lo, "end": hi, "cells": summary, "pool": POOL_DIR,
                     "saw_2026": any(c["saw_2026"] for c in summary.values())},
                    handle, protocol=5)
    print(f"wrote {SUMMARY}: {len(summary)} cells, {_label(lo)}..{_label(hi)}")


def _one_group(symbol, bar):
    here = _here()
    items = [(name, cell) for name, cell in pool_cells()
             if _protocol(name, cell) == here
             and (cell["symbol"], cell["bar_minutes"]) == (symbol, bar)]
    _log_group(((symbol, bar), items))


# --------------------------------------------------------------------------- #
# the rule: members from trades closed before `hi`
# --------------------------------------------------------------------------- #

def _stats(days, k=1.0):
    if not len(days):
        return {"return": 0.0, "dd": 0.0, "trades": 0, "pf": None}
    days = days.copy()
    days[:, 2:5] *= k
    balance = ACCOUNT + np.cumsum(days[:, 2])
    peak = np.maximum.accumulate(np.maximum(balance, ACCOUNT))
    loss = days[:, 4].sum()
    return {"return": round(100 * float(days[:, 2].sum()) / ACCOUNT, 2),
            "dd": round(100 * float(((peak - balance) / peak).max()), 2),
            "trades": int(days[:, 5].sum()),
            "pf": round(float(days[:, 3].sum() / loss), 3) if loss else None}


class _Streams(dict):
    def __init__(self, days):
        super().__init__()
        self.days = days

    def __missing__(self, key):
        self[key] = {int(d): float(r) for d, r in self.days[key][:, :2]}
        return self[key]


def _rule(paths):
    return {"rank": "return on trades closed in the window",
            "max_members": None, "sizes": list(SIZES), "max_down_rho": GATE_RHO,
            "max_loss_lift": GATE_LIFT, "risks": list(RISKS),
            "paths": paths, "account": ACCOUNT, "challenge": "the5ers high stakes",
            "rules": dict(rules.RULES),
            "leverage": "highest whole ladder k with 2024-2025 dd on target",
            "lev_cap": LEV_CAP, "dud_share": DUD_SHARE, "draws": DRAWS, "dud_unit": "symbol",
            "overshoot_max": OVERSHOOT_MAX, "pass_tolerance": PASS_TOLERANCE,
            "dial_alive": DIAL_ALIVE, "dial_keep": DIAL_KEEP,
            "dial": "highest pass rate on the Monte Carlo where 1-dial_alive of the "
                    "symbols have no edge and the rest keep dial_keep of it, over the "
                    "sleeves that fit the minimum lot at each risk; within "
                    "pass_tolerance of it, the fewest expected days to a pass with "
                    "retries, then the higher pass rate, then the most members",
            "ban_layer": {"news_guard_s": NEWS_GUARD_SECONDS, "news_defer_s": NEWS_DEFER_SECONDS,
                          "news_orders": "open and close", "bulk_gap_s": BULK_GAP_SECONDS,
                          "max_open": MAX_OPEN, "one_per_instrument": ONE_PER_INSTRUMENT,
                          "correlated": [dict(g) for g in CORRELATED],
                          "group_max_same": GROUP_MAX_SAME,
                          "rollover_block": list(ROLLOVER_BLOCK), "no_weekend": NO_WEEKEND,
                          "max_trade_risk": MAX_TRADE_RISK,
                          "vol_max_multiplier": base.VOL_MAX_MULTIPLIER},
            "lessons": {"cost_r_drop_share": round(COST_R_DROP_SHARE, 6),
                        "cost_r_min_trades": COST_R_MIN_TRADES,
                        "max_per_symbol": MAX_PER_SYMBOL, "max_per_family": MAX_PER_FAMILY,
                        "clock": "every stamp to real New York before the book"}}


def _family(row):
    return row["family"].split("@", 1)[0]


def choose(cells, hi):
    """Every cell, best 2024-2025 return first, through: the cost screen (top
    third of the pool by cost_R out), the FundedNext admission gates, then the
    symbol / family caps. Returns (rows, chosen, refused {reason: n})."""
    rows, days = [], {}
    for key, cell in cells.items():
        if cell["symbol"] in EXCLUDE_SYMBOLS:
            continue
        days[key] = cell["days"][cell["days"][:, 0] * base.DAY < hi]
        k = min(cell["k"], LEV_CAP)
        rows.append({c: cell[c] for c in ("symbol", "family", "alias", "file", "group")}
                    | {"k": k, "cost_r": cell.get("cost_r")} | _stats(days[key], k))
    rows.sort(key=lambda r: -r["return"])
    known = sorted(r["cost_r"] for r in rows if r["cost_r"] is not None)
    cut = known[min(len(known) - 1, int(len(known) * (1 - COST_R_DROP_SHARE)))] if known else 0.0
    screened = [r for r in rows if r["cost_r"] is not None and r["cost_r"] <= cut]
    refused = {"cost_r": len(rows) - len(screened)}
    gated, rejected = base.choose_members(screened, _Streams(days), limit=len(screened),
                                          max_rho=GATE_RHO, max_lift=GATE_LIFT)
    refused["gates"] = len(rejected)
    chosen, per_symbol, per_family = [], {}, {}
    for r in gated:
        if per_symbol.get(r["symbol"], 0) >= MAX_PER_SYMBOL:
            refused["symbol_cap"] = refused.get("symbol_cap", 0) + 1
            continue
        if per_family.get(_family(r), 0) >= MAX_PER_FAMILY:
            refused["family_cap"] = refused.get("family_cap", 0) + 1
            continue
        per_symbol[r["symbol"]] = per_symbol.get(r["symbol"], 0) + 1
        per_family[_family(r)] = per_family.get(_family(r), 0) + 1
        chosen.append(r)
    refused["cost_r_cut"] = round(cut, 4)
    return rows, chosen, refused


def _series(m):
    return m["symbol"] if m["alias"] == m["symbol"] else f"{m['symbol']}~off"


def _bar_of(group):
    """The bar minutes in a group file name (`xauusd~off_30m_ab12cd34.pkl`)."""
    return int(group.rsplit("_", 2)[-2][:-1])


def load_state(members, lo, hi):
    """The members' trades and marking bars, every stamp moved to REAL New York
    wall clock (`cfg["shift_hours"]`: 7 for every non-RTH window, 6 for the
    shifted Asian cash sessions, 0 for an RTH cell), so all sleeves share one
    timeline and The5ers' server day (NY + 7) is cut at the true 17:00 NY."""
    by_group = {}
    for m in members:
        by_group.setdefault(m["group"], []).append(m)
    first = lo - WARMUP_DAYS * base.DAY
    logs, bars_by, ctx_by, minlot = {}, {}, {}, {}
    for group, ms in by_group.items():
        with open(os.path.join(GROUP_DIR, group), "rb") as handle:
            got = pickle.load(handle)
        for m in ms:
            series = _series(m)
            spec = got["ctx"][m["alias"]]["cfg"]
            shift = int(spec.get("shift_hours") or 0) * 3_600
            hold = _bar_of(group) * 60
            logs[_key(m)] = [dict(t, symbol=series, market=m["symbol"],
                                  entry_ts=t["entry_ts"] - shift,
                                  exit_ts=t["exit_ts"] - shift, bar_seconds=hold)
                             for t in got["logs"][_key(m)] if t["exit_ts"] - shift < hi]
            floor = sorted(spec["volume_min"] * t["distance"] * spec["multiplier"]
                           for t in logs[_key(m)] if t["distance"] > 0)
            minlot[_key(m)] = floor[len(floor) // 2] if floor else 0.0
            if series not in bars_by:
                rows = []
                for b in got["bars"][m["alias"]]:
                    ts = b[ef.TS] - shift
                    if first <= ts < hi:
                        b = list(b)
                        b[ef.TS] = ts
                        rows.append(tuple(b))
                bars_by[series] = rows
                ctx_by[series] = got["ctx"][m["alias"]]
    return {"members": [dict(m, alias=_series(m), minlot_usd=round(minlot[_key(m)], 2))
                        for m in members], "logs": logs,
            "bars_by": bars_by, "ctx_by": ctx_by,
            "scale": {_key(m): m["k"] for m in members}, "start": lo, "end": hi,
            "intrabar": True}


_STATE = None


def _init(path):
    global _STATE
    with open(os.path.join(twohost.ROOT, path), "rb") as handle:
        _STATE = pickle.load(handle)


def _scale(k, risk):
    """A sleeve's replay scale at `risk`: its ladder k, cut so one trade never
    risks more than `MAX_TRADE_RISK` of the account at its stop."""
    return min(k, MAX_TRADE_RISK / (ef.RISK_FRACTION * risk))


def _asked(m, risk):
    """The dollars a sleeve's stop is asked to risk at `risk` (capped)."""
    return ACCOUNT * ef.RISK_FRACTION * _scale(m["k"], risk) * risk


def _fits(m, risk):
    return m["minlot_usd"] <= OVERSHOOT_MAX * _asked(m, risk)


def _prefix(state, n, risk=None):
    members = [m for m in state["members"] if risk is None or _fits(m, risk)][:n]
    keys = {_key(m) for m in members}
    aliases = {m["alias"] for m in members}
    return {**state, "members": members,
            "logs": {k: v for k, v in state["logs"].items() if k in keys},
            "bars_by": {a: v for a, v in state["bars_by"].items() if a in aliases},
            "ctx_by": {a: v for a, v in state["ctx_by"].items() if a in aliases},
            "scale": {k: v for k, v in state["scale"].items() if k in keys}}


def _dud(log, keep=0.0):
    r = [t["points"] / t["distance"] for t in log if t["distance"] > 0]
    cut = (1 - keep) * (sum(r) / len(r) if r else 0.0)
    return [dict(t, points=t["points"] - cut * t["distance"]) if t["distance"] > 0
            else t for t in log]


def _costed(log, cost_r):
    return [dict(t, points=t["points"] - cost_r * t["distance"]) if t["distance"] > 0
            else t for t in log]


# --------------------------------------------------------------------------- #
# The5ers challenge: the replay, its per-day series, and the Monte Carlo
# --------------------------------------------------------------------------- #

def _group_of(market):
    for i, group in enumerate(CORRELATED):
        if market in group:
            return i, group[market]
    return None, 0


_COMPLY = {}


def comply(state):
    """`(kept, counts)`: which trades the ban layer lets the book take --
    `kept` a set of (sleeve, entry_ts) -- walking every member's trades in
    entry order (ties: member order, best first). An entry is skipped when:

      rollover        it opens in `ROLLOVER_BLOCK` (NY minutes)
      bulk            another entry opened under `BULK_GAP_SECONDS` before it
      max_open        `MAX_OPEN` positions are already open
      same_instrument a position on the same instrument is open
      opposed         a position in its correlated group points the other way
      group_cap       `GROUP_MAX_SAME` positions are open in its group

    A position counts as open until its exit is EXECUTED: the exit fires on
    the bar `exit_ts` and fills one bar later (`fill_models.exness.
    exit_delay_seconds`), plus a minute for lag and any news deferral.
    Timing only -- a dud or costed copy of the logs keeps the same trades."""
    key = (tuple(_key(m) for m in state["members"]), state["start"], state["end"])
    if key in _COMPLY:
        return _COMPLY[key]
    order = {_key(m): i for i, m in enumerate(state["members"])}
    items = sorted(((t["entry_ts"], order[k], k, t) for k, log in state["logs"].items()
                    for t in log if state["start"] <= t["entry_ts"] < state["end"]),
                   key=lambda x: (x[0], x[1]))
    lo_block, hi_block = ROLLOVER_BLOCK
    held, last, kept = [], None, set()
    counts = {k: 0 for k in ("trades", "weekend", "rollover", "bulk", "max_open",
                             "same_instrument", "opposed", "group_cap", "kept")}
    for ts, _, k, t in items:
        counts["trades"] += 1
        held = [p for p in held if p[0] > ts]
        if NO_WEEKEND and in_weekend(ts):
            counts["weekend"] += 1
            continue
        if lo_block <= (ts // 60) % 1_440 < hi_block:
            counts["rollover"] += 1
            continue
        if last is not None and ts - last < BULK_GAP_SECONDS:
            counts["bulk"] += 1
            continue
        if len(held) >= MAX_OPEN:
            counts["max_open"] += 1
            continue
        market = t["market"]
        if ONE_PER_INSTRUMENT and any(p[1] == market for p in held):
            counts["same_instrument"] += 1
            continue
        group, sign = _group_of(market)
        direction = t["side"] * sign
        if group is not None:
            same = [p for p in held if p[2] == group]
            if any(p[3] != direction for p in same):
                counts["opposed"] += 1
                continue
            if len(same) >= GROUP_MAX_SAME:
                counts["group_cap"] += 1
                continue
        held.append((t["exit_ts"] + t.get("bar_seconds", 0) + 60, market, group, direction))
        last = ts
        kept.add((k, ts))
        counts["kept"] += 1
    if len(_COMPLY) > 64:
        _COMPLY.clear()
    _COMPLY[key] = (kept, counts)
    return kept, counts


def run_book(state, risk, lo, hi):
    """The book at `risk` after the ban layer: only the trades `comply` keeps,
    every sleeve's scale capped at `MAX_TRADE_RISK` a trade."""
    kept, _ = comply(state)
    logs = {k: [t for t in v if (k, t["entry_ts"]) in kept]
            for k, v in state["logs"].items()}
    scale = {k: _scale(v, risk) for k, v in (state.get("scale") or {}).items()}
    return base.replay(state["members"], logs, state["bars_by"], state["ctx_by"],
                       scale=scale,
                       sizing_cap=state.get("sizing_cap") or {}, risk_scale=risk,
                       gross_cap=None, fair_cap=False, initial=ACCOUNT,
                       lo=lo, hi=hi, force_minimum=True, marking_cache=mc._MARKING)


_EXTREMES = {}


def worst_marks(state, book):
    """`book["marked"]` with every open position at the worst price its 30m bar
    touched (`fundednext_challenge.worst_marks`, content-keyed cache)."""
    key = tuple(sorted((sym, len(bars), bars[0][ef.TS] if bars else 0,
                        bars[-1][ef.TS] if bars else 0)
                       for sym, bars in state["bars_by"].items()))
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
            if bar is None:
                continue
            price = bar[1] if q["side"] > 0 else bar[0]
            open_value += q["side"] * (price - q["entry"]) * q["lots"] * q["money_per_point"]
        out.append((stamp, min(marked, balance + open_value)))
    return out


_NEWS = None


def news_events():
    """Red-folder events on disk (`the5ers_rules.load_news`), read once."""
    global _NEWS
    if _NEWS is None:
        _NEWS = rules.load_news()
    return _NEWS


def _market(trade):
    """The study symbol of a trade (its series alias minus any `~window`)."""
    return trade.get("market") or trade["symbol"].split("~", 1)[0]


def daily_series(state, risk):
    """Per server day of one fixed-size replay (every sleeve sized off at most
    the challenge's $5,000; every figure a fraction of $5,000):

      ret       close equity - open equity
      dip       the day's worst equity (worst marks) - open equity, <= 0
      traded    an entry that day (activity)
      bal_open  closed balance at the rollover - open equity
      bal_close closed balance at the next rollover - close equity
      span      calendar days since the previous series day (inactivity clock)
      cut       news deduction booked that day (winning trades opened in a
                related red-folder window, at their exit)

    `bal_open` gives both the daily reference (max(balance, equity) at the
    rollover = open equity + max(bal_open, 0)) and the previous midnight's
    balance for the profitable-day test; `bal_close` gives the midnight
    balance that test compares."""
    state = {**state, "sizing_cap": {key: ACCOUNT for key in state["logs"]}}
    book = run_book(state, risk, state["start"], state["end"])
    traded = {server_day(p["entry_ts"]) for p in book["settled"]}
    cut_by = {}
    events = news_events()
    if events:
        for t in book["settled"]:
            if t.get("pnl", 0.0) > 0 and rules.in_news_window(_market(t), t["entry_ts"], events):
                d = server_day(t["exit_ts"])
                cut_by[d] = cut_by.get(d, 0.0) + t["pnl"]
    closes = sorted(book["curve"])
    lows = worst_marks(state, book) if state.get("intrabar") else book["marked"]
    rows, prev, balance, ci = [], ACCOUNT, ACCOUNT, 0
    current = opened = low = close = bal_open = None
    last_day = None
    for (stamp, marked), (_, worst) in zip(book["marked"], lows):
        day = server_day(stamp)
        if day != current:
            if current is not None:
                rows.append([current, close - opened, min(low, opened) - opened,
                             current in traded, bal_open - opened, close, last_day])
                prev = close
            while ci < len(closes) and closes[ci][0] < day_start(day):
                balance = closes[ci][1]
                ci += 1
            last_day = current
            current, opened, low, bal_open = day, prev, worst, balance
        low = min(low, worst)
        close = marked
    if current is not None:
        rows.append([current, close - opened, min(low, opened) - opened,
                     current in traded, bal_open - opened, close, last_day])
    while ci < len(closes):        # the balance after the last rollover
        balance = closes[ci][1]
        ci += 1
    out = []
    for i, (day, ret, dip, did, bo, close, before) in enumerate(rows):
        # the next rollover's balance gap is relative to the next open equity
        # (= this close); after the last day, the final closed balance
        bc = rows[i + 1][4] if i + 1 < len(rows) else balance - close
        span = 1 if before is None else day - before
        out.append((ret / ACCOUNT, dip / ACCOUNT, did, bo / ACCOUNT, bc / ACCOUNT,
                    int(span), cut_by.get(day, 0.0) / ACCOUNT))
    return out


MC_BLOCK_DAYS = 10
MC_PATHS = 5000
MC_MAX_DAYS = 500        # server days per step before a path counts as "stuck"
MC_WITHIN = (20, 40, 60)
OUTCOMES = ("pass", "daily", "max", "inactive", "stuck")


def walk(days, target):
    """One step from a fresh $5,000 over the `daily_series` days `days` yields.
    Returns (outcome, days used, worst day $ under the daily reference, deepest
    $ under $5,000); outcome pass / daily / max / inactive / stuck, or open when
    `days` runs out. Every rule is applied here:

      * daily: the day's low (worst marks; or its close after a news cut,
        if lower) at or under max(balance, equity) at the rollover - 5%;
      * max: the low at or under $4,500, static;
      * profitable day: min(close balance, close equity) - open balance >= 0.5%;
      * pass: close balance and close equity at the target, 3 profitable days;
      * inactive: 30 calendar days in a row without an entry.
    """
    a = ACCOUNT
    equity, used, profitable, idle = a, 0, 0, 0
    worst_day = deepest = 0.0
    goal, floor = a * (1 + target), a * (1 - MAX_LOSS)
    for ret, dip, did, bo, bc, span, cut in days:
        if used >= MC_MAX_DAYS:
            return "stuck", used, worst_day, deepest
        used += 1
        balance_open = equity + bo * a
        ref = max(balance_open, equity)
        # a deducted profit leaves the day's close at ret - cut
        low = equity + min(dip, ret - cut) * a
        worst_day, deepest = max(worst_day, ref - low), max(deepest, a - low)
        if low <= floor:
            return "max", used, worst_day, deepest
        if low <= ref - DAILY_LOSS * a:
            return "daily", used, worst_day, deepest
        equity += (ret - cut) * a
        balance_close = equity + bc * a
        if min(balance_close, equity) - balance_open >= PROFITABLE_DAY_MIN * a - 1e-9:
            profitable += 1
        idle = 0 if did else idle + span
        if idle >= INACTIVE_DAYS:
            return "inactive", used, worst_day, deepest
        if balance_close >= goal and equity >= goal and profitable >= PROFITABLE_DAYS:
            return "pass", used, worst_day, deepest
    return "open", used, worst_day, deepest


def _blocks(days, rng):
    n = len(days)
    while True:
        start = rng.randrange(0, max(1, n - MC_BLOCK_DAYS))
        yield from days[start:start + MC_BLOCK_DAYS]


def mc_paths(days, paths, seed):
    """`paths` challenges (both steps, each a fresh $5,000) on resampled day
    blocks: [(outcome, days used, worst day $, deepest $)]."""
    import random
    rng = random.Random(seed)
    out = []
    for _ in range(paths):
        total, worst_day, deepest = 0, 0.0, 0.0
        for target in STEP_TARGETS:
            outcome, used, day, deep = walk(_blocks(days, rng), target)
            total, worst_day, deepest = total + used, max(worst_day, day), max(deepest, deep)
            if outcome != "pass":
                break
        out.append((outcome, total, worst_day, deepest))
    return out


def mc_stats(rows):
    """`fundednext_challenge.mc_stats` plus the inactive outcome. Days are
    server days used; drawdowns over passing paths."""
    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(q * len(values)))] if values else None

    n = len(rows)
    won = [r for r in rows if r[0] == "pass"]
    p = len(won) / n
    days = [r[1] for r in won]
    return {**{k: round(100 * sum(r[0] == k for r in rows) / n, 1) for k in OUTCOMES},
            "paths": n,
            "pass_se": round(100 * (p * (1 - p) / n) ** 0.5, 2),
            "median_trading_days": pct(days, 0.5), "p90_trading_days": pct(days, 0.9),
            "p75_trading_days": pct(days, 0.75), "p95_trading_days": pct(days, 0.95),
            "within": {d: round(100 * sum(x <= d for x in days) / n, 1) for d in MC_WITHIN},
            "expected_days": round(sum(r[1] for r in rows) / n / p, 1) if p else None,
            **{f"{name}_{q}": (round(pct([r[i] for r in won], q / 100)) if won else None)
               for name, i in (("worst_day", 2), ("deepest", 3)) for q in (50, 75, 95)}}


# --------------------------------------------------------------------------- #
# stressed Monte Carlo, the dial, the report
# --------------------------------------------------------------------------- #

EDGE_KEPT = (1.0, 0.75, 0.5, 0.25)


def _edge_label(keep):
    return f"edge {keep:.0%}"


def _mc_job(job):
    n, risk, paths, _ = job
    state = _prefix(_STATE, n, risk)
    rows, days, duds = stressed_paths(state, risk, paths, 1 - DIAL_ALIVE, 0.0, DRAWS, DIAL_KEEP)
    return {"risk": risk, "members": n, "days": days, **mc_stats(rows),
            "dud_share_drawn": round(duds / (DRAWS * max(1, n)), 2)}


HARSH = {"dud75": (0.75, 0.0, 20), "costly": (DUD_SHARE, 0.05, DRAWS)}


def stressed_paths(state, risk, paths, share=DUD_SHARE, cost_r=0.0, draws=DRAWS, keep=1.0):
    """FundedNext's stressed Monte Carlo on The5ers' rules: each SYMBOL is a dud
    (every sleeve on it at no edge) in exactly `share` of `draws` draws."""
    import random
    if abs(share * draws - round(share * draws)) > 1e-9:
        raise ValueError(f"a dud share of {share} is not a whole number of {draws} draws")
    mc._MARKING.clear()
    per = max(1, paths // draws)
    symbols = {k.split(":", 1)[0] for k in state["logs"]}
    dud_in = {s: set(random.Random(s).sample(range(draws), round(share * draws)))
              for s in symbols}
    rows, duds, days = [], 0, []
    for d in range(draws):
        out = {k for k in state["logs"] if d in dud_in[k.split(":", 1)[0]]}
        duds += len(out)
        logs = {k: (_dud(v) if k in out else v if keep == 1 else _dud(v, keep))
                for k, v in state["logs"].items()}
        if cost_r:
            logs = {k: _costed(v, cost_r) for k, v in logs.items()}
        days = daily_series({**state, "logs": logs}, risk)
        rows += mc_paths(days, per, 7000 + d)
    return rows, len(days), duds


SYMBOLS_ALIVE = (1.0, 0.75, 0.5, 0.25)
GRID_DRAWS = 20
REPORT_PATHS = 10_000
_GRID = {}


def _grid_job(job):
    path, risk, alive, keep, paths, seed = job
    if path not in _GRID:
        _GRID.clear()
        with open(os.path.join(twohost.ROOT, path), "rb") as handle:
            _GRID[path] = pickle.load(handle)
    state = _GRID[path]
    if alive == 1:
        mc._MARKING.clear()
        logs = state["logs"] if keep == 1 else {
            k: _dud(v, keep) for k, v in state["logs"].items()}
        rows = mc_paths(daily_series({**state, "logs": logs}, risk), paths, seed)
    else:
        rows, _, _ = stressed_paths(state, risk, paths, 1 - alive, 0.0, GRID_DRAWS, keep)
    return {"alive": alive, "keep": keep, **mc_stats(rows)}


def _grid_name(alive, keep):
    if alive == 1 and keep == 1:
        return "full backtest edge"
    return f"sym alive {alive:.0%}" if keep == 1 else f"edge kept {keep:.0%}"


GRID_NOTE = ("  sym alive N%: N% of the symbols keep their full edge, every strategy on the "
             f"others has none ({GRID_DRAWS} draws); edge kept N%: every strategy keeps N% of "
             "its backtest edge; full backtest edge: the backtest days as they were")

MC_NAMES = {"dial": f"{1 - DIAL_ALIVE:.0%} sym dead, {DIAL_KEEP:.0%} edge",
            "stressed": "1/2 sym no edge", "dud75": "3/4 sym no edge",
            "costly": "1/2 sym no edge+cost", "plain": "edge kept 100%",
            **{_edge_label(k): f"edge kept {k:.0%}" for k in EDGE_KEPT}}
_NAME_W = max(len(v) for v in (*MC_NAMES.values(), "full backtest edge")) + 1
MC_HEAD = (f"{'':{_NAME_W}}{'pass':>7}{'max':>6}{'daily':>6}{'idle':>6}{'stuck':>6}{'days p50/75/95':>16}"
           f"{'<=20d':>7}{'<=40d':>7}{'<=60d':>7}{'w/ retry':>10}"
           f"{'worst day p50/75/95':>21}{'deepest p50/75/95':>19}")
MC_NOTE = (f"  days: server days to pass both steps (p50/p75/p95 of passing paths); idle: "
           "expired after 30 days without a trade; stuck: no pass or breach in "
           f"{MC_MAX_DAYS} days a step; <=Nd: share of ALL paths passed within "
           "N; w/ retry: expected days until a pass restarting every failed attempt; worst "
           "day / deepest: $ p50/p75/p95 of PASSING paths")


def _cut(v):
    return "-" if v is None else f"{v:g}"


def _p3(c, prefix=None):
    if prefix is None:
        vals = (c["median_trading_days"], c.get("p75_trading_days"), c.get("p95_trading_days"))
    else:
        vals = (c.get(f"{prefix}_50"), c.get(f"{prefix}_75"), c.get(f"{prefix}_95"))
    return "/".join(map(_cut, vals))


def _mc_line(c, label=""):
    return (f"{MC_NAMES.get(label, label):{_NAME_W}}{c['pass']:>6.1f}%{c['max']:>5.1f}%"
            f"{c['daily']:>5.1f}%{c.get('inactive', 0.0):>5.1f}%{c.get('stuck', 0.0):>5.1f}%{_p3(c):>16}"
            + "".join(f"{c['within'][d]:>6.1f}%" for d in sorted(c["within"]))
            + f"{_cut(c['expected_days']):>10}"
            f"{_p3(c, 'worst_day'):>21}{_p3(c, 'deepest'):>19}")


def _rules_line():
    n = len(news_events())
    return (f"  The5ers High Stakes ${ACCOUNT:,.0f}: +{STEP_TARGETS[0]:.0%} then "
            f"+{STEP_TARGETS[1]:.0%}, daily {DAILY_LOSS:.0%} of max(balance, equity) at "
            f"00:00 server, max {MAX_LOSS:.0%} static, {PROFITABLE_DAYS} profitable days "
            f"(>= {PROFITABLE_DAY_MIN:.1%}), {INACTIVE_DAYS}d inactivity; news: {n} red-folder "
            f"events on disk" + ("" if n else " -- NO deductions booked (floor on cost)"))


def _grid_table(cells):
    print(MC_HEAD)
    for alive, keep in ((1.0, 1.0), *((a, 1.0) for a in SYMBOLS_ALIVE[1:]),
                        *((1.0, k) for k in EDGE_KEPT[1:])):
        print(_mc_line(cells[(alive, keep)], _grid_name(alive, keep)))
    print(GRID_NOTE)
    print(MC_NOTE)
    print(_rules_line())
    print("\n  each cell: pass % / server days to pass / deepest drop $ (passing paths)")
    for q, title in ((50, "p50 challenge"), (75, "p75 challenge"), (95, "p95 challenge")):
        print(f"\n  {title:<48}" + "".join(f"{'edge kept ' + format(k, '.0%'):>22}"
                                         for k in EDGE_KEPT))
        for a in SYMBOLS_ALIVE:
            row = []
            for k in EDGE_KEPT:
                c = cells[(a, k)]
                days = c["median_trading_days"] if q == 50 else c.get(f"p{q}_trading_days")
                row.append(f"{c['pass']:.1f}% / {_cut(days)}d / ${_cut(c.get(f'deepest_{q}'))}")
            print(f"  {'sym alive ' + format(a, '.0%'):<48}" + "".join(f"{x:>22}" for x in row))


def _mc_table(out):
    print(MC_HEAD)
    for label in ("dial", "stressed", *HARSH, *map(_edge_label, EDGE_KEPT), "plain"):
        if label in out:
            print(_mc_line(out[label], label))
    print(MC_NOTE)
    print(_rules_line())


def _dial_jobs(state, paths):
    fit = {risk: sum(_fits(m, risk) for m in state["members"]) for risk in RISKS}
    jobs = sorted(((n, risk, paths, 0) for risk in RISKS if fit[risk]
                   for n in sorted({n for n in SIZES if n < fit[risk]} | {fit[risk]})),
                  key=lambda job: -job[0])
    return jobs, fit


def _rel(path):
    return os.path.relpath(os.path.abspath(path), twohost.ROOT).replace(os.sep, "/")


def _claimed(item):
    fn, unit, job = item
    if not twohost.claim(unit):
        return None
    out = fn(job)
    twohost.finish(unit, out)
    return out


def _run_local(fn, jobs, units, workers, init=None, initargs=()):
    items, work = ([(fn, u, j) for j, u in zip(jobs, units)], _claimed) if units else (jobs, fn)
    out = []
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(max(1, min(workers, len(jobs))), initializer=init, initargs=initargs) as pool:
        for i, got in enumerate(pool.imap(work, items), 1):
            out.append(got)
            if i % max(1, len(jobs) // 10) == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    return out


def _shared(name, fn, jobs, workers, init=None, initargs=(), files=()):
    """As FundedNext's: with `TWOHOST_RUN` set the Mac works the list from the far end."""
    if not twohost.enabled():
        return _run_local(fn, jobs, None, workers, init, initargs)
    units = [twohost.unit_id(f"t5_{name}", *job) for job in jobs]
    spec = os.path.join(_CACHE, f"twohost_t5_{name}.pkl")
    with open(spec, "wb") as handle:
        pickle.dump({"fn": fn.__name__, "jobs": jobs, "units": units,
                     "init": init.__name__ if init else None, "initargs": initargs}, handle)
    twohost.push([*files, spec])
    twohost.start_mac("sandbox.research.the5ers_combined_strategies",
                      ["_shared", "--spec", _rel(spec)], f"t5_{name}.log")
    _run_local(fn, jobs, units, workers, init, initargs)
    got = twohost.collect(units)
    return [got[u] for u in units]


def _mac_shared(spec, workers):
    with open(os.path.join(twohost.ROOT, spec), "rb") as handle:
        s = pickle.load(handle)
    init = globals()[s["init"]] if s["init"] else None
    _run_local(globals()[s["fn"]], s["jobs"][::-1], s["units"][::-1], workers,
               init, tuple(s["initargs"]))
    print("twohost: Mac side finished", flush=True)


def dial(state, path, paths, workers):
    with open(path, "wb") as handle:
        pickle.dump(state, handle, protocol=5)
    jobs, fit = _dial_jobs(state, paths)
    print(f"Monte Carlo: {len(jobs)} (size, risk) cells; sleeves that fit the minimum "
          f"lot: " + ", ".join(f"{r} {fit[r]}" for r in RISKS), flush=True)
    name = f"dial_{os.path.splitext(os.path.basename(path))[0]}"
    cells = [c for c in _shared(name, _mc_job, jobs, workers, _init, (_rel(path),), [path]) if c]
    cells.sort(key=lambda c: (c["members"], c["risk"]))
    top = max(c["pass"] for c in cells)
    best = min((c for c in cells if c["pass"] >= top - PASS_TOLERANCE),
               key=lambda c: (c["expected_days"] or float("inf"), -c["pass"], -c["members"]))
    return cells, best


def _book(state, chosen, best):
    fitted = [m for m in state["members"] if _fits(m, best["risk"])][:best["members"]]
    rows = {_key(r): r for r in chosen}
    return [dict(rows[_key(m)], minlot_usd=m["minlot_usd"]) for m in fitted]


def compliance(state, risk, label=""):
    """The ban layer's report for one book at one dial over the state's window,
    from a fixed-size replay (every sleeve sized off at most $5,000). Returns
    the figures it prints."""
    kept, counts = comply(state)
    mc._MARKING.clear()
    book = run_book({**state, "sizing_cap": {k: ACCOUNT for k in state["logs"]}},
                    risk, state["start"], state["end"])
    settled = sorted(book["settled"], key=lambda p: p["entry_ts"])
    n = len(settled)
    if not n:
        print(f"\nBAN-RULE CHECK {label}: no trades")
        return {}

    def pct(values, q):
        values = sorted(values)
        return values[min(len(values) - 1, int(q * len(values)))]

    holds = [p["exit_ts"] - p["entry_ts"] for p in settled]
    risk_usd = [p["lots"] * p["money_per_point"] * p["distance"] for p in settled
                if p.get("distance", 0) > 0]
    # concurrency, counting a position open until its exit fills
    events = sorted([(p["entry_ts"], 1) for p in settled]
                    + [(p["exit_ts"] + p.get("bar_seconds", 0), -1) for p in settled],
                    key=lambda e: (e[0], e[1]))
    live, most = 0, 0
    for _, step in events:
        live += step
        most = max(most, live)
    entries = [p["entry_ts"] for p in settled]
    same_minute = sum(1 for a, b in zip(entries, entries[1:]) if b - a < BULK_GAP_SECONDS)
    lag = ef.BRIDGE_QUEUE_SECONDS + 1
    news = {}
    for p in settled:
        news.setdefault(p["market"], _related_events(p["market"]))
    in_news = sum(_defer(p["entry_ts"] + lag, news[p["market"]])[0] for p in settled)
    weeks = max(1.0, (state["end"] - state["start"]) / (7 * DAY))
    best_trade = max(p["pnl"] for p in settled)
    longs = sum(p["side"] > 0 for p in settled) / n
    out = {"trades": n, "per_week": round(n / weeks, 1),
           "skipped": {k: v for k, v in counts.items() if k not in ("trades", "kept")},
           "kept_share": round(counts["kept"] / max(1, counts["trades"]), 3),
           "max_open": most, "entries_within_bulk_gap": same_minute,
           "entries_in_news_window": in_news, "long_share": round(longs, 3),
           "hold_min_s": min(holds), "hold_p5_s": pct(holds, 0.05),
           "hold_median_s": pct(holds, 0.5),
           "risk_usd_p50": round(pct(risk_usd, 0.5), 2) if risk_usd else None,
           "risk_usd_p95": round(pct(risk_usd, 0.95), 2) if risk_usd else None,
           "risk_usd_max": round(max(risk_usd), 2) if risk_usd else None,
           "best_trade_usd": round(best_trade, 2),
           "best_trade_of_step1": round(best_trade / (STEP_TARGETS[0] * ACCOUNT), 3)}
    print(f"\nBAN-RULE CHECK {label}  ({_label(state['start'])}..{_label(state['end'])}, "
          f"risk {risk}, fixed $5,000 sizing)")
    print(f"  trades {n} ({out['per_week']}/week); entries the ban layer skipped: "
          + ", ".join(f"{k} {v}" for k, v in out["skipped"].items())
          + f"  -> {out['kept_share']:.0%} of signals kept")
    print(f"  news: entries inside +-{NEWS_GUARD_SECONDS}s of a related red folder {in_news} "
          f"(want 0; closes there are deferred at log time); {len(news_events())} events")
    print(f"  bulk: entries < {BULK_GAP_SECONDS}s apart {same_minute} (want 0); most open at "
          f"once {most} (cap {MAX_OPEN})")
    print(f"  HFT / tick scalping: hold min {out['hold_min_s'] / 60:.0f} min, p5 "
          f"{out['hold_p5_s'] / 60:.0f} min, median {out['hold_median_s'] / 60:.0f} min")
    print(f"  one-sided: {longs:.0%} long / {1 - longs:.0%} short")
    if risk_usd:
        print(f"  risk per trade at its stop: p50 ${out['risk_usd_p50']}, p95 "
              f"${out['risk_usd_p95']}, max ${out['risk_usd_max']} "
              f"(cap {MAX_TRADE_RISK:.0%} = ${MAX_TRADE_RISK * ACCOUNT:,.0f}; min-lot "
              f"overshoot up to {OVERSHOOT_MAX}x)")
    print(f"  concentrated profit: best single trade ${best_trade:,.0f} = "
          f"{out['best_trade_of_step1']:.0%} of the step-1 target")
    return out


def _show(title, rows, chosen, rejected, cells, best, paths, members):
    print(f"\n{title}")
    print(f"pool {len(rows)} -> {len(chosen)} admitted; refused: cost_R top third "
          f"{rejected.get('cost_r', 0)} (cut {rejected.get('cost_r_cut')}), gates "
          f"(down_rho <= {GATE_RHO}, loss_lift <= {GATE_LIFT}) {rejected.get('gates', 0)}, "
          f"symbol cap {MAX_PER_SYMBOL} {rejected.get('symbol_cap', 0)}, family cap "
          f"{MAX_PER_FAMILY} {rejected.get('family_cap', 0)}")
    print(f"\n'{MC_NAMES['dial']}' The5ers challenge Monte Carlo pass both % -- in each "
          f"draw {1 - DIAL_ALIVE:.0%} of the SYMBOLS have no edge and every other sleeve "
          f"keeps {DIAL_KEEP:.0%} of its own; {DRAWS} draws, {paths:,} paths, "
          f"{MC_BLOCK_DAYS}-day blocks of {cells[0]['days']} server days; in-sample; "
          f"ban layer on")
    sizes = sorted({c["members"] for c in cells})
    grid = {(c["members"], c["risk"]): c for c in cells}
    for field, head in (("pass", "pass %"), ("expected_days", "expected days w/ retry")):
        print(f"\n{head}\n{'members':>8}" + "".join(f"{r:>8}" for r in RISKS))
        for n in sizes:
            line = []
            for r in RISKS:
                c = grid.get((n, r))
                if c is None:
                    line.append(f"{'-':>7} ")
                    continue
                v = c[field]
                text = f"{v:.1f}" if field == "pass" else _cut(v)
                line.append(f"{text:>7}{'*' if c is best else ' '}")
            print(f"{n:>8}" + "".join(line))
    top = max(c["pass"] for c in cells)
    print("  - : fewer sleeves fit the minimum lot at that risk")
    print(f"  * chosen: {best['members']} members at risk {best['risk']} -- fewest expected "
          f"days to a pass with retries ({_cut(best['expected_days'])}), within "
          f"{PASS_TOLERANCE:g} point of the best pass rate ({top:.1f}%)")
    _mc_table({"dial": best})
    by_symbol = {}
    for m in members:
        by_symbol[m["symbol"]] = by_symbol.get(m["symbol"], 0) + 1
    print(f"\n{len(members)} sleeves on {len(by_symbol)} symbols: " + ", ".join(
        f"{sym} {n}" for sym, n in sorted(by_symbol.items(), key=lambda kv: -kv[1])))
    print(f"\n{'sleeve':46}{'lev':>5}{'return %':>10}{'closed dd %':>12}{'trades':>8}"
          f"{'PF':>7}{'min-lot x':>10}")
    for m in members:
        pf = f"{m['pf']:.2f}" if m["pf"] else "-"
        over = max(1.0, m["minlot_usd"] / _asked(m, best["risk"]))
        print(f"{_key(m):46}{m['k']:>4.0f}x{m['return']:>10.1f}{m['dd']:>12.1f}"
              f"{m['trades']:>8}{pf:>7}{over:>9.1f}x")
    print(f"  return / dd: the sleeve alone at its leverage on ${ACCOUNT:,.0f}, P&L summed "
          f"(the book caps each at {MAX_TRADE_RISK:.0%} a trade)")


# --------------------------------------------------------------------------- #
# build -> test -> final
# --------------------------------------------------------------------------- #

def _summary():
    with open(SUMMARY, "rb") as handle:
        summary = pickle.load(handle)
    if summary["end"] < ef.OOS_END:
        raise SystemExit(f"logs end {_label(summary['end'])}; run `logs` first")
    return summary


def build(workers, paths):
    """The rule on 2024-2025 -> sealed."""
    summary = _summary()
    rows, chosen, rejected = choose(summary["cells"], ef.IS_END)
    state = load_state(chosen, summary["start"], ef.IS_END)
    cells, best = dial(state, IS_STATE, paths, workers)
    members = _book(state, chosen, best)
    _show(f"STEP 1 -- the rule on {_label(summary['start'])}..{_label(ef.IS_END)} only "
          f"(no 2026 trade is read)", rows, chosen, rejected, cells, best, paths, members)
    keys = [_key(m) for m in members]
    check = compliance(load_state(members, summary["start"], ef.IS_END), best["risk"],
                       "-- the sealed book on 2024-2025")
    seal_id = hashlib.sha256(json.dumps({"members": keys, "risk": best["risk"],
                                         "rule": _rule(paths)},
                                        sort_keys=True, default=str).encode()).hexdigest()[:12]
    seal = {"id": seal_id,
            "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chosen_on": [_label(summary["start"]), _label(ef.IS_END)],
            "rule": _rule(paths), "pool": len(rows), "pool_dir": summary["pool"],
            "pool_saw_2026": summary["saw_2026"], "risk": best["risk"],
            "admitted": len(chosen), "refused": rejected, "members": members,
            "in_sample_montecarlo": cells, "ban_check_2024_2025": check}
    with open(SEAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(seal, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nsealed {seal_id}: {len(members)} sleeves at risk {best['risk']} -> {SEAL_PATH}")


def _tests():
    if not os.path.exists(TEST_LOG):
        return []
    with open(TEST_LOG, encoding="utf-8") as handle:
        return json.load(handle)


def test(workers):
    """The sealed book at the sealed dial: the challenge Monte Carlo grid on
    2024-2025 (where it was chosen) and on 2026."""
    with open(SEAL_PATH, encoding="utf-8") as handle:
        seal = json.load(handle)
    history = _tests()
    first = history[0]["seal"] if history else seal["id"]
    clean = first == seal["id"] and not seal["pool_saw_2026"]
    start, lo, hi = _stamp(seal["chosen_on"][0]), ef.IS_END, ef.OOS_END
    risk, paths = seal["risk"], REPORT_PATHS
    jobs, files, where = [], [], {}
    for label, x, y in (("2024-2025", start, lo), ("2026", lo, hi)):
        path = os.path.join(_CACHE, f"the5ers_challenge_test_{label}.pkl")
        with open(path, "wb") as handle:
            pickle.dump(load_state(seal["members"], x, y), handle, protocol=5)
        files.append(path)
        where[_rel(path)] = label
        jobs += [(_rel(path), risk, a, k, paths, 2026) for a in SYMBOLS_ALIVE for k in EDGE_KEPT]
    windows = {label: {} for label in where.values()}
    for job, cell in zip(jobs, _shared(f"grid_{seal['id']}", _grid_job, jobs, workers,
                                       files=files)):
        windows[where[job[0]]][(job[2], job[3])] = cell
    print(f"\nSTEP 2 -- 2026 TEST  seal {seal['id']}  {len(seal['members'])} sleeves  "
          f"risk {risk}  {_label(lo)}..{_label(hi)}")
    if clean:
        print("  CLEAN: the first test of 2026")
    elif seal["pool_saw_2026"]:
        print("  NOT CLEAN: the pool was screened on 2026 -- this reads high")
    else:
        print(f"  NOT CLEAN: 2026 was already spent on seal {first}")
    print(f"\nThe5ers challenge Monte Carlo, {paths:,} paths a cell, {MC_BLOCK_DAYS}-day blocks")
    for label, cells in windows.items():
        print(f"\n{label}" + ("  -- where the book was chosen" if label != "2026" else ""))
        _grid_table(cells)
    print("  Loss limits judged at each bar's worst price (1-minute highs/lows).")
    check = compliance(load_state(seal["members"], lo, hi), risk, "-- the sealed book on 2026")
    grid = {label: {f"{a:g}/{k:g}": c for (a, k), c in cells.items()}
            for label, cells in windows.items()}
    history.append({"seal": seal["id"], "clean": clean, "risk": risk, "paths": paths,
                    "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "news_events": len(news_events()),
                    "montecarlo": windows["2026"][(1.0, 1.0)],
                    "stressed": windows["2026"][(0.5, 1.0)], "grid": grid,
                    "ban_check_2026": check})
    with open(TEST_LOG, "w", encoding="utf-8") as handle:
        json.dump(history, handle, indent=2, default=str)
        handle.write("\n")


def final(workers):
    """The sealed rule, unchanged, on 2024-2026: the book to trade."""
    with open(SEAL_PATH, encoding="utf-8") as handle:
        seal = json.load(handle)
    tested = [h for h in _tests() if h["seal"] == seal["id"]]
    if not tested:
        raise SystemExit(f"no 2026 test of seal {seal['id']}; run `test` first")
    paths = seal["rule"]["paths"]
    if json.loads(json.dumps(_rule(paths))) != seal["rule"]:
        raise SystemExit("the rule changed after the 2026 test; that would tune it on 2026")
    summary = _summary()
    rows, chosen, rejected = choose(summary["cells"], ef.OOS_END)
    state = load_state(chosen, summary["start"], ef.OOS_END)
    cells, best = dial(state, FINAL_STATE, paths, workers)
    members = _book(state, chosen, best)
    _show(f"STEP 3 -- the same rule on {_label(summary['start'])}..{_label(ef.OOS_END)}: "
          f"THE BOOK TO TRADE", rows, chosen, rejected, cells, best, paths, members)
    t = tested[-1]
    print(f"\nexpect the 2026 test{'' if t['clean'] else ' or LESS (it was not clean)'}"
          f", not the table above (seal {seal['id']}, risk {seal['risk']}):")
    _grid_table({tuple(map(float, k.split("/"))): v for k, v in t["grid"]["2026"].items()})
    check = compliance(load_state(members, summary["start"], ef.OOS_END), best["risk"],
                       "-- the book to trade, 2024-2026")
    with open(FINAL_PATH, "w", encoding="utf-8") as handle:
        json.dump({"seal": seal["id"], "rule": seal["rule"], "risk": best["risk"],
                   "chosen_on": [_label(summary["start"]), _label(ef.OOS_END)],
                   "pool": len(rows), "admitted": len(chosen), "refused": rejected,
                   "members": [dict(m, scale=round(_scale(m["k"], best["risk"]), 4))
                               for m in members],
                   "in_sample_montecarlo": cells, "test_2026": t, "ban_check": check},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"wrote {FINAL_PATH}")


def replay(state, risk_scale, initial=ACCOUNT, lo=None, hi=None):
    """The members on one shared balance, ban layer and risk cap on."""
    kept, _ = comply(state)
    logs = {k: [t for t in v if (k, t["entry_ts"]) in kept]
            for k, v in state["logs"].items()}
    scale = {k: _scale(v, risk_scale) for k, v in (state.get("scale") or {}).items()}
    return base.replay(state["members"], logs, state["bars_by"],
                       state["ctx_by"], scale=scale, sizing_cap={},
                       risk_scale=risk_scale, gross_cap=None, fair_cap=False,
                       initial=initial, force_minimum=True,
                       lo=state["start"] if lo is None else lo,
                       hi=state["end"] if hi is None else hi)


# --------------------------------------------------------------------------- #
# selftest: every challenge rule on hand-built days
# --------------------------------------------------------------------------- #

def selftest():
    a = ACCOUNT
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  {'ok ' if good else 'BAD'} {name}: {got} (want {want})")

    def day(ret=0.0, dip=None, did=True, bo=0.0, bc=0.0, span=1, cut=0.0):
        """Dollars in, `daily_series` fractions out."""
        dip = min(0.0, ret) if dip is None else dip
        return (ret / a, dip / a, did, bo / a, bc / a, span, cut / a)

    up = day(200)
    check("3 x +$200: pass step 1 on day 3", walk([up] * 5, 0.10)[:2], ("pass", 3))
    check("+$600 in 1 day then flat: 1 profitable day, open",
          walk([day(600)] + [day(0)] * 6, 0.10)[0], "open")
    check("+$600 then 2 x +$30: pass on 3rd profitable day",
          walk([day(600), day(30), day(30)], 0.10)[:2], ("pass", 3))
    check("a +$20 day is not profitable (< $25)",
          walk([day(600), day(20), day(30), day(30)], 0.10)[:2], ("pass", 4))
    # daily reference = max(balance, equity) at the rollover: equity 5,300 on
    # a 5,000 balance carried over; next day equity dips to 5,040 (-260)
    carry = [day(300, bc=-300), day(0, dip=-260, bo=-300)]
    check("open profit over the rollover raises the daily floor",
          walk(carry, 0.10)[0], "daily")
    check("a $240 dip under a closed $5,300 survives",
          walk([day(300), day(0, dip=-240)], 0.10)[0], "open")
    check("balance above equity at rollover: floor from the balance",
          walk([day(-100, bc=100), day(0, dip=-160, bo=100)], 0.10)[0], "daily")
    check("max loss: equity to $4,500 (static)",
          walk([day(-200), day(-200), day(0, dip=-100)], 0.10)[0], "max")
    check("max loss is static: +$400 first does not move $4,500",
          walk([day(400, bc=0), day(-240), day(-240), day(-240), day(-150)], 0.10)[0], "open")
    check("intraday dip of exactly 5% breaks daily",
          walk([day(0, dip=-250)], 0.10)[0], "daily")
    # profitable day on min(balance, equity): closes equity +$100 but balance
    # only +$10 (open profit) -> not profitable
    check("profitable day needs min(balance, equity)",
          walk([day(600)] + [day(100, bc=-90)] * 2 + [day(0, bo=0)] * 2, 0.10)[0], "open")
    # equity 5,600 and 3 profitable days on day 3, but the closed balance is
    # 5,450; it closes up the next day
    check("target needs the closed balance too",
          walk([day(300), day(100), day(200, bc=-150), day(0, bo=-150)], 0.10)[:2],
          ("pass", 4))
    check("29 idle days survive", walk([day(0, did=False)] * 29, 0.10)[0], "open")
    check("30 idle days expire", walk([day(0, did=False)] * 30, 0.10)[:2], ("inactive", 30))
    check("weekend spans count calendar days",
          walk([day(0, did=False, span=3)] * 10, 0.10)[:2], ("inactive", 10))
    check("a trade resets the idle clock",
          walk([day(0, did=False)] * 29 + [day(0)] + [day(0, did=False)] * 29, 0.10)[0], "open")
    check("news: a +$300 winner's profit is deducted",
          walk([day(300, cut=300)] * 3, 0.10)[0], "open")
    check("news: deduction counts toward the daily loss",
          walk([day(0, dip=-100, cut=300)], 0.10)[0], "daily")
    rows = mc_paths([up] * 30, 5, 1)
    check("two steps: +10% (3 days) then +5% on a fresh $5,000 (3 days)",
          {r[:2] for r in rows}, {("pass", 6)})
    rows = mc_paths([day(130)] * 30, 3, 1)
    check("step 2 at +5% needs 3 profitable days, not the +10%",
          {r[:2] for r in rows}, {("pass", 7)})   # 4 days to $520, then 3 to $390>=250

    # --- the news guard ---
    t0 = 1_000_020   # a red folder at t0 (NY wall clock)
    check("news: an order 2 s after a release is deferred to +2 min +5 s",
          _defer(t0 + 2, [t0]), (True, t0 + NEWS_GUARD_SECONDS + NEWS_DEFER_SECONDS))
    check("news: an order 2 min before is inside (inclusive)",
          _defer(t0 - 120, [t0])[0], True)
    check("news: an order 121 s after is outside", _defer(t0 + 121, [t0]), (False, t0 + 121))
    check("news: back-to-back releases chain the deferral",
          _defer(t0, [t0, t0 + 180]), (True, t0 + 180 + 125))

    # --- the ban layer on hand-built trades ---
    h9 = 9 * 3_600 + 30 * 60     # 09:30 NY on day 0

    def tr(market, entry, exit_, side=1):
        return {"market": market, "entry_ts": entry, "exit_ts": exit_, "side": side,
                "bar_seconds": 0, "points": 1.0, "distance": 1.0}

    def kept_of(trades, start=0, end=10 ** 9):
        members = [{"symbol": f"s{i}", "family": "f"} for i in range(len(trades))]
        state = {"members": members, "start": start, "end": end,
                 "logs": {f"s{i}:f": [t] for i, t in enumerate(trades)}}
        _COMPLY.clear()
        kept, counts = comply(state)
        return [i for i in range(len(trades)) if (f"s{i}:f", trades[i]["entry_ts"]) in kept], counts

    got, counts = kept_of([tr("eurusd", h9, h9 + 3600), tr("xauusd", h9, h9 + 3600)])
    check("bulk: two entries in the same minute -> the first (best) only",
          (got, counts["bulk"]), ([0], 1))
    got, _ = kept_of([tr("eurusd", h9, h9 + 3600), tr("xauusd", h9 + 60, h9 + 3600)])
    check("bulk: a minute apart -> both", got, [0, 1])
    got, counts = kept_of([tr("xauusd", h9, h9 + 3600), tr("xauusd", h9 + 600, h9 + 1200)])
    check("one position per instrument", (got, counts["same_instrument"]), ([0], 1))
    got, counts = kept_of([tr("us500", h9, h9 + 3600, 1), tr("ustec", h9 + 600, h9 + 1200, -1)])
    check("no opposing positions in a correlated group", (got, counts["opposed"]), ([0], 1))
    got, _ = kept_of([tr("eurusd", h9, h9 + 3600, 1), tr("usdchf", h9 + 600, h9 + 1200, -1)])
    check("negative correlation: long EURUSD + short USDCHF is the SAME bet (allowed)",
          got, [0, 1])
    got, counts = kept_of([tr("eurusd", h9, h9 + 3600, 1), tr("usdchf", h9 + 600, h9 + 1200, 1)])
    check("...long EURUSD + long USDCHF is a hedge (refused)", (got, counts["opposed"]), ([0], 1))
    got, counts = kept_of([tr("btc", h9, h9 + 9000), tr("ethusd", h9 + 60, h9 + 9000),
                           tr("solusd", h9 + 120, h9 + 9000)])
    check("group cap: a third same-way crypto position is refused",
          (got, counts["group_cap"]), ([0, 1], 1))
    many = [tr(m, h9 + 120 * i, h9 + 9000) for i, m in
            enumerate(("eurusd", "xauusd", "us500", "de40", "ukoil", "btc", "audusd"))]
    got, counts = kept_of(many)
    check(f"max open {MAX_OPEN}", (len(got), counts["max_open"]), (MAX_OPEN, 7 - MAX_OPEN))
    got, _ = kept_of([tr("xauusd", h9, h9 + 600), tr("xauusd", h9 + 600, h9 + 1200)])
    check("an exit and a re-entry on the same bar: still open until the exit fills",
          got, [0])
    late = 17 * 3_600
    got, counts = kept_of([tr("eurusd", late, late + 3600)])
    check("rollover: no entry at 17:00 NY", (got, counts["rollover"]), ([], 1))
    got, _ = kept_of([tr("eurusd", 18 * 3600 + 15 * 60, 19 * 3600)])
    check("rollover: 18:15 NY is open again", got, [0])

    # --- risk cap and cost_R ---
    check("risk cap: k 5 at dial 0.2 is cut to 1% a trade",
          round(_scale(5.0, 0.2) * ef.RISK_FRACTION * 0.2, 6), MAX_TRADE_RISK)
    check("risk cap: k 1 at dial 0.03 is untouched", _scale(1.0, 0.03), 1.0)
    spec = {"multiplier": 1_000.0, "volume_min": 0.01, "volume_step": 0.01,
            "volume_max": 100.0, "contract_size": 1_000.0, "fx_to_usd": 1.0,
            "asset_class": "forex", "symbol": "eurusd"}
    trade = {"distance": 0.1, "entry": 1.1}
    lots, money = _size("s:f", ACCOUNT, trade, {"cfg": spec, "symbol": "eurusd"},
                        {"s:f": 5.0}, {}, 0.2, vol_mult=3.0, force_minimum=True)
    check("hard cap at sizing: k 5 x dial 0.2 x vol 3 -> $50 at the stop",
          round(lots * money * trade["distance"], 6), MAX_TRADE_RISK * ACCOUNT)
    log = [{"gross": 1.0, "points": 0.9, "distance": 2.0, "exit_ts": 5}] * 25
    check("cost_R = median (gross - points) / distance", round(_cost_r(log, 0, 0, 10), 6), 0.05)
    print("the5ers_rules selftest:")
    ok &= rules.selftest()
    print("selftest", "PASSED" if ok else "FAILED")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("command", choices=("selftest", "logs", "build", "test", "final",
                                            "_group", "_shared"))
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--paths", type=int, default=MC_PATHS)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--symbol")
    parser.add_argument("--bar", type=int)
    parser.add_argument("--spec")
    args = parser.parse_args()
    if args.command == "selftest":
        raise SystemExit(0 if selftest() else 1)
    if args.command == "_group":
        _one_group(args.symbol, args.bar)
    elif args.command == "_shared":
        _mac_shared(args.spec, args.workers)
    elif args.command == "logs":
        pool_logs(args.workers, _stamp(LOG_START), ef.OOS_END, args.fresh)
    elif args.command == "build":
        build(args.workers, args.paths)
    elif args.command == "test":
        test(args.workers)
    else:
        final(args.workers)


if __name__ == "__main__":
    main()
