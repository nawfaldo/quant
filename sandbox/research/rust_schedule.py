"""Generate the Rust engine's `SLEEVE_EXPOSURE_SCHEDULE` for the live book.

    py -B -m sandbox.research.rust_schedule
    SLEEVE_EXPOSURE_SCHEDULE=sandbox/.cache/exposure_schedule.json \
        live_trade/target/debug/live_trade.exe

`backtest/exposure.rs` reads the file once per RUN, so a sweep may rewrite it
between candidates against one warm server. It also fails OPEN: a file it cannot
parse logs to stderr and leaves every sleeve at 1.0, so a run against a stale
binary looks successful while ignoring the schedule entirely. That happened
during this study -- the giveaway was a "gated" result identical to a previous
untouched run. Rebuild the server after changing `exposure.rs`.

THE CONFIGURATION, and why each part of it exists.

TARGET 13. Swept against the ENGINE's mark-to-market drawdown, not py's
closed-trade figure; the two differ by 1.28-1.57x and only the first is what an
account experiences. Drawdown is not monotone in the target -- 10 -> 14.35%,
11 -> 13.36%, 12 -> 13.67%, 13 -> 16.11% -- because the 3.0 cap and the lot step
make the response lumpy, so it was swept rather than solved.

MAROY LADDER OFF. One BTC lot is ~900 dollars, and this sleeve's own sizing
asks for ~1.15x notional. Throttled to the target-13 median multiplier of 0.364
it wants 0.0046 BTC, less than half the 0.01 minimum, so it filled only 73 of
the 246 trades it wanted -- and not a random 73: it filled when the multiplier
was high, which is the low-volatility subset. Its extraction also never
reproduced the engine (+35.0% standalone against a published +18.2%). Off.

PDR AND DONCHIAN, FLOORED AT 0.65. Same lot-step problem, less severe: at 1.98x
and 2.01x notional they need a multiplier near 0.46 and got 0.364, filling 49%
of their signals. A floor beats a constant boost because it spends the extra
size only where the throttle would otherwise make the sleeve unfundable: 97%
fill at 17.70% drawdown, against 21.57% for the 2.0x boost that reaches the same
fill.

This is deliberately breaking the volatility throttle for those two sleeves. In
the most violent regimes they now carry 0.65x where the signal asked for 0.28x.
That is the trade for funding a BTC lot on a small account, and it costs about
1.6 points of drawdown.
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox.research import combined_book as cb

OUTPUT = os.path.join(os.path.dirname(__file__), "..", ".cache",
                      "exposure_schedule.json")

TARGET = 13.0

#: py sleeve label -> the Rust engine's display name.
NAMES = {
    "nq:ofi": "NQ Deep OFI Momentum",
    "nq:hdr": "NQ Hourly Delta Reversal",
    "nq:drift_vwap": "NQ Drift VWAP",
    "btcusd:maroy_ladder": "BTC Maroy Ladder",
    "btcusd:pdr": "BTC PDR",
    "btcusd:donchian": "BTC Donchian",
    "ethusd:vwap": "ETHUSD VWAP",
    "ethbtc:orb": "ETHBTC ORB",
    "ethbtc:gap": "ETHBTC Gap",
    "xalusd:ma_cross": "XALUSD MA Cross",
    "xalusd:pdr": "XALUSD PDR",
    "xalusd:overnight": "XALUSD Overnight",
    "xngusd:donchian": "XNGUSD Donchian",
    "xngusd:gap": "XNGUSD Gap",
    "xngusd:zscore": "XNGUSD Z-score",
    # Registered in `strategies/mod.rs` and dispatched by `engine.rs`, so they
    # take a schedule entry like any other sleeve. They are deliberately NOT in
    # `COMBINED_NAMES` below: mapping a name only lets the engine throttle the
    # sleeve correctly if it runs, and does not add it to the canonical book.
    "aus200:momentum": "AUS200 Momentum",
    "aus200:pdr": "AUS200 PDR",
    "fr40:gap": "FR40 Gap",
    "hk50:gap": "HK50 Gap",
    # Ported to Rust as parameter sets on the strategies they are variants of:
    # `MaroyParams::ETHUSD` on `btc_maroy_time.rs` and `DriftParams::ETHUSD` on
    # `nq_drift_vwap.rs`. Both charge their own 0.5 bp entry cost internally,
    # because the engine's spread is one run-wide number.
    "ethusd:maroy": "ETHUSD Maroy",
    "ethusd:drift_vwap": "ETHUSD Drift VWAP",
}

#: The Rust `/api/combine` request corresponding to the canonical Python book.
#: BTC and NQ stay registered in Rust for standalone runs but are intentionally
#: absent from this selection.
COMBINED_NAMES = tuple(
    NAMES[f"nq:{short}"] for short in cb.NQ_SLEEVES
) + (NAMES[cb.DRIFT_VWAP_SLEEVE],) + tuple(
    NAMES[f"{symbol}:{family}"]
    for symbol, family in (*cb.CRYPTO_SLEEVES, *cb.COMMODITY_SLEEVES,
                           *cb.INDEX_SLEEVES)
) + (NAMES[cb.ETH_MAROY_SLEEVE], NAMES[cb.ETH_DRIFT_SLEEVE])

#: Factor 0.0 stands a sleeve down without removing it from the run, so its
#: indicators stay warm and re-enabling it is a one-line change.
DISABLED = {"BTC Maroy Ladder"}

#: Minimum multiplier, per sleeve. Only the two BTC sleeves that are kept.
FLOORS = {"BTC PDR": 0.65, "BTC Donchian": 0.65}

#: Optional balance gate, per sleeve: below `min_balance` the sleeve stands down
#: entirely rather than filling the subset of signals that clears the lot step;
#: it resumes above `resume_balance`. Empty here because Maroy is disabled
#: outright and the other two are floored into being fundable.
BALANCE_GATES: dict[str, tuple[float, float]] = {}

#: The canonical target-12 book does not override BTC. Keeping these mappings
#: explicit makes experimental floors/scales available through the CLI without
#: silently changing the live book's risk.
COMBINED_BTC_FLOORS: dict[str, float] = {}
COMBINED_BTC_SCALES: dict[str, float] = {}
COMBINED_SCALES = {
    NAMES[sleeve]: scale for sleeve, scale in cb.SLEEVE_SCALE.items()
}

#: Staged small-account activation. Bring back the strongest/fundable BTC
#: sleeve first and the weakest-to-fund last, rather than holding every BTC
#: strategy off until a near-worst-case volatility multiplier is affordable.
#: Each sleeve switches off 10% below its activation level, preventing churn
#: when the shared balance fluctuates around a threshold.
COMBINED_BALANCE_GATES = {
    NAMES[sleeve]: gate for sleeve, gate in cb.BALANCE_GATES.items()
}

#: Maroy was profitable when funded from the beginning of the measured $3,000
#: run, but lost when a $1,500 run crossed $3,000 late. Do not treat those paths
#: as equivalent: below this starting balance Maroy remains ineligible for the
#: entire run, even if other sleeves later compound the account above $3,000.
COMBINED_INITIAL_BALANCE_GATES = {
    NAMES[sleeve]: gate for sleeve, gate in cb.INITIAL_BALANCE_GATES.items()
}


def build(target=TARGET, floors=None, disabled=None, gates=None, scales=None,
          initial_gates=None):
    floors = FLOORS if floors is None else floors
    disabled = DISABLED if disabled is None else disabled
    gates = BALANCE_GATES if gates is None else gates
    scales = {} if scales is None else scales
    initial_gates = {} if initial_gates is None else initial_gates
    by_market = cb.exposure_schedule({**cb.EXPOSURE, "target": target})
    out = {}
    for sleeve, name in NAMES.items():
        if name in disabled:
            out[name] = {"default": 0.0}
            continue
        driver = cb.DRIVER.get(sleeve, "btc")
        # Native XNG sleeves deliberately bypass the daily multiplier. Their
        # risk is controlled by max_sizing_balance instead, before lot rounding.
        daily = by_market.get(driver, {})
        floor = floors.get(name, 0.0)
        scale = scales.get(name, 1.0)
        # `default` carries the scale too, for two reasons. A sleeve whose
        # driver has no daily series at all -- the native XNG pair, which
        # bypasses the multiplier -- has no other entry for the scale to land
        # on, so a 0.0 in SLEEVE_SCALE would leave it running at full size in
        # Rust while Python stood it down. And for every other sleeve `default`
        # is the fallback for days outside the emitted range, which must not
        # resurrect a stood-down sleeve at 1.0. Same expression as a day entry
        # with `mult` = 1.0.
        entry = {"default": round(max(scale, floor), 6)}
        entry.update({day: round(max(mult * scale, floor), 6)
                      for day, mult in sorted(daily.items())})
        if name in gates:
            minimum, resume = gates[name]
            entry["min_balance"] = float(minimum)
            entry["resume_balance"] = float(resume)
        if name in initial_gates:
            entry["min_initial_balance"] = float(initial_gates[name])
        if sleeve in cb.SIZING_EQUITY_CAP:
            entry["max_sizing_balance"] = float(cb.SIZING_EQUITY_CAP[sleeve])
        out[name] = entry
    return out


def combined_book_schedule(target=None, floors=None, scales=None, gates=None):
    """Exact exposure schedule used by ``combined_book.py`` for Rust parity.

    Unlike the older small-account live policy above, no sleeve is disabled or
    floored and the target defaults to the book's frozen setting with a 3x cap.
    """
    target = cb.EXPOSURE["target"] if target is None else target
    gates = COMBINED_BALANCE_GATES if gates is None else gates
    scales = {**COMBINED_SCALES, **(scales or {})}
    return build(target=target, floors=floors or {}, disabled=set(), gates=gates,
                 scales=scales,
                 initial_gates=COMBINED_INITIAL_BALANCE_GATES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=float)
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--combined-book", action="store_true")
    parser.add_argument("--btc-pdr-floor", type=float, default=0.0)
    parser.add_argument("--btc-donchian-floor", type=float, default=0.0)
    parser.add_argument("--btc-maroy-floor", type=float,
                        default=COMBINED_BTC_FLOORS.get("BTC Maroy Ladder", 0.0))
    parser.add_argument("--btc-pdr-scale", type=float,
                        default=COMBINED_BTC_SCALES.get("BTC PDR", 1.0))
    parser.add_argument("--btc-donchian-scale", type=float,
                        default=COMBINED_BTC_SCALES.get("BTC Donchian", 1.0))
    parser.add_argument("--btc-maroy-scale", type=float, default=1.0)
    args = parser.parse_args()

    target = args.target
    if target is None:
        target = cb.EXPOSURE["target"] if args.combined_book else TARGET
    combined_floors = {
        "BTC PDR": args.btc_pdr_floor,
        "BTC Donchian": args.btc_donchian_floor,
        "BTC Maroy Ladder": args.btc_maroy_floor,
    }
    combined_scales = {
        "BTC PDR": args.btc_pdr_scale,
        "BTC Donchian": args.btc_donchian_scale,
        "BTC Maroy Ladder": args.btc_maroy_scale,
    }
    schedule = (combined_book_schedule(target, combined_floors, combined_scales)
                if args.combined_book else build(target))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(schedule, handle, indent=1, sort_keys=True)
        handle.write("\n")

    print(f"wrote {os.path.abspath(args.out)}  (target {target:g})")
    for name, entry in sorted(schedule.items()):
        days = [k for k in entry if len(k) == 10 and k[4] == "-"]
        if not days:
            if float(entry.get("default", 0.0)) == 0.0:
                print(f"  {name:<26} DISABLED")
            else:
                cap = entry.get("max_sizing_balance")
                note = f", cap ${cap:g}" if cap is not None else ""
                print(f"  {name:<26} NATIVE{note}")
            continue
        values = [entry[d] for d in days]
        note = (
            f"  floor {FLOORS[name]}"
            if not args.combined_book and name in FLOORS
            else ""
        )
        print(f"  {name:<26} {len(days):>5} days, "
              f"min {min(values):.3f} max {max(values):.3f}{note}")


if __name__ == "__main__":
    main()
