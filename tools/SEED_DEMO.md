# Local dashboard demo data

From the repository root, run:

```bash
bun tools/seed_demo.ts
```

The script creates the SQLite schema when needed, a `demo` web user with a
random Argon2id password, and two `LOCAL-DUMMY` trading accounts. Each new
account has 60 synthetic closed EURUSD trades across 20 weekdays, its matching
completed execution records, and a balance snapshot. These transactions are
fixtures, not Dukascopy backtest results. Market-data Parquet files are separate.

Save the password printed on the first run. Re-running preserves the existing
`demo` user's password and skips accounts already seeded. Existing users and
accounts are preserved. Before writing, existing databases are backed up with
SQLite `VACUUM INTO` to `*.db.bak-demo-<timestamp>` beside the original file.
Do not commit the databases or their backups.

Open the local web app at `/`. Its current login is hidden: type `demo`, press
Enter, type the generated password, then press Enter. Successful login opens
`/chart`. The account cards and History tabs show the seeded records.

Dummy strategies are inactive, commands are completed, and broker credentials
are placeholders. Keep these accounts inactive; they are for exploring the UI.
The web user is separate from broker accounts: the current dashboard lists
accounts globally rather than assigning them to individual users.

The script requires Bun because it imports the app's native Argon2 helper.
The web server also needs a runtime supporting both `node:sqlite` and
`node:crypto.argon2Sync`; Node 22 lacks the latter.

To add eight synthetic open positions for testing the Account/Symbols filters:

```bash
python3 tools/seed_open_positions.py
```

This seeds EURUSD, XAUUSD, BTCUSD and ETHUSD on both LOCAL-DUMMY accounts,
with buy/sell directions and positive/negative PnL. Reruns update the same
synthetic tickets. The script backs up app.db first and creates no execution
commands. These are static fixtures, not broker positions or market quotes.
