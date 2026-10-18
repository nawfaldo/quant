"""Smoke-check one creator group of `cfd_tt_families` WITHOUT sweeping it.

For every family in `--group`, on each symbol/bar:
  1. every SIGNAL setting computes, and its event count is printed (min/median
     over settings), so a family that barely fires shows up before a sweep;
  2. every `exit_mode` value backtests once (first signal setting);
  3. truncation: events computed on the first 60% of the series equal the
     full run's events over the same bars -- a look-ahead check.

    py -m sandbox.research._tt_verify --group fluxchart
    py -m sandbox.research._tt_verify --group fluxchart --cases us500:30,btc:15
"""
from __future__ import annotations

import argparse
import itertools
import statistics
import sys
import time
import traceback

sys_argv = sys.argv
sys.argv = [sys.argv[0]]
from sandbox.research import cfd_tt_families as tt       # noqa: E402  (installs the registry)
sys.argv = sys_argv
from sandbox.research import cfd_families as cf          # noqa: E402

CASES = "us500:30,eurusd:30,jp225:30,btc:15"
SIGNAL_SKIP = tt.EVENT_EXIT_KEYS


def signal_settings(spec):
    keys = [k for k in spec if k not in SIGNAL_SKIP]
    return [dict(zip(keys, v)) for v in itertools.product(*(spec[k] for k in keys))]


def run_case(symbol, bar, members, cut=0.6):
    cf.resolve(symbol, allow_stale=True)
    grid = cf.axes(symbol, bar, set(members))
    bars, ctx = cf.context(symbol, "validate", bar, set(members))
    full = cf.all_bars(symbol, "validate", bar)
    short_full = full[:int(len(full) * cut)]
    tbars, tctx = tt.lean_context(symbol, "validate", bar, set(members),
                                  full_bars=short_full)
    n_cut = len(tbars)
    assert [b[0] for b in tbars] == [b[0] for b in bars[:n_cut]], "prefix mismatch"
    problems = []
    rows = []
    for fam in members:
        if fam not in grid:
            rows.append((fam, "not in grid (symbol/bar skipped)"))
            continue
        spec = grid[fam]
        compute = tt.EVENT_FAMILIES[fam]
        counts = []
        t0 = time.time()
        for s in signal_settings(spec):
            try:
                ev = compute(bars, ctx, dict(s))
                tev = compute(tbars, tctx, dict(s))
            except Exception:
                problems.append(f"{symbol}:{bar} {fam} {s}: compute raised\n"
                                + traceback.format_exc(limit=3))
                continue
            counts.append(len(ev))
            want = {i: v for i, v in ev.items() if i < n_cut}
            if set(want) != set(tev) or any(
                    want[i][0] != tev[i][0] or abs(want[i][1] - tev[i][1]) > 1e-9
                    for i in tev):
                diff = sorted(set(want) ^ set(tev))[:5]
                problems.append(f"{symbol}:{bar} {fam} {s}: TRUNCATION differs "
                                f"(full {len(want)} vs cut {len(tev)}, e.g. {diff})")
        first = {k: v[0] for k, v in spec.items()}
        tr0 = None
        for mode in spec.get("exit_mode", ()):
            cell = dict(first, exit_mode=mode)
            try:
                r = cf.backtest(fam, bars, ctx, cell)
            except Exception:
                problems.append(f"{symbol}:{bar} {fam} exit {mode}: backtest raised\n"
                                + traceback.format_exc(limit=4))
                continue
            if mode == spec["exit_mode"][0]:
                tr0 = r.get("trades")
        if counts:
            rows.append((fam, f"settings {len(counts):3d}  events min {min(counts):6d}  "
                              f"median {int(statistics.median(counts)):6d}  "
                              f"trades(first cell) {tr0}  {time.time() - t0:5.1f}s"))
    return rows, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--cases", default=CASES)
    ap.add_argument("--families", default=None)
    args = ap.parse_args()
    members = list(tt.GROUPS[args.group])
    if args.families:
        members = [m for m in members if m in args.families.split(",")]
    all_problems = []
    for case in args.cases.split(","):
        symbol, bar = case.split(":")
        print(f"== {symbol} {bar}m", flush=True)
        rows, problems = run_case(symbol, int(bar), members)
        for fam, text in rows:
            print(f"  {fam:32s} {text}", flush=True)
        all_problems += problems
    print("\nPROBLEMS:" if all_problems else "\nno problems")
    for p in all_problems:
        print("  " + p)


if __name__ == "__main__":
    main()
