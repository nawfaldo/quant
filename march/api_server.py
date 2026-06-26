"""
march/python/api_server.py

FastAPI server — receives trade execution commands from the Zig API server and
carries them out on MetaTrader 5.

Each /execute carries the `strategy` that fired. We look that strategy up in
march.db (mt5_account_strategies) to find every MT5 account running it and the
symbol each trades, then log into each account in turn and enter/exit there.

Endpoints:
    GET  /health            → {"status":"ok"}
    POST /execute           → {"action": "long"|"short"|"flat"|"close", "volume": float, "strategy": str}
    GET  /positions         → open MT5 positions across all configured accounts
"""

import threading
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from config import config
from logger import get_logger
from mt5_client import MT5Client

log = get_logger("api_server")

app = FastAPI(title="march Python API", version="1.0.0")

# The web UI (served from the Vite dev server / a different origin) polls
# /accounts/status directly from the browser, so allow cross-origin reads.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared MT5 client — initialised once by start_server(). The lock serialises all
# MT5 access: trading multiple accounts means re-logging in the single terminal
# session, so concurrent /execute calls must not interleave.
_mt5: MT5Client | None = None
_lock = threading.Lock()


# ── Models ─────────────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    action: str                    # "long" | "short" | "flat" | "close"
    volume: float | None = None    # lots for entries (from the Zig strategy config)
    strategy: str | None = None    # which strategy fired → routes to accounts in march.db
    trade_id: int | None = None    # SQLite trade row ID to log timestamps
    closed_trade_id: int | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/execute")
def execute(req: ExecuteRequest):
    """Called by the Zig API when a strategy signal changes."""
    action = req.action.lower()
    volume = req.volume
    strategy = (req.strategy or "").strip()
    log.info(f"Execute command received: {action} (volume={volume}, strategy={strategy or '?'}, trade_id={req.trade_id}, closed_trade_id={req.closed_trade_id})")

    if _mt5 is None:
        raise HTTPException(status_code=503, detail="MT5 not connected")

    # Entries need a size, and it comes only from the strategy config (Zig).
    if action in ("long", "short") and (volume is None or volume <= 0):
        raise HTTPException(
            status_code=400,
            detail="missing/invalid volume for entry — set contracts/leverage in the strategy config",
        )

    if not strategy:
        raise HTTPException(
            status_code=400,
            detail="missing strategy — cannot route to an MT5 account",
        )

    # Which accounts run this strategy, and on which symbol each.
    targets = db.accounts_for_strategy(strategy)
    if not targets:
        log.warning(f"No MT5 account runs strategy '{strategy}' — nothing to execute")
        return {"ok": True, "executed": 0, "reason": "no accounts for strategy"}

    if config.dry_run:
        for t in targets:
            log.info(f"DRY RUN — would {action} {volume} {t.symbol} on account {t.login}")
        return {
            "ok": True, "dry_run": True, "action": action,
            "accounts": [t.login for t in targets],
        }

    executed = 0
    errors: list[str] = []
    mt5_time = None
    entry_price = None
    entry_spread = None
    close_price = None
    with _lock:
        for t in targets:
            try:
                if not _mt5.login(t.login, t.password, t.server):
                    errors.append(f"login failed for account {t.login}")
                    continue
                if not _mt5.ensure_symbol(t.symbol):
                    errors.append(f"symbol '{t.symbol}' unavailable on account {t.login}")
                    continue
                t_res = _execute_on_account(action, volume, t.symbol)
                if t_res:
                    if t_res["time"] and mt5_time is None:
                        mt5_time = t_res["time"]
                    if t_res["entry_price"] is not None and entry_price is None:
                        entry_price = t_res["entry_price"]
                    if t_res["entry_spread"] is not None and entry_spread is None:
                        entry_spread = t_res["entry_spread"]
                    if t_res["close_price"] is not None and close_price is None:
                        close_price = t_res["close_price"]
                executed += 1
            except Exception as e:
                log.error(f"Execute failed for account {t.login} ({t.symbol}): {e}")
                errors.append(f"account {t.login}: {e}")

    if mt5_time is not None:
        try:
            if req.trade_id is not None and req.trade_id != -1:
                db.update_open_times(req.trade_id, mt5_time, entry_price or 0.0, (entry_price or 0.0) + (entry_spread or 0.0))
            if req.closed_trade_id is not None and req.closed_trade_id != -1:
                db.update_close_times(req.closed_trade_id, mt5_time, close_price or 0.0)
        except Exception as e:
            log.error(f"Failed to update trade log in DB: {e}")

    return {"ok": len(errors) == 0, "action": action, "executed": executed, "errors": errors}


