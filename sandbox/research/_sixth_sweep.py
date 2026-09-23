"""Run the sixth wave's `select` over every non-stock symbol, one process each.

ONE PROCESS PER SYMBOL, AND THAT IS NOT A STYLE CHOICE. A single `select` with
`--symbols a,b,c` holds every symbol's context alive at once: the run grows past
9 GB, `fit_workers` sees the shrinking headroom and starves its own pool down to
one worker, and a sweep that should take twenty minutes takes six hours
([[multi-symbol-select-leaks-into-one-worker]]). A fresh interpreter per symbol
gives every symbol the full sixteen and returns the memory in between.

ORDERED BY WHERE THE EDGE HAS ALREADY BEEN FOUND, not alphabetically. The
existing 39-symbol scoreboard is lopsided -- ethusd passes 46 of 52 families,
btc 26 of 53, jp225 25 of 42, while aus200 and xcuusd pass nothing at all -- so
the symbols that have ever produced anything run first. If the queue is stopped
half way, what is on disk is the half worth having.

    python -m sandbox.research._sixth_sweep                 # every non-stock
    python -m sandbox.research._sixth_sweep --symbols btc,nq
    python -m sandbox.research._sixth_sweep --resume        # skip sealed files
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from sandbox.research import exness_families as ef

#: The order the queue runs in. Everything not named here follows, alphabetically.
PRIORITY = ("ethusd", "btc", "jp225", "de40", "usdjpy", "nq", "es", "xaueur",
            "xaugbp", "fr40", "gbpjpy", "gbpusd", "ethbtc", "xauaud", "uk100",
            "hk50", "xalusd", "ukoil", "eurjpy", "audusd")


def queue(symbols=None):
    names = symbols or [s for s in ef.UNIVERSE if ef.CLASS[s] != "stock"]
    rank = {name: index for index, name in enumerate(PRIORITY)}
    return sorted(names, key=lambda s: (rank.get(s, len(PRIORITY)), s))


def sealed(symbol, bar):
    return os.path.exists(
        ef.output_path(symbol, bar, set(ef.ALIASES["sixth"])))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 2)))
    parser.add_argument("--resume", action="store_true",
                        help="skip symbols whose sealed file already exists")
    args = parser.parse_args()

    names = queue(args.symbols.replace(" ", "").split(",")
                  if args.symbols else None)
    started = time.time()
    done, failed, skipped = [], [], []
    for index, symbol in enumerate(names, 1):
        if args.resume and sealed(symbol, args.bar_minutes):
            skipped.append(symbol)
            print(f"[{index}/{len(names)}] {symbol}: already sealed, skipping",
                  flush=True)
            continue
        elapsed = time.time() - started
        rate = elapsed / len(done) if done else None
        eta = (f", eta {(len(names) - index + 1) * rate / 3600:.1f}h"
               if rate else "")
        print(f"\n[{index}/{len(names)}] {symbol} "
              f"({elapsed / 3600:.1f}h elapsed{eta})", flush=True)
        clock = time.time()
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m",
             "sandbox.research.exness_families", "select",
             "--symbols", symbol, "--bar-minutes", str(args.bar_minutes),
             "--groups", "sixth", "--workers", str(args.workers),
             "--stale-spreads"],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        took = (time.time() - clock) / 60.0
        # A non-zero exit OR a missing sealed file both count as failure: the
        # module catches per-symbol errors and exits 0 with the symbol skipped,
        # so the exit code alone would report a hollow sweep as a complete one.
        if result.returncode == 0 and sealed(symbol, args.bar_minutes):
            done.append(symbol)
            print(f"    ok in {took:.1f} min", flush=True)
        else:
            failed.append(symbol)
            tail = (result.stdout or "").strip().splitlines()[-6:]
            error = (result.stderr or "").strip().splitlines()[-6:]
            print(f"    FAILED in {took:.1f} min (exit {result.returncode})",
                  flush=True)
            for line in tail + error:
                print(f"      {line}", flush=True)

    print(f"\n=== {len(done)} sealed, {len(failed)} failed, "
          f"{len(skipped)} skipped, {(time.time() - started) / 3600:.1f}h ===",
          flush=True)
    if failed:
        print("failed: " + ", ".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
