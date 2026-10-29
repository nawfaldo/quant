"""The FundedNext combined book, judged ONLY on one $6,000 two-step challenge.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE: terminal output does
NOT reach the user -- paste the tables into the reply.

THE RULE IS FROZEN BEFORE 2026 IS SEEN. It is first chosen on 2024-2025 and
scored once on 2026; only then is the SAME rule re-run on 2024-2026 to pick the
book that trades. 2026 validates the rule; it never tunes it.

THE POOL. `results/filtered/leveraged_fundednext/` (the user's choice,
2026-10-17): 1,461 FundedNext survivors, each with a leverage ladder set on its
2024-2025 drawdown (`_leverage_fundednext`). A sleeve trades at `_lev`, the
highest whole k whose 2024-2025 drawdown stays <= the ladder's 10% target,
capped at `LEV_CAP` -- its `scale` in the replay -- and the dial multiplies
every sleeve together.

THAT POOL WAS SCREENED ON 2026 (2026 return > 0, PF >= 1.05, beat a 2026 coin
flip, then 2026 trades >= 30 and net t >= 1), so its 2026 test is printed NOT
CLEAN and reads high: it can only show the rule did not fall apart. The clean
test of this book is data after 2026-10-13. `FN_POOL_DIR=results/fundednext_is`
(every 2024-2025 winner, `--is-only` exports) is the pool whose test is clean.

NEVER es OR nq. They are futures-data twins of us500 / ustec and trade the same
FundedNext SPX500 / NDX100 (`EXCLUDE_SYMBOLS`; the export drops them too).

  logs   every pool cell's 2024-01-01..2026-10-13 trade log at FundedNext on a
         $6,000 balance, in FundedNext's quoted hours, fills only (as the ncf
         search, the 2026 filter and the leverage ladder ran). A cell runs
         under the protocol it was searched with, and those flags bind at import,
         so each protocol is generated in its own child interpreter. Every
         (protocol, symbol, bar) group is one file in `GROUP_DIR`, so a broken
         run resumes where it stopped (`--fresh` starts over); `SUMMARY` keeps
         each cell's per-day stream, enough to rank and gate the whole pool
         without loading a log.
  build  the rule, on trades CLOSED before 2026-01-01 only:
           1. admitted: best return first, in only if its losing days do not
              line up with a sitting member's (the Exness book's `down_rho` /
              `loss_lift` gates at their fixed defaults) -- NO member cap;
           2. size and dial: the best-first n of the admitted list, for every n
              in `SIZES` and the whole list, at every `RISKS` setting, through
              the STRESSED challenge Monte Carlo: in each of `DRAWS` draws every
              SYMBOL is a dud in a `DUD_SHARE` of draws -- every sleeve on it
              keeps its trades with its average trade set to zero (`_dud`) -- because in-sample every
              book above a handful of sleeves passes ~100% and the plain pass
              rate cannot tell them apart (a 3-sleeve book won that tie and
              failed 2026 max-loss 23%). At each dial only the sleeves whose
              broker-minimum lot puts on at most `OVERSHOOT_MAX` times the risk
              the dial asks of them take part (`_fits`). The highest stressed
              pass rate wins; within `PASS_TOLERANCE` points of it, the fewest
              stressed expected days to a pass with retries (a slow book's
              tie-break: "lowest risk" sealed 100 @ 0.01, a 107-day median,
              2026-10-21), then the higher pass rate, then the most members.
              The 2026 test runs stressed and plain (the real days).
         -> sealed to `SEAL_PATH`, then `test`, then `final`.
  test   the sealed book, at the sealed dial only, by challenge Monte Carlo
         (the default report, `_mc_table`): stressed and edge kept 100/75/50/25%, on 2024-2025
         where it was chosen and on 2026 -- pass, fails, median / p90 days,
         passed within 20/40/60 days, expected days with retries, and worst
         day / deepest on passing paths. Appended to `TEST_LOG`; a test of a
         different seal than the first is printed NOT CLEAN.
  final  the sealed rule, unchanged, on 2024-2026 -> `FINAL_PATH`, the book to
         trade. Refused without a test of the seal. Its own pass rate is
         in-sample; the number to expect is the 2026 test's (less, if that test
         was NOT CLEAN).

THE CHALLENGE (`fundednext_challenge`): +$480 then +$300 on a fresh $6,000,
$300 daily from the closed balance at the 17:00 NY rollover, $600 static, 5
trading days a phase. A path ends the moment it passes phase 2 or breaks a
limit. The loss limits are judged with every open position at the worst price
its 30m bar touched (the bars' 1-minute highs and lows, all at once), so a
spike inside a bar breaks them as it would live -- if anything harsher. A book
with no edge passes roughly a third of the time (a race to +8% before -10%,
then +5% before -10%) -- that, not zero, is the line a pass rate must clear.

    py -m sandbox.research._fundednext_export --is-only
    py -m sandbox.research._ncf_fundednext_export --is-only
    py -m sandbox.research.fundednext_combined_strategies logs [--workers N] [--fresh]
    py -m sandbox.research.fundednext_combined_strategies build [--paths 5000]
    py -m sandbox.research.fundednext_combined_strategies test | final
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import pickle
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

# BEFORE ANY `cfd_families` IMPORT. The broker profile and the split bind at
# import, so a process that already imported it cannot be switched afterwards.
if os.environ.get("CFD_BROKER", "fundednext") != "fundednext":
    raise SystemExit(f"CFD_BROKER={os.environ['CFD_BROKER']}; this module "
                     f"prices FundedNext only")
os.environ["CFD_BROKER"] = "fundednext"
os.environ.setdefault("CFD_SPLIT", "2024-2025")
os.environ.setdefault("CFD_OOS_END", "2026-10-13")

from sandbox.research import cfd_families as ef  # noqa: E402
from sandbox.research import twohost  # noqa: E402

# A child generating TikTok cells needs the `cfd_tt_families` registry
# installed before anything reads it (see `PROTOCOL_VARS`).
if os.environ.get("FN_TT") == "1":
    import platform
    platform._wmi = None
    from sandbox.research import cfd_tt_families  # noqa: E402,F401
    os.environ["CFD_BROKER"] = "fundednext"

if ef.BROKER != "fundednext":
    raise SystemExit("cfd_families was imported for another broker earlier in "
                     "this process; run this module in a fresh interpreter")

_SPLIT_WINDOW = (ef.IS_END, ef.OOS_END)
from sandbox.research import exness_combined_strategies as base  # noqa: E402

# The Exness module moves `ef.OOS_END` to its own data end at import; the pool
# was scored on the CFD_SPLIT window, so put that back.
ef.IS_END, ef.OOS_END = _SPLIT_WINDOW

# The Exness module checks the es_1m -> Yahoo es_30m handoff before every es
# context; es is never traded here, so the check is off.
base._validate_es_transition = lambda: None

RESULTS = ef.RESULTS
#: `FN_POOL_DIR` / `FN_OUT_DIR` point a dry run at a few cells and keep every
#: file it writes -- the seal and the 2026 test log above all -- out of the real ones.
POOL_DIR = (os.environ.get("FN_POOL_DIR")
            or os.path.join(RESULTS, "filtered", "leveraged_fundednext"))
_CACHE = os.environ.get("FN_OUT_DIR") or base.CACHE_DIR
_OUT = os.environ.get("FN_OUT_DIR") or RESULTS
#: Resumable group files, one directory per pool so two pools never mix.
GROUP_DIR = os.path.join(
    _CACHE, f"fundednext_pool_{os.path.basename(os.path.normpath(POOL_DIR))}")
SUMMARY = os.path.join(GROUP_DIR, "summary.pkl")
IS_STATE = os.path.join(_CACHE, "fundednext_challenge_is.pkl")
TEST_STATE = os.path.join(_CACHE, "fundednext_challenge_test.pkl")
FINAL_STATE = os.path.join(_CACHE, "fundednext_challenge_final.pkl")
SEAL_PATH = os.path.join(_OUT, "fundednext_challenge_seal.json")
TEST_LOG = os.path.join(_OUT, "fundednext_challenge_tests.json")
FINAL_PATH = os.path.join(_OUT, "fundednext_challenge_final.json")
LOG_START = "2024-01-01"
#: The dial grid, fixed before any run. Wide enough for a book of 1x..18x sleeves.
RISKS = (0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2)
#: Member counts tried: best-first prefixes of everything the gates admit, plus
#: the whole admitted list. Fixed before any run.
SIZES = (1, 2, 3, 5, 8, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 150, 200,
         250, 300, 400, 500, 700, 1000)
#: Stressed pass-rate points from the best within which a pair counts as tied.
PASS_TOLERANCE = 1.0
#: The stress: each symbol is a dud (no edge, every sleeve on it) in this share of draws.
DUD_SHARE = 0.5
DRAWS = 10
#: Ladder leverage ceiling: a k set on a tiny 2024-2025 drawdown (18x) had a
#: 33.9% standalone 2026 drawdown.
LEV_CAP = 5.0
#: A sleeve joins a book at a dial only if the broker's minimum lot puts on at
#: most this many times the risk the dial asks of it. At $6,000 and risk 0.03 a
#: 1x gold or US30 sleeve is asked for $2.70 at its stop and the minimum lot
#: risks $6-17: 27 of the sealed 60-sleeve book traded 2.5-6.4x their size and
#: made 69% of its 15 worst days' losses, and no lower dial could shrink them.
#: Membership, not refusal -- a sleeve trades every signal or is not in the book.
OVERSHOOT_MAX = 1.5
#: The admission gates, pairwise against every sitting member: a sleeve's
#: losing days may correlate with none above GATE_RHO (`base.down_rho`), and no
#: member's losing day may lift its own loss rate above GATE_LIFT times its base
#: rate (`base.loss_lift`). Looser than the Exness book's 0.40 / 1.40: on
#: 2024-2025 the wider list (415 admitted, not 229) put the same speed at a
#: lower drawdown at 6 of 8 speeds, and the canon cut from it (80 @ 0.03) held
#: 2026 at -$143 worst day / -$184 deepest against -$179 / -$271 (2026-10-18).
GATE_RHO = 0.50
GATE_LIFT = 1.60
#: Marking bars kept before a replay's first day: the EWMA vol throttle
#: (`base.daily_multipliers`, 20-day half-life) is warmed on them, and older
#: bars weigh under 0.2% in it.
WARMUP_DAYS = 180

ACCOUNT = 6_000.0

#: The oct-13 live book's cached state and windows: `_fundednext_parity canon`
#: and `fundednext_report` still replay it. Nothing here writes it any more.
WINDOW_STATE = os.path.join(base.CACHE_DIR, "fundednext_challenge_windows.pkl")
WINDOWS = (("2024-2025 IS", "2024-01-01", "2026-01-01"),
           ("2026 OOS", "2026-01-01", None),
           ("2024-2026", "2024-01-01", None))
RISK_SCALE = 0.05


def default_workers():
    """Half the cores on this Windows PC (it is also the user's gaming PC);
    every core on the Mac."""
    cpus = os.cpu_count() or 4
    return max(1, cpus // 2) if sys.platform == "win32" else cpus


EXCLUDE_SYMBOLS = {"es", "nq", "jp225"}   # jp225: vendor data does not track the broker market

#: What a cell's protocol sets at import. Each is read once when `cfd_families`
#: / `cfd_tt_families` load, so cells searched under different settings are
#: generated in separate child interpreters (as `tt_book` does).
PROTOCOL_VARS = ("FN_TT", "EXNESS_BAR_LOOKBACKS", "EXNESS_WINDOW",
                 "EXNESS_IS_FIRST_YEAR", "EXNESS_TT_STOP_WIDEN",
                 "CFD_BROKER_HOURS", "EXNESS_FILLS_ONLY")


def _protocol(name, cell):
    env = {k: str(v) for k, v in (cell.get("protocol_change") or {}).items()}
    if (cell.get("search") == "cfd_tt_families"
            or str(cell.get("study_wave", "")).startswith("tiktok")):
        env["FN_TT"] = "1"
    stem = cell["timeframe"]
    if "-barlb" in stem:
        env["EXNESS_BAR_LOOKBACKS"] = "1"
    if stem.endswith("-off"):
        env["EXNESS_WINDOW"] = "off"
    # FundedNext's quoted hours, fills only: how the ncf search, the 2026
    # filter and the leverage ladder all ran (`ncf/run.sh`, `_leverage_fundednext`)
    env.update(CFD_BROKER_HOURS="1", EXNESS_FILLS_ONLY="1")
    if cell.get("ncf_window") not in (None, "rth"):
        env["EXNESS_WINDOW"] = cell["ncf_window"]
    return tuple(sorted(env.items()))


def _here():
    """The protocol this interpreter was started under."""
    return tuple(sorted((k, os.environ[k]) for k in PROTOCOL_VARS
                        if k in os.environ))


def _tf_of(name, cell):
    """`30m`, `15m-barlb`, `60m-barlb-off`... -- the cell's `timeframe`
    (ncf cells add their window: `30m-night`)."""
    return f"{cell['timeframe']}-{cell['window']}" if "ncf_window" in cell else cell["timeframe"]


def _alias(symbol):
    """The series a cell marks on: `<symbol>` in RTH, `<symbol>~<window>` for any
    other window (`~off`, `~w1100-1700`) -- every bar of the day, its own key,
    so a night cell's bars never overwrite an RTH cell's."""
    window = os.environ.get("EXNESS_WINDOW") or "rth"
    return symbol if window == "rth" else f"{symbol}~{window}"


def _key(row):
    return f"{row['symbol']}:{row['family']}"


def _stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def _label(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _lev(cell):
    """The highest whole leverage whose 2024-2025 drawdown stays on target (1x
    when none does, or the cell has no ladder)."""
    lev = cell.get("leverage") or {}
    ok = [s["k"] for s in lev.get("ladder", [])
          if s["2024-2025"]["max_dd_pct"] <= lev["target_dd_pct"]]
    return float(max(ok)) if ok else 1.0


def pool_cells():
    """`[(name, cell)]`: every cell in `POOL_DIR` but es / nq."""
    out = []
    wave = os.environ.get("FN_LOG_WAVE")
    for name in sorted(os.listdir(POOL_DIR)):
        if not name.endswith(".json") or name == "SURVIVORS.json":
            continue
        with open(os.path.join(POOL_DIR, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        if "symbol" not in cell:  # `screen.json` and other run files, not cells
            continue
        if cell["symbol"] in EXCLUDE_SYMBOLS:
            continue
        if wave and cell.get("study_wave") != wave:
            continue
        # ncf cells (new_cfd_families export): bar and window come from the
        # timeframe label and the searched protocol's execution window
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
    """A (symbol, bar) group's file under `protocol` (this interpreter's when
    None) -- so the parent can find a child's file without starting it."""
    protocol = _here() if protocol is None else tuple(
        (k, v) for k, v in protocol if k in PROTOCOL_VARS)
    window = dict(protocol).get("EXNESS_WINDOW") or "rth"
    alias = symbol if window == "rth" else f"{symbol}~{window}"
    tag = hashlib.sha1(repr(protocol).encode()).hexdigest()[:8]
    return os.path.join(GROUP_DIR, f"{alias}_{bar}m_{tag}.pkl")


