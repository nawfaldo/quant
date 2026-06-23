"""March Trading Bot — main entry point (API-based architecture).

Flow:
  1. Connect to MetaTrader 5.
  2. Start Python API server (port 5001) in the foreground.
  3. Listen to Zig execution commands (/execute).
"""

import uvicorn
import api_server
from config import config
from logger import get_logger
from mt5_client import MT5Client

log = get_logger("main")

def main() -> None:
    log.info("=" * 60)
    log.info("March Trading Bot starting (API execution receiver mode)")
    log.info(f"  MT5 server:  {config.mt5_server}")
    log.info(f"  Symbol:      {config.symbol}")
    log.info(f"  Volume:      {config.volume}")
    log.info(f"  Dry run:     {config.dry_run}")
    log.info("=" * 60)

    # Connect to MT5.
    mt5 = MT5Client()
    if not mt5.connect(config.mt5_login, config.mt5_password, config.mt5_server):
        log.error("Cannot connect to MT5. Exiting.")
        return

    symbol_info = mt5.symbol_info(config.symbol)
    if symbol_info is None:
        log.error(f"Symbol '{config.symbol}' not found. Check broker symbol name.")
        mt5.disconnect()
        return
    if not symbol_info.visible:
        mt5.symbol_select(config.symbol, True)
        log.info(f"Added {config.symbol} to Market Watch")

    # Set the shared MT5 client for the FastAPI app.
    api_server._mt5 = mt5

    try:
        log.info(f"Starting Python API server on port {config.python_api_port}...")
        uvicorn.run(api_server.app, host="127.0.0.1", port=config.python_api_port, log_level="warning")
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received.")
    finally:
        log.info("Shutting down...")
        mt5.disconnect()
        log.info("March Trading Bot stopped.")

if __name__ == "__main__":
    main()
