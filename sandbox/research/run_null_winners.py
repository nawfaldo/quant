"""Coin-flip control (`why`) for the out-of-sample winner list, one run per process.

Same care as `run_fastbar_sweep`: children at BELOW_NORMAL priority, a small
pool, a 4 GB memory reserve, and a wait for free memory before each run -- the
machine carries the live runtime and another sweep at the same time.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

#: (symbol, bar minutes, families) -- the 51 winners at >= +10% OOS, 2026-09-24.
RUNS = (
    ("ustec", 1440, "williams"),
    ("uk100", 240, "macd"),
    ("uk100", 60, "volume_breakout"),
    ("us500", 60, "mfi bop"),
    ("ustec", 240, "eom ichimoku chop roc awesome"),
    ("ustec", 60, "aroon stoch dual_osc"),
    ("us500", 240, "obv_ema cmo ema_cross vwap_band candle"),
    ("de40", 60, "volume_breakout trend_pullback aroon"),
    ("de40", 240, "roc trend_pullback vote macd"),
    ("ethusd", 240, "bollinger roc keltner ma_zoo vote squeeze linreg_channel cmo eom"),
    ("de40", 5, "adx"),
    ("ustec", 5, "adx aroon rvi williams klinger"),
    ("us500", 5, "camarilla trix macd heikin candle triple_screen tsi trend_pullback rsi roc"),
)


def free_gb():
    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys / 1e9


def already_done(symbol, bar, names):
    """A null file for this exact family set exists, so the run is complete."""
    import glob
    import json
    label = "1d" if bar >= 1440 else f"{bar}m"
    for path in glob.glob(f"sandbox/results/exness_families_null_{symbol}_{label}_*.json"):
        rows = json.load(open(path)).get("null_control") or {}
        if set(names.split(",")) <= set(rows) and all(len(rows[n]) == 3 for n in names.split(",")):
            return True
    return False


def main():
    workers = sys.argv[1] if len(sys.argv) > 1 else "2"
    reserve = sys.argv[2] if len(sys.argv) > 2 else "4"
    for symbol, bar, families in RUNS:
        prefix = "fb_" if bar == 5 else "sb_"
        names = ",".join(prefix + f for f in families.split())
        if already_done(symbol, bar, names):
            print(f"{datetime.now():%H:%M} SKIP null {symbol} {bar}m (done)", flush=True)
            continue
        env = dict(os.environ, PYTHONWARNINGS="ignore", EXNESS_FILLS_ONLY="1",
                   EXNESS_MEMORY_RESERVE_GB=reserve)
        if bar != 5:
            env["EXNESS_FULL_DAY"] = "1"
        need = 3.5 if bar == 5 else 2.0
        while free_gb() < need + 1.0:
            print(f"{datetime.now():%H:%M} waiting: {free_gb():.1f} GB free", flush=True)
            time.sleep(60)
        started = time.time()
        print(f"{datetime.now():%H:%M} START null {symbol} {bar}m {names}", flush=True)
        code = subprocess.call(
            [sys.executable, "-m", "sandbox.research.cfd_families", "why",
             "--symbols", symbol, "--bar-minutes", str(bar), "--families", names,
             "--workers", workers]
            # US500's constant spread was quoted at a weekend close; it prices
            # only the 0.6% of bars the broker table misses.
            + (["--stale-spreads"] if symbol == "us500" else []),
            env=env, creationflags=BELOW_NORMAL_PRIORITY_CLASS)
        print(f"{datetime.now():%H:%M} DONE null {symbol} {bar}m exit={code} "
              f"{(time.time() - started) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
