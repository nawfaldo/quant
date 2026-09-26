"""US500 coin-flip reruns with `--stale-spreads`, queued after `run_null_winners`.

US500's constant spread was quoted at a weekend close, so `resolve` refuses it
without the flag; the constant only prices the 0.6% of bars the broker table
misses. Same etiquette as the other runners: below-normal priority, 2 workers.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
LOG = "sandbox/results/null_winners.log"
RUNS = (
    (60, "sb_mfi,sb_bop"),
    (240, "sb_obv_ema,sb_cmo,sb_ema_cross,sb_vwap_band,sb_candle"),
    (5, "fb_camarilla,fb_trix,fb_macd,fb_heikin,fb_candle,fb_triple_screen,"
        "fb_tsi,fb_trend_pullback,fb_rsi,fb_roc"),
)

while "DONE null us500 5m" not in open(LOG, encoding="utf-8", errors="ignore").read():
    time.sleep(60)
for bar, names in RUNS:
    env = dict(os.environ, PYTHONWARNINGS="ignore", EXNESS_FILLS_ONLY="1",
               EXNESS_MEMORY_RESERVE_GB="4")
    if bar != 5:
        env["EXNESS_FULL_DAY"] = "1"
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"{datetime.now():%H:%M} START null us500 {bar}m (stale spreads)\n")
        log.flush()
        code = subprocess.call(
            [sys.executable, "-m", "sandbox.research.cfd_families", "why",
             "--symbols", "us500", "--bar-minutes", str(bar), "--families", names,
             "--workers", "2", "--stale-spreads"],
            env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=BELOW_NORMAL_PRIORITY_CLASS)
        log.write(f"{datetime.now():%H:%M} DONE null us500 {bar}m (stale) exit={code}\n")
