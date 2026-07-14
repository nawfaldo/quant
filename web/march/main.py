"""March Trading Bot — main entry point (API-based architecture).

Flow:
  1. Initialize the MetaTrader 5 terminal connection.
  2. Start the Python API server (port 5001).
  3. Receive Zig execution commands (/execute); each names the strategy that
     fired, which is routed to the MT5 accounts running it (from march.db) and
     executed on each account's configured symbol.

MT5 accounts and per-strategy symbols are managed from the web UI and stored in
march.db — there is no .env configuration anymore.
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
    log.info(f"  Accounts:    from march.db (per-strategy symbols)")
    log.info(f"  Volume:      per-strategy (from march/zig config)")
    log.info(f"  Dry run:     {config.dry_run}")
    log.info("=" * 60)

    # Initialize the MT5 terminal. Account login happens per /execute, driven by
    # which accounts in march.db run the signalling strategy.
    mt5 = MT5Client()
    if not mt5.connect():
        log.error("Cannot initialize MT5. Exiting.")
        return

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
