"""The FundedNext search as a two-fold WALK-FORWARD, with its overfit measured.

WHY IT IS BUILT THIS WAY (2026-10-31). The old run picked params on 2024-2025,
then used 2026 three times: as the survivor filter, as the book's ranking and
as the final re-pick, so its "2026 OOS" was in-sample (the 14 trading days after
the canon was sealed came in at the 17th percentile). Here every number a later
step may select on is out of sample for the params that produced it:

    fold A   params chosen on 2024        -> traded out of sample in 2025
    fold B   params chosen on 2024-2025   -> traded out of sample in 2026

so each cell carries a walk-forward out-of-sample stream: 2025 on fold-A params,
2026 on fold-B params (`wf_params`). FundedNext's own minute tables start
2024-01, so two calendar folds are all the data allows (`CFD_SPLIT` is
calendar years). Live trades fold-B params; fold C (IS 2024-2026) is the
January 2027 refit.

FEWER TRIES. Six non-overlapping windows (the cash session, off-hours, the day
in four six-hour slices) instead of fourteen overlapping ones; the eighths stay
available with `--window`. The 86 families are kept (the user's choice; 98 since
2026-11-13 with 12 @tradebydata survivors) --
NOTE: they were pre-filtered on Exness survivors that saw 2025-2026, a
family-level leak this module cannot undo.

PEAK-PROOF PARAMS. `EXNESS_SELECT_RANK=plateau`: a cell is ranked by the median
t-stat of itself and its neighbours, so the pick sits on a plateau, not a spike.

OVERFIT, MEASURED. `CFD_TRIAL_STREAMS=1` makes `select` keep every trial's
weekly returns (`<result>_trials.npz`). `export` turns them into:
  pbo   CSCV probability of backtest overfitting (Bailey, Borwein, Lopez de
        Prado, Zhu 2014): over 70 splits of the in-sample weeks, the share where
        the in-sample best trial lands below the median out of sample
  dsr   deflated Sharpe of the fold's winner, for the real number of trials and
        their Sharpe variance (Bailey & Lopez de Prado 2014) -- a diagnostic

    py -m sandbox.research.new_cfd_families run [--symbols ...] [--bar-minutes 5,15,30,60]
        [--window ...] [--kinds cfd,tt] [--workers N] [--host pc]   # resumable
    py -m sandbox.research.new_cfd_families export     # -> results/fundednext_wf/
    py -m sandbox.research.new_cfd_families summary

WHICH PROP FIRM. `--broker fundednext` (the default; every result above) or
`--broker the5ers` (also `NCF_BROKER=the5ers`), placed anywhere on the line.
The broker sets the cost model, the account size, the symbol list, the folds
and every output folder (`BROKER_SETUPS`), and is passed to each child as
`CFD_BROKER`, so `cfd_families` prices that firm's spread (its own 1m table),
commission, swap, daily break and leverage (`cfd_families.BROKERS`). The
account-level rules -- targets, daily / max loss, profitable days, the news
window -- are not a family-search question; they live in
`sandbox/research/the5ers_rules.py` for The5ers and `fundednext_challenge.py`
for FundedNext.

    py -m sandbox.research.new_cfd_families run --broker the5ers --symbols xauusd
"""
from __future__ import annotations

import argparse
import glob
import io
import itertools
import json
import math
import os
import statistics
import subprocess
import sys
import tarfile
import time

import numpy as np

# --------------------------------------------------------------------------- #
# cost models -- both charge spread from the broker's own 1m table
# (`cfd_families`); they differ only in commission. Rules are `(kind, value)`
# per asset class, charged once per trade: `usd_per_lot` is USD per lot,
# `notional_pct` is percent of notional.
# --------------------------------------------------------------------------- #

#: Exness Pro: spread only; commission measured at 0.0000.
EXNESS_PRO = {}

#: FundedNext, MEASURED 2026-10-14 on demo 35188708 (mt5/FUNDEDNEXT_COMMISSION.md):
#: $3/lot on forex and oil, booked at the close; indices $0. The close-vs-open
#: timing does not change a per-trade backtest, so it is charged once.
#: Metals are ASSUMED to follow oil ($3/lot) -- unmeasured. Crypto and stocks
#: keep the help-centre figures (0.04%, 0.004%) -- also unmeasured.
FUNDEDNEXT = {
    "forex": ("usd_per_lot", 3.0),
    "commodity": ("usd_per_lot", 3.0),
    "crypto": ("notional_pct", 0.04),
    "stock": ("notional_pct", 0.004),
}

#: The5ers High Stakes (FivePercentOnline-Real), UNMEASURED -- the account has
#: no trade yet: forex $4/lot round turn, metals 0.0008% of notional (oil
#: ASSUMED the same), indices 0. The same figures as
#: `cfd_families.BROKERS['the5ers']`, which documents the sources.
THE5ERS = {
    "forex": ("usd_per_lot", 4.0),
    "commodity": ("notional_pct", 0.0008),
    "crypto": ("notional_pct", 0.04),
}

COST_MODELS = {"exness": EXNESS_PRO, "fundednext": FUNDEDNEXT, "the5ers": THE5ERS}

# --------------------------------------------------------------------------- #
# families
# --------------------------------------------------------------------------- #

FAMILIES = (
    "adaptive_pullback", "amihud", "aroon", "atp_alligator_ema200",
    "av_ema_systems", "av_first_hour_fib", "av_morning_momentum",
    "break_retest", "cci", "cf_runner_red_flip", "climax", "confluence",
    "cusum", "day_of_week", "efficiency", "entropy", "estimator",
    "failed_break", "floor_pivot", "fracdiff", "fx_breakout_delta",
    "gated_donchian", "gated_fade", "gated_orb", "gated_swing", "half_life",
    "ib", "jt_hull_suite", "jump", "kalman", "kendall", "key_reversal",
    "level_confluence", "linreg_trend", "lux_htf_manipulation",
    "lux_structure_poc", "luxalgo_manipulation", "macd_hist", "momentum",
    "momentum_stack", "mql_big_move_cont", "nmk_value_area_80",
    "nt_donchian_close", "obv_break", "obv_divergence", "orb", "overnight",
    "pdr", "pullback", "qc_ema_cross", "qp_ma_cross", "qp_rsi_extreme",
    "qp_volume_breakout", "qtt_alma_macd", "qtt_ema_macd",
    "qtt_rsi_ema_retest", "qtt_triple_ema", "qtt_williams_rsi_macd",
    "quantile_break", "range_expansion", "regime_breakout", "roofing", "rvol",
    "sar", "skew", "squeeze", "stochastic", "structure", "supertrend",
    "swing_break", "swing_ma", "td_halftrend", "tqb_red_days_long",
    "tr_darvas", "tr_zero_cross", "trap", "two_stage", "value_area",
    "vol_of_vol", "vol_regime", "volatility_breakout", "volume_thrust", "vwap",
    "xma_cross", "xma_ribbon", "yf_alphatrend",
    # @tradebydata (2026-11-13). The 12 families with at least one FundedNext
    # survivor in the simple-split run (in-sample 2024-25, out of sample
    # 2026-01-01..11-12, 30m, 29 symbols x 6 windows): an OOS winner (return > 0,
    # PF >= 1.05, >= 20 trades) whose `why` is PASS or PASS*. Results in
    # results/new_cfd_families/fundednext_tbd/. Clock/multiday rules, see
    # `tt_tradebydata`.
    "tbd_candle_count", "tbd_fib_dynamic", "tbd_fractal3", "tbd_ibs",
    "tbd_macdv", "tbd_multi_span", "tbd_overnight", "tbd_session_box",
    "tbd_sma_regime", "tbd_vol_breakout", "tbd_volatile_red", "tbd_weekday",
)
assert len(FAMILIES) == len(set(FAMILIES)) == 98

# --------------------------------------------------------------------------- #
# time windows -- New York clock. The day starts at the 17:00 rollover, the
# broker day, because no window may cross it (a position would pay a swap the
# session model never charges). Folder name -> what `--window` is given.
# --------------------------------------------------------------------------- #

