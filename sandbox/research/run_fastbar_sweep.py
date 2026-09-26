"""Run the `fastbar` select sweep one symbol per process, politely.

The live runtime shares this machine, so the sweep must never compete with it:
this process drops itself to BELOW_NORMAL priority before launching anything
(Windows children inherit a below-normal class), caps the worker pool, and
waits for free memory before each symbol. One symbol per process because a
multi-symbol `select` leaks into one worker ([[multi-symbol-select-leaks-into-one-worker]]).

    py -m sandbox.research.run_fastbar_sweep --bar-minutes 5 --workers 8 --symbols usdjpy,eurjpy
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
MIN_FREE_GB = 3.0


def lower_priority():
    kernel = ctypes.windll.kernel32
    kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)


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


def _alive(pid):
    SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--bar-minutes", default="5")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--groups", default="fastbar")
    parser.add_argument("--reserve-gb", type=float, default=None,
                        help="free memory the pool must leave, GB (default 4)")
    parser.add_argument("--full-day", action="store_true",
                        help="hold and watch on every bar, not just the session")
    parser.add_argument("--swap-free", action="store_true",
                        help="charge no swap on multiday holds")
    parser.add_argument("--fills-only", action="store_true",
                        help="refuse entries on bars the broker table does not price")
    parser.add_argument("--after-pid", type=int, default=None,
                        help="wait for this process (an earlier runner) to exit first")
    parser.add_argument("--stale-spreads-for", default="",
                        help="symbols whose weekend-quoted constant spread is accepted")
    args = parser.parse_args()
    lower_priority()
    env = dict(os.environ, PYTHONWARNINGS="ignore")
    if args.fills_only:
        env["EXNESS_FILLS_ONLY"] = "1"
    if args.reserve_gb is not None:
        env["EXNESS_MEMORY_RESERVE_GB"] = str(args.reserve_gb)
    if args.full_day:
        env["EXNESS_FULL_DAY"] = "1"
    if args.swap_free:
        env["EXNESS_SWAP_FREE"] = "1"
    stale = set(filter(None, args.stale_spreads_for.split(",")))
    # QUEUED, NOT CONCURRENT. Two sweeps at once would each get half the
    # memory the guard allows, and a pool squeezed that far is how a machine
    # carrying a live runtime starts to swap.
    if args.after_pid:
        print(f"{datetime.now():%H:%M} waiting for runner {args.after_pid} to exit", flush=True)
        while _alive(args.after_pid):
            time.sleep(60)
    for symbol in args.symbols.split(","):
        while free_gb() < MIN_FREE_GB:
            print(f"{datetime.now():%H:%M} waiting: {free_gb():.1f} GB free", flush=True)
            time.sleep(60)
        started = time.time()
        print(f"{datetime.now():%H:%M} START {symbol} ({free_gb():.1f} GB free)", flush=True)
        code = subprocess.call([sys.executable, "-m", "sandbox.research.cfd_families",
                                "select", "--symbols", symbol, "--bar-minutes", args.bar_minutes,
                                "--groups", args.groups, "--workers", str(args.workers)]
                               + (["--stale-spreads"] if symbol in stale else []),
                               env=env,
                               # EXPLICIT, not inherited: SetPriorityClass on
                               # this process did not take under the `py`
                               # launcher, and the first run came up Normal.
                               creationflags=BELOW_NORMAL_PRIORITY_CLASS)
        print(f"{datetime.now():%H:%M} DONE {symbol} exit={code} "
              f"{(time.time() - started) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
