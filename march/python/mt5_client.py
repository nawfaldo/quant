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

    def connect(self, login: int = 0, password: str = "", server: str = "") -> bool:
        """Initialize MT5 and optionally log in."""
        if not mt5.initialize():
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        if login and password and server:
            if not mt5.login(login, password=password, server=server):
                logger.error(f"MT5 login failed: {mt5.last_error()}")
                return False
            logger.info(f"MT5 logged in as {login} on {server}")
        else:
            account = mt5.account_info()
            if account:
                logger.info(
                    f"MT5 connected to existing session: "
                    f"{account.login} on {account.server}"
                )
            else:
                logger.warning("MT5 initialized but no account info available")

        return True

    def disconnect(self) -> None:
        """Shut down the MT5 connection."""
        mt5.shutdown()
        logger.info("MT5 disconnected")

    def symbol_info(self, symbol: str):
        """Get symbol information."""
        return mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, visible: bool) -> bool:
        """Select a symbol in Market Watch."""
        return mt5.symbol_select(symbol, visible)

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

    def get_positions(self) -> list:
        """Get all open positions for this symbol and magic number."""
        positions = mt5.positions_get(symbol=config.symbol)
        if positions is None:
            return []
        return [p for p in positions if p.magic == self.magic]

    def close_position(self, pos) -> bool:
        """Close a specific open position."""
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
            return False
        else:
            logger.info(
                f"Closed position {pos.ticket}: "
                f"{'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL'} "
                f"{pos.volume} {pos.symbol} @ {price}"
            )
            return True

    def open_trade(self, side: str, symbol: str, volume: float) -> bool:
        """Open a market order. side is 'buy' or 'sell' (or 'LONG'/'SHORT')."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Cannot get tick for {symbol}: {mt5.last_error()}")
            return False

        side_upper = side.upper()
        if side_upper in ("BUY", "LONG"):
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif side_upper in ("SELL", "SHORT"):
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            logger.error(f"Invalid side: {side}")
            return False

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
            return False

        logger.info(
            f"Order filled: {side} {volume} {symbol} @ {price} "
            f"(order={result.order})"
        )
        return True
