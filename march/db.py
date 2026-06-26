"""Read-only access to march.db (the SQLite database owned by the Zig server).

The Zig API (the march server, now integrated into web/backend) creates and
writes the `mt5_accounts` and `mt5_account_strategies` tables; the web UI manages
their rows. Python only reads them here to learn, for a given strategy, which MT5
accounts to trade and on which symbol.
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# The march server (inside web/backend) opens march.db at web/backend/march.db.
# This file lives in march/, so the DB is at ../web/backend/march.db.
DB_PATH = Path(__file__).resolve().parent.parent / "web" / "backend" / "march.db"


@dataclass
class AccountTarget:
    """One MT5 account + the symbol it trades for a particular strategy."""
    account_id: int
    name: str
    login: int
    password: str
    server: str
    symbol: str


def accounts_for_strategy(strategy: str) -> list[AccountTarget]:
    """Every account that has `strategy` attached, with that account's symbol.

    Returns an empty list if the DB does not exist yet or no account runs the
    strategy. `login` is stored as TEXT in the DB and coerced to int here.
    """
    if not strategy or not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.name, a.login, a.password, a.server, s.symbol
            FROM mt5_account_strategies s
            JOIN mt5_accounts a ON a.id = s.account_id
            WHERE s.strategy = ? AND s.active = 1
            ORDER BY a.id
            """,
            (strategy,),
        ).fetchall()
    finally:
        conn.close()

    return [_row_to_target(r) for r in rows]


def all_accounts() -> list[AccountTarget]:
    """Every MT5 account in the DB (symbol left empty — not strategy-specific)."""
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT id, name, login, password, server FROM mt5_accounts ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    return [_row_to_target((*r, "")) for r in rows]


def _row_to_target(row) -> AccountTarget:
    account_id, name, login, password, server, symbol = row
    try:
        login_int = int(login)
    except (TypeError, ValueError):
        login_int = 0
    return AccountTarget(
        account_id=account_id,
        name=name or "",
        login=login_int,
        password=password or "",
        server=server or "",
        symbol=symbol or "",
    )


def update_open_times(trade_id: int, mt5_open_time: str, mt5_entry_price: float, mt5_entry_price_spread: float) -> None:
    """Update mt5_open_time, mt5_entry_price, and mt5_entry_price_spread in the database for the given trade_id."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            UPDATE trades 
            SET mt5_open_time = ?, mt5_entry_price = ?, mt5_entry_price_spread = ? 
            WHERE id = ?
            """,
            (mt5_open_time, mt5_entry_price, mt5_entry_price_spread, trade_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_close_times(trade_id: int, mt5_close_time: str, mt5_close_price: float) -> None:
    """Update mt5_close_time, and mt5_close_price in the database for the given trade_id."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            UPDATE trades 
            SET mt5_close_time = ?, mt5_close_price = ? 
            WHERE id = ?
            """,
            (mt5_close_time, mt5_close_price, trade_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_position_strategies() -> dict[tuple[int, str], list[str]]:
    """Returns a dict mapping (login_int, symbol_upper) -> [list of strategy names]."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """
            SELECT a.login, s.symbol, s.strategy
            FROM mt5_account_strategies s
            JOIN mt5_accounts a ON a.id = s.account_id
            """
        ).fetchall()
    finally:
        conn.close()

    mapping: dict[tuple[int, str], list[str]] = {}
    for login, symbol, strategy in rows:
        try:
            l_int = int(login)
        except (TypeError, ValueError):
            l_int = 0
        key = (l_int, symbol.upper())
        mapping.setdefault(key, []).append(strategy)
    return mapping


def get_open_trades() -> dict[str, dict[str, Any]]:
    """Returns a dict mapping strategy_name -> {'price': float, 'open_time': str} for open trades."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """
            SELECT strategy_name, zig_entry_price, zig_open_time
            FROM trades
            WHERE zig_close_time = ''
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        conn.close()
    
    mapping = {}
    for strategy_name, zig_entry_price, zig_open_time in rows:
        if strategy_name not in mapping:
            mapping[strategy_name] = {
                "price": zig_entry_price,
                "open_time": zig_open_time
            }
    return mapping
