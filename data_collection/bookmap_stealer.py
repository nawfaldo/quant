"""
Bookmap Trades Collector Addon (High Performance)
================================
Captures real-time trades from all subscribed instruments (ES, NQ, etc.)
and saves to QuestDB via InfluxDB Line Protocol.

Highly optimized for low latency and high throughput using background flushing.

Author: Auto-generated for quant/steal project
"""

import bookmap as bm
import os
import re
import time
import traceback
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional
from collections import defaultdict
from questdb.ingress import Sender, TimestampNanos

def get_ny_timestamp_ns() -> int:
    try:
        # Get current time in New York
        ny_dt = datetime.now(ZoneInfo("America/New_York"))
        # Convert to a naive datetime representing the wall-clock time in New York
        naive_ny = ny_dt.replace(tzinfo=None)
        # Convert that naive datetime to a timestamp as if it were in UTC
        return int(naive_ny.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    except Exception:
        # Fallback to manual offset calculation if ZoneInfo is not available
        utc_now = datetime.now(timezone.utc)
        is_dst = 3 < utc_now.month < 11
        if utc_now.month == 3:
            dst_start = 8 + (6 - datetime(utc_now.year, 3, 1).weekday()) % 7
            is_dst = utc_now.day >= dst_start
        elif utc_now.month == 11:
            dst_end = 1 + (6 - datetime(utc_now.year, 11, 1).weekday()) % 7
            is_dst = utc_now.day < dst_end
            
        offset_hours = -4 if is_dst else -5
        ny_dt = utc_now + timedelta(hours=offset_hours)
        return int(ny_dt.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)

# ============================================================================
# CONFIGURATION
# ============================================================================

# QuestDB connection string (ILP over HTTP)
QUESTDB_CONF = "http::addr=localhost:9000;"

# Global state
instruments: Dict[str, Dict[str, Any]] = {}
volume_delta: Dict[str, Dict[str, float]] = defaultdict(lambda: {"buy_vol": 0.0, "sell_vol": 0.0})
alias_to_symbol: Dict[str, str] = {}
event_counts: Dict[str, int] = defaultdict(int)

# Ingestion caches (speeds up on_trade callback by avoiding dict/string operations)
pips_cache: Dict[str, float] = {}
size_mult_cache: Dict[str, float] = {}
table_name_cache: Dict[str, str] = {}

# Ingress Sender & Thread Lock
sender: Optional[Sender] = None
pending_rows = 0
sender_lock = threading.Lock()
running = True

# ============================================================================
# QUESTDB SENDER METHODS
# ============================================================================

def get_sender() -> Sender:
    """Get (or create) the QuestDB ILP sender."""
    global sender, pending_rows
    if sender is None:
        print(f"[COLLECTOR] Opening QuestDB connection: {QUESTDB_CONF}", flush=True)
        s = Sender.from_conf(QUESTDB_CONF)
        s.__enter__()
        sender = s
        pending_rows = 0
        print("[COLLECTOR] QuestDB sender ready.", flush=True)
    return sender

def clear_sender() -> None:
    """Safely close and reset the sender."""
    global sender, pending_rows
    if sender is not None:
        try:
            sender.close()
        except Exception:
            pass
        sender = None
        pending_rows = 0

def send_row_safe(table_name: str, symbols: dict, columns: dict, at: TimestampNanos) -> None:
    """Thread-safe row insertion into QuestDB."""
    global pending_rows
    with sender_lock:
        for attempt in range(2):
            try:
                get_sender().row(table_name, symbols=symbols, columns=columns, at=at)
                pending_rows += 1
                return
            except Exception as e:
                print(f"[COLLECTOR] ROW ERROR (attempt {attempt+1}): {e}", flush=True)
                clear_sender()

def flush_loop() -> None:
    """Background thread to flush pending ticks to QuestDB periodically (low latency, high throughput)."""
    global pending_rows, sender, running
    while running:
        time.sleep(0.1)  # Flush buffer every 100ms
        if pending_rows > 0:
            with sender_lock:
                if sender is not None:
                    try:
                        sender.flush()
                        pending_rows = 0
                    except Exception as e:
                        print(f"[COLLECTOR] Background Flush Error: {e}", flush=True)
                        clear_sender()

def close_sender() -> None:
    """Flush and close the sender on shutdown."""
    clear_sender()

# ============================================================================
# HELPERS
# ============================================================================

def normalize_symbol(alias: str) -> str:
    """
    Convert Bookmap instrument alias to a clean root symbol.
    Examples:
        ESM6.CME@RITHMIC  -> ES
        NQK7.CME@DXFEED   -> NQ
    """
    base = alias.split(".")[0].split("@")[0]
    match = re.match(r'^([A-Z]{1,4})[FGHJKMNQUVXZ]\d+$', base, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return base.upper()

# ============================================================================
# BOOKMAP HANDLERS
# ============================================================================

def handle_subscribe_instrument(
    addon: Any, alias: str, full_name: str, is_crypto: bool,
    pips: float, size_multiplier: float, instrument_multiplier: float,
    supported_features: Dict[str, object]
) -> None:
    try:
        print(f"[COLLECTOR] ======== SUBSCRIBE ========", flush=True)
        print(f"[COLLECTOR] Instrument: {alias}", flush=True)
        print(f"[COLLECTOR] Full name:  {full_name}", flush=True)
        print(f"[COLLECTOR] Pips:       {pips}", flush=True)
        print(f"[COLLECTOR] Size mult:  {size_multiplier}", flush=True)
        print(f"[COLLECTOR] Inst mult:  {instrument_multiplier}", flush=True)
        print(f"[COLLECTOR] ================================", flush=True)

        symbol = normalize_symbol(alias)
        alias_to_symbol[alias] = symbol
        print(f"[COLLECTOR] Normalized: {alias} -> {symbol}", flush=True)

        instruments[alias] = {
            "pips": pips,
            "size_multiplier": size_multiplier,
            "instrument_multiplier": instrument_multiplier,
            "full_name": full_name,
            "is_crypto": is_crypto,
            "symbol": symbol,
        }

        # Cache variables for high-speed retrieval in callbacks
        pips_cache[alias] = pips
        size_mult_cache[alias] = size_multiplier
        table_name_cache[alias] = f"bm_{symbol.lower()}_ticks"

        # Reset volume state
        volume_delta[alias] = {"buy_vol": 0.0, "sell_vol": 0.0}

        # Subscribe only to trades
        bm.subscribe_to_trades(addon, alias, 2)
        print(f"[COLLECTOR] >> Subscribed to TRADES for {alias}", flush=True)

    except Exception as e:
        print(f"[COLLECTOR] ERROR in subscribe: {e}", flush=True)
        traceback.print_exc()


def handle_unsubscribe_instrument(addon: Any, alias: str) -> None:
    try:
        print(f"[COLLECTOR] Unsubscribing: {alias}", flush=True)
        print(f"[COLLECTOR] Total trade events received: {event_counts.get(f'{alias}_trades', 0)}", flush=True)
        
        # Flush pending buffers
        with sender_lock:
            if sender is not None:
                try:
                    sender.flush()
                except Exception:
                    pass

        # Cleanup caches
        instruments.pop(alias, None)
        volume_delta.pop(alias, None)
        pips_cache.pop(alias, None)
        size_mult_cache.pop(alias, None)
        table_name_cache.pop(alias, None)
    except Exception as e:
        print(f"[COLLECTOR] ERROR in unsubscribe: {e}", flush=True)


def on_trade(
    addon: Any, alias: str, price_level: float, size_level: int,
    is_otc: bool, is_bid: bool,
    is_execution_start: bool, is_execution_end: bool,
    aggressor_order_id: Optional[str], passive_order_id: Optional[str]
) -> None:
    try:
        # High speed cached pip and size lookup (avoids dict lookups and helper calls)
        price = int(price_level) * pips_cache.get(alias, 1.0)
        sm = size_mult_cache.get(alias, 0.0)
        size = size_level / sm if sm != 0.0 else float(size_level)
        side = "BUY" if is_bid else "SELL"

        # Count events for debugging
        event_counts[f"{alias}_trades"] += 1
        count = event_counts[f"{alias}_trades"]

        if count <= 5 or count % 10000 == 0:
            print(f"[COLLECTOR] TRADE #{count} | {alias} | {side} | price={price} size={size}", flush=True)

        # Volume delta
        v_delta = volume_delta[alias]
        if is_bid:
            v_delta["buy_vol"] += size
        else:
            v_delta["sell_vol"] += size
        delta = v_delta["buy_vol"] - v_delta["sell_vol"]

        # Cache lookup for table name
        table_name = table_name_cache.get(alias)
        
        # Send trade row (TCP/HTTP ILP buffer write, extremely fast)
        t_ns = get_ny_timestamp_ns()
        send_row_safe(
            table_name,
            symbols={"side": side},
            columns={
                "price":         price,
                "size":          size,
                "is_otc":        is_otc,
                "is_exec_start": is_execution_start,
                "is_exec_end":   is_execution_end,
                "buy_vol":       v_delta["buy_vol"],
                "sell_vol":      v_delta["sell_vol"],
                "delta":         delta,
            },
            at=TimestampNanos(t_ns),
        )

    except Exception as e:
        print(f"[COLLECTOR] ERROR in on_trade: {e}", flush=True)
        traceback.print_exc()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("[COLLECTOR] Bookmap Trades Collector - Optimized (QuestDB)", flush=True)
    print(f"[COLLECTOR] QuestDB: {QUESTDB_CONF}", flush=True)
    print(f"[COLLECTOR] Mode: ASYNC REAL-TIME TRADES", flush=True)
    print("=" * 60, flush=True)

    # Warm up the sender connection
    get_sender()

    # Start background flush loop thread
    running = True
    flush_thread = threading.Thread(target=flush_loop, daemon=True)
    flush_thread.start()

    addon = bm.create_addon()

    # Register trades handler only
    bm.add_trades_handler(addon, on_trade)

    print("[COLLECTOR] Handlers registered: TRADES", flush=True)
    print("[COLLECTOR] Waiting for Bookmap to subscribe instruments...", flush=True)
    print("[COLLECTOR] Make sure addon is ENABLED on your ES/NQ charts!", flush=True)

    bm.start_addon(addon, handle_subscribe_instrument, handle_unsubscribe_instrument)
    bm.wait_until_addon_is_turned_off(addon)

    # Shutdown background thread and close sender
    running = False
    flush_thread.join(timeout=1.0)
    close_sender()

    # Summary
    print("[COLLECTOR] === FINAL SUMMARY ===", flush=True)
    for key, cnt in event_counts.items():
        print(f"[COLLECTOR]   {key}: {cnt} events", flush=True)

    print("[COLLECTOR] Done.", flush=True)
