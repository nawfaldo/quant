"""Put the FundedNext account on the FundedNext book (2026-10-18, seal 9e0e0b402357, 80 sleeves). Idempotent.

    py -3 tools/go_live_fundednext.py            # create/switch if needed
    py -3 tools/go_live_fundednext.py --check    # exit 1 if not on it, change nothing

The rows come from the built `fundednext_live_book` registry (strategy ids and
FundedNext's own broker symbols), never from a list typed here. The account
row is created on first run: login FUNDEDNEXT_LOGIN, server FUNDEDNEXT_SERVER,
an EMPTY password, so the bridge logs in with the credentials saved in the
FundedNext terminal.

When the rows already match it touches nothing, so a restart keeps
`live_started_at`. Otherwise it backs up app.db and rewrites the account's
rows -- and REFUSES while that account has an open position or an unsettled
command, as `activate_canonical_live` does.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "live_trade" / "app.db"
LOGIN = os.environ.get("FUNDEDNEXT_LOGIN", "35188708")   # the $6,000 challenge account
SERVER = os.environ.get("FUNDEDNEXT_SERVER", "FundedNext-Server 3")
NAME = "FundedNext"
#: Sleeves written INACTIVE: their market has no live feed that matches the
#: history they were backtested on (see `NOT_FED` in idk_market_live_data_feeds).
HELD: set[str] = set()   # none since fr40/hk50 were re-imported from Dukascopy


def registry():
    for name in ("fundednext_live_book.exe", "fundednext_live_book"):
        binary = ROOT / "live_trade" / "target" / "release" / name
        if binary.is_file():
            break
    else:
        sys.exit("no live_trade/target/release/fundednext_live_book -- build it first "
                 "(cargo build --release --bins)")
    done = subprocess.run([str(binary)], capture_output=True, text=True,
                          check=True, cwd=ROOT)
    payload = json.loads(done.stdout)
    return payload["book_id"], {row["strategy"]: row["symbol"] for row in payload["strategies"]}


def account_id(db):
    row = db.execute("SELECT id FROM mt5_accounts WHERE login=? AND server=?",
                     (LOGIN, SERVER)).fetchone()
    return None if row is None else int(row[0])


def rows(db, account):
    return {s: (sym, bool(a)) for s, sym, a in db.execute(
        "SELECT strategy,symbol,active FROM mt5_account_strategies WHERE account_id=?",
        (account,))}


def differences(wanted, have):
    out = []
    for strategy, symbol in sorted(wanted.items()):
        got = have.get(strategy)
        if got is None:
            out.append(f"missing  {strategy} ({symbol})")
        elif got[0] != symbol or got[1] != (strategy not in HELD):
            out.append(f"wrong    {strategy}: {got[0]} active={got[1]}, wants {symbol}")
    out.extend(f"extra    {s}" for s in sorted(set(have) - set(wanted)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    book_id, wanted = registry()
    db = sqlite3.connect(DB, timeout=30)
    account = account_id(db)
    diff = (["no FundedNext account row"] if account is None
            else differences(wanted, rows(db, account)))
    if not diff:
        print(f"FundedNext account {account} already on {book_id}, "
              f"{len(wanted) - len(HELD)} active + {len(HELD)} held")
        return
    print(f"FundedNext account is NOT on its book ({len(diff)} differences):")
    for line in diff[:30]:
        print(f"   {line}")
    if args.check:
        sys.exit(1)

    if account is not None:
        open_positions = db.execute(
            "SELECT COUNT(*) FROM mt5_strategy_positions WHERE account_id=? "
            "AND status!='closed' AND remaining_volume>0", (account,)).fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM mt5_execution_commands WHERE account_id=? "
            "AND status NOT IN ('filled','closed','failed','cancelled')",
            (account,)).fetchone()[0]
        if open_positions or pending:
            sys.exit(f"REFUSING: account {account} has {open_positions} open positions "
                     f"and {pending} pending commands")

    db.close()
    backup = DB.with_name(f"app.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(DB, backup)
    print(f"backup  {backup.name}")

    db = sqlite3.connect(DB, timeout=30)
    db.execute("BEGIN IMMEDIATE")
    if account is None:
        db.execute(
            "INSERT INTO mt5_accounts(name,login,password,server,created_at,account_type) "
            "VALUES (?,?,'',?,datetime('now'),'mt5')", (NAME, LOGIN, SERVER))
        account = account_id(db)
        print(f"created account {account}: {NAME} {LOGIN} @ {SERVER}")
    db.execute(
        f"DELETE FROM mt5_account_strategies WHERE account_id=? AND strategy NOT IN "
        f"({','.join('?' * len(wanted))})", (account, *sorted(wanted)))
    for strategy, symbol in sorted(wanted.items()):
        db.execute(
            "INSERT INTO mt5_account_strategies(account_id,strategy,symbol,active,book,created_at) "
            "SELECT ?,?,?,1,?,datetime('now') WHERE NOT EXISTS ("
            "SELECT 1 FROM mt5_account_strategies WHERE account_id=? AND strategy=?)",
            (account, strategy, symbol, book_id, account, strategy))
        db.execute(
            "UPDATE mt5_account_strategies SET symbol=?,active=?,live_started_at=0,book=? "
            "WHERE account_id=? AND strategy=?",
            (symbol, int(strategy not in HELD), book_id, account, strategy))
    db.commit()
    left = differences(wanted, rows(db, account))
    if left:
        sys.exit("THE ACCOUNT STILL DIFFERS -- do not start the stack\n" + "\n".join(left))
    print(f"FundedNext account {account} on {book_id}: {len(wanted) - len(HELD)} sleeves "
          f"active, held inactive: {', '.join(sorted(HELD))}")


if __name__ == "__main__":
    main()
