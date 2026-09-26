"""One-shot switch of the live account to the 22-sleeve canon (2026-09-26).

    py -3 tools/go_live_canon_22.py

1. Backs up live_trade/app.db.
2. Runs `activate_canonical_live --apply` -- rows straight from the Rust
   registry, `live_started_at` reset. It refuses while anything is open.
3. Clears `environment_cost_rules`; the server rebuilds it on boot
   ([[activating-a-book-is-four-places]]).

The 22: base 18 less eurjpy_swing_ma and usdjpy_kendall, plus ethusd_roofing,
ethusd_level_confluence, usdjpy_rvol, and the WEEKEND-ONLY ethusd_efficiency,
ethusd_cci and ethusd_linreg_trend.

Then start the stack with tools\\start_live_trade.bat.
"""
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "live_trade" / "app.db"
EXPECTED = 22

backup = DB.with_name(f"app.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(DB, backup)
print(f"1. backup      {backup.name}")

result = subprocess.run(
    [sys.executable, "-m", "sandbox.research.activate_canonical_live", "--apply"],
    cwd=ROOT)
if result.returncode != 0:
    sys.exit("2. activation REFUSED -- nothing else was changed after the backup")
print("2. activated")

db = sqlite3.connect(DB, timeout=30)
with db:
    cleared = db.execute("DELETE FROM environment_cost_rules").rowcount
rows = db.execute(
    "SELECT strategy, symbol FROM mt5_account_strategies "
    "WHERE active=1 ORDER BY id").fetchall()
db.close()
print(f"3. cleared     {cleared} stale cost rows")
print(f"\n{len(rows)} active live rows:")
for strategy, symbol in rows:
    print(f"   {strategy:30} {symbol}")
if len(rows) != EXPECTED:
    sys.exit(f"\nEXPECTED {EXPECTED} ACTIVE ROWS, FOUND {len(rows)} -- do not start the stack")
print("\nReady. Start with tools\\start_live_trade.bat")
