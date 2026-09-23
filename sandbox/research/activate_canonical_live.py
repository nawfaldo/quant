"""Atomically activate the 2026-09-07 Exness book on the IDK MT5 account.

THE STRATEGY LIST IS NOT WRITTEN HERE. `mt5_account_strategies.symbol` is what
the bridge sends an order to, and a sleeve carrying another instrument's symbol
is stepped with its own market's bars while its real orders go somewhere else --
the failure that cost 800 dollars at an 88% drawdown while the standalone
backtest looked fine. Transcribing twenty-two ids and twenty-two broker symbols
into a second language is how that happens, so the rows come from

    cargo run --release --bin exness_live_book

which resolves each id through `required_symbol`, the same function
`symbol_matches_strategy` validates a stored row against. This script only
applies what that prints.

IT REFUSES TO RUN WITH ANYTHING OPEN. Deactivating a strategy that still holds a
position orphans it: the runtime drops the slot and stops managing the exit,
while the broker still has the trade. So an open position or an unsettled
command is a hard stop rather than a warning.

    py -m sandbox.research.activate_canonical_live            # dry run
    py -m sandbox.research.activate_canonical_live --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "live_trade" / "app.db"
SERVER = ROOT / "live_trade"


def book_rows():
    """`[(strategy, symbol)]` straight from the Rust registry."""
    result = subprocess.run(
        ["cargo", "run", "--quiet", "--release", "--bin", "exness_live_book"],
        cwd=SERVER, capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    return payload["book_id"], [(row["strategy"], row["symbol"])
                                for row in payload["strategies"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the change; without it nothing is touched")
    args = parser.parse_args()

    book_id, target = book_rows()
    db = sqlite3.connect(DATABASE, timeout=30)
    db.row_factory = sqlite3.Row
    accounts = db.execute(
        "SELECT a.id FROM mt5_accounts a JOIN environments e "
        "ON e.id=a.environment_id WHERE lower(e.name)=lower(?)",
        ("idk",),
    ).fetchall()
    if len(accounts) != 1:
        raise SystemExit(f"expected exactly one IDK account, found {len(accounts)}")
    account_id = int(accounts[0]["id"])

    open_positions = db.execute(
        "SELECT COUNT(*) FROM mt5_strategy_positions "
        "WHERE account_id=? AND status!='closed' AND remaining_volume>0",
        (account_id,),
    ).fetchone()[0]
    pending_commands = db.execute(
        "SELECT COUNT(*) FROM mt5_execution_commands WHERE account_id=? "
        "AND status NOT IN ('filled','closed','failed','cancelled')",
        (account_id,),
    ).fetchone()[0]
    if open_positions or pending_commands:
        raise SystemExit(
            f"refusing activation with {open_positions} open positions and "
            f"{pending_commands} pending commands")

    before = [dict(row) for row in db.execute(
        "SELECT strategy,symbol,active FROM mt5_account_strategies "
        "WHERE account_id=? ORDER BY strategy", (account_id,))]
    wanted = {strategy for strategy, _ in target}
    retiring = sorted(row["strategy"] for row in before
                      if row["strategy"] not in wanted)

    print(f"book            {book_id}")
    print(f"account         {account_id}")
    print(f"currently holds {len(before)} rows, {sum(r['active'] for r in before)} active")
    if retiring:
        print(f"removing        {', '.join(retiring)}")
    print(f"activating      {len(target)} sleeves")
    if not args.apply:
        print("\nDRY RUN -- nothing was written. Re-run with --apply.")
        return

    db.execute("BEGIN IMMEDIATE")
    # DELETED, NOT DEACTIVATED. Every one of these is a retired name the engine
    # can no longer build, so a dormant row is a row that can only ever confuse
    # the next person reading the account.
    db.execute(
        "DELETE FROM mt5_account_strategies WHERE account_id=? AND strategy NOT IN "
        f"({','.join('?' * len(wanted))})",
        (account_id, *sorted(wanted)),
    )
    for strategy, symbol in target:
        db.execute(
            "INSERT INTO mt5_account_strategies"
            "(account_id,strategy,symbol,active,book,created_at) "
            "SELECT ?,?,?,1,?,datetime('now') WHERE NOT EXISTS ("
            "SELECT 1 FROM mt5_account_strategies WHERE account_id=? AND strategy=?)",
            (account_id, strategy, symbol, book_id, account_id, strategy),
        )
        # `live_started_at` is reset so the runtime stamps it on THIS start.
        # Carrying an old one over would let the account count trades from a
        # previous book as this one's.
        #
        # `book` is stamped here too, so a row that predates the column -- or
        # one left over from an earlier book -- is relabelled rather than left
        # claiming to belong to nothing.
        db.execute(
            "UPDATE mt5_account_strategies "
            "SET symbol=?,active=1,live_started_at=0,book=? "
            "WHERE account_id=? AND strategy=?",
            (symbol, book_id, account_id, strategy),
        )
    db.commit()

    rows = [dict(row) for row in db.execute(
        "SELECT id,strategy,symbol,active,book,live_started_at "
        "FROM mt5_account_strategies WHERE account_id=? "
        "ORDER BY active DESC,strategy", (account_id,))]
    print(json.dumps({"account_id": account_id, "book": book_id,
                      "strategies": rows}, indent=2))


if __name__ == "__main__":
    main()
