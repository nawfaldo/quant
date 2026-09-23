#!/usr/bin/env python3
"""Reset selected flat-broker live strategies so they can accept new entries."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--cursor", type=int, required=True)
    parser.add_argument("--strategy", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.db.resolve()
    backup = args.backup.resolve()
    names = tuple(dict.fromkeys(args.strategy))
    if not database.is_file():
        raise RuntimeError(f"database not found: {database}")
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    if backup.parent != database.parent:
        raise RuntimeError("backup must stay beside the application database")

    connection = sqlite3.connect(database)
    try:
        placeholders = ",".join("?" for _ in names)
        rows = connection.execute(
            "SELECT id,strategy FROM mt5_account_strategies "
            f"WHERE active=1 AND strategy IN ({placeholders}) ORDER BY id",
            names,
        ).fetchall()
        found = {str(row[1]) for row in rows}
        if found != set(names):
            raise RuntimeError(
                f"active strategy set changed: expected {sorted(names)}, got {sorted(found)}"
            )
        if connection.execute("SELECT count() FROM mt5_bridge_positions").fetchone()[0]:
            raise RuntimeError("broker positions are not empty")
        pending = connection.execute(
            "SELECT count() FROM mt5_execution_commands "
            "WHERE status IN ('pending','leased')"
        ).fetchone()[0]
        if pending:
            raise RuntimeError("execution commands are pending")

        with sqlite3.connect(backup) as destination:
            connection.backup(destination)

        ids = tuple(int(row[0]) for row in rows)
        id_placeholders = ",".join("?" for _ in ids)
        with connection:
            connection.execute(
                "UPDATE mt5_account_strategies SET live_started_at=? "
                f"WHERE id IN ({id_placeholders})",
                (args.cursor, *ids),
            )
            connection.execute(
                "UPDATE mt5_strategy_positions SET status='failed',remaining_volume=0,"
                "updated_at=datetime('now') "
                f"WHERE account_strategy_id IN ({id_placeholders}) "
                "AND status IN ('pending_open','open','pending_close')",
                ids,
            )
            connection.executemany(
                "INSERT INTO live_events"
                "(at,bar_time,account_strategy_id,strategy,kind,position_key,detail) "
                "VALUES(datetime('now'),'',?,?,'state_reset','',"
                "'broker confirmed flat; live epoch reset for new entries')",
                rows,
            )
    finally:
        connection.close()
    print(f"Reset {len(names)} strategies at cursor {args.cursor}; backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