def _cells_path(path):
    """The pool file names a group file was generated from, one per line."""
    return path[:-len(".pkl")] + ".cells"


#: Bumped whenever the backtest's trade path changes, so every cached group log
#: is regenerated. `flatten-clock-...`: the FundedNext early session flatten
#: (`cfd_families.flatten_lead_seconds`); `fn-sessions`: no fill outside its
#: trade session (`cfd_families.FUNDEDNEXT_CLOSED`).
LOG_RULE = "rule:flatten-clock-fx1654-1658-us500-1656:fn-sessions"


def _write_cells(path, names):
    with open(_cells_path(path), "w", encoding="utf-8") as handle:
        handle.write("\n".join([LOG_RULE, *sorted(names)]))


def _is_current(path, names):
    """A group file is reused only if it was generated from exactly these cells
    (a pool edit that keeps a group's name must not keep its old logs) under
    the current `LOG_RULE`."""
    try:
        with open(_cells_path(path), encoding="utf-8") as handle:
            return (os.path.exists(path)
                    and handle.read() == "\n".join([LOG_RULE, *sorted(names)]))
    except OSError:
        return False


def _log_group(job):
    """One (symbol, bar) group's logs -> its own file."""
    (symbol, bar), items = job
    path = _group_path(symbol, bar)
    lo, hi = int(os.environ["FN_LOG_LO"]), int(os.environ["FN_LOG_HI"])
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
                        "group": os.path.basename(path), "days": _days(log)}
    # The book marks every position on its symbol's 30m bars: in-session for
    # an RTH cell, every bar of the day for any other window (`tt_book`).
    ef.BAR_MINUTES = 30
    marks = [b for b in ef.all_bars(symbol, "validate", 30)
             if b[ef.TS] < hi and (alias != symbol or ef.in_session(symbol, b[ef.TS]))]
    with open(path + ".tmp", "wb") as handle:
        pickle.dump({"summary": summary, "logs": logs, "bars": {alias: marks},
                     "ctx": {alias: {"cfg": ctx["cfg"], "symbol": symbol}}},
                    handle, protocol=5)
    os.replace(path + ".tmp", path)
    _write_cells(path, [name for name, _ in items])