#: The day in 8 slices of 3 hours (opt-in: they overlap QUARTERS).
EIGHTHS = {
    "evening": "17:00-20:00",
    "tokyo_open": "20:00-23:00",
    "tokyo": "23:00-02:00",
    "london_open": "02:00-05:00",
    "london": "05:00-08:00",
    "ny_open": "08:00-11:00",
    "ny_midday": "11:00-14:00",
    "ny_close": "14:00-17:00",
}

#: The day in 4 slices of 6 hours.
QUARTERS = {
    "night": "17:00-23:00",
    "early_morning": "23:00-05:00",
    "morning": "05:00-11:00",
    "afternoon": "11:00-17:00",
}

#: Every window a run may name.
WINDOWS = {"rth": "rth", "offhour": "off", **QUARTERS, **EIGHTHS}
#: The default: no two overlap in kind, so one market hour is tried at most twice
#: (once in its slice, once as cash session or off-hours), not four times.
DEFAULT_WINDOWS = ("rth", "offhour", *QUARTERS)

# --------------------------------------------------------------------------- #
# the walk-forward
# --------------------------------------------------------------------------- #

#: Every prop firm this search can run for. `folds` maps fold -> `CFD_SPLIT`
#: (in-sample calendar years); `fold_oos` is when each fold's params trade
#: out of sample (`None` = to `data_end`). `symbols` are the ones with broker
#: minutes (never es / nq: futures twins of us500 / ustec; never jp225:
#: vendor data does not track the broker market, [[no-jp225-anywhere]]).
BROKER_SETUPS = {
    "fundednext": {
        "balance": 6000,
        "folds": {"A": "2024", "B": "2024-2025"},
        "fold_oos": {"A": ("2025-01-01", "2026-01-01"), "B": ("2026-01-01", None)},
        "data_end": os.environ.get("FN_DATA_END", "2026-10-31"),
        "symbols": ("audjpy", "audusd", "aus200", "cadjpy", "chfjpy", "de40",
                    "euraud", "eurjpy", "eurusd", "fr40", "gbpaud", "gbpjpy",
                    "gbpusd", "hk50", "nth25", "nzdusd", "stoxx50", "swi20",
                    "uk100", "us2000", "us30", "us500", "usdcad", "usdchf",
                    "usdjpy", "ustec", "xagusd", "xauusd", "xptusd"),
        "wave": "fundednext_wf",
    },
    #: The5ers High Stakes $5K (26823080). NOT a walk-forward (the user,
    #: 2026-11-07: "dont do walk forward, just like current fundednext live"):
    #: the protocol the FundedNext LIVE book's pool came from
    #: (`sandbox/results/logs/ncf/run.sh`, 2026-10-16) -- one split, select ->
    #: validate -> `why` on the OOS winners (return > 0, PF >= 1.05, >= 20
    #: trades), survivors = `why` PASS / PASS* (`survivors`). The SPLIT is
    #: longer, because The5ers' own M1 reaches 2020 where FundedNext's starts
    #: 2024 (the user: "thats too low considering all 5ers symbol is from
    #: 2020"): in-sample 2020-2024 (5 years), out of sample 2025-01-01 to
    #: `data_end` (~22 months) -- twice FundedNext's OOS for `why` to judge.
    #: Symbols:
    #: every research symbol it quotes that FundedNext's search also ran,
    #: plus ukoil / usoil (it quotes Brent and WTI; FundedNext's search left
    #: oil out). nth25 / swi20 / us2000 are not quoted there.
    #: SUPERSEDED 2026-11-07 (the user: "stop this and use walk forward"):
    #: the single split above showed 2025's gold rally carrying cells that
    #: made ~nothing in 2026. Now THREE ANCHORED FOLDS, each OOS year judged
    #: only on params chosen before it (`protocol: wf3`, `run_wf3`):
    #:   A  IS 2020-2023 -> OOS 2024      filter year 1
    #:   B  IS 2020-2024 -> OOS 2025      filter year 2, + `why` (coin flip)
    #:   C  IS 2020-2025 -> OOS 2026      final check; live trades C's params
    #: Survivor (`survivors`): OOS winner in BOTH 2024 and 2025 (return > 0,
    #: PF >= 1.05, >= 20 trades) and `why` PASS / PASS* on fold B. 2026 is
    #: never selected on. Ranking stays the plain t-stat (as the FundedNext
    #: live pool), no trial streams.
    "the5ers": {
        "balance": 5000,
        "protocol": "wf3",
        "folds": {"A": "2020-2023", "B": "2020-2024", "C": "2020-2025"},
        "fold_oos": {"A": ("2024-01-01", "2025-01-01"), "B": ("2025-01-01", "2026-01-01"),
                     "C": ("2026-01-01", None)},
        "data_end": os.environ.get("T5_DATA_END", "2026-11-06"),
        "symbols": ("audjpy", "audusd", "aus200", "cadjpy", "chfjpy", "de40",
                    "euraud", "eurjpy", "eurusd", "fr40", "gbpaud", "gbpjpy",
                    "gbpusd", "hk50", "nzdusd", "stoxx50", "uk100", "ukoil",
                    "us30", "us500", "usdcad", "usdchf", "usdjpy", "usoil",
                    "ustec", "xagusd", "xauusd", "xptusd",
                    # crypto (all `trade_mode` FULL): Binance vendor bars,
                    # filled on the5ers_*_1m. Spread runs 0.4 bp (BTCUSD) to
                    # 804 bp (DOGUSD) -- kept so the search, not a guess,
                    # rejects the expensive ones.
                    "btc", "ethusd", "solusd", "btceur", "etheur", "ltcusd",
                    "xrpusd", "dogeusd"),
        "wave": "the5ers_wf",
    },
}


def _argv_broker():
    """`--broker X` anywhere on the line (before or after the sub-command),
    else `NCF_BROKER`, else fundednext -- read at import because every
    constant below depends on it."""
    argv = sys.argv[1:]
    for i, token in enumerate(argv):
        if token == "--broker" and i + 1 < len(argv):
            return argv[i + 1]
        if token.startswith("--broker="):
            return token.split("=", 1)[1]
    return os.environ.get("NCF_BROKER", "fundednext")


#: T5_PROTOCOL=fn (the user, 2026-11-08: "use the same year as fundednext"):
#: The5ers on FundedNext's live-pool protocol -- one split, in-sample
#: 2024-2025, out of sample 2026, select -> validate -> `why` on the OOS
#: winners -- for the symbols FundedNext never searched, so their cells
#: compare with the FundedNext pool replayed on The5ers (`_replay_the5ers`).
if os.environ.get("T5_PROTOCOL") == "fn":
    BROKER_SETUPS["the5ers"] = dict(BROKER_SETUPS["the5ers"], protocol="single",
                                    folds={"S": "2024-2025"},
                                    fold_oos={"S": ("2026-01-01", None)},
                                    wave="the5ers_fn")

#: FN_PROTOCOL=single (the user, 2026-11-13, for the @tradebydata group):
#: FundedNext on the live pool's simple split -- in-sample 2024-2025, out of
#: sample 2026 to `FN_DATA_END`, select -> validate -> `why` on the OOS
#: winners -- instead of the two-fold walk-forward. Results go to their own
#: wave (`NCF_WAVE`, default `fundednext_fn`) so no walk-forward file is touched.
if os.environ.get("FN_PROTOCOL") == "single":
    BROKER_SETUPS["fundednext"] = dict(BROKER_SETUPS["fundednext"], protocol="single",
                                       folds={"S": "2024-2025"},
                                       fold_oos={"S": ("2026-01-01", None)},
                                       wave=os.environ.get("NCF_WAVE", "fundednext_fn"))

BROKER = _argv_broker()
if BROKER not in BROKER_SETUPS:
    raise SystemExit(f"unknown --broker {BROKER!r}; known: {sorted(BROKER_SETUPS)}")
SETUP = BROKER_SETUPS[BROKER]

