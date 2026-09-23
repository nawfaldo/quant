"""Local MetaTrader 5 execution bridge for the Quant Rust backend.

`py bridge.py` reads every environment account from `live_trade/app.db` and runs
them all. The Rust backend remains the owner of strategies, routing, command
persistence, and completed trade data.

The official MetaTrader integration binds one terminal session per *process*, so
a worker process is forked per account and supervised here. Two accounts
therefore need two terminal installations; map them with `--terminal ID=PATH`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import MetaTrader5 as _mt5
except ImportError:
    print(
        "MetaTrader5 is not installed. Run: "
        "py -m pip install -r mt5\\requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)

from ipc_lock import mt5_ipc_lock, synchronized_mt5

mt5 = synchronized_mt5(_mt5)


#: Where the Rust backend listens. MUST TRACK `live_trade/src/main.rs`, which reads
#: `PORT` and falls back to 4000 -- this said 8080 and every bridge start failed
#: with `WinError 10061` against a server that was running perfectly. A port
#: typed in two languages is a port that drifts, so the fallback here is the
#: same literal as the fallback there, and `PORT` overrides both.
DEFAULT_BACKEND = f"http://127.0.0.1:{os.environ.get('PORT', '4000')}"
DEFAULT_MAGIC = 26_032_026
SUCCESS_RETCODES = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
#: `deal.time` is broker server time while command timestamps come from this
#: host, so a deal-history lower bound needs slack for the gap between the two.
#: Wide enough for any realistic offset, far short of the repeat interval that
#: let a day-old deal match.
SKEW_GRACE_SECONDS = 6 * 60 * 60


class BridgeError(RuntimeError):
    pass


#: Set on worker processes so interleaved output names the account it came from.
LOG_PREFIX = ""


def log(message: str, *, error: bool = False) -> None:
    print(
        f"{LOG_PREFIX}{message}",
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


@dataclass(frozen=True)
class Account:
    account_id: int
    login: int
    password: str
    server: str
    name: str = ""
    environment_id: int | None = None
    account_type: str = "mt5"

    def label(self) -> str:
        return self.name.strip() or f"account {self.account_id}"


@dataclass
class Fill:
    entry_price: float = 0.0
    entry_spread: float = 0.0
    close_price: float = 0.0
    ticket: int = 0
    fill_time: int = 0
    mt5pnl: float = 0.0


def terminal_mapping(value: str) -> tuple[int, Path]:
    account_id, separator, path = value.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError(
            f"expected ACCOUNT_ID=PATH, got {value!r}"
        )
    try:
        return int(account_id), Path(path)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"invalid account id in {value!r}"
        ) from error


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--account-id",
        type=int,
        help="run only this mt5_accounts row instead of every account",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=root / "live_trade" / "app.db",
        help="Quant SQLite database",
    )
    parser.add_argument(
        "--terminal-path",
        type=Path,
        help="terminal64.exe path used by every account without a --terminal entry",
    )
    parser.add_argument(
        "--terminal",
        type=terminal_mapping,
        action="append",
        default=[],
        metavar="ACCOUNT_ID=PATH",
        help=(
            "per-account terminal64.exe; repeatable. Required beyond one account, "
            "since a terminal cannot hold two logins at once"
        ),
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument(
        "--token",
        default=os.environ.get("MT5_BRIDGE_TOKEN", ""),
        help="must match MT5_BRIDGE_TOKEN on the Rust backend",
    )
    # 0.1s: an HTTP GET to the backend on this host, so unlike the runtime's
    # market poll it costs no file work -- the average wait it removes from
    # every order is worth more than ten local requests a second.
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    parser.add_argument("--positions-seconds", type=float, default=0.25)
    parser.add_argument(
        "--backend-failsafe-seconds",
        type=float,
        default=90.0,
        help=(
            "close March-owned positions after this many seconds without the "
            "Rust backend; 0 disables the fail-safe"
        ),
    )
    parser.add_argument(
        "--magic",
        type=int,
        default=int(os.environ.get("MT5_MAGIC_NUMBER", str(DEFAULT_MAGIC))),
        help="position magic number; must match the Rust backend",
    )
    parser.add_argument("--portable", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--list",
        action="store_true",
        help="print the accounts found in the database, then exit",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify each account and terminal, then exit without polling",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="accepted for compatibility; running is the default",
    )
    args = parser.parse_args()
    if args.poll_seconds < 0.05:
        parser.error("--poll-seconds must be at least 0.05")
    if args.positions_seconds < 0.2:
        parser.error("--positions-seconds must be at least 0.2")
    if args.backend_failsafe_seconds < 0:
        parser.error("--backend-failsafe-seconds cannot be negative")
    return args


def load_accounts(db_path: Path, account_id: int | None = None) -> list[Account]:
    """Every environment MT5 account, or just one when `account_id` is given."""
    if not db_path.is_file():
        raise BridgeError(f"database not found: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sql = (
            "SELECT id,login,password,server,name,environment_id,account_type "
            "FROM mt5_accounts WHERE environment_id IS NOT NULL"
        )
        params: tuple[Any, ...] = ()
        if account_id is not None:
            sql += " AND id=?"
            params = (account_id,)
        rows = connection.execute(sql + " ORDER BY id", params).fetchall()
    finally:
        connection.close()
    if not rows:
        qualifier = f" with id {account_id}" if account_id is not None else ""
        raise BridgeError(f"no environment MT5 account{qualifier} found")
    accounts = []
    for row in rows:
        try:
            login = int(row[1])
        except (TypeError, ValueError) as error:
            raise BridgeError(f"account {row[0]} has an invalid login") from error
        if not row[2] or not row[3]:
            raise BridgeError(f"account {row[0]} is missing password or server")
        accounts.append(
            Account(
                account_id=int(row[0]),
                login=login,
                password=str(row[2]),
                server=str(row[3]),
                name=str(row[4] or ""),
                environment_id=row[5],
                account_type=str(row[6] or "mt5"),
            )
        )
    return accounts


def load_account_magics(db_path: Path, account_id: int) -> set[int]:
    """Every magic number belonging to active strategies for this account."""
    if not db_path.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT id FROM mt5_account_strategies WHERE account_id=? AND active=1",
                (account_id,),
            ).fetchall()
            return {26_100_000 + (int(r[0]) % 800_000) for r in rows}
        finally:
            connection.close()
    except Exception:
        return set()


def print_accounts(accounts: list[Account], db_path: Path) -> None:
    print(f"{len(accounts)} account(s) in {db_path}:")
    for account in accounts:
        print(
            f"  [{account.account_id}] {account.label()} | login {account.login} "
            f"| {account.server} | environment {account.environment_id} "
            f"| {account.account_type}"
        )


def terminal_for(args: argparse.Namespace, account: Account) -> Path | None:
    return dict(args.terminal).get(account.account_id, args.terminal_path)


def connect(account: Account, terminal_path: Path | None, portable: bool) -> None:
    kwargs = {
        "login": account.login,
        "password": account.password,
        "server": account.server,
        "timeout": 60_000,
        "portable": portable,
    }
    if terminal_path is not None:
        if not terminal_path.is_file():
            raise BridgeError(f"terminal not found: {terminal_path}")
        connected = mt5.initialize(str(terminal_path), **kwargs)
    else:
        connected = mt5.initialize(**kwargs)
    if not connected:
        raise BridgeError(f"MT5 initialize failed: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None:
        raise BridgeError(f"MT5 account_info failed: {mt5.last_error()}")
    if int(info.login) != account.login:
        raise BridgeError(
            f"terminal connected to login {info.login}, expected {account.login}"
        )
    log(f"Connected account {info.login} on {info.server}")


def post_json(backend: str, path: str, payload: dict[str, Any]) -> tuple[int, str]:
    request = Request(
        backend.rstrip("/") + path,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, ConnectionError) as error:
        # The Rust backend may restart during development or deployment. Keep
        # the MT5 session alive and let the existing poll/result retry loops
        # recover instead of terminating the bridge process.
        reason = getattr(error, "reason", error)
        return 0, f"backend unavailable: {reason}"


def backend_unhealthy(status: int) -> bool:
    """Only transport/server outages justify protective liquidation.

    A 4xx response proves the backend is reachable and normally indicates a
    bad token, login, or command. Treating configuration errors as an outage
    would close healthy positions merely because the operator mistyped a
    credential.
    """
    return status == 0 or status >= 500


def normalize_volume(symbol: str, requested: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None or requested <= 0:
        raise BridgeError(f"invalid volume or unavailable symbol: {symbol}")
    step = float(info.volume_step)
    minimum = float(info.volume_min)
    maximum = float(info.volume_max)
    if step <= 0 or minimum <= 0 or maximum <= 0:
        raise BridgeError(f"invalid broker volume limits for {symbol}")
    if requested < minimum - 1e-12:
        raise BridgeError(
            f"requested volume {requested} is below {symbol} minimum {minimum}"
        )
    if requested > maximum + 1e-12:
        raise BridgeError(
            f"requested volume {requested} exceeds {symbol} maximum {maximum}"
        )
    # Strategies and backtests floor to the legal step. Nearest-step rounding
    # can increase risk live relative to the modeled order.
    volume = math.floor((requested + 1e-12) / step) * step
    if volume < minimum - 1e-12:
        raise BridgeError(
            f"floored volume {volume} is below {symbol} minimum {minimum}"
        )
    digits = max(0, min(8, -math.floor(math.log10(step)))) if step < 1 else 0
    return round(volume, digits)


def filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_RETURN
    modes = int(info.filling_mode)
    if modes & 2:
        return mt5.ORDER_FILLING_IOC
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def route_symbol(symbol: str) -> str:
    symbol = symbol.strip()
    return "USTEC" if symbol.casefold() == "nq" else symbol


def send_deal(
    *,
    symbol: str,
    order_type: int,
    volume: float,
    price: float,
    magic: int,
    deviation: int,
    comment: str,
    position: int = 0,
) -> tuple[float, int]:
    request: dict[str, Any] = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode(symbol),
    }
    if position:
        request["position"] = position
    # order_send's C-extension parser must receive its request directly and
    # positionally. Calling it through the transparent proxy is the one MT5
    # operation that this build rejects, so hold the same mutex explicitly.
    with mt5_ipc_lock():
        result = _mt5.order_send(request)
    if result is None:
        raise BridgeError(f"order_send failed: {mt5.last_error()}")
    if result.retcode not in SUCCESS_RETCODES:
        raise BridgeError(
            f"order_send retcode={result.retcode}: {result.comment}"
        )
    filled_price = float(result.price) if result.price else price
    ticket = int(result.deal or result.order or 0)
    return filled_price, ticket


def close_position(
    position: Any, volume: float, magic: int, deviation: int, comment: str
) -> tuple[float, int]:
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        raise BridgeError(f"no tick available while closing {position.symbol}")
    closing_type = (
        mt5.ORDER_TYPE_SELL
        if position.type == mt5.POSITION_TYPE_BUY
        else mt5.ORDER_TYPE_BUY
    )
    price = float(tick.bid if closing_type == mt5.ORDER_TYPE_SELL else tick.ask)
    return send_deal(
        symbol=position.symbol,
        order_type=closing_type,
        volume=normalize_volume(position.symbol, min(float(position.volume), volume)),
        price=price,
        position=int(position.ticket),
        magic=magic,
        deviation=deviation,
        comment=comment,
    )


def recover_fill(command_id: int, action: str, issued_at: int) -> Fill | None:
    # A deal is only this command's if it happened after the backend created the
    # command. Without that bound the 7-day scan could match an older deal that
    # carried the same comment and hand back its price, P&L and -- worst of all
    # -- its `deal.time`, which is how closes ended up stamped a day before the
    # entry they belonged to. SKEW_GRACE absorbs drift between this host's clock
    # and the broker server's, which is what `deal.time` is measured against.
    floor = datetime.fromtimestamp(max(0, issued_at - SKEW_GRACE_SECONDS))
    now = datetime.now()
    deals = mt5.history_deals_get(min(floor, now), now + timedelta(minutes=1))
    if deals is None:
        return None
    cutoff = max(0, issued_at - SKEW_GRACE_SECONDS)
    comment = f"march:{command_id}"
    fill = Fill()
    found_entry = False
    found_close = False
    closed_positions: set[int] = set()
    for deal in deals:
        if deal.comment != comment or int(deal.time) < cutoff:
            continue
        if deal.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT):
            fill.entry_price = float(deal.price)
            if int(deal.position_id):
                fill.ticket = int(deal.position_id)
            found_entry = True
        if deal.entry in (
            mt5.DEAL_ENTRY_OUT,
            mt5.DEAL_ENTRY_OUT_BY,
            mt5.DEAL_ENTRY_INOUT,
        ):
            fill.close_price = float(deal.price)
            fill.mt5pnl += float(deal.profit)
            fill.mt5pnl += float(deal.commission)
            fill.mt5pnl += float(deal.swap)
            fill.mt5pnl += float(deal.fee)
            if int(deal.position_id):
                closed_positions.add(int(deal.position_id))
            found_close = True
        if not fill.ticket:
            fill.ticket = int(deal.ticket)
        fill.fill_time = int(deal.time)
    if found_close and closed_positions:
        position_pnl = 0.0
        complete_history = True
        for position_id in closed_positions:
            position_deals = mt5.history_deals_get(position=position_id)
            if position_deals is None:
                complete_history = False
                break
            for deal in position_deals:
                position_pnl += float(deal.profit)
                position_pnl += float(deal.commission)
                position_pnl += float(deal.swap)
                position_pnl += float(deal.fee)
        if complete_history:
            fill.mt5pnl = position_pnl
    wanted = found_entry if action in ("long", "short") else found_close
    return fill if wanted else None


def wait_for_fill(command_id: int, action: str, issued_at: int) -> Fill | None:
    for _ in range(10):
        recovered = recover_fill(command_id, action, issued_at)
        if recovered is not None:
            return recovered
        time.sleep(0.05)
    return None


def execute_command(fields: list[str]) -> dict[str, Any]:
    if len(fields) != 9 or fields[0] != "ORDER":
        raise BridgeError(f"invalid backend command: {'|'.join(fields)}")
    command_id = int(fields[1])
    action = fields[2]
    symbol = route_symbol(fields[3])
    requested_volume = float(fields[4])
    magic = int(fields[5])
    deviation = int(fields[6])
    target_ticket = int(fields[7])
    issued_at = int(fields[8])
    if action not in ("long", "short", "close", "flat"):
        raise BridgeError(f"unknown action: {action}")
    if not mt5.symbol_select(symbol, True):
        raise BridgeError(f"symbol is unavailable: {symbol}")

    recovered = recover_fill(command_id, action, issued_at)
    if recovered is not None:
        return result_payload(command_id, True, recovered)

    comment = f"march:{command_id}"
    fill = Fill(fill_time=int(time.time()))
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        raise BridgeError(f"positions_get failed: {mt5.last_error()}")
    should_replace_existing = action in ("long", "short") and target_ticket >= 0
    if action in ("close", "flat") or should_replace_existing:
        matches = [
            position
            for position in positions
            if int(position.magic) == magic
            and (target_ticket <= 0 or int(position.ticket) == target_ticket)
        ]
        if target_ticket > 0 and not matches:
            raise BridgeError(f"managed position ticket {target_ticket} is unavailable")
        remaining = requested_volume
        for position in matches:
            close_volume = (
                float(position.volume)
                if target_ticket <= 0
                else min(float(position.volume), remaining)
            )
            fill.close_price, fill.ticket = close_position(
                position, close_volume, magic, deviation, comment
            )
            fill.fill_time = int(time.time())
            remaining = max(0.0, remaining - close_volume)
            if target_ticket > 0:
                break

    if action in ("close", "flat"):
        recorded = wait_for_fill(command_id, action, issued_at) if fill.ticket else None
        return result_payload(command_id, True, recorded or fill)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise BridgeError(f"no tick available for {symbol}")
    fill.entry_spread = float(tick.ask - tick.bid)
    order_type = mt5.ORDER_TYPE_BUY if action == "long" else mt5.ORDER_TYPE_SELL
    price = float(tick.ask if action == "long" else tick.bid)
    fill.entry_price, fill.ticket = send_deal(
        symbol=symbol,
        order_type=order_type,
        volume=normalize_volume(symbol, requested_volume),
        price=price,
        magic=magic,
        deviation=deviation,
        comment=comment,
    )
    fill.fill_time = int(time.time())
    recorded = wait_for_fill(command_id, action, issued_at)
    if recorded is not None:
        recorded.entry_spread = fill.entry_spread
        return result_payload(command_id, True, recorded)
    return result_payload(command_id, True, fill)


def result_payload(
    command_id: int, filled: bool, fill: Fill, error: str = ""
) -> dict[str, Any]:
    return {
        "command_id": command_id,
        "status": "filled" if filled else "failed",
        "ticket": fill.ticket,
        "entry_price": fill.entry_price,
        "entry_spread": fill.entry_spread,
        "close_price": fill.close_price,
        "mt5pnl": fill.mt5pnl,
        "fill_time": fill.fill_time,
        "error": error,
    }


def position_payload(
    token: str,
    login: str,
    server: str,
    managed_magics: set[int],
    positions: Any = None,
) -> dict[str, Any]:
    if positions is None:
        positions = mt5.positions_get()
    if positions is None:
        raise BridgeError(f"positions_get failed: {mt5.last_error()}")
    rows = []
    for position in positions:
        comment = str(getattr(position, "comment", ""))
        if int(position.magic) not in managed_magics and not comment.startswith("march:"):
            continue
        rows.append(
            {
                "ticket": int(position.ticket),
                "type": (
                    "long"
                    if position.type == mt5.POSITION_TYPE_BUY
                    else "short"
                ),
                "symbol": position.symbol,
                "volume": float(position.volume),
                "profit": float(position.profit),
                "open_price": float(position.price_open),
                "open_time": int(position.time),
                "magic": int(position.magic),
            }
        )
    return {
        "token": token,
        "login": login,
        "server": server,
        "positions": rows,
    }


def is_managed_position(position: Any, managed_magics: set[int]) -> bool:
    comment = str(getattr(position, "comment", ""))
    return int(position.magic) in managed_magics or comment.startswith("march:")


def protective_receipt(
    login: str,
    position: Any,
    fallback_price: float,
    reason: str = "bridge_backend_failsafe",
) -> dict[str, Any]:
    close_price = fallback_price
    fill_time = 0
    pnl = 0.0
    deals = mt5.history_deals_get(position=int(position.ticket))
    if deals is not None:
        for deal in deals:
            pnl += float(deal.profit)
            pnl += float(deal.commission)
            pnl += float(deal.swap)
            pnl += float(deal.fee)
            if deal.entry in (
                mt5.DEAL_ENTRY_OUT,
                mt5.DEAL_ENTRY_OUT_BY,
                mt5.DEAL_ENTRY_INOUT,
            ):
                close_price = float(deal.price)
                fill_time = max(fill_time, int(deal.time))
    return {
        "login": login,
        "ticket": int(position.ticket),
        "close_price": close_price,
        "pnl": pnl,
        "fill_time": fill_time or int(time.time()),
        "reason": reason,
    }


def backend_failsafe(
    login: str,
    managed_magics: set[int],
) -> list[dict[str, Any]]:
    positions = mt5.positions_get()
    if positions is None:
        raise BridgeError(f"positions_get failed during fail-safe: {mt5.last_error()}")
    receipts: list[dict[str, Any]] = []
    for position in positions:
        if not is_managed_position(position, managed_magics):
            continue
        close_price, _ = close_position(
            position,
            float(position.volume),
            int(position.magic),
            int(os.environ.get("MT5_DEVIATION_POINTS", "20")),
            "march:backend-failsafe",
        )
        # Give terminal history a brief chance to expose the closing deal so
        # the receipt carries authoritative P&L rather than only the send price.
        time.sleep(0.05)
        receipts.append(protective_receipt(login, position, close_price))
        log(
            f"BACKEND FAIL-SAFE closed ticket {int(position.ticket)} "
            f"{position.symbol} {float(position.volume):.8g}",
            error=True,
        )
    return receipts


def maybe_fire_backend_failsafe(
    args: argparse.Namespace,
    login: str,
    managed_magics: set[int],
    unavailable_since: float | None,
    already_fired: bool,
    receipts: list[dict[str, Any]],
) -> bool:
    if (
        already_fired
        or unavailable_since is None
        or args.backend_failsafe_seconds <= 0
        or time.monotonic() - unavailable_since < args.backend_failsafe_seconds
    ):
        return already_fired
    try:
        receipts.extend(backend_failsafe(login, managed_magics))
        return True
    except Exception as error:
        log(f"Backend fail-safe close failed: {error}", error=True)
        return False


def run(args: argparse.Namespace, account: Account) -> None:
    connect(account, terminal_for(args, account), args.portable)
    pending_result: dict[str, Any] | None = None
    last_positions = 0.0
    active_magic = args.magic
    managed_magics = {args.magic} | load_account_magics(args.db, account.account_id)
    backend_unavailable_since: float | None = None
    failsafe_fired = False
    protective_receipts: list[dict[str, Any]] = []
    tracked_positions: dict[int, Any] = {}
    first_poll = True
    while True:
        started = time.monotonic()
        info = mt5.account_info()
        if info is None:
            raise BridgeError(f"lost MT5 connection: {mt5.last_error()}")
        login = str(info.login)
        server = str(info.server)

        # Track active managed positions to catch manual/external closes
        raw_positions = mt5.positions_get()
        if raw_positions is not None:
            current_managed = {
                int(p.ticket): p
                for p in raw_positions
                if is_managed_position(p, managed_magics)
            }
            if not first_poll:
                for ticket, old_pos in list(tracked_positions.items()):
                    if ticket not in current_managed:
                        receipt = protective_receipt(
                            login,
                            old_pos,
                            float(getattr(old_pos, "price_current", 0.0) or getattr(old_pos, "price_open", 0.0)),
                            reason="manual_close",
                        )
                        protective_receipts.append(receipt)
                        log(
                            f"Detected manual/external close for ticket {ticket} "
                            f"({getattr(old_pos, 'symbol', '')}); queued close receipt"
                        )
            first_poll = False
            tracked_positions = current_managed

        if pending_result is not None:
            body = {"token": args.token, "login": login, **pending_result}
            status, response = post_json(
                args.backend, "/api/march/mt5/bridge/result", body
            )
            if 200 <= status < 300:
                pending_result = None
                backend_unavailable_since = None
                failsafe_fired = False
            else:
                log(f"Result delivery failed ({status}): {response}", error=True)
                if backend_unhealthy(status):
                    if backend_unavailable_since is None:
                        backend_unavailable_since = time.monotonic()
                    failsafe_fired = maybe_fire_backend_failsafe(
                        args,
                        login,
                        managed_magics,
                        backend_unavailable_since,
                        failsafe_fired,
                        protective_receipts,
                    )
                else:
                    backend_unavailable_since = None
                time.sleep(args.poll_seconds)
                continue

        while protective_receipts:
            receipt = {"token": args.token, **protective_receipts[0]}
            status, response = post_json(
                args.backend,
                "/api/march/mt5/bridge/protective-close",
                receipt,
            )
            if 200 <= status < 300 or status == 404:
                # A 404 means the command result already reconciled the same
                # ticket; the receipt is idempotently obsolete.
                protective_receipts.pop(0)
                backend_unavailable_since = None
                failsafe_fired = False
            else:
                log(
                    f"Protective close delivery failed ({status}): {response}",
                    error=True,
                )
                if backend_unavailable_since is None:
                    backend_unavailable_since = time.monotonic()
                time.sleep(args.poll_seconds)
                break
        if protective_receipts:
            continue

        now = time.monotonic()
        if now - last_positions >= args.positions_seconds:
            status, response = post_json(
                args.backend,
                "/api/march/mt5/bridge/positions",
                position_payload(args.token, login, server, managed_magics, raw_positions),
            )
            if status < 200 or status >= 300:
                log(f"Position report failed ({status}): {response}", error=True)
            last_positions = now

        poll = {
            "token": args.token,
            "login": login,
            "server": server,
            "balance": float(info.balance),
            "equity": float(info.equity),
            "currency": str(info.currency),
        }
        status, response = post_json(
            args.backend, "/api/march/mt5/bridge/poll", poll
        )
        if status < 200 or status >= 300:
            log(f"Poll failed ({status}): {response}", error=True)
            if backend_unhealthy(status):
                if backend_unavailable_since is None:
                    backend_unavailable_since = time.monotonic()
                failsafe_fired = maybe_fire_backend_failsafe(
                    args,
                    login,
                    managed_magics,
                    backend_unavailable_since,
                    failsafe_fired,
                    protective_receipts,
                )
            else:
                backend_unavailable_since = None
        elif response and response != "NONE":
            backend_unavailable_since = None
            failsafe_fired = False
            fields = response.split("|")
            command_id = int(fields[1]) if len(fields) > 1 else 0
            try:
                active_magic = int(fields[5])
                managed_magics.add(active_magic)
                pending_result = execute_command(fields)
            except Exception as error:
                pending_result = result_payload(
                    command_id, False, Fill(fill_time=int(time.time())), str(error)
                )
        else:
            backend_unavailable_since = None
            failsafe_fired = False

        elapsed = time.monotonic() - started
        time.sleep(max(0.0, args.poll_seconds - elapsed))


def check_accounts(args: argparse.Namespace, accounts: list[Account]) -> int:
    """Log into each account in turn, then exit without polling."""
    for account in accounts:
        try:
            connect(account, terminal_for(args, account), args.portable)
        finally:
            mt5.shutdown()
    log(f"Connection check passed for {len(accounts)} account(s); nothing polled")
    return 0


def run_worker(args: argparse.Namespace, account: Account) -> int:
    """Keep one account connected until the operator explicitly stops it."""
    while True:
        try:
            run(args, account)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            log(
                f"Worker error: {error}; reconnecting in 5s",
                error=True,
            )
        finally:
            mt5.shutdown()
        time.sleep(5.0)


def child_command(args: argparse.Namespace, account: Account) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--account-id",
        str(account.account_id),
        "--db",
        str(args.db),
        "--backend",
        args.backend,
        "--poll-seconds",
        str(args.poll_seconds),
        "--positions-seconds",
        str(args.positions_seconds),
        "--backend-failsafe-seconds",
        str(args.backend_failsafe_seconds),
        "--magic",
        str(args.magic),
    ]
    terminal = terminal_for(args, account)
    if terminal is not None:
        command += ["--terminal-path", str(terminal)]
    if args.portable:
        command.append("--portable")
    return command


def supervise(args: argparse.Namespace, accounts: list[Account]) -> int:
    """One worker process per account, restarted if it dies.

    MetaTrader binds a terminal session per process, and a terminal holds one
    login, so every account past the first needs its own terminal installation.
    """
    mapped = dict(args.terminal)
    unmapped = [account for account in accounts if account.account_id not in mapped]
    if len(unmapped) > 1:
        log(
            "Warning: "
            + ", ".join(f"account {a.account_id}" for a in unmapped)
            + " share one terminal. A terminal holds a single login, so these "
            "will fight over it; give each its own with --terminal ID=PATH.",
            error=True,
        )

    # The token travels through the environment rather than argv, which is
    # readable by any process on the machine.
    environment = os.environ.copy()
    environment["MT5_BRIDGE_TOKEN"] = args.token
    environment["QUANT_BRIDGE_CHILD"] = "1"

    workers: dict[int, subprocess.Popen[bytes]] = {}
    try:
        for account in accounts:
            workers[account.account_id] = subprocess.Popen(
                child_command(args, account), env=environment
            )
            log(f"Started worker for {account.label()} (login {account.login})")
        while True:
            time.sleep(1.0)
            for account in accounts:
                worker = workers[account.account_id]
                if worker.poll() is None:
                    continue
                log(
                    f"Worker for {account.label()} exited ({worker.returncode}); "
                    "restarting in 5s",
                    error=True,
                )
                time.sleep(5.0)
                workers[account.account_id] = subprocess.Popen(
                    child_command(args, account), env=environment
                )
    except KeyboardInterrupt:
        log("Stopping workers...")
    finally:
        for worker in workers.values():
            if worker.poll() is None:
                worker.terminate()
        for worker in workers.values():
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
    return 0


def main() -> int:
    global LOG_PREFIX
    args = parse_args()
    db_path = args.db.resolve()
    try:
        accounts = load_accounts(db_path, args.account_id)
        if args.list:
            print_accounts(accounts, db_path)
            return 0
        if os.environ.get("QUANT_BRIDGE_CHILD") and len(accounts) == 1:
            LOG_PREFIX = f"[account {accounts[0].account_id}] "
        else:
            print_accounts(accounts, db_path)
        if args.check:
            return check_accounts(args, accounts)
        if len(accounts) == 1:
            return run_worker(args, accounts[0])
        return supervise(args, accounts)
    except KeyboardInterrupt:
        log("Bridge stopped")
        return 0
    except BridgeError as error:
        log(f"Bridge error: {error}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
