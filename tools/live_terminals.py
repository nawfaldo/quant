"""Prints `MT5_TERMINALS` for `start_live_trade.bat`: every active account
mapped to the terminal installation its broker runs in.

    py tools/live_terminals.py <exness terminal64.exe> <fundednext terminal64.exe>

An active account is one with an active strategy. Exness accounts go to the
first path, FundedNext accounts to the second; an active account on any other
server is an error, because a terminal cannot be guessed.
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "live_trade" / "app.db"


def main():
    exness, fundednext = sys.argv[1], sys.argv[2]
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    rows = db.execute(
        "SELECT a.id, a.server FROM mt5_accounts a WHERE EXISTS (SELECT 1 FROM "
        "mt5_account_strategies s WHERE s.account_id=a.id AND s.active=1) ORDER BY a.id"
    ).fetchall()
    out = []
    for account, server in rows:
        if server.lower().startswith("exness"):
            out.append(f"{account}={exness}")
        elif server.lower().startswith("fundednext"):
            out.append(f"{account}={fundednext}")
        else:
            sys.exit(f"account {account} on {server!r}: no terminal known for it")
    if not out:
        sys.exit("no active account")
    print(";".join(out))


if __name__ == "__main__":
    main()