FOLDS = SETUP["folds"]
FOLD_OOS = SETUP["fold_oos"]
DATA_END = SETUP["data_end"]
#: `walkforward` (folds A/B, coinflip, plateau rank, trial streams -- the
#: FundedNext default) or `single` (the FundedNext live pool's protocol:
#: one split, select -> validate -> why on the OOS winners).
PROTOCOL = SETUP.get("protocol", "walkforward")
SINGLE = PROTOCOL == "single"
#: The5ers' three-fold walk-forward with `why` on fold B (`run_wf3`). Shares
#: the single protocol's env (plain t-stat, no trial streams) and export path.
WF3 = PROTOCOL == "wf3"
SINGLE = SINGLE or WF3

#: Stages per fold. Walk-forward: `why` (three full re-searches) is gone, the
#: coin flip on the OOS window is the null and the shrinkage downstream does
#: the rest. Single: `why` stays -- it is the user's coin flip
#: ([[coin-flip-means-why]]) -- and runs only on the OOS winners.
STAGES = ("select", "validate", "why") if SINGLE else ("select", "validate", "coinflip")
#: An OOS winner, the cells `why` is run on (single protocol; `ncf/winners.py`).
WIN_MIN_TRADES, WIN_MIN_PF = 20, 1.05

#: The protocol every cell is searched under (was ncf/run.sh), for this broker.
ENV = {
    "CFD_BALANCE": str(SETUP["balance"]), "CFD_BROKER": BROKER,
    "CFD_BROKER_HOURS": "1", "EXNESS_FILLS_ONLY": "1", "EXNESS_HOLD_SKIP": "1",
    # bit-identical results, a quarter of the memory (`cfd_families`)
    "CFD_TRIM_WARMUP": "1",
    "CFD_COMMISSION": json.dumps(COST_MODELS[BROKER]),
    "PYTHONIOENCODING": "utf-8",
}
if not SINGLE:
    # walk-forward only: the FundedNext live pool was ranked on the plain
    # t-stat and kept no trial streams
    ENV.update({"CFD_TRIAL_STREAMS": "1", "EXNESS_SELECT_RANK": "plateau"})

SYMBOLS = SETUP["symbols"]
BARS = (5, 15, 30, 60)
#: Ordered last in a run's queue (the user, 2026-11-07: "crypto last").
CRYPTO = frozenset(("btc", "ethusd", "solusd", "btceur", "etheur", "ltcusd",
                    "xrpusd", "dogeusd"))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RESULTS = os.path.join(HERE, "..", "results", "new_cfd_families")
SRC = os.path.join(RESULTS, SETUP["wave"])
STATE = os.path.join(SRC, "_run")
POOL = os.path.join(HERE, "..", "results", SETUP["wave"])

#: Export pre-filter, fold A only (2025, before anything downstream trades):
#: enough trades to judge, and the direction is not a coin flip.
MIN_OOS_TRADES = 20
MAX_COINFLIP_P = 0.30
#: CSCV blocks (8 -> 70 in-sample / out-of-sample splits).
CSCV_BLOCKS = 8


def timeframe(bar):
    return "1d" if int(bar) >= 1440 else ("1h" if int(bar) == 60 else f"{int(bar)}m")


def result_dir(kind, symbol, bar, window):
    return os.path.join(SRC, kind, f"{symbol}-{timeframe(bar)}-{window}")


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def families_by_kind(families):
    """{'cfd': [...], 'tt': [...]}: a family registered in `cfd_families` runs
    there, the rest are `cfd_tt_families`."""
    os.environ.setdefault("CFD_BROKER", BROKER)
    from sandbox.research import cfd_families   # noqa: PLC0415  (slow import)
    cfd = [f for f in families if f in cfd_families.FAMILIES]
    return {"cfd": cfd, "tt": [f for f in families if f not in cfd]}


def _done(host):
    path = os.path.join(STATE, f"done_{host}.txt")
    seen = set()
    for name in glob.glob(os.path.join(STATE, "done_*.txt")):
        with open(name, encoding="utf-8") as handle:
            # "DONE <kind> <symbol> <window> <bar> <date>"
            seen |= {" ".join(line.split()[1:5]) for line in handle
                     if line.startswith("DONE ")}
    return path, seen


def _stage_cmd(kind, symbol, window, bar, families, stage):
    module = f"sandbox.research.{'cfd_families' if kind == 'cfd' else 'cfd_tt_families'}"
    # cfd_tt_families refuses `--broker`; it reads `CFD_BROKER` from `ENV`.
    broker = ["--broker", BROKER] if kind == "cfd" else []
    return [sys.executable, "-m", module, stage, *broker, "--symbols", symbol,
            "--families", ",".join(families), "--bar-minutes", str(bar),
            "--window", WINDOWS[window]]


def _fold_env(fold, out):
    """A fold's child env. Fold A's out of sample ENDS at 2026-01-01: its
    validate and coin flip feed the filter, so they may not see 2026 (the
    2026-10-31 run let them run to `DATA_END` -- a leak, fixed by `revalidate`)."""
    end = FOLD_OOS[fold][1] or DATA_END
    return dict(os.environ, **ENV, CFD_SPLIT=FOLDS[fold], CFD_OOS_END=end,
                CFD_RESULTS_DIR=out)


def oos_winners(out):
    """Families of one job folder that won out of sample: return > 0,
    PF >= 1.05, >= 20 trades (the FundedNext live pool's `ncf/winners.py`)."""
    found = set()
    for path in glob.glob(os.path.join(out, "*.json")):
        if path.endswith("_coinflip.json") or "_null_" in path:
            continue
        payload = _load(path)
        validation = payload.get("validation") or {}
        for family, cell in (payload.get("families") or {}).items():
            oos = (validation.get(family) or {}).get("oos") or {}
            if (cell and oos.get("return_pct", 0) > 0 and (oos.get("pf") or 0) >= WIN_MIN_PF
                    and oos.get("trades", 0) >= WIN_MIN_TRADES):
                found.add(family)
    return sorted(found)


def run_single(kind, symbol, window, bar, families, workers, log):
    """One job under the single protocol: select (2024-2025) -> validate
    (2026) -> `why` on the OOS winners. True if every stage ran."""
    out = result_dir(kind, symbol, bar, window)
    os.makedirs(out, exist_ok=True)
    env = _fold_env("S", out)
    for stage in ("select", "validate", "why"):
        fams = families
        if stage == "why":
            fams = [f for f in oos_winners(out) if f in families]
            if not fams:
                log.write("== why: no OOS winner\n")
                break
        cmd = _stage_cmd(kind, symbol, window, bar, fams, stage) + ["--workers", str(workers)]
        log.write(f"== {stage} @{time.strftime('%H:%M:%S')}: {' '.join(cmd[2:])}\n")
        log.flush()
        start = os.path.getsize(log.name)
        done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
        log.flush()
        with open(log.name, encoding="utf-8", errors="replace") as handle:
            handle.seek(start)
            said = handle.read()
        # The scheduled runners (`cfd_tt_families select/validate/why`) exit 0
        # even when their one job failed ("1 job(s) failed", 2026-11-07: every
        # Mac job "done" in 0.0 min on a missing module), so the stage's own
        # output is read as well as its exit code.
        if done.returncode != 0 or "job(s) failed" in said or "Traceback (most recent" in said:
            # as ncf/run.sh: any failed stage is a FAIL, retried by the next pass
            log.write(f"!! {stage} failed (exit {done.returncode})\n")
            return False
    return True


def _stage(kind, symbol, window, bar, fams, stage, env, workers, log):
    """Run one stage; False on a non-zero exit or a failed scheduled job."""
    cmd = _stage_cmd(kind, symbol, window, bar, fams, stage) + ["--workers", str(workers)]
    log.write(f"== {env.get('CFD_SPLIT')} {stage} @{time.strftime('%H:%M:%S')}: {' '.join(cmd[2:])}\n")
    log.flush()
    start = os.path.getsize(log.name)
    done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
    log.flush()
    with open(log.name, encoding="utf-8", errors="replace") as handle:
        handle.seek(start)
        said = handle.read()
    if done.returncode != 0 or "job(s) failed" in said or "Traceback (most recent" in said:
        log.write(f"!! {env.get('CFD_SPLIT')} {stage} failed (exit {done.returncode})\n")
        return False
    return True


def fold_winners(out, fold):
    """OOS winners of one fold's result file in a job folder."""
    path = _fold_files(out).get(fold)
    if not path:
        return set()
    payload = _load(path)
    validation = payload.get("validation") or {}
    return {f for f, cell in (payload.get("families") or {}).items()
            if cell and _wins((validation.get(f) or {}).get("oos") or {})}


