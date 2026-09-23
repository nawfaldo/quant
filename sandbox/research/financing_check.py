"""What the fourth wave's survivors cost once overnight financing is charged.

THE MODEL DOES NOT CHARGE IT AT ALL. `cost_bp` is the quoted spread plus a
slippage allowance, taken ONCE at entry, so a position held sixty days is priced
exactly like a thirty-minute scalp. That was defensible while every family in
the study was flattened at the close. It stopped being defensible the moment a
holding regime was added whose entire thesis is surviving the close.

Swap is read live from the terminal in POINTS (`swap_mode == 1`) and converted
here to the module's unit -- basis points of notional per night -- so it can be
compared with `gross_bp_per_trade` and `edge_vs_drift_bp` directly.

CALENDAR NIGHTS, NOT SESSIONS. A position open over a weekend is charged three
nights, and most symbols here charge triple on a set weekday
(`swap_rollover3days`). Rather than model the weekday rule, this counts the
ACTUAL calendar days each trade spanned, from its own trade log, which gets the
weekends right by construction and is conservative about nothing.
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys

from sandbox.research import exness_families as f

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
SWAPS = os.path.join(os.path.dirname(__file__), "..", "..", "swaps_raw.json")


def swap_bp_per_night(symbol, spec, raw):
    """`(long, short)` financing in bp of notional per calendar night."""
    entry = raw.get(symbol)
    if entry is None:
        return None
    _broker, mode, long_pts, short_pts, _r3, notional = entry
    if mode != 1 or not notional:
        return None
    point, multiplier = spec["point"], spec["multiplier"]

    def convert(points):
        return 1e4 * abs(points) * point * multiplier / notional

    return convert(long_pts), convert(short_pts)


def main():
    raw = json.load(io.open(SWAPS, encoding="utf-8"))
    specs = f.load_specs()["symbols"]
    pattern = sys.argv[1] if len(sys.argv) > 1 else "exness_families_*_fourth.json"

    jobs = {}
    for path in sorted(glob.glob(os.path.join(RESULTS, pattern))):
        doc = json.load(io.open(path, encoding="utf-8"))
        for name, entry in doc["families"].items():
            if entry and entry.get("params"):
                jobs.setdefault((doc["symbol"], doc["bar_minutes"]), []).append(
                    (name, entry))

    print(f"{'symbol':7}{'bar':>5} {'family':22}{'nights':>8}{'edge':>9}"
          f"{'swap':>9}{'net':>9}{'net t':>8}  verdict")
    survived = killed = unpriced = 0
    for (symbol, bar), members in sorted(jobs.items()):
        f.BAR_MINUTES = bar
        f.resolve(symbol, allow_stale=True)
        rates = swap_bp_per_night(symbol, specs[symbol], raw)
        bars, ctx = f.context(symbol, "select", bar, {n for n, _e in members})
        for name, entry in members:
            params = f.rehydrate(entry["params"])
            stat = f.backtest(name, bars, ctx, params, include_trades=True)
            log = stat["trade_log"]
            if not log:
                continue
            nights = [max(0, (t["exit_ts"] - t["entry_ts"]) // 86_400) for t in log]
            mean_nights = sum(nights) / len(nights)
            edge = entry["in_sample"].get("edge_vs_drift_bp", 0.0)
            if rates is None:
                print(f"{symbol:7}{f.label_bar(bar):>5} {name:22}"
                      f"{mean_nights:>8.1f}{edge:>9.2f}{'-':>9}{'-':>9}{'-':>8}"
                      "  no swap reading")
                unpriced += 1
                continue
            # Each trade pays the rate for the side it actually took.
            charge = sum(rates[0] if t["side"] == 1 else rates[1]
                         for t in log for _ in range(1)) / len(log)
            per_trade = sum(
                (rates[0] if t["side"] == 1 else rates[1]) * n
                for t, n in zip(log, nights)) / len(log)
            net = edge - per_trade
            old_t = entry["in_sample"].get("edge_vs_drift_t_stat", 0.0)
            net_t = old_t * (net / edge) if edge else 0.0
            verdict = ("survives" if net > 0 and abs(net_t) >= 2.0
                       else "weakened" if net > 0 else "WIPED")
            survived += verdict == "survives"
            killed += verdict == "WIPED"
            print(f"{symbol:7}{f.label_bar(bar):>5} {name:22}"
                  f"{mean_nights:>8.1f}{edge:>9.2f}{per_trade:>9.2f}"
                  f"{net:>9.2f}{net_t:>8.2f}  {verdict}")

    print(f"\n{survived} still reach |t|>=2 after financing, {killed} wiped, "
          f"{unpriced} unpriced.")
    print("`swap` is bp of notional charged over the trade's own calendar span, "
          "at the side it took.\nRates are TODAY's from the terminal and are "
          "applied to the whole history -- they move with policy,\nso this is "
          "an order-of-magnitude correction, not a re-backtest.")


if __name__ == "__main__":
    main()
