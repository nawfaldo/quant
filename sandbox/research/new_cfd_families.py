"""The filtered CFD families, priced on Exness Pro or FundedNext.

Filter: beat the coin-flip null on >= 2 symbols in
sandbox/results/exness/SURVIVORS.json.
FAMILIES holds 86, one list: the 59 cfd_families of
sandbox/results/SESSION_2026-10-11_families.md (wave 1: 45 by the filter,
wave 2: 14) and the 27 cfd_tt_families that pass the same filter (2026-10-16).

Pipeline per family x timeframe x window:
  select (IS) -> validate (OOS on the IS winner) -> coinflip (on the OOS winner)
Windows (default all 14): rth, offhour, the day in 8 three-hour slices and in
4 six-hour slices (`EIGHTHS`, `QUARTERS`), into sandbox/results/new_cfd_families/<broker>/<family>-<timeframe>-<window>/

    python -m sandbox.research.new_cfd_families --broker fundednext \
        --bar-minutes 30,60 [--window rth,offhour,morning] [--families vwap,orb] [--symbols all]
"""

import argparse
import json
import os
import subprocess
import sys

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

COST_MODELS = {"exness": EXNESS_PRO, "fundednext": FUNDEDNEXT}

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
)
assert len(FAMILIES) == len(set(FAMILIES)) == 86

# --------------------------------------------------------------------------- #
# time windows -- New York clock. The day starts at the 17:00 rollover, the
# broker day, because no window may cross it (a position would pay a swap the
# session model never charges). Folder name -> what `--window` is given.
# --------------------------------------------------------------------------- #

#: The day in 8 slices of 3 hours.
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

#: Every window, run in this order by default: the cash session, entries only
#: while it is shut, then the slices.
WINDOWS = {"rth": "rth", "offhour": "off", **EIGHTHS, **QUARTERS}

# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #

#: Pick the winner in-sample, run it out-of-sample, then coin-flip the
#: directions of every OOS winner (OOS return > 0).
STAGES = ("select", "validate", "coinflip")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RESULTS = os.path.join(HERE, "..", "results", "new_cfd_families")


def timeframe(bar):
    return "1d" if int(bar) >= 1440 else f"{int(bar)}m"


def result_dir(broker, family, bar, window):
    return os.path.join(RESULTS, broker, f"{family}-{timeframe(bar)}-{window}")


def main():
    parser = argparse.ArgumentParser(description="filtered CFD families: "
                                                 "IS -> OOS -> coin flip")
    parser.add_argument("--broker", choices=sorted(COST_MODELS), required=True)
    parser.add_argument("--bar-minutes", default="30")
    parser.add_argument("--window", default=",".join(WINDOWS),
                        help=f"comma-separated, of {', '.join(WINDOWS)}")
    parser.add_argument("--families", default=",".join(FAMILIES))
    parser.add_argument("--symbols", default="all")
    parser.add_argument("--skip-done", action="store_true",
                        help="skip a cell whose directory already holds a result")
    args, extra = parser.parse_known_args()

    windows = args.window.split(",")
    bad = sorted(set(windows) - set(WINDOWS))
    if bad:
        raise SystemExit(f"unknown window {bad}; known: {list(WINDOWS)}")
    families = args.families.split(",")
    unknown = sorted(set(families) - set(FAMILIES))
    if unknown:
        raise SystemExit(f"not a filtered family: {unknown}")

    from sandbox.research import cfd_families     # noqa: PLC0415  (slow import)
    env = dict(os.environ, CFD_BROKER=args.broker,
               CFD_COMMISSION=json.dumps(COST_MODELS[args.broker]))
    for family in families:
        # A cfd_tt family is registered only in cfd_tt_families, which also
        # refuses --broker and reads CFD_BROKER instead.
        tt = family not in cfd_families.FAMILIES
        module = ("sandbox.research.cfd_tt_families" if tt
                  else "sandbox.research.cfd_families")
        broker_flag = [] if tt else ["--broker", args.broker]
        for bar in args.bar_minutes.split(","):
            for window in windows:
                out = result_dir(args.broker, family, bar, window)
                if args.skip_done and os.path.isdir(out) and os.listdir(out):
                    continue
                os.makedirs(out, exist_ok=True)
                print(f"== {args.broker}/{os.path.basename(out)}", flush=True)
                for stage in STAGES:
                    subprocess.run(
                        [sys.executable, "-m", module, stage, *broker_flag,
                         "--symbols", args.symbols, "--families", family,
                         "--bar-minutes", bar, "--window", WINDOWS[window],
                         *extra],
                        cwd=ROOT, env=dict(env, CFD_RESULTS_DIR=out), check=True)


if __name__ == "__main__":
    main()
