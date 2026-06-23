"""
march/python/api_server.py

FastAPI server — receives trade execution commands from the Zig API server
and carries them out on MetaTrader 5.

Endpoints:
    GET  /health            → {"status":"ok"}
    POST /execute           → {"action": "long"|"short"|"flat"|"close"}
    GET  /positions         → list of open MT5 positions
"""

import threading
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import config
from logger import get_logger
from mt5_client import MT5Client

log = get_logger("api_server")

app = FastAPI(title="march Python API", version="1.0.0")

# Shared MT5 client — initialised once by start_server().
_mt5: MT5Client | None = None
_lock = threading.Lock()


# ── Models ─────────────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    action: str  # "long" | "short" | "flat" | "close"


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/execute")
def execute(req: ExecuteRequest):
    """Called by the Zig API when strategy signal changes."""
    action = req.action.lower()
    log.info(f"Execute command received: {action}")

    if _mt5 is None:
        raise HTTPException(status_code=503, detail="MT5 not connected")

    if config.dry_run:
        log.info(f"DRY RUN — would execute: {action}")
        return {"ok": True, "dry_run": True, "action": action}

    with _lock:
        try:
            if action in ("close", "flat"):
                positions = _mt5.get_positions()
                for pos in positions:
                    _mt5.close_position(pos)
                log.info(f"Closed {len(positions)} position(s)")
                return {"ok": True, "action": action, "closed": len(positions)}

            elif action == "long":
                positions = _mt5.get_positions()
                # Close any existing shorts first.
                for pos in positions:
                    if pos.type == 1:  # SELL
                        _mt5.close_position(pos)
                _mt5.open_trade("buy", config.symbol, config.volume)
                log.info(f"Opened LONG {config.volume} {config.symbol}")
                return {"ok": True, "action": action}

            elif action == "short":
                positions = _mt5.get_positions()
                # Close any existing longs first.
                for pos in positions:
                    if pos.type == 0:  # BUY
                        _mt5.close_position(pos)
                _mt5.open_trade("sell", config.symbol, config.volume)
                log.info(f"Opened SHORT {config.volume} {config.symbol}")
                return {"ok": True, "action": action}

            else:
                raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

        except Exception as e:
            log.error(f"Execute failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions")
def positions():
    if _mt5 is None:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    with _lock:
        pos_list = _mt5.get_positions()
    return [
        {
            "ticket": p.ticket,
            "type": "long" if p.type == 0 else "short",
            "symbol": p.symbol,
            "volume": p.volume,
            "profit": p.profit,
            "open_price": p.price_open,
        }
        for p in pos_list
    ]


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_server(mt5_client: MT5Client, port: int = 5001) -> threading.Thread:
    """Start the API server in a background daemon thread."""
    global _mt5
    _mt5 = mt5_client

    def _run():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    log.info(f"Python API server started on http://127.0.0.1:{port}")
    return t