def _wins(oos):
    return (oos.get("return_pct", 0) > 0 and (oos.get("pf") or 0) >= WIN_MIN_PF
            and oos.get("trades", 0) >= WIN_MIN_TRADES)


def run_wf3(kind, symbol, window, bar, families, workers, log):
    """Three anchored folds: select + validate on A, B, C (each fold's OOS is
    its own year), then `why` on fold B for the families that won OOS in
    BOTH A (2024) and B (2025). True if every stage ran."""
    out = result_dir(kind, symbol, bar, window)
    os.makedirs(out, exist_ok=True)
    for fold in FOLDS:
        env = _fold_env(fold, out)
        for stage in ("select", "validate"):
            if not _stage(kind, symbol, window, bar, families, stage, env, workers, log):
                return False
    both = sorted((fold_winners(out, "A") & fold_winners(out, "B")) & set(families))
    if not both:
        log.write("== why: no family won OOS in both 2024 and 2025\n")
        return True
    return _stage(kind, symbol, window, bar, both, "why", _fold_env("B", out), workers, log)


def run_job(kind, symbol, window, bar, families, workers, log):
    """One (kind, symbol, window, bar): both folds, every stage. True if all ran."""
    if WF3:
        return run_wf3(kind, symbol, window, bar, families, workers, log)
    if SINGLE:
        return run_single(kind, symbol, window, bar, families, workers, log)
    out = result_dir(kind, symbol, bar, window)
    os.makedirs(out, exist_ok=True)
    for fold in FOLDS:
        env = _fold_env(fold, out)
        # Speed (2026-10-31): only fold A's PBO and coin flip are gates, so
        # fold B keeps no trial streams and runs no coin flip (pbo_B is then
        # absent -- it was a diagnostic only).
        stages = STAGES
        if fold != "A":
            env["CFD_TRIAL_STREAMS"] = "0"
            stages = tuple(s for s in STAGES if s != "coinflip")
        for stage in stages:
            cmd = _stage_cmd(kind, symbol, window, bar, families, stage)
            if stage == "select":
                cmd += ["--workers", str(workers)]
            log.write(f"== fold {fold} {stage} @{time.strftime('%H:%M:%S')}: {' '.join(cmd[2:])}\n")
            log.flush()
            done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
            if done.returncode != 0:
                # no family selected -> validate / coinflip have nothing to read
                if stage != "select" and "no OOS winner" in _tail(log.name):
                    break
                log.write(f"!! fold {fold} {stage} exit {done.returncode}\n")
                return False
    return True


def _tail(path, n=4000):
    with open(path, encoding="utf-8", errors="replace") as handle:
        handle.seek(max(0, os.path.getsize(path) - n))
        return handle.read()


def run(args):
    windows = args.window.split(",")
    bad = sorted(set(windows) - set(WINDOWS))
    if bad:
        raise SystemExit(f"unknown window {bad}; known: {list(WINDOWS)}")
    if args.filtered5 and BROKER != "fundednext":
        raise SystemExit("--filtered5 reads results/filtered/fundednext; "
                         f"there is no {BROKER} equivalent")
    families = args.families.split(",")
    if args.group:
        # A creator group of `cfd_tt_families` (e.g. tradebydata), run as is:
        # its families need not be in FAMILIES.
        # Imported with this command line hidden: `cfd_families` reads
        # `--window` / `--broker` from argv at import, and ours are not its.
        saved, sys.argv = sys.argv, sys.argv[:1]
        try:
            from sandbox.research import cfd_tt_families   # noqa: PLC0415
        finally:
            sys.argv = saved
        families = list(cfd_tt_families.GROUPS[args.group])
        if args.families != ",".join(FAMILIES):
            # `--group X --families a,b`: only those members of the group
            wanted = set(args.families.split(","))
            families = [f for f in families if f in wanted]
    unknown = sorted(set(families) - set(FAMILIES))
    if unknown and not args.group:
        raise SystemExit(f"not a filtered family: {unknown}")
    symbols = SYMBOLS if args.symbols == "all" else tuple(args.symbols.split(","))
    # A group is all `tt`; classifying it would misfire, since importing
    # `cfd_tt_families` installs its families into `cfd_families.FAMILIES`.
    kinds = ({"tt": families} if args.group else
             {k: v for k, v in families_by_kind(families).items()
              if k in args.kinds.split(",") and v})
    os.makedirs(os.path.join(STATE, "logs"), exist_ok=True)
    os.makedirs(os.path.join(STATE, "locks"), exist_ok=True)
    jobs = [(k, s, w, b) for b in map(int, args.bar_minutes.split(","))
            for s in symbols for w in windows for k in kinds]
    done_path, seen = _done(args.host)
    from sandbox.research import twohost   # noqa: PLC0415
    shared = twohost.enabled()
    todo = [j for j in jobs if " ".join(map(str, j)) not in seen]
    # WHO TAKES WHAT. A 5m cfd select holds a 3.3 GB context per worker; the
    # PC (Windows spawns, 17 GB) fits one or two, the Mac (fork, shared pages)
    # runs six. So the Mac takes every 5m cfd job first and the PC never does;
    # everything else is shared, the PC from the light end, the Mac from the far end.
    heavy = [j for j in todo if j[0] == "cfd" and j[3] == 5]
    light = [j for j in todo if not (j[0] == "cfd" and j[3] == 5)]
    # tt 5m jobs go last on the light lists too (the user: "5m last").
    light = [j for j in light if j[3] != 5] + [j for j in light if j[3] == 5]
    if args.filtered5:
        # The user (2026-11-01): cfd 5m only for the families that already
        # survived at 5m on that symbol AND window in results/filtered/fundednext. NOTE
        # that pool was picked on data including 2026, so these 5m cells are
        # pre-selected with 2026 in view.
        heavy = [j for j in heavy if _filtered5(j[1], j[2], kinds["cfd"])]
    if args.order == "mac":
        todo = heavy + light[::-1]
    elif args.order == "pc":
        todo = light
    elif args.order == "light-rev":   # the Mac without 5m cfd (8 GB swaps itself to death)
        n5 = [j for j in light if j[3] != 5]
        todo = n5[::-1] + [j for j in light if j[3] == 5]
    elif shared and twohost.IS_MAC:
        todo = todo[::-1]          # the hosts meet in the middle
    if args.order == "all":
        # the user (2026-11-07): "5m last"
        todo = [j for j in todo if j[3] != 5] + [j for j in todo if j[3] == 5]
    # the user (2026-11-07): "crypto last" -- within each bar group, on both
    # hosts (a stable partition, so 5m stays after everything else)
    todo = sorted(todo, key=lambda j: (j[3] == 5, j[1] in CRYPTO))
    print(f"{len(jobs)} jobs, {len(todo)} to run ({len(jobs) - len(todo)} done)"
          + (f"; shared with the other host (run {twohost.RUN})" if shared else ""), flush=True)
    for i, (kind, symbol, window, bar) in enumerate(todo, 1):
        tag = " ".join(map(str, (kind, symbol, window, bar)))
        if shared and twohost.IS_MAC and _free_gb() < MIN_FREE_GB:
            print(f"only {_free_gb():.1f} GB free -- this host stops taking jobs", flush=True)
            break
        unit = twohost.unit_id("ncf_wf", tag)
        if shared:
            if not twohost.claim(unit):
                continue
        else:
            lock = os.path.join(STATE, "locks", tag.replace(" ", "_"))
            try:
                os.mkdir(lock)            # another process already has it
            except FileExistsError:
                continue
        started = time.time()
        log_path = os.path.join(STATE, "logs", tag.replace(" ", "_") + ".log")
        with open(log_path, "a", encoding="utf-8") as log:
            fams = kinds[kind]
            if args.filtered5 and kind == "cfd" and bar == 5:
                fams = _filtered5(symbol, window, fams)
                print(f"  {tag}: filtered families {','.join(fams)}", file=log, flush=True)
            ok = run_job(kind, symbol, window, bar, fams, args.workers, log)
        with open(done_path, "a", encoding="utf-8") as handle:
            handle.write(f"{'DONE' if ok else 'FAIL'} {tag} {time.strftime('%F_%T')}\n")
        if shared:
            twohost.finish(unit, _job_result(tag, kind, symbol, window, bar, ok))
        elif not ok:
            os.rmdir(lock)
        print(f"  {i}/{len(todo)} {tag}: {'done' if ok else 'FAILED'} "
              f"({(time.time() - started) / 60:.1f} min)", flush=True)
    # `NCF_NO_COLLECT=1`: a supervisor (sandbox/results/logs/the5ers/driver.py)
    # pulls the other host's results itself, so this host is free to take new
    # jobs at once instead of waiting here for the other host's last units.
    if shared and not twohost.IS_MAC and os.environ.get("NCF_NO_COLLECT") != "1":
        units = {twohost.unit_id("ncf_wf", " ".join(map(str, j))): j for j in jobs
                 if " ".join(map(str, j)) not in seen}
        got = twohost.collect(units)
        installed = 0
        for unit, result in got.items():
            if result.get("tar"):
                with tarfile.open(fileobj=io.BytesIO(result["tar"])) as tar:
                    tar.extractall(SRC, filter="data")
                installed += 1
        failed = [r["tag"] for r in got.values() if not r.get("ok")]
        print(f"all {len(units)} jobs back; {installed} came from the other host; "
              f"{len(failed)} failed{': ' + ', '.join(failed[:20]) if failed else ''}")


