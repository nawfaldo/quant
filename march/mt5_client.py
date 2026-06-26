"""MetaTrader 5 API wrapper.

Handles connection, bar fetching, position management, and order execution.
"""

import logging
from datetime import datetime
import MetaTrader5 as mt5
from config import config

logger = logging.getLogger("march")

class MT5Client:
    def __init__(self):
        self.magic = config.magic_number
        # Login currently active on the connected terminal. The MT5 package holds
        # one session at a time, so trading several accounts means re-logging in
        # (see login()); this lets us skip a redundant login for the same account.
        self.current_login: int = 0

    def connect(self) -> bool:
        """Initialize the MT5 terminal connection (no account login yet).

        Per-account login happens later via login(), driven by which accounts in
        march.db run the signalling strategy.
        """
        if not mt5.initialize():
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        logger.info("MT5 terminal initialized")
        return True

    def login(self, login: int, password: str, server: str) -> bool:
        """Log the terminal into a specific MT5 account.

        No-op (returns True) if that account is already the active session.
        """
        if not login or not password or not server:
            logger.error("login() requires login, password and server")
            return False
        if self.current_login == login:
            return True
        if not mt5.login(login, password=password, server=server):
            logger.error(f"MT5 login failed for {login} on {server}: {mt5.last_error()}")
            self.current_login = 0
            return False
        self.current_login = login
        logger.info(f"MT5 logged in as {login} on {server}")
        return True

    def disconnect(self) -> None:
        """Shut down the MT5 connection."""
        mt5.shutdown()
        self.current_login = 0
        logger.info("MT5 disconnected")

    def account_status(self, login: int, password: str, server: str) -> dict:
        """Verify one account's credentials/connection without trading.

        Logs the shared terminal into the account (a no-op if it is already the
        active session) and reads its account_info. Returns a small dict the web
        UI renders as a status dot:
            {"status": "ready"|"incomplete"|"error", "detail": <str>}
        """
        if not login or not password or not server:
            return {"status": "incomplete", "detail": "missing login, password or server"}
        if not self.login(login, password, server):
            return {"status": "error", "detail": f"login failed: {mt5.last_error()}"}
        info = mt5.account_info()
        if info is None:
            return {"status": "error", "detail": f"no account info: {mt5.last_error()}"}
        return {
            "status": "ready",
            "detail": f"{info.server} · balance {info.balance:.2f} {info.currency}",
            "balance": info.balance,
            "equity": info.equity,
            "currency": info.currency
        }

    def symbol_info(self, symbol: str):
        """Get symbol information."""
        return mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, visible: bool) -> bool:
        """Select a symbol in Market Watch."""
        return mt5.symbol_select(symbol, visible)

    def ensure_symbol(self, symbol: str) -> bool:
        """Make sure `symbol` exists on the current account and is in Market Watch."""
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error(f"Symbol '{symbol}' not found on account {self.current_login}")
            return False
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Could not add {symbol} to Market Watch: {mt5.last_error()}")
                return False
        return True

    def get_latest_bar(self, symbol: str) -> dict | None:
        """Fetch the most recently COMPLETED 1-minute bar."""
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 1)
        if rates is None or len(rates) == 0:
            logger.warning(f"No bar data for {symbol}: {mt5.last_error()}")
            return None

        bar = rates[0]
        return {
            "time": datetime.utcfromtimestamp(bar["time"]),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": int(bar["tick_volume"]),
        }

    def get_positions(self, symbol: str) -> list:
        """Get open positions for `symbol` on the current account (our magic only)."""
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return [p for p in positions if p.magic == self.magic]

    def get_all_positions(self) -> list:
        """All open positions on the current account (our magic only)."""
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [p for p in positions if p.magic == self.magic]

    def close_position(self, pos) -> float | None:
        """Close a specific open position. Returns close_price or None on failure."""
        close_type = (
            mt5.ORDER_TYPE_SELL
            if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        price = (
            mt5.symbol_info_tick(pos.symbol).bid
            if close_type == mt5.ORDER_TYPE_SELL
            else mt5.symbol_info_tick(pos.symbol).ask
        )

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": self.magic,
            "comment": "march close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"Failed to close position {pos.ticket}: "
                f"retcode={result.retcode}, comment={result.comment}"
            )
            return None
        else:
            logger.info(
                f"Closed position {pos.ticket}: "
                f"{'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL'} "
                f"{pos.volume} {pos.symbol} @ {result.price}"
            )
            return float(result.price)

    def open_trade(self, side: str, symbol: str, volume: float) -> tuple[float, float] | None:
        """Open a market order. side is 'buy' or 'sell' (or 'LONG'/'SHORT'). Returns (fill_price, spread) or None."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Cannot get tick for {symbol}: {mt5.last_error()}")
            return None

        spread = tick.ask - tick.bid
        side_upper = side.upper()
        if side_upper in ("BUY", "LONG"):
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif side_upper in ("SELL", "SHORT"):
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            logger.error(f"Invalid side: {side}")
            return None

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": self.magic,
            "comment": f"march {side.lower()}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"Order failed: {side} {volume} {symbol} @ {price} — "
                f"retcode={result.retcode}, comment={result.comment}"
            )
            return None

        logger.info(
            f"Order filled: {side} {volume} {symbol} @ {result.price} "
            f"(order={result.order})"
        )
        return (float(result.price), float(spread))
