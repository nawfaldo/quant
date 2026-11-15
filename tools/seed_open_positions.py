"""Run python3 tools/seed_open_positions.py to seed local filter examples."""
from pathlib import Path
import sqlite3
import time

path = Path(__file__).resolve().parents[1] / 'live_trade' / 'app.db'
if not path.exists():
    raise SystemExit('Run the demo account seed first.')
db = sqlite3.connect(path)
db.execute('PRAGMA busy_timeout=5000')
db.execute('PRAGMA foreign_keys=ON')
accounts = db.execute("SELECT id, login FROM mt5_accounts WHERE server='LOCAL-DUMMY' AND login IN ('DUMMY-1','DUMMY-2') ORDER BY login").fetchall()
if len(accounts) != 2:
    raise SystemExit('Both LOCAL-DUMMY demo accounts must exist first.')
backup = path.with_name(path.name + f'.bak-open-positions-{time.time_ns()}')
with sqlite3.connect(backup) as target:
    db.backup(target)
# Unique synthetic tickets, reused on reruns. No execution commands are created.
examples = [
    ('EURUSD', 1.0824, .10, 12.50),
    ('XAUUSD', 2650.80, .02, -8.20),
    ('BTCUSD', 72000.00, .01, 24.60),
    ('ETHUSD', 3100.50, .05, -3.40),
]
now = int(time.time())
with db:
    for account_index, (account_id, login) in enumerate(accounts):
        for index, (symbol, price, lot, pnl) in enumerate(examples):
            ticket = 990000000 + account_index * 100 + index
            side = 'long' if (index + account_index) % 2 == 0 else 'short'
            db.execute('''INSERT INTO mt5_bridge_positions
                (account_id,ticket,position_type,symbol,volume,profit,open_price,open_time,magic)
                VALUES (?,?,?,?,?,?,?,?,0)
                ON CONFLICT(account_id,ticket) DO UPDATE SET
                position_type=excluded.position_type, symbol=excluded.symbol,
                volume=excluded.volume, profit=excluded.profit,
                open_price=excluded.open_price, open_time=excluded.open_time''',
                (account_id, ticket, side, symbol, lot, pnl * (account_index + 1), price, now - (index + 1) * 300))
print(f'Seeded {len(accounts) * len(examples)} dummy positions across DUMMY-1 and DUMMY-2.')
db.close()