def _bar_of(tf):
    return {"1h": 60, "1d": 1440}.get(tf) or int(tf[:-1])


def _leaky(kind, tf):
    """cfd 5m jobs ran with `--filtered5`: their families were picked from
    results/filtered/fundednext, which saw 2026. Out of the honest pool."""
    return kind == "cfd" and tf == "5m"


def _foldA_years(path):
    """Every calendar year in a fold-A file's OOS validation."""
    payload = _load(path)
    return {y for v in (payload.get("validation") or {}).values()
            for y in ((v or {}).get("oos") or {}).get("annual", {})}


def _revalidate_one(kind, job):
    """Fold A's validate + coin flip again, out of sample 2025 only. The stale
    coin-flip file goes first, so a cell with no 2025 winner keeps none."""
    symbol, tf, window = os.path.basename(job).split("-", 2)
    files = _fold_files(job)
    if "A" not in files:
        return "no fold A"
    stale = files["A"][:-len(".json")] + "_coinflip.json"
    if os.path.exists(stale):
        os.remove(stale)
    families = families_by_kind(FAMILIES)[kind]
    env = _fold_env("A", job)
    log_path = os.path.join(STATE, "logs", f"reval_{kind}_{symbol}_{window}_{_bar_of(tf)}.log")
    with open(log_path, "a", encoding="utf-8") as log:
        for stage in ("validate", "coinflip"):
            cmd = _stage_cmd(kind, symbol, window, _bar_of(tf), families, stage)
            log.write(f"== reval fold A {stage} @{time.strftime('%H:%M:%S')}: {' '.join(cmd[2:])}\n")
            log.flush()
            done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
            if done.returncode != 0 and not (stage == "coinflip" and "no OOS winner" in _tail(log_path)):
                return f"{stage} exit {done.returncode}"
    years = _foldA_years(files["A"])
    return "ok" if years <= {"2025"} else f"OOS years {sorted(years)}"


def revalidate(workers, only=None):
    """Re-run every honest job's fold-A validate + coin flip with OOS 2025 only."""
    from concurrent.futures import ThreadPoolExecutor, as_completed   # noqa: PLC0415
    done_path = os.path.join(STATE, "done_revalidate.txt")
    seen = set()
    if os.path.exists(done_path):
        with open(done_path, encoding="utf-8") as handle:
            seen = {line.split()[1] for line in handle if line.startswith("DONE ")}
    jobs = []
    for kind in ("cfd", "tt"):
        for job in sorted(glob.glob(os.path.join(SRC, kind, "*"))):
            tag = f"{kind}/{os.path.basename(job)}"
            tf = os.path.basename(job).split("-", 2)[1]
            if _leaky(kind, tf) or tag in seen or "A" not in _fold_files(job):
                continue
            if only and tag not in only:
                continue
            jobs.append((kind, job, tag))
    families_by_kind(FAMILIES)   # the slow import, once, before the threads
    print(f"{len(jobs)} jobs to revalidate on {workers} workers ({len(seen)} done)", flush=True)
    started = time.time()
    with ThreadPoolExecutor(max(1, workers)) as threads, \
            open(done_path, "a", encoding="utf-8") as out:
        futures = {threads.submit(_revalidate_one, k, j): t for k, j, t in jobs}
        for i, future in enumerate(as_completed(futures), 1):
            tag = futures[future]
            try:
                status = future.result()
            except Exception as error:   # noqa: BLE001
                status = f"{type(error).__name__}: {error}"
            out.write(f"{'DONE' if status in ('ok', 'no fold A') else 'FAIL'} {tag} {status} "
                      f"{time.strftime('%F_%T')}\n")
            out.flush()
            rate = (time.time() - started) / i
            print(f"  {i}/{len(jobs)} {tag}: {status} ({rate:.0f} s/job avg)", flush=True)


FILTERED = os.path.join(ROOT, "sandbox", "results", "filtered", "fundednext")


def _filtered5(symbol, window, families):
    """`families` with a 5m file for `symbol` AND `window` in results/filtered/fundednext."""
    tail = f"-5m-{window}.json"
    have = {name[len(symbol) + 1:-len(tail)]
            for name in os.listdir(FILTERED)
            if name.startswith(symbol + "_") and name.endswith(tail)}
    return [f for f in families if f in have]


#: The Mac stops taking jobs below this much free disk (it has crashed when full).
MIN_FREE_GB = 1.5


def _free_gb():
    import shutil   # noqa: PLC0415
    return shutil.disk_usage(ROOT).free / 1e9


def _job_result(tag, kind, symbol, window, bar, ok):
    """The twohost result of one job: on the Mac, its whole result directory as
    a tar (installed into `SRC` on the PC by `collect`), then removed locally to
    keep the Mac's small disk free."""
    out = {"tag": tag, "ok": ok}
    from sandbox.research import twohost   # noqa: PLC0415
    if twohost.IS_MAC:
        job = result_dir(kind, symbol, bar, window)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            tar.add(job, arcname=os.path.relpath(job, SRC))
        out["tar"] = buffer.getvalue()
        import shutil   # noqa: PLC0415
        shutil.rmtree(job, ignore_errors=True)
    return out


# --------------------------------------------------------------------------- #
# overfit statistics
# --------------------------------------------------------------------------- #

def _sharpe(matrix):
    """Per-row Sharpe of a trials x weeks matrix (0 where a row never moves)."""
    sd = matrix.std(axis=1)
    mean = matrix.mean(axis=1)
    return np.divide(mean, sd, out=np.zeros_like(mean), where=sd > 0)


def pbo(matrix, blocks=CSCV_BLOCKS):
    """CSCV probability of backtest overfitting: over every half/half split of
    `blocks` contiguous week blocks, the share where the trial that is best on
    one half ranks at or below the median on the other. `None` if too small."""
    trials, weeks = matrix.shape
    if trials < 2 or weeks < 2 * blocks:
        return None
    edges = np.linspace(0, weeks, blocks + 1).astype(int)
    parts = [np.arange(edges[i], edges[i + 1]) for i in range(blocks)]
    logits = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        ins = np.concatenate([parts[i] for i in chosen])
        outs = np.concatenate([parts[i] for i in range(blocks) if i not in chosen])
        best = int(np.argmax(_sharpe(matrix[:, ins])))
        oos = _sharpe(matrix[:, outs])
        rank = (oos < oos[best]).sum() + 0.5 * ((oos == oos[best]).sum() - 1) + 1
        omega = rank / (trials + 1)
        logits.append(math.log(omega / (1 - omega)))
    return round(sum(x <= 0 for x in logits) / len(logits), 3)