def _execute_on_account(action: str, volume: float | None, symbol: str) -> dict | None:
    """Run one action on the currently-logged-in account for `symbol`.

    Entries close any existing position on the symbol first before opening the new trade.
    """
    res = {
        "time": None,
        "entry_price": None,
        "entry_spread": None,
        "close_price": None,
    }

    if action in ("close", "flat"):
        positions = _mt5.get_positions(symbol)
        close_prices = []
        for pos in positions:
            p = _mt5.close_position(pos)
            if p is not None:
                close_prices.append(p)
        log.info(f"Closed {len(positions)} position(s) on {symbol} (account {_mt5.current_login})")
        res["time"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        if close_prices:
            res["close_price"] = close_prices[-1]
        return res

    elif action == "long":
        close_prices = []
        for pos in _mt5.get_positions(symbol):
            p = _mt5.close_position(pos)
            if p is not None:
                close_prices.append(p)

        trade_res = _mt5.open_trade("buy", symbol, volume)
        if trade_res is not None:
            price, spread = trade_res
            log.info(f"Opened LONG {volume} {symbol} (account {_mt5.current_login})")
            res["time"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            res["entry_price"] = price
            res["entry_spread"] = spread
            if close_prices:
                res["close_price"] = close_prices[-1]
            return res

    elif action == "short":
        close_prices = []
        for pos in _mt5.get_positions(symbol):
            p = _mt5.close_position(pos)
            if p is not None:
                close_prices.append(p)

        trade_res = _mt5.open_trade("sell", symbol, volume)
        if trade_res is not None:
            price, spread = trade_res
            log.info(f"Opened SHORT {volume} {symbol} (account {_mt5.current_login})")
            res["time"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            res["entry_price"] = price
            res["entry_spread"] = spread
            if close_prices:
                res["close_price"] = close_prices[-1]
            return res

    else:
        raise ValueError(f"Unknown action: {action}")
    return None


@app.get("/accounts/status")
def accounts_status():
    """Per-account MT5 connection health for the web UI status dots.

    Logs the shared terminal into each account in turn and reads account_info.
    Keyed by `account_id` (march.db mt5_accounts.id) so the frontend can match
    rows. Safe to poll: login() is a no-op when an account is already active.
    """
    accounts = db.all_accounts()
    if _mt5 is None:
        return [
            {"account_id": a.account_id, "login": a.login, "status": "offline",
             "detail": "MT5 terminal not connected"}
            for a in accounts
        ]

    out = []
    with _lock:
        for a in accounts:
            st = _mt5.account_status(a.login, a.password, a.server)
            out.append({"account_id": a.account_id, "login": a.login, **st})
    return out


@app.get("/positions")
def positions():
    if _mt5 is None:
        raise HTTPException(status_code=503, detail="MT5 not connected")

    out = []
    strat_map = db.get_position_strategies()
    open_trades = db.get_open_trades()
    with _lock:
        for acc in db.all_accounts():
            if not _mt5.login(acc.login, acc.password, acc.server):
                continue
            for p in _mt5.get_all_positions():
                strats = strat_map.get((acc.login, p.symbol.upper()), [])
                # Emit one row per strategy that has an open Zig trade for this
                # position. If none match, emit one row with MT5 fallback values.
                matched = []
                for strat in strats:
                    trade_info = open_trades.get(strat, {})
                    if trade_info:
                        matched.append((strat, trade_info))
                if not matched:
                    matched = [("", {})]

                for strat, trade_info in matched:
                    zig_price = trade_info.get("price", p.price_open)
                    zig_open_time = trade_info.get("open_time", "")

                    entry_ts = int(p.time)
                    if zig_open_time:
                        try:
                            clean_time = zig_open_time.split(".")[0]
                            dt = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S")
                            entry_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                        except Exception as e:
                            log.error(f"Error parsing trade open time: {e}")

                    out.append({
                        "account": acc.login,
                        "account_name": acc.name,
                        "ticket": p.ticket,
                        "type": "long" if p.type == 0 else "short",
                        "symbol": p.symbol,
                        "volume": p.volume,
                        "profit": p.profit,
                        "open_price": p.price_open,
                        "strategy": strat,
                        "zig_entry_price": zig_price,
                        "zig_entry_time": entry_ts,
                    })
    return out


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
