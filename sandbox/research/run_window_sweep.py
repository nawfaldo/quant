"""The window study: `select` then `validate`, one symbol per process.

    python -m sandbox.research.run_window_sweep            # select only
    python -m sandbox.research.run_window_sweep --validate # + holdout
    python -m sandbox.research.run_window_sweep --plan     # print the queue

One process per (window, symbol) because a multi-symbol sweep grows into one
worker ([[multi-symbol-select-leaks-into-one-worker]]). Resumable: a job whose
seal already carries a `validation` block is skipped, and one whose seal exists
without it is only validated. Progress lands in `results/window_sweep.log`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import platform
platform._wmi = None

from sandbox.research import cfd_families as ef

GROUP = "windowed"
BAR = 30
SYMBOLS = ("us500", "ustec", "de40", "uk100", "jp225", "hk50", "fr40",
           "stoxx50", "aus200", "audusd", "audjpy", "eurusd", "usdcad", "eurjpy",
           "gbpjpy", "gbpusd",
           "usdjpy", "ethbtc", "btc")
#: `rth` was already swept for everything except the modelled-cost symbols,
#: which could not be priced before `fill_models.modelled_minutes`.
RTH_ONLY = ("fr40", "stoxx50", "aus200", "ethbtc", "audjpy", "eurusd", "usdcad")
LOG = os.path.join(ef.RESULTS, "window_sweep.log")


def queue():
    jobs = [("rth", s) for s in RTH_ONLY]
    windows = ["day", "off", *ef.NAMED_WINDOWS, *ef.GRID_WINDOWS]
    jobs += [(w, s) for w in windows for s in SYMBOLS]
    return jobs


def seal_path(window, symbol):
    """Where `select` seals this job, without resolving the symbol."""
    tag = ef.scope_tag(ef.expand_families(None, GROUP))
    return os.path.join(
        ef.RESULTS, f"{ef.RESULT_PREFIX}_{symbol}_{BAR}m_{tag}"
                    f"{'_win-' + window if window != 'rth' else ''}.json")


def state(window, symbol):
    path = seal_path(window, symbol)
    if not os.path.exists(path):
        return "todo"
    with open(path, encoding="utf-8") as handle:
        return "done" if "validation" in json.load(handle) else "sealed"


def log(line):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line}\n")
    print(f"[{stamp}] {line}", flush=True)


def run(command, window, symbol):
    args = [sys.executable, "-m", "sandbox.research.cfd_families", command,
            "--window", window, "--symbols", symbol, "--groups", GROUP,
            "--bar-minutes", str(BAR), "--stale-spreads"]
    started = time.time()
    result = subprocess.run(args, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip().splitlines()
    # `cfd_families` catches a per-symbol failure and exits 0, printing
    # FAILED and a traceback -- so the whole output is searched, not the tail.
    failed = (result.returncode != 0
              or any(line.startswith(("FAILED", "SKIPPED")) for line in output)
              or not os.path.exists(seal_path(window, symbol)) and command == "select")
    tail = [line for line in output if line.startswith(("FAILED", "SKIPPED"))][-2:]         or output[-3:]
    log(f"{command:8} {window:12} {symbol:8} "
        f"{'FAILED' if failed else 'ok':6} {time.time() - started:7.0f}s"
        + ("  | " + " / ".join(tail) if failed else ""))
    return not failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    # In-sample first, by operator instruction 2026-09-27: the holdout and the
    # coin flip wait until the in-sample picture has been read.
    parser.add_argument("--validate", action="store_true",
                        help="also score the holdout (off by default)")
    args = parser.parse_args()
    jobs = queue()
    if args.plan:
        finished = ("done",) if args.validate else ("done", "sealed")
        todo = [j for j in jobs if state(*j) not in finished]
        print(f"{len(jobs)} jobs, {len(todo)} left")
        return
    log(f"start: {len(jobs)} jobs, group {GROUP}, {BAR}m, "
        f"{'select + validate' if args.validate else 'select only'}")
    for index, (window, symbol) in enumerate(jobs, 1):
        status = state(window, symbol)
        if status == "done" or (status == "sealed" and not args.validate):
            continue
        log(f"job {index}/{len(jobs)}")
        if status == "todo" and not run("select", window, symbol):
            continue
        if args.validate and os.path.exists(seal_path(window, symbol)):
            run("validate", window, symbol)
    log("finished")


if __name__ == "__main__":
    main()
