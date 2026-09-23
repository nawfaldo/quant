#!/usr/bin/env python3
"""Hot-reset flat strategy slots without restarting other live positions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen

#: Where the Rust backend listens. MUST TRACK `live_trade/src/main.rs`, which reads
#: `PORT` and falls back to 4000. This said 8080 -- a port typed in two
#: languages drifts, and the failure is a connection refused against a server
#: that is running perfectly.
DEFAULT_BACKEND = f"http://127.0.0.1:{os.environ.get('PORT', '4000')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--cursor", type=int, required=True)
    parser.add_argument("--strategy", action="append", required=True)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    return parser.parse_args()


def status(backend: str) -> dict[str, object]:
    with urlopen(f"{backend.rstrip('/')}/api/march/live/status", timeout=5) as response:
        return json.load(response)


def wait_for(backend: str, predicate: object, description: str) -> dict[str, object]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = status(backend)
        if predicate(current):  # type: ignore[operator]
            return current
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {description}")


def strategy_magic(account_strategy_id: int) -> int:
    return 26_100_000 + account_strategy_id % 800_000


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

    connection = sqlite3.connect(database, timeout=10)
    disabled = False
    try:
        placeholders = ",".join("?" for _ in names)
        rows = connection.execute(
            "SELECT id,account_id,strategy FROM mt5_account_strategies "
            f"WHERE active=1 AND strategy IN ({placeholders}) ORDER BY id",
            names,
        ).fetchall()
        found = {str(row[2]) for row in rows}
        if found != set(names):
            raise RuntimeError(
                f"active strategy set changed: expected {sorted(names)}, got {sorted(found)}"
            )
        for strategy_id, account_id, strategy in rows:
            broker_positions = connection.execute(
                "SELECT count() FROM mt5_bridge_positions "
                "WHERE account_id=? AND magic=?",
                (account_id, strategy_magic(strategy_id)),
            ).fetchone()[0]
            if broker_positions:
                raise RuntimeError(f"{strategy} owns a broker position")
            pending = connection.execute(
                "SELECT count() FROM mt5_execution_commands "
                "WHERE account_strategy_id=? AND status IN ('pending','leased')",
                (strategy_id,),
            ).fetchone()[0]
            if pending:
                raise RuntimeError(f"{strategy} has a pending execution command")

        before = int(status(args.backend)["strategies"])
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)

        ids = tuple(int(row[0]) for row in rows)
        id_placeholders = ",".join("?" for _ in ids)
        with connection:
            connection.execute(
                f"UPDATE mt5_account_strategies SET active=0 WHERE id IN ({id_placeholders})",
                ids,
            )
        disabled = True
        wait_for(
            args.backend,
            lambda value: int(value["strategies"]) == before - len(ids),
            "old strategy slots to leave the runtime",
        )

        event_rows = [(row[0], row[2]) for row in rows]
        with connection:
            connection.execute(
                "UPDATE mt5_account_strategies SET live_started_at=?,active=1 "
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
                "'broker confirmed flat; strategy hot-reset for new entries')",
                event_rows,
            )
        disabled = False
        final = wait_for(
            args.backend,
            lambda value: int(value["strategies"]) == before
            and not set(names).intersection(value.get("blocked", [])),
            "reset strategy slots to return unblocked",
        )
    finally:
        if disabled:
            with connection:
                connection.execute(
                    f"UPDATE mt5_account_strategies SET active=1 WHERE strategy IN ({placeholders})",
                    names,
                )
        connection.close()
    print(json.dumps(final, separators=(",", ":")))
    print(f"Hot-reset {', '.join(names)}; backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