def deflated_sharpe(returns, trial_sharpes):
    """Probability the winner's true Sharpe beats the best a pool of
    `len(trial_sharpes)` no-edge trials would show (per-period units)."""
    n, k = len(returns), len(trial_sharpes)
    if n < 10 or k < 2:
        return None
    r = np.asarray(returns, dtype=float)
    sd = r.std()
    if sd == 0:
        return None
    sr = r.mean() / sd
    skew = float(((r - r.mean()) ** 3).mean() / sd ** 3)
    kurt = float(((r - r.mean()) ** 4).mean() / sd ** 4)
    var = float(np.var(trial_sharpes))
    norm = statistics.NormalDist()
    gamma = 0.5772156649
    sr0 = math.sqrt(max(var, 0.0)) * ((1 - gamma) * norm.inv_cdf(1 - 1 / k)
                                      + gamma * norm.inv_cdf(1 - 1 / (k * math.e)))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return None
    return round(norm.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)), 4)


def _trial_streams(path, family):
    """(keys, weeks, matrix) of one family in a `_trials.npz`, or None."""
    if not os.path.exists(path):
        return None
    with np.load(path) as data:
        if f"{family}__r" not in data:
            return None
        return (list(data[f"{family}__keys"]), data[f"{family}__weeks"],
                data[f"{family}__r"])


def _frozen_json(params):
    """The npz's key for a params dict (`es.frozen`: every value as `str`)."""
    # JSON turned the grid's tuples into lists (`cfd_families.rehydrate`)
    return json.dumps({k: str(tuple(v) if isinstance(v, list) else v)
                       for k, v in params.items()}, sort_keys=True)


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

def _fold_files(job):
    """{fold: result json path} for one job directory."""
    out = {}
    for path in glob.glob(os.path.join(job, "*.json")):
        name = os.path.basename(path)
        if "_null_" in name or name.endswith("_coinflip.json"):
            continue
        for fold, split in FOLDS.items():
            first, _, last = split.partition("-")
            # `cfd_families.SPLIT_TAG`: "2024" is tagged `_is2024-2024`
            if f"_is{first}-{last or first}_" in name:
                out[fold] = path
    return out


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _shift(a, b):
    if b is None:
        return "no fold-B pick"
    diff = sum(str(a.get(k)) != str(b.get(k)) for k in set(a) | set(b))
    return "same" if diff == 0 else ("one axis" if diff == 1 else f"{diff} axes")


def cells():
    """Every (job, family) with a fold-A pick, with its walk-forward record."""
    out = []
    for kind in ("cfd", "tt"):
        for job in sorted(glob.glob(os.path.join(SRC, kind, "*"))):
            symbol, tf, window = os.path.basename(job).split("-", 2)
            if _leaky(kind, tf):
                continue
            files = _fold_files(job)
            if "A" not in files:
                continue
            fold = {f: _load(p) for f, p in files.items()}
            flips = {}
            coin = files["A"][:-len(".json")] + "_coinflip.json"
            if os.path.exists(coin):
                flips = _load(coin).get("coinflip") or {}
            for family, a in (fold["A"].get("families") or {}).items():
                if not a:
                    continue
                oos = ((fold["A"].get("validation") or {}).get(family) or {}).get("oos")
                if not oos:
                    continue
                b = ((fold.get("B") or {}).get("families") or {}).get(family)
                b_oos = (((fold.get("B") or {}).get("validation") or {})
                         .get(family) or {}).get("oos")
                stats = {}
                for f, payload in fold.items():
                    got = _trial_streams(files[f][:-len(".json")] + "_trials.npz", family)
                    if got is None:
                        continue
                    keys, _, matrix = got
                    stats[f"pbo_{f}"] = pbo(matrix)
                    stats[f"trials_{f}"] = len(keys)
                    win = (payload.get("families") or {}).get(family)
                    if win and f == "A":
                        k = _frozen_json(win["params"])
                        if k in keys:
                            stats["dsr_A"] = deflated_sharpe(
                                matrix[keys.index(k)], _sharpe(matrix))
                flip = flips.get(family) or {}
                out.append({
                    "symbol": symbol, "family": family, "timeframe": tf,
                    "window": window, "broker": BROKER, "initial_balance": SETUP["balance"],
                    "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
                    "protocol": fold["A"].get("protocol"),
                    # live and the book's last segment trade fold B's params
                    "params": (b or a)["params"],
                    "wf_params": [
                        {"from": FOLD_OOS["A"][0], "to": FOLD_OOS["A"][1],
                         "split": FOLDS["A"], "params": a["params"]},
                        {"from": FOLD_OOS["B"][0], "to": DATA_END,
                         "split": FOLDS["B"], "params": b["params"] if b else None}],
                    "fold_A": {"in_sample": a["in_sample"], "oos": oos,
                               "coinflip_p": flip.get("p_value"),
                               "flip_median_pct": flip.get("flip_median_pct"),
                               "robust_neighbours": a.get("robust_neighbours")},
                    "fold_B": {"note": "2026 -- never read before 2026",
                               "in_sample": (b or {}).get("in_sample"), "oos": b_oos},
                    "param_shift": _shift(a["params"], (b or {}).get("params")),
                    **stats, "study_wave": SETUP["wave"],
                    "provenance": {"fold_A": os.path.relpath(files["A"], SRC),
                                   "fold_B": os.path.relpath(files["B"], SRC)
                                   if "B" in files else None,
                                   "written": time.strftime("%Y-%m-%d")}})
    return out


def _passes_prefilter(cell):
    oos, p = cell["fold_A"]["oos"], cell["fold_A"]["coinflip_p"]
    # the filter reads 2025 only: a fold-A file still validated to 2026 is a leak
    late = set(oos.get("annual", {})) - {"2025"}
    assert not late, (f"{cell['provenance']['fold_A']}: fold-A OOS reaches {sorted(late)} "
                      f"-- run `revalidate`")
    return (oos["trades"] >= MIN_OOS_TRADES and oos["return_pct"] > 0
            and p is not None and p <= MAX_COINFLIP_P)


def _clone_key(c):
    """Cells with the same symbol / bar / window and identical 2025 trades are
    one strategy under several names (entropy / estimator / vol_of_vol ...)."""
    o = c["fold_A"]["oos"]
    return (c["symbol"], c["timeframe"], c["window"], o["trades"], o["return_pct"],
            o["max_dd_pct"], o.get("pnl"))