def _lead_has():
    """Group files the PC held when a two-host run started (`FN_LOG_HAVE`, a
    pushed listing of `GROUP_DIR`): the Mac's own cache is empty, so without it
    the Mac would redo every group the PC already has."""
    path = os.environ.get("FN_LOG_HAVE")
    if not path:
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def pool_logs(workers, lo, hi, fresh=False):
    """Every pool cell's trade log over [lo, hi). Each missing (protocol, symbol,
    bar) group runs in its own child started under the cell's protocol, all
    protocols on one queue of `workers` children; a cached group is read here.

    Two hosts (`twohost`): each claims a group before running it, the Mac from
    the far end of the queue, and hands its group file back to the PC."""
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
        have = _lead_has()
        todo = [k for k in todo[::-1]
                if os.path.basename(_group_path(k[1], k[2], k[0])) not in have]
    print(f"{sum(len(v) for v in groups.values())} cells in {len(keys)} groups; "
          f"{len(todo)} groups to run on {workers} workers", flush=True)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def unit(key):
        return twohost.unit_id("logs", os.path.basename(_group_path(key[1], key[2], key[0])),
                               sorted(groups[key]))

    def run(key):
        protocol, symbol, bar = key
        if twohost.enabled() and not twohost.claim(unit(key)):
            return "other host"
        env = {k: v for k, v in os.environ.items() if k not in PROTOCOL_VARS}
        env.update(dict(protocol))
        # the holding skip both searches ran with (`run_fns.sh`, `ncf/run.sh`)
        env.update(PYTHONIOENCODING="utf-8", EXNESS_HOLD_SKIP="1",
                   FN_LOG_LO=str(lo), FN_LOG_HI=str(hi))
        done = subprocess.run([sys.executable, "-m",
                               "sandbox.research.fundednext_combined_strategies",
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
            print("twohost: Mac side finished; the PC collects and writes the summary")
            return
        results = twohost.collect(unit(k) for k in todo)
        for k in todo:
            got = results[unit(k)]
            if "data" in got:  # a group the Mac worked: install it here
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
    """Child: one (symbol, bar) group of this interpreter's protocol."""
    here = _here()
    items = [(name, cell) for name, cell in pool_cells()
             if _protocol(name, cell) == here
             and (cell["symbol"], cell["bar_minutes"]) == (symbol, bar)]
    _log_group(((symbol, bar), items))


# --------------------------------------------------------------------------- #
# the rule: members and dial from trades that closed before `hi`
# --------------------------------------------------------------------------- #

def _stats(days, k=1.0):
    """Return %, closed drawdown %, trades and PF of a per-day array on $6,000,
    the P&L taken k times (the sleeve at its leverage, without compounding)."""
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
    """`base.choose_members`' daily streams, built only for the cells it reaches."""

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
            "paths": paths, "account": ACCOUNT,
            "leverage": "highest whole ladder k with 2024-2025 dd on target",
            "lev_cap": LEV_CAP, "dud_share": DUD_SHARE, "draws": DRAWS, "dud_unit": "symbol",
            "overshoot_max": OVERSHOOT_MAX,
            "pass_tolerance": PASS_TOLERANCE,
            "dial": "highest stressed Monte Carlo pass rate over the sleeves that fit "
                    "the minimum lot at each risk; within pass_tolerance of it, the "
                    "fewest stressed expected days to a pass with retries, then the "
                    "higher pass rate, then the most members"}


def choose(cells, hi):
    """Every cell the gates admit, best first, from trades closed before `hi` only."""
    rows, days = [], {}
    for key, cell in cells.items():
        if cell["symbol"] in EXCLUDE_SYMBOLS:
            continue
        days[key] = cell["days"][cell["days"][:, 0] * base.DAY < hi]
        k = min(cell["k"], LEV_CAP)
        rows.append({c: cell[c] for c in ("symbol", "family", "alias", "file", "group")}
                    | {"k": k} | _stats(days[key], k))
    rows.sort(key=lambda r: -r["return"])
    chosen, rejected = base.choose_members(rows, _Streams(days), limit=len(rows),
                                           max_rho=GATE_RHO, max_lift=GATE_LIFT)
    return rows, chosen, rejected


def _series(m):
    """The bars a member marks on: `<symbol>` for an RTH cell, `<symbol>~off` for
    any other window. Every non-RTH window marks on the same all-day bars, so
    they share one series instead of carrying a copy each."""
    return m["symbol"] if m["alias"] == m["symbol"] else f"{m['symbol']}~off"


def load_state(members, lo, hi):
    """The members' trades closed before `hi`, and marking bars from
    `WARMUP_DAYS` before `lo` to `hi`; a replay of it opens nothing before `lo`."""
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
            logs[_key(m)] = [dict(t, symbol=series) for t in got["logs"][_key(m)]
                             if t["exit_ts"] < hi]
            spec = got["ctx"][m["alias"]]["cfg"]
            floor = sorted(spec["volume_min"] * t["distance"] * spec["multiplier"]
                           for t in logs[_key(m)] if t["distance"] > 0)
            minlot[_key(m)] = floor[len(floor) // 2] if floor else 0.0
            if series not in bars_by:
                bars_by[series] = [b for b in got["bars"][m["alias"]]
                                   if first <= b[ef.TS] < hi]
                ctx_by[series] = got["ctx"][m["alias"]]
    return {"members": [dict(m, alias=_series(m), minlot_usd=round(minlot[_key(m)], 2))
                        for m in members], "logs": logs,
            "bars_by": bars_by, "ctx_by": ctx_by,
            "scale": {_key(m): m["k"] for m in members}, "start": lo, "end": hi,
            # loss limits judged at each bar's worst price (`ch.worst_marks`)
            "intrabar": True}


_STATE = None


def _init(path):
    global _STATE
    with open(os.path.join(twohost.ROOT, path), "rb") as handle:   # absolute or repo-relative
        _STATE = pickle.load(handle)


def _asked(m, risk):
    """The dollars a sleeve's stop is asked to risk at `risk` on $6,000."""
    return ACCOUNT * ef.RISK_FRACTION * m["k"] * risk


def _fits(m, risk):
    """The broker-minimum lot's risk at the stop (median over the sleeve's trades)
    is at most `OVERSHOOT_MAX` times what the dial asks of the sleeve."""
    return m["minlot_usd"] <= OVERSHOOT_MAX * _asked(m, risk)


def _prefix(state, n, risk=None):
    """The state cut to its best-first `n` members (that fit the minimum lot at
    `risk`, when one is given)."""
    members = [m for m in state["members"] if risk is None or _fits(m, risk)][:n]
    keys = {_key(m) for m in members}
    aliases = {m["alias"] for m in members}
    return {**state, "members": members,
            "logs": {k: v for k, v in state["logs"].items() if k in keys},
            "bars_by": {a: v for a, v in state["bars_by"].items() if a in aliases},
            "ctx_by": {a: v for a, v in state["ctx_by"].items() if a in aliases},
            "scale": {k: v for k, v in state["scale"].items() if k in keys}}


def _dud(log, keep=0.0):
    """The sleeve with its edge taken out (`keep` of it left): every trade keeps
    its timing, side and size, but its points lose (1 - keep) times the sleeve's
    mean R-multiple times its own stop distance -- at keep 0 the sleeve nets
    zero under risk sizing."""
    r = [t["points"] / t["distance"] for t in log if t["distance"] > 0]
    cut = (1 - keep) * (sum(r) / len(r) if r else 0.0)
    return [dict(t, points=t["points"] - cut * t["distance"]) if t["distance"] > 0
            else t for t in log]


#: The default report's "if my edge shrinks" rows: every sleeve keeps this
#: share of its own edge (1.0 = the real days as they were).
EDGE_KEPT = (1.0, 0.75, 0.5, 0.25)


def _edge_label(keep):
    return f"edge {keep:.0%}"


def _mc_job(job):
    """One (size, dial) STRESSED challenge Monte Carlo of the best-first `n`."""
    n, risk, paths, _ = job
    state = _prefix(_STATE, n, risk)
    rows, days, duds = stressed_paths(state, risk, paths)
    from sandbox.research import fundednext_challenge as ch
    return {"risk": risk, "members": n, "days": days, **ch.mc_stats(rows),
            "dud_share_drawn": round(duds / (DRAWS * max(1, n)), 2)}


def _costed(log, cost_r):
    """Every trade pays `cost_r` of its own stop distance more."""
    return [dict(t, points=t["points"] - cost_r * t["distance"]) if t["distance"] > 0
            else t for t in log]


#: The harsher Monte Carlos (`montecarlo(harsh=True)`): label -> (dud share,
#: extra R per trade, draws). 20 draws make 75% exact (10 draws rounded it to 80%).
HARSH = {"dud75": (0.75, 0.0, 20), "costly": (DUD_SHARE, 0.05, DRAWS)}


def stressed_paths(state, risk, paths, share=DUD_SHARE, cost_r=0.0, draws=DRAWS, keep=1.0):
    """The STRESSED challenge Monte Carlo's paths: `draws` draws, each with
    every sleeve on a dud symbol (exactly `share` of the draws) losing all its
    edge, every other sleeve keeping `keep` of its edge, and every trade paying
    `cost_r` R more, `paths` split across them. Returns (`mc_paths` rows,
    server days, duds drawn)."""
    import random
    from sandbox.research import exness_combined_montecarlo as mc
    from sandbox.research import fundednext_challenge as ch
    if abs(share * draws - round(share * draws)) > 1e-9:
        raise ValueError(f"a dud share of {share} is not a whole number of {draws} draws")
    mc._MARKING.clear()   # the marking cache holds for one set of bars only
    per = max(1, paths // draws)
    # The dud is a SYMBOL: a fake edge is usually the market's regime, and it
    # takes every sleeve on that market with it (a sleeve-level dud let 15 gold
    # sleeves count as 15 independent bets; that book failed 2026 max-loss 68
    # times in 193 starts). Common luck: a symbol is a dud in exactly DUD_SHARE
    # of the draws, the same draws in every (size, risk) cell, and draw d samples
    # the same Monte Carlo paths everywhere -- so cells differ by their books,
    # not by their dice. Free draws let a 1-sleeve book dodge its duds.
    symbols = {k.split(":", 1)[0] for k in state["logs"]}
    dud_in = {s: set(random.Random(s).sample(range(draws), round(share * draws)))
              for s in symbols}
    rows, duds = [], 0
    for d in range(draws):
        out = {k for k in state["logs"] if d in dud_in[k.split(":", 1)[0]]}
        duds += len(out)
        logs = {k: (_dud(v) if k in out else v if keep == 1 else _dud(v, keep))
                for k, v in state["logs"].items()}
        if cost_r:
            logs = {k: _costed(v, cost_r) for k, v in logs.items()}
        days = ch.daily_series({**state, "logs": logs}, risk, fixed=True)
        rows += ch.mc_paths(days, per, 7000 + d)
    return rows, len(days), duds


def montecarlo(state, risk, paths, seed, harsh=False):
    """The book's challenge Monte Carlos: STRESSED (symbol duds), then one per
    `EDGE_KEPT` share -- every sleeve keeping that share of its edge, its days
    block-resampled with `seed` ("edge 100%" is the real days as they were);
    with `harsh`, also each `HARSH` one."""
    from sandbox.research import exness_combined_montecarlo as mc
    from sandbox.research import fundednext_challenge as ch
    rows, days, _ = stressed_paths(state, risk, paths)
    out = {"stressed": {"risk": risk, "days": days, **ch.mc_stats(rows)}}
    for label, (share, cost_r, draws) in (HARSH.items() if harsh else ()):
        rows, _, _ = stressed_paths(state, risk, paths, share, cost_r, draws)
        out[label] = {"risk": risk, "days": days, **ch.mc_stats(rows)}
    for keep in EDGE_KEPT:
        mc._MARKING.clear()
        logs = state["logs"] if keep == 1 else {
            k: _dud(v, keep) for k, v in state["logs"].items()}
        series = ch.daily_series({**state, "logs": logs}, risk, fixed=True)
        out[_edge_label(keep)] = {"risk": risk, "days": days,
                                  **ch.mc_stats(ch.mc_paths(series, paths, seed))}
    return out


#: The default report's grid, every cell `REPORT_PATHS` challenges: SYMBOLS
#: ALIVE (the share of symbols that keep their full edge; every strategy on the
#: others has none) x EDGE KEPT (the share of its edge every surviving strategy keeps).
SYMBOLS_ALIVE = (1.0, 0.75, 0.5, 0.25)
GRID_DRAWS = 20          # whole quarters of the symbols' draws
REPORT_PATHS = 10_000
_GRID = {}


def _grid_job(job):
    """One grid cell: (state path, risk, symbols alive, edge kept, paths, seed)."""
    from sandbox.research import exness_combined_montecarlo as mc
    from sandbox.research import fundednext_challenge as ch
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
        rows = ch.mc_paths(ch.daily_series({**state, "logs": logs}, risk, fixed=True), paths, seed)
    else:
        rows, _, _ = stressed_paths(state, risk, paths, 1 - alive, 0.0, GRID_DRAWS, keep)
    return {"alive": alive, "keep": keep, **ch.mc_stats(rows)}


def _grid_name(alive, keep):
    if alive == 1 and keep == 1:
        return "full backtest edge"
    return f"sym alive {alive:.0%}" if keep == 1 else f"edge kept {keep:.0%}"


GRID_NOTE = ("  sym alive N%: N% of the symbols keep their full edge, every strategy on the "
             f"others has none ({GRID_DRAWS} draws); edge kept N%: every strategy keeps N% of "
             "its backtest edge; full backtest edge: the backtest days as they were")


def _grid_table(cells):
    """`cells` {(alive, keep): stats}: the one-axis rows in full, then pass % /
    median trading days for every (symbols alive, edge kept) pair."""
    print(MC_HEAD)
    for alive, keep in ((1.0, 1.0), *((a, 1.0) for a in SYMBOLS_ALIVE[1:]),
                        *((1.0, k) for k in EDGE_KEPT[1:])):
        print(_mc_line(cells[(alive, keep)], _grid_name(alive, keep)))
    print(GRID_NOTE)
    print(MC_NOTE)
    print("\n  each cell: pass % / trading days to pass / deepest drop $ (passing paths)")
    for q, title in ((50, "p50 challenge"), (75, "p75 challenge"), (95, "p95 challenge")):
        print(f"\n  {title:<48}" + "".join(f"{'edge kept ' + format(k, '.0%'):>22}"
                                         for k in EDGE_KEPT))
        for a in SYMBOLS_ALIVE:
            row = []
            for k in EDGE_KEPT:
                c = cells[(a, k)]
                days = c["median_trading_days"] if q == 50 else c.get(f"p{q}_trading_days")
                text = f"{_cut(days)}d / ${_cut(c.get(f'deepest_{q}'))}"
                row.append(f"{c['pass']:.1f}% / " + text)   # pass % is one per cell
            print(f"  {'sym alive ' + format(a, '.0%'):<48}" + "".join(f"{x:>22}" for x in row))


def _dial_jobs(state, paths):
    fit = {risk: sum(_fits(m, risk) for m in state["members"]) for risk in RISKS}
    jobs = sorted(((n, risk, paths, 0) for risk in RISKS if fit[risk]
                   for n in sorted({n for n in SIZES if n < fit[risk]} | {fit[risk]})),
                  key=lambda job: -job[0])   # the biggest books first
    return jobs, fit


def _rel(path):
    """`path` relative to the repo, '/'-separated: the same file on both hosts."""
    return os.path.relpath(os.path.abspath(path), twohost.ROOT).replace(os.sep, "/")


def _claimed(item):
    """One job claimed on the two-host board first (None: the other host has it)."""
    fn, unit, job = item
    if not twohost.claim(unit):
        return None
    out = fn(job)
    twohost.finish(unit, out)
    return out


def _run_local(fn, jobs, units, workers, init=None, initargs=()):
    """`fn(job)` for every job on `workers` local processes, in job order;
    with `units` each job is claimed first."""
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
    """`fn(job)` for every job, results in job order. With `TWOHOST_RUN` set the
    Mac works the same list from the far end: `files` (repo paths the jobs read)
    and the list go to it, it starts `_shared --spec`, and this host collects --
    every job is claimed once, so none runs twice."""
    if not twohost.enabled():
        return _run_local(fn, jobs, None, workers, init, initargs)
    units = [twohost.unit_id(name, *job) for job in jobs]
    spec = os.path.join(_CACHE, f"twohost_{name}.pkl")
    with open(spec, "wb") as handle:
        pickle.dump({"fn": fn.__name__, "jobs": jobs, "units": units,
                     "init": init.__name__ if init else None, "initargs": initargs}, handle)
    twohost.push([*files, spec])
    twohost.start_mac("sandbox.research.fundednext_combined_strategies",
                      ["_shared", "--spec", _rel(spec)], f"{name}.log")
    _run_local(fn, jobs, units, workers, init, initargs)
    got = twohost.collect(units)
    return [got[u] for u in units]


def _mac_shared(spec, workers):
    """The Mac's side of `_shared`: the pushed list, from the far end."""
    with open(os.path.join(twohost.ROOT, spec), "rb") as handle:
        s = pickle.load(handle)
    init = globals()[s["init"]] if s["init"] else None
    _run_local(globals()[s["fn"]], s["jobs"][::-1], s["units"][::-1], workers,
               init, tuple(s["initargs"]))
    print("twohost: Mac side finished", flush=True)


def dial(state, path, paths, workers):
    """Every (size, `RISKS`) pair through the Monte Carlo; the best one. With
    `TWOHOST_RUN` set the Mac takes cells too: the state is pushed to it, it
    starts `_dial` on the same list from the small end, and this host collects."""
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
    """The chosen cell's sleeves: the admitted rows, best first, that fit the
    minimum lot at its risk, cut to its size."""
    fitted = [m for m in state["members"] if _fits(m, best["risk"])][:best["members"]]
    rows = {_key(r): r for r in chosen}
    return [dict(rows[_key(m)], minlot_usd=m["minlot_usd"]) for m in fitted]


#: Row names as printed (the result keys stay `stressed`, `dud75`, ... in seals and logs).
MC_NAMES = {"stressed": "1/2 sym no edge", "dud75": "3/4 sym no edge",
            "costly": "1/2 sym no edge+cost", "plain": "edge kept 100%",
            **{_edge_label(k): f"edge kept {k:.0%}" for k in EDGE_KEPT}}
_NAME_W = max(map(len, MC_NAMES.values())) + 1
MC_HEAD = (f"{'':{_NAME_W}}{'pass':>7}{'max':>6}{'daily':>6}{'days p50/75/95':>16}"
           f"{'<=20d':>7}{'<=40d':>7}{'<=60d':>7}{'w/ retry':>10}"
           f"{'worst day p50/75/95':>21}{'deepest p50/75/95':>19}")
MC_NOTE = ("  days: trading days to pass both phases (p50 / p75 / p95 of passing paths); "
           "<=Nd: share of ALL paths passed within N; w/ retry: expected days until a pass "
           "restarting every failed attempt; worst day / deepest: $ p50/p75/p95 of PASSING paths")


def _cut(v):
    return "-" if v is None else f"{v:g}"


def _p3(c, prefix=None):
    """p50/p75/p95 of days (no `prefix`) or of `worst_day` / `deepest`."""
    if prefix is None:
        vals = (c["median_trading_days"], c.get("p75_trading_days"), c.get("p95_trading_days"))
    else:
        vals = (c.get(f"{prefix}_50"), c.get(f"{prefix}_75"), c.get(f"{prefix}_95"))
    return "/".join(map(_cut, vals))


def _mc_line(c, label=""):
    return (f"{MC_NAMES.get(label, label):{_NAME_W}}{c['pass']:>6.1f}%{c['max']:>5.1f}%{c['daily']:>5.1f}%"
            f"{_p3(c):>16}"
            + "".join(f"{c['within'][d]:>6.1f}%" for d in sorted(c["within"]))
            + f"{_cut(c['expected_days']):>10}"
            f"{_p3(c, 'worst_day'):>21}{_p3(c, 'deepest'):>19}")


MC_LABELS = ("stressed", *HARSH, *map(_edge_label, EDGE_KEPT), "plain")
MC_ROWS_NOTE = ("  1/2 sym no edge: in each draw half the SYMBOLS lose all edge (every "
                "strategy on them), the rest keep theirs -- the book is picked on it; "
                "edge kept N%: every strategy keeps N% of its backtest edge "
                "(100% = the backtest days as they were)")


def _mc_table(out):
    print(MC_HEAD)
    for label in MC_LABELS:   # "plain" = edge 100% in tests logged before the edge rows
        if label in out:
            print(_mc_line(out[label], label))
    print(MC_ROWS_NOTE)
    print(MC_NOTE)


def _show(title, rows, chosen, rejected, cells, best, paths, members):
    from sandbox.research import fundednext_challenge as ch
    print(f"\n{title}")
    print(f"pool {len(rows)} -> {len(chosen)} admitted (down_rho <= {GATE_RHO}, "
          f"loss_lift <= {GATE_LIFT}, no cap); {len(rejected)} refused")
    print(f"\n'{MC_NAMES['stressed']}' challenge Monte Carlo pass both % -- each SYMBOL "
          f"loses all edge with p={DUD_SHARE:g}, {DRAWS} draws, {paths:,} paths, {ch.MC_BLOCK_DAYS}-day "
          f"blocks of {cells[0]['days']} server days; in-sample")
    sizes = sorted({c["members"] for c in cells})
    grid = {(c["members"], c["risk"]): c for c in cells}
    print(f"{'members':>8}" + "".join(f"{r:>8}" for r in RISKS))
    for n in sizes:
        print(f"{n:>8}" + "".join(
            (f"{grid[(n, r)]['pass']:>7.1f}{'*' if grid[(n, r)] is best else ' '}"
             if (n, r) in grid else f"{'-':>7} ") for r in RISKS))
    print(f"\n'{MC_NAMES['stressed']}' expected trading days to a pass with retries (the tie-break)")
    print(f"{'members':>8}" + "".join(f"{r:>8}" for r in RISKS))
    for n in sizes:
        print(f"{n:>8}" + "".join(
            (f"{grid[(n, r)]['expected_days'] or '-':>7}{'*' if grid[(n, r)] is best else ' '}"
             if (n, r) in grid else f"{'-':>7} ") for r in RISKS))
    top = max(c["pass"] for c in cells)
    print(f"  - : fewer sleeves fit the minimum lot at that risk")
    print(f"  * chosen: {best['members']} members at risk {best['risk']} -- the fewest "
          f"expected days to a pass with retries ({best['expected_days']:g}), within "
          f"{PASS_TOLERANCE:g} point of the best stressed pass rate ({top:.1f}%); every "
          f"sleeve's minimum lot within {OVERSHOOT_MAX:g}x of its asked risk")
    _mc_table({"stressed": best})
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
    print("  return / dd: the sleeve alone at its leverage on $6,000, P&L summed (no compounding)")


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
    admitted, chosen = len(chosen), members
    keys = [_key(m) for m in chosen]
    seal_id = hashlib.sha256(json.dumps({"members": keys, "risk": best["risk"]},
                                        sort_keys=True).encode()).hexdigest()[:12]
    seal = {"id": seal_id,
            "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chosen_on": [_label(summary["start"]), _label(ef.IS_END)],
            "rule": _rule(paths), "pool": len(rows), "pool_dir": summary["pool"],
            "pool_saw_2026": summary["saw_2026"], "risk": best["risk"],
            "admitted": admitted, "members": chosen, "in_sample_montecarlo": cells}
    with open(SEAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(seal, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nsealed {seal_id}: {len(chosen)} sleeves at risk {best['risk']} -> {SEAL_PATH}")


def _tests():
    if not os.path.exists(TEST_LOG):
        return []
    with open(TEST_LOG, encoding="utf-8") as handle:
        return json.load(handle)


def test(workers):
    """The sealed book on 2026, once, at the sealed dial: the challenge Monte
    Carlo grid (`SYMBOLS_ALIVE` x `EDGE_KEPT`, `REPORT_PATHS` paths a cell) on
    2024-2025, where it was chosen, and on 2026 -- on both hosts with `TWOHOST_RUN`."""
    from sandbox.research import fundednext_challenge as ch
    with open(SEAL_PATH, encoding="utf-8") as handle:
        seal = json.load(handle)
    history = _tests()
    first = history[0]["seal"] if history else seal["id"]
    clean = first == seal["id"] and not seal["pool_saw_2026"]
    start, lo, hi = _stamp(seal["chosen_on"][0]), ef.IS_END, ef.OOS_END
    risk, paths = seal["risk"], REPORT_PATHS
    jobs, files, where = [], [], {}
    for label, x, y in (("2024-2025", start, lo), ("2026", lo, hi)):
        path = os.path.join(_CACHE, f"fundednext_challenge_test_{label}.pkl")
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
        print("  NOT CLEAN: the pool was screened on 2026 -- this reads high; the "
              "clean test is data after 2026-10-13")
    else:
        print(f"  NOT CLEAN: 2026 was already spent on seal {first}")
    print(f"\nchallenge Monte Carlo, {paths:,} paths a cell, {ch.MC_BLOCK_DAYS}-day blocks")
    for label, cells in windows.items():
        print(f"\n{label}" + ("  -- where the book was chosen" if label != "2026" else ""))
        _grid_table(cells)
    print("  Loss limits judged at each bar's worst price (1-minute highs/lows) -> if anything harsh.")
    grid = {label: {f"{a:g}/{k:g}": c for (a, k), c in cells.items()}
            for label, cells in windows.items()}
    history.append({"seal": seal["id"], "clean": clean, "risk": risk, "paths": paths,
                    "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "montecarlo": windows["2026"][(1.0, 1.0)],
                    "stressed": windows["2026"][(0.5, 1.0)], "grid": grid})
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
    if _rule(paths) != seal["rule"]:
        raise SystemExit("the rule changed after the 2026 test; that would tune it on 2026")
    summary = _summary()
    rows, chosen, rejected = choose(summary["cells"], ef.OOS_END)
    state = load_state(chosen, summary["start"], ef.OOS_END)
    cells, best = dial(state, FINAL_STATE, paths, workers)
    members = _book(state, chosen, best)
    _show(f"STEP 3 -- the same rule on {_label(summary['start'])}..{_label(ef.OOS_END)}: "
          f"THE BOOK TO TRADE", rows, chosen, rejected, cells, best, paths, members)
    admitted, chosen = len(chosen), members
    t = tested[-1]
    print(f"\nexpect the 2026 test{'' if t['clean'] else ' or LESS (it was not clean)'}"
          f", not the table above (seal {seal['id']}, risk {seal['risk']}):")
    if "grid" in t:
        _grid_table({tuple(map(float, k.split("/"))): v for k, v in t["grid"]["2026"].items()})
    elif "edges" in t:   # tests logged before the grid
        _mc_table(t["edges"])
    elif "stressed" in t:
        _mc_table({"stressed": t["stressed"], "plain": t["montecarlo"]})
    else:   # a test logged before the Monte Carlo report
        print(f"  Monte Carlo {t['montecarlo']['pass']:.1f}% pass")
    with open(FINAL_PATH, "w", encoding="utf-8") as handle:
        json.dump({"seal": seal["id"], "rule": seal["rule"], "risk": best["risk"],
                   "chosen_on": [_label(summary["start"]), _label(ef.OOS_END)],
                   "pool": len(rows), "admitted": admitted, "members": chosen,
                   "in_sample_montecarlo": cells, "test_2026": t},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"wrote {FINAL_PATH}")


def replay(state, risk_scale, initial=ACCOUNT, lo=None, hi=None):
    """The members on one shared balance (the oct-13 live book's tools use it)."""
    return base.replay(state["members"], state["logs"], state["bars_by"],
                       state["ctx_by"], scale=state.get("scale") or {}, sizing_cap={},
                       risk_scale=risk_scale, gross_cap=None, fair_cap=False,
                       initial=initial, force_minimum=True,
                       lo=state["start"] if lo is None else lo,
                       hi=state["end"] if hi is None else hi)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("logs", "_group", "build", "test", "final", "_shared"))
    parser.add_argument("--spec", help="_shared: the pushed job list (the Mac's side of `_shared`)")
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--symbol", help="_group: the child's symbol")
    parser.add_argument("--bar", type=int, help="_group: the child's bar minutes")
    parser.add_argument("--fresh", action="store_true", help="logs: drop resumable groups")
    parser.add_argument("--paths", type=int, default=10_000,
                        help="Monte Carlo paths per dial (sealed with the rule)")
    args = parser.parse_args()
    if args.command == "logs":
        pool_logs(args.workers, _stamp(LOG_START), ef.OOS_END, args.fresh)
    elif args.command == "_group":
        _one_group(args.symbol, args.bar)
    elif args.command == "_shared":   # the Mac's side of a two-host `_shared`
        _mac_shared(args.spec, args.workers)
    elif args.command == "build":
        build(args.workers, args.paths)
        test(args.workers)
        final(args.workers)
    elif args.command == "test":
        test(args.workers)
    else:
        final(args.workers)


if __name__ == "__main__":
    main()
