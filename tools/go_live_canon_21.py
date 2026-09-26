"""One-shot switch of the live account to the 21-sleeve canon (2026-09-23).

    py -3 tools/go_live_canon_21.py

1. Backs up live_trade/app.db.
2. Records the USDJPY 0.01 (`usdjpy_half_life`, ticket 3859708870) that MT5
   closed on 2026-09-22 08:54 at 156.969 (-3.10) outside the runtime, so the
   database no longer thinks it is open. MT5 was checked: 0 positions.
3. Runs `activate_canonical_live --apply` -- rows straight from the Rust
   registry, `live_started_at` reset.
4. Clears `environment_cost_rules`; the server rebuilds it on boot
   ([[activating-a-book-is-four-places]]).

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

backup = DB.with_name(f"app.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(DB, backup)
print(f"1. backup      {backup.name}")

db = sqlite3.connect(DB, timeout=30)
with db:
    n1 = db.execute(
        "UPDATE mt5_strategy_positions SET status='closed', remaining_volume=0 "
        "WHERE ticket=3859708870 AND status!='closed'").rowcount
    n2 = db.execute(
        "UPDATE live_trades SET mt5_close_price=156.969, "
        "mt5_close_time='2026-09-22 08:54', exit_reason='manual_reconcile', "
        "closed_at='2026-09-22 08:54:52' WHERE id=112 AND "
        "(mt5_close_time='' OR mt5_close_time IS NULL)").rowcount
db.close()
print(f"2. reconciled  {n1} position row, {n2} trade row")

result = subprocess.run(
    [sys.executable, "-m", "sandbox.research.activate_canonical_live", "--apply"],
    cwd=ROOT)
if result.returncode != 0:
    sys.exit("3. activation REFUSED -- nothing else was changed after the backup")
print("3. activated")

db = sqlite3.connect(DB, timeout=30)
with db:
    n3 = db.execute("DELETE FROM environment_cost_rules").rowcount
rows = db.execute(
    "SELECT strategy, symbol, active FROM mt5_account_strategies "
    "WHERE active=1 ORDER BY id").fetchall()
db.close()
print(f"4. cleared     {n3} stale cost rows")
print(f"\n{len(rows)} active live rows:")
for strategy, symbol, _ in rows:
    print(f"   {strategy:28} {symbol}")
if len(rows) != 21:
    sys.exit(f"\nEXPECTED 21 ACTIVE ROWS, FOUND {len(rows)} -- do not start the stack")
print("\nReady. Start with tools\\start_live_trade.bat")