def dedupe(cells_):
    """First of each clone group, by family name (identity, not results)."""
    seen, out = set(), []
    for c in sorted(cells_, key=lambda c: (c["symbol"], c["timeframe"], c["window"], c["family"])):
        key = _clone_key(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def export(dry_run=False, out=POOL):
    every = cells()
    passed = [c for c in every if _passes_prefilter(c)]
    pool = dedupe(passed)
    print(f"{len(passed) - len(pool)} clones dropped")
    # The shrinkage prior: EVERY fold-A pick's 2025 result, losers included --
    # the honest cross-section the winner's curse is corrected against.
    prior = [{"symbol": c["symbol"], "family": c["family"],
              "t": c["fold_A"]["oos"].get("edge_vs_drift_t_stat"),
              "trades": c["fold_A"]["oos"]["trades"],
              "return_pct": c["fold_A"]["oos"]["return_pct"]} for c in every]
    print(f"{len(every)} fold-A picks; {len(pool)} pass the 2025 pre-filter "
          f"(trades >= {MIN_OOS_TRADES}, return > 0, coin flip p <= {MAX_COINFLIP_P})")
    if dry_run:
        return
    os.makedirs(out, exist_ok=True)
    for name in glob.glob(os.path.join(out, "*.json")):
        os.remove(name)
    for c in pool:
        name = f"{c['symbol']}_{c['family']}-{c['timeframe']}-{c['window']}.json"
        with open(os.path.join(out, name), "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
    with open(os.path.join(out, "_prior.json"), "w", encoding="utf-8") as handle:
        json.dump({"source": "fold A out of sample (2025), every pick",
                   "cells": prior}, handle, indent=1)
        handle.write("\n")
    print(f"wrote {len(pool)} cells + _prior.json -> {out}")


def summary():
    every = cells()
    if not every:
        print("no fold-A results yet")
        return
    pool = [c for c in every if _passes_prefilter(c)]
    def dist(key):
        v = sorted(c[key] for c in every if c.get(key) is not None)
        return (f"n={len(v)} p25 {v[len(v) // 4]:.2f} median {v[len(v) // 2]:.2f} "
                f"p75 {v[3 * len(v) // 4]:.2f}") if v else "-"
    print(f"fold-A picks {len(every)}; with a fold-B pick "
          f"{sum(c['wf_params'][1]['params'] is not None for c in every)}; "
          f"pass the 2025 pre-filter {len(pool)}")
    for key in ("pbo_A", "pbo_B", "dsr_A"):
        print(f"  {key:6} {dist(key)}")
    shifts = {}
    for c in every:
        shifts[c["param_shift"]] = shifts.get(c["param_shift"], 0) + 1
    print("  params A -> B:", ", ".join(f"{k} {v}" for k, v in sorted(shifts.items(), key=lambda kv: -kv[1])))
    both = [(c["fold_A"]["in_sample"].get("edge_vs_drift_t_stat") or 0,
             c["fold_A"]["oos"].get("edge_vs_drift_t_stat") or 0) for c in every]
    if len(both) > 2:
        a, b = (np.argsort(np.argsort(x)) for x in zip(*both))
        print(f"  fold A: in-sample t vs 2025 t, Spearman {np.corrcoef(a, b)[0, 1]:+.3f} "
              f"(~0 = the in-sample pick carries no information)")
    print(f"  total trials: {sum(c.get('trials_A') or 0 for c in every):,} (fold A)")


def scorecard(draws=10000, seed=7):
    """2026 (fold-B params) of each filter stage vs random picks of the same
    size. Read once; the thresholds are not tuned on it."""
    if BROKER != "fundednext":
        raise SystemExit(f"scorecard reads the FundedNext book's RULE; there is no "
                         f"{BROKER} book yet")
    every = dedupe(cells())
    rng = np.random.default_rng(seed)
    r26 = lambda c: (c["fold_B"]["oos"] or {}).get("return_pct")
    pool = [c for c in every if r26(c) is not None]
    allr = np.array([r26(c) for c in pool])
    import importlib   # noqa: PLC0415
    os.environ.setdefault("FN_WF_POOL_DIR", os.path.join(ROOT, "sandbox", "results", "fundednext", "wf"))
    book = importlib.import_module("sandbox.research.fundednext_combined_strategies")
    rule = book.RULE
    s25 = lambda c: (c["fold_A"]["oos"].get("edge_vs_drift_t_stat") or 0) / math.sqrt(max(1, c["fold_A"]["oos"]["trades"]))
    groups = {
        "all fold-A picks (deduped)": pool,
        "export filter (2025: n>=20, ret>0, p<=0.30)": [c for c in pool if _passes_prefilter(c)],
        "RULE gates (2025: n>=40, p<=0.20, PBO<=0.50)": [
            c for c in pool if c["fold_A"]["oos"]["trades"] >= rule["min_trades"]
            and c["fold_A"]["oos"]["return_pct"] > 0
            and (c["fold_A"]["coinflip_p"] is not None and c["fold_A"]["coinflip_p"] <= rule["max_coinflip_p"])
            and (c.get("pbo_A") is None or c["pbo_A"] <= rule["max_pbo"])]}
    gated = groups["RULE gates (2025: n>=40, p<=0.20, PBO<=0.50)"]
    top = sorted(gated, key=lambda c: -s25(c))[:max(1, len(gated) // 10)]
    groups["top 10% of RULE gates by 2025 Sharpe"] = top
    lines = [f"## scorecard {time.strftime('%Y-%m-%d %H:%M')} (rule {book.rule_hash()})", "",
             "| group | n | 2026 median | 2026 mean | % profitable | p vs random |",
             "|---|---|---|---|---|---|"]
    for name, g in groups.items():
        v = np.array([r26(c) for c in g])
        if not len(v):
            lines.append(f"| {name} | 0 | - | - | - | - |")
            continue
        sims = np.array([rng.choice(allr, len(v), replace=False).mean() for _ in range(draws)])
        p = float((sims >= v.mean()).mean())
        lines.append(f"| {name} | {len(v)} | {np.median(v):+.1f}% | {v.mean():+.1f}% | "
                     f"{(v > 0).mean() * 100:.0f}% | {p:.3f} |")
    a = np.array([s25(c) for c in gated]); b = np.array([r26(c) for c in gated])
    if len(a) > 2:
        ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
        lines += ["", f"Spearman, 2025 Sharpe vs 2026 return (RULE-gated): {np.corrcoef(ra, rb)[0, 1]:+.3f}"]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(ROOT, "sandbox", "results", "fundednext_wf_scorecard.md"), "a",
              encoding="utf-8") as handle:
        handle.write(text + "\n\n")


def survivors(dry_run=False):
    """Single protocol: every OOS winner whose `why` verdict is PASS (beats
    every coin-flip seed's OOS) or PASS* (no seed found an IS cell), written
    as `results/<broker>/<sym>_<family>-<tf>-<window>.json` + SURVIVORS.json
    -- the format `_ncf_fundednext_export` gave the FundedNext live pool."""
    if not SINGLE:
        raise SystemExit("survivors is the single protocol's export; use `export`")
    if WF3:
        return survivors_wf3(dry_run)
    dst = os.path.join(HERE, "..", "results", BROKER)
    wave = f"{SETUP['wave']}_{SETUP['balance']}"
    holdout = f"{FOLD_OOS['S'][0]} through {DATA_END}"
    out, winners_seen = [], 0
    for kind in ("cfd", "tt"):
        for job in sorted(glob.glob(os.path.join(SRC, kind, "*"))):
            symbol, tf, window = os.path.basename(job).split("-", 2)
            nulls = {}
            for q in glob.glob(os.path.join(job, "*_null_*.json")):
                nulls.update(_load(q).get("null_control") or {})
            for path in glob.glob(os.path.join(job, "*.json")):
                if "_null_" in path or path.endswith("_coinflip.json"):
                    continue
                payload = _load(path)
                validation = payload.get("validation") or {}
                for family, winner in (payload.get("families") or {}).items():
                    oos = (validation.get(family) or {}).get("oos")
                    if not winner or not oos:
                        continue
                    if (oos["return_pct"] <= 0 or (oos.get("pf") or 0) < WIN_MIN_PF
                            or oos["trades"] < WIN_MIN_TRADES):
                        continue
                    winners_seen += 1
                    if family not in nulls:
                        continue          # `why` not run (or not finished) on it
                    seeds = [r for r in nulls[family] if r]
                    top = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
                    if top is not None and oos["return_pct"] <= top:
                        continue
                    out.append({
                        "symbol": symbol, "family": family, "timeframe": tf,
                        "window": window, "broker": BROKER,
                        "initial_balance": SETUP["balance"],
                        "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
                        "in_sample": winner["in_sample"],
                        "in_sample_years": list(range(int(FOLDS["S"][:4]),
                                                      int(FOLDS["S"][-4:]) + 1)),
                        "params": winner["params"],
                        "robust_neighbours": winner.get("robust_neighbours"),
                        "protocol": payload.get("protocol"), "study_wave": wave,
                        "holdout": holdout, "out_of_sample": oos,
                        "cost_sweep_bp": (validation.get(family) or {}).get("cost_sweep_bp") or {},
                        "null_control": {
                            "method": "why: search re-run x3 with random sides",
                            "best_null_oos_return_pct": top,
                            "margin_pts": None if top is None else round(oos["return_pct"] - top, 2),
                            "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                                     for r in seeds}},
                        "null_verdict": "PASS*" if top is None else "PASS",
                        "provenance": {"seal_sha256": payload.get("seal_sha256"),
                                       "select": os.path.relpath(path, SRC),
                                       "written": time.strftime("%Y-%m-%d")}})
    verdicts = {}
    for c in out:
        verdicts[c["null_verdict"]] = verdicts.get(c["null_verdict"], 0) + 1
    print(f"{winners_seen} OOS winners; {len(out)} survive `why` {verdicts}")
    if dry_run:
        return out
    os.makedirs(dst, exist_ok=True)
    for name in glob.glob(os.path.join(dst, "*.json")):
        if os.path.basename(name) != "SURVIVORS.json" and _load(name).get("study_wave") == wave:
            os.remove(name)
    rows = []
    for c in out:
        name = f"{c['symbol']}_{c['family']}-{c['timeframe']}-{c['window']}.json"
        with open(os.path.join(dst, name), "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True)
            handle.write("\n")
        rows.append({"file": name, "symbol": c["symbol"], "family": c["family"],
                     "timeframe": c["timeframe"], "window": c["window"],
                     "oos_return_pct": c["out_of_sample"]["return_pct"],
                     "oos_max_dd_pct": c["out_of_sample"]["max_dd_pct"],
                     "oos_trades": c["out_of_sample"]["trades"],
                     "null_verdict": c["null_verdict"], "study_wave": wave})
    with open(os.path.join(dst, "SURVIVORS.json"), "w", encoding="utf-8") as handle:
        json.dump({"survivors": rows, "count": len(rows), "written": time.strftime("%Y-%m-%d"),
                   "broker": BROKER, "holdout": holdout}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(rows)} survivors -> {os.path.abspath(dst)}")
    return out


def survivors_wf3(dry_run=False):
    """Three-fold walk-forward export: a cell survives when it won OOS in 2024
    (fold A) AND 2025 (fold B) and its `why` on fold B is PASS / PASS*. Each
    survivor carries every fold's params and OOS, with fold C's 2026 as the
    untouched check and fold C's params as the ones live trades."""
    dst = os.path.join(HERE, "..", "results", f"{BROKER}_wf3")
    wave = f"{SETUP['wave']}_wf3_{SETUP['balance']}"
    out, both_seen, cells_seen = [], 0, 0
    for kind in ("cfd", "tt"):
        for job in sorted(glob.glob(os.path.join(SRC, kind, "*"))):
            symbol, tf, window = os.path.basename(job).split("-", 2)
            files = _fold_files(job)
            if set(files) != set(FOLDS):
                continue
            fold = {f: _load(p) for f, p in files.items()}
            nulls = {}
            for q in glob.glob(os.path.join(job, "*_null_*.json")):
                nulls.update(_load(q).get("null_control") or {})
            for family, b_cell in (fold["B"].get("families") or {}).items():
                picks = {f: (fold[f].get("families") or {}).get(family) for f in FOLDS}
                oos = {f: ((fold[f].get("validation") or {}).get(family) or {}).get("oos")
                       for f in FOLDS}
                if not b_cell:
                    continue
                cells_seen += 1
                if not (oos["A"] and oos["B"] and _wins(oos["A"]) and _wins(oos["B"])):
                    continue
                both_seen += 1
                if family not in nulls:
                    continue
                seeds = [r for r in nulls[family] if r]
                top = max((r["out_of_sample"]["return_pct"] for r in seeds), default=None)
                if top is not None and oos["B"]["return_pct"] <= top:
                    continue
                out.append({
                    "symbol": symbol, "family": family, "timeframe": tf, "window": window,
                    "broker": BROKER, "initial_balance": SETUP["balance"],
                    "search": "cfd_families" if kind == "cfd" else "cfd_tt_families",
                    "protocol": "wf3", "study_wave": wave,
                    # live trades fold C's params (fit on 2020-2025)
                    "params": (picks["C"] or b_cell)["params"],
                    "wf_params": [{"fold": f, "is": FOLDS[f], "oos_from": FOLD_OOS[f][0],
                                   "oos_to": FOLD_OOS[f][1] or DATA_END,
                                   "params": (picks[f] or {}).get("params")} for f in FOLDS],
                    "folds": {f: {"in_sample": (picks[f] or {}).get("in_sample"), "oos": oos[f]}
                              for f in FOLDS},
                    "oos_2024": oos["A"], "oos_2025": oos["B"], "oos_2026": oos["C"],
                    "param_shift_BC": _shift(b_cell["params"], (picks["C"] or {}).get("params")),
                    "null_control": {
                        "method": "why on fold B: search re-run x3 with random sides",
                        "best_null_oos_return_pct": top,
                        "seeds_oos_return_pct": {str(r["seed"]): r["out_of_sample"]["return_pct"]
                                                 for r in seeds}},
                    "null_verdict": "PASS*" if top is None else "PASS",
                    "provenance": {f: os.path.relpath(files[f], SRC) for f in FOLDS}})
    print(f"{cells_seen} fold-B picks; {both_seen} won OOS in 2024 AND 2025; "
          f"{len(out)} also pass `why`")
    if dry_run:
        return out
    os.makedirs(dst, exist_ok=True)
    for name in glob.glob(os.path.join(dst, "*.json")):
        os.remove(name)
    rows = []
    for c in out:
        name = f"{c['symbol']}_{c['family']}-{c['timeframe']}-{c['window']}.json"
        with open(os.path.join(dst, name), "w", encoding="utf-8") as handle:
            json.dump(c, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        c26 = c["oos_2026"] or {}
        rows.append({"file": name, "symbol": c["symbol"], "family": c["family"],
                     "timeframe": c["timeframe"], "window": c["window"],
                     "oos_2024_pct": c["oos_2024"]["return_pct"],
                     "oos_2025_pct": c["oos_2025"]["return_pct"],
                     "oos_2026_pct": c26.get("return_pct"), "null_verdict": c["null_verdict"]})
    with open(os.path.join(dst, "SURVIVORS.json"), "w", encoding="utf-8") as handle:
        json.dump({"survivors": rows, "count": len(rows), "written": time.strftime("%Y-%m-%d"),
                   "broker": BROKER, "protocol": "wf3", "folds": FOLDS, "fold_oos": FOLD_OOS},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"wrote {len(rows)} survivors -> {os.path.abspath(dst)}")
    return out


def main():
    parser = argparse.ArgumentParser(description="Prop-firm walk-forward search "
                                                 "(FundedNext or The5ers)")
    # Read at import by `_argv_broker` (every constant depends on it); declared
    # here so argparse accepts it before the sub-command.
    parser.add_argument("--broker", choices=sorted(BROKER_SETUPS), default=BROKER)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--bar-minutes", default=",".join(map(str, BARS)))
    r.add_argument("--window", default=",".join(DEFAULT_WINDOWS),
                   help=f"comma-separated, of {', '.join(WINDOWS)}")
    r.add_argument("--families", default=",".join(FAMILIES))
    r.add_argument("--group", default="",
                   help="a cfd_tt_families group instead of --families (e.g. tradebydata)")
    r.add_argument("--symbols", default="all")
    r.add_argument("--kinds", default="cfd,tt")
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    r.add_argument("--host", default="pc", help="done-file tag (pc / mac)")
    r.add_argument("--filtered5", action="store_true",
                   help="cfd 5m: only families with a 5m survivor on that symbol and window "
                        "in results/filtered/fundednext")
    r.add_argument("--order", choices=("all", "pc", "mac", "light-rev"), default="all",
                   help="pc: never the 5m cfd jobs; mac: 5m cfd jobs first; light-rev: pc's list from the far end")
    e = sub.add_parser("export")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--out", default=POOL, help="pool folder (default results/fundednext_wf)")
    sub.add_parser("summary")
    v = sub.add_parser("revalidate", help="fold A validate + coin flip again, OOS 2025 only")
    v.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    v.add_argument("--only", default="", help="comma-separated kind/dir tags, e.g. cfd/chfjpy-30m-night")
    sub.add_parser("scorecard")
    s = sub.add_parser("survivors", help="single protocol: why PASS / PASS* -> results/<broker>/")
    s.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if SINGLE and args.command in ("export", "summary", "revalidate", "scorecard"):
        raise SystemExit(f"{args.command} is walk-forward only; {BROKER} runs the "
                         f"single protocol -- use `survivors`")
    if args.command == "run":
        run(args)
    elif args.command == "survivors":
        survivors(args.dry_run)
    elif args.command == "export":
        export(args.dry_run, os.path.abspath(args.out))
    elif args.command == "revalidate":
        revalidate(args.workers, set(filter(None, args.only.split(","))))
    elif args.command == "scorecard":
        scorecard()
    else:
        summary()


if __name__ == "__main__":
    main()
