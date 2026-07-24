"""
Bookmap Trades + L2 DOM Collector Addon (High Performance)
===========================================================
Captures real-time trades and every market-by-price (L2/DOM) update from all
subscribed instruments (ES, NQ, etc.) and saves them to QuestDB via InfluxDB
Line Protocol.

Highly optimized for low latency and high throughput using background flushing.

Author: Auto-generated for quant/steal project
"""

import bookmap as bm
import json
import os
import queue
import re
import tempfile
import time
import traceback
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, Tuple
from collections import defaultdict
from questdb.ingress import Sender, TimestampNanos
from build_nq_l2_features_1s import Event as L2Event, FeatureAccumulator, FeatureRow

import asyncio
try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

def get_ny_timestamp_ns() -> int:
    try:
        # Preserve the repository's New York wall-clock-as-UTC convention while
        # retaining the nanoseconds from time.time_ns(). datetime.timestamp()
        # converts through a float and loses precision at current epoch values.
        utc_ns = time.time_ns()
        offset = datetime.now(ZoneInfo("America/New_York")).utcoffset()
        if offset is None:
            raise RuntimeError("New York UTC offset is unavailable")
        return utc_ns + int(offset.total_seconds()) * 1_000_000_000
    except Exception:
        # Windows Python installations may not include the IANA timezone
        # database. Apply the current US DST transition rules in UTC without
        # converting the nanosecond clock through a float.
        utc_now = datetime.now(timezone.utc)
        march_first = datetime(utc_now.year, 3, 1, tzinfo=timezone.utc)
        november_first = datetime(utc_now.year, 11, 1, tzinfo=timezone.utc)
        second_sunday_march = 8 + (6 - march_first.weekday()) % 7
        first_sunday_november = 1 + (6 - november_first.weekday()) % 7
        dst_start = datetime(
            utc_now.year, 3, second_sunday_march, 7, tzinfo=timezone.utc
        )
        dst_end = datetime(
            utc_now.year, 11, first_sunday_november, 6, tzinfo=timezone.utc
        )
        is_dst = dst_start <= utc_now < dst_end
        offset_hours = -4 if is_dst else -5
        return time.time_ns() + offset_hours * 3_600_000_000_000

# ============================================================================
# CONFIGURATION
# ============================================================================

# QuestDB connection string (ILP over HTTP)
QUESTDB_CONF = os.getenv("QUESTDB_CONF", "http::addr=localhost:9000;")
COLLECTOR_RUN_ID = uuid.uuid4().hex
SCHEMA_VERSION = 2
STORE_ORDER_IDS = os.getenv("BOOKMAP_STORE_ORDER_IDS", "0") == "1"
ROW_QUEUE_SIZE = max(1, int(os.getenv("BOOKMAP_ROW_QUEUE_SIZE", "250000")))
SPOOL_PATH = Path(
    os.getenv(
        "BOOKMAP_SPOOL_PATH",
        str(
            Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir()))
            / "quant"
            / "bookmap_spool.jsonl"
        ),
    )
)

# Global state
instruments: Dict[str, Dict[str, Any]] = {}
volume_delta: Dict[str, Dict[str, float]] = defaultdict(lambda: {"buy_vol": 0.0, "sell_vol": 0.0})
alias_to_symbol: Dict[str, str] = {}
event_counts: Dict[str, int] = defaultdict(int)
depth_sequence: Dict[str, int] = defaultdict(int)
trade_sequence: Dict[str, int] = defaultdict(int)
subscription_generation: Dict[str, int] = defaultdict(int)
stream_ids: Dict[str, str] = {}
bids_book: Dict[str, Dict[int, float]] = defaultdict(dict)
asks_book: Dict[str, Dict[int, float]] = defaultdict(dict)
best_bid_level: Dict[str, int] = {}
best_ask_level: Dict[str, int] = {}
book_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
feature_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
feature_accumulators: Dict[str, FeatureAccumulator] = {}

# Ingestion caches (speeds up on_trade callback by avoiding dict/string operations)
pips_cache: Dict[str, float] = {}
size_mult_cache: Dict[str, float] = {}
table_name_cache: Dict[str, str] = {}
depth_table_name_cache: Dict[str, str] = {}

# Ingress Sender & Thread Lock
sender: Optional[Sender] = None
pending_rows = 0
sender_lock = threading.Lock()
spool_lock = threading.Lock()
running = True
feature_running = True
Row = Tuple[str, Dict[str, str], Dict[str, Any], int]
row_queue: "queue.Queue[Row]" = queue.Queue(maxsize=ROW_QUEUE_SIZE)
health_counts: Dict[str, int] = defaultdict(int)

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

def spool_rows(rows: list[Row], reason: str) -> None:
    """Durably preserve rows that could not be queued or flushed.

    The spool is intentionally append-only. It is never automatically deleted
    or replayed, avoiding accidental duplication in QuestDB after an uncertain
    network acknowledgement.
    """
    if not rows:
        return
    try:
        with spool_lock:
            SPOOL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with SPOOL_PATH.open("a", encoding="utf-8") as spool:
                for table_name, symbols, columns, timestamp_ns in rows:
                    record = {
                        "table": table_name,
                        "symbols": symbols,
                        "columns": columns,
                        "timestamp_ns": timestamp_ns,
                        "reason": reason,
                        "collector_run_id": COLLECTOR_RUN_ID,
                    }
                    spool.write(json.dumps(record, separators=(",", ":")) + "\n")
                spool.flush()
        health_counts["rows_spooled"] += len(rows)
    except Exception as exc:
        health_counts["spool_errors"] += len(rows)
        print(
            f"[COLLECTOR] CRITICAL: unable to spool {len(rows)} failed rows: {exc}",
            flush=True,
        )

def spool_row(row: Row, reason: str) -> None:
    spool_rows([row], reason)

def send_row_safe(
    table_name: str, symbols: Dict[str, str], columns: Dict[str, Any], at_ns: int
) -> None:
    """Quickly hand an immutable row to the single QuestDB writer thread."""
    row = (table_name, dict(symbols), dict(columns), at_ns)
    try:
        row_queue.put_nowait(row)
        health_counts["rows_queued"] += 1
    except queue.Full:
        # Do not freeze Bookmap's event callback during a prolonged database
        # outage. The append-only local spool preserves the event for recovery.
        health_counts["queue_overflows"] += 1
        spool_row(row, "queue_full")


def queue_feature_row(alias: str, row: FeatureRow) -> None:
    """Persist one completed NQ second using the normalized feature schema."""
    symbol = alias_to_symbol.get(alias, "").lower()
    if symbol != "nq":
        return
    send_row_safe(
        "nq_l2_features_1s",
        symbols={"source": "bm", "symbol": symbol},
        columns=row.values,
        at_ns=row.timestamp_ns,
    )
    health_counts["feature_rows_queued"] += 1


def record_feature_event(alias: str, event: L2Event) -> None:
    if alias_to_symbol.get(alias) != "NQ":
        return
    with feature_locks[alias]:
        accumulator = feature_accumulators.get(alias)
        if accumulator is None:
            accumulator = FeatureAccumulator(0.25)
            feature_accumulators[alias] = accumulator
        for row in accumulator.on_event(event):
            queue_feature_row(alias, row)


def feature_flush_loop() -> None:
    """Flush elapsed seconds even when the next market event is delayed."""
    while feature_running:
        # A short grace period prevents a callback captured just before the
        # boundary from creating a second row after that bucket was flushed.
        completed_before = (
            get_ny_timestamp_ns() - 500_000_000
        ) // 1_000_000_000
        for alias in list(feature_accumulators):
            with feature_locks[alias]:
                accumulator = feature_accumulators.get(alias)
                row = (
                    accumulator.flush_completed(completed_before)
                    if accumulator is not None
                    else None
                )
                if row is not None:
                    queue_feature_row(alias, row)
        time.sleep(0.1)

def write_row(row: Row) -> bool:
    """Buffer one queued row in the QuestDB sender."""
    global pending_rows
    table_name, symbols, columns, at_ns = row
    with sender_lock:
        try:
            get_sender().row(
                table_name,
                symbols=symbols,
                columns=columns,
                at=TimestampNanos(at_ns),
            )
            pending_rows += 1
            health_counts["rows_buffered"] += 1
            return True
        except Exception as e:
            health_counts["row_errors"] += 1
            print(f"[COLLECTOR] ROW ERROR: {e}", flush=True)
            clear_sender()
            return False

def writer_loop() -> None:
    """Own QuestDB writes, periodic flushing, and collector health reporting."""
    global pending_rows, sender, running
    next_flush = time.monotonic() + 0.1
    next_health = time.monotonic() + 60.0
    unflushed_rows: list[Row] = []

    def flush_unflushed() -> None:
        global pending_rows
        if not unflushed_rows:
            return
        with sender_lock:
            if sender is None:
                spool_rows(unflushed_rows, "sender_unavailable")
                unflushed_rows.clear()
                pending_rows = 0
                return
            try:
                sender.flush()
                health_counts["flushes"] += 1
                health_counts["rows_persisted"] += len(unflushed_rows)
                unflushed_rows.clear()
                pending_rows = 0
            except Exception as e:
                health_counts["flush_errors"] += 1
                print(f"[COLLECTOR] Background Flush Error: {e}", flush=True)
                # A failed HTTP flush has an uncertain acknowledgement. Preserve
                # the complete batch for explicit recovery instead of assuming
                # that any buffered row reached QuestDB.
                spool_rows(unflushed_rows, "questdb_flush_failed")
                unflushed_rows.clear()
                clear_sender()

    while running or not row_queue.empty():
        row: Optional[Row] = None
        try:
            row = row_queue.get(timeout=0.05)
            if write_row(row):
                unflushed_rows.append(row)
            else:
                # Opening/resetting a sender can discard its prior buffer.
                # Preserve both the old batch and the rejected current row.
                spool_rows(unflushed_rows + [row], "questdb_write_failed")
                unflushed_rows.clear()
        except queue.Empty:
            pass
        finally:
            if row is not None:
                row_queue.task_done()

        now = time.monotonic()
        if now >= next_flush:
            flush_unflushed()
            next_flush = now + 0.1

        if now >= next_health:
            print(
                "[COLLECTOR] HEALTH | "
                f"queue={row_queue.qsize()}/{ROW_QUEUE_SIZE} "
                f"persisted={health_counts['rows_persisted']} "
                f"row_errors={health_counts['row_errors']} "
                f"spooled={health_counts['rows_spooled']} "
                f"flush_errors={health_counts['flush_errors']}",
                flush=True,
            )
            next_health = now + 60.0
    flush_unflushed()

def close_sender() -> None:
    """Flush and close the sender on shutdown."""
    global pending_rows
    with sender_lock:
        if sender is not None and pending_rows > 0:
            try:
                sender.flush()
                pending_rows = 0
            except Exception as exc:
                health_counts["flush_errors"] += 1
                print(f"[COLLECTOR] Final flush error: {exc}", flush=True)
        clear_sender()

# ============================================================================
# WEBSOCKET LIVE PUSH (sub-100ms path to the browser, bypasses QuestDB)
# ============================================================================
# The QuestDB path persists every tick but has flush + WAL-commit latency that
# makes sub-100ms read-after-write impossible. For the realtime chart we push
# each trade straight to connected browsers over a WebSocket the instant it
# arrives in on_trade. Browsers receive ticks in ~1-10ms; QuestDB stays the
# source of truth for historical backfill on page load.

WS_HOST = os.getenv("BOOKMAP_WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("BOOKMAP_WS_PORT", "8765"))
WS_QUEUE_SIZE = max(1, int(os.getenv("BOOKMAP_WS_QUEUE_SIZE", "50000")))
ws_loop: Optional[asyncio.AbstractEventLoop] = None
ws_queue: Optional["asyncio.Queue"] = None
ws_clients: set = set()

async def _ws_handler(websocket, *_) -> None:
    ws_clients.add(websocket)
    print(f"[WS] client connected ({len(ws_clients)} total)", flush=True)
    try:
        await websocket.wait_closed()
    finally:
        ws_clients.discard(websocket)
        print(f"[WS] client disconnected ({len(ws_clients)} total)", flush=True)

async def _ws_safe_send(client, payload: str) -> None:
    try:
        await client.send(payload)
    except Exception:
        ws_clients.discard(client)

async def _ws_consumer() -> None:
    """Drain the queue and fan out to clients. Coalesces bursts into one batch
    (immediate when idle, naturally batched under load). Same JSON shape as
    /api/march/ticks so the frontend handles both paths identically."""
    global ws_queue
    ws_queue = asyncio.Queue(maxsize=WS_QUEUE_SIZE)
    while True:
        first = await ws_queue.get()
        batch = [first]
        while not ws_queue.empty() and len(batch) < 2000:
            batch.append(ws_queue.get_nowait())
        if ws_clients:
            payload = "[" + ",".join(batch) + "]"
            await asyncio.gather(
                *[_ws_safe_send(c, payload) for c in list(ws_clients)],
                return_exceptions=True,
            )

async def _ws_main() -> None:
    async with websockets.serve(_ws_handler, WS_HOST, WS_PORT, ping_interval=20):
        print(f"[WS] live-push server listening on {WS_HOST}:{WS_PORT}", flush=True)
        await _ws_consumer()

def start_ws_server() -> None:
    """Run the WebSocket server in a daemon thread with its own event loop."""
    global ws_loop
    if not _WS_AVAILABLE:
        print("[WS] 'websockets' not installed — live push DISABLED. "
              "Run: pip install websockets  (chart falls back to QuestDB polling)", flush=True)
        return
    ws_loop = asyncio.new_event_loop()
    def _run():
        asyncio.set_event_loop(ws_loop)
        try:
            ws_loop.run_until_complete(_ws_main())
        except Exception as e:
            print(f"[WS] server crashed: {e}", flush=True)
    threading.Thread(target=_run, daemon=True, name="ws-server").start()

def ws_push(sym: str, t_ns: int, price: float, size: float, side: str, bid: float, ask: float) -> None:
    """Non-blocking hand-off of one trade to the WS loop. Safe to call from the
    Bookmap on_trade callback thread. No-op when nobody is watching."""
    if ws_loop is None or ws_queue is None or not ws_clients:
        return
    row = f'{{"sym":"{sym}","ts":{t_ns},"price":{price},"size":{size},"side":"{side}","best_bid":{bid},"best_ask":{ask}}}'
    try:
        def _enqueue() -> None:
            try:
                ws_queue.put_nowait(row)
            except asyncio.QueueFull:
                health_counts["ws_dropped"] += 1
        ws_loop.call_soon_threadsafe(_enqueue)
    except RuntimeError:
        pass

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
        depth_table_name_cache[alias] = f"bm_{symbol.lower()}_depth"

        # Every subscription starts a new, independently replayable stream.
        # Resetting the in-memory book prevents stale levels from contaminating
        # BBO values while Bookmap emits the new initial snapshot.
        subscription_generation[alias] += 1
        stream_ids[alias] = (
            f"{COLLECTOR_RUN_ID}:{alias}:{subscription_generation[alias]}"
        )
        volume_delta[alias] = {"buy_vol": 0.0, "sell_vol": 0.0}
        depth_sequence[alias] = 0
        trade_sequence[alias] = 0
        with book_locks[alias]:
            bids_book[alias].clear()
            asks_book[alias].clear()
            best_bid_level.pop(alias, None)
            best_ask_level.pop(alias, None)
        with feature_locks[alias]:
            if symbol == "NQ":
                feature_accumulators[alias] = FeatureAccumulator(pips)
            else:
                feature_accumulators.pop(alias, None)

        # Keep trades, and subscribe to every market-by-price L2/DOM update.
        # A depth subscription begins with Bookmap's current book snapshot,
        # followed by individual price-level changes. A size of zero removes
        # the corresponding bid/ask level from the reconstructed DOM.
        bm.subscribe_to_trades(addon, alias, 2)
        print(f"[COLLECTOR] >> Subscribed to TRADES for {alias}", flush=True)
        if supported_features.get("depth", False):
            bm.subscribe_to_depth(addon, alias, 1)
            print(f"[COLLECTOR] >> Subscribed to L2 DOM for {alias}", flush=True)
        else:
            print(
                f"[COLLECTOR] >> L2 DOM unavailable for {alias} "
                "(the connected Bookmap feed does not advertise depth)",
                flush=True,
            )

    except Exception as e:
        print(f"[COLLECTOR] ERROR in subscribe: {e}", flush=True)
        traceback.print_exc()


def handle_unsubscribe_instrument(addon: Any, alias: str) -> None:
    try:
        print(f"[COLLECTOR] Unsubscribing: {alias}", flush=True)
        print(f"[COLLECTOR] Total trade events received: {event_counts.get(f'{alias}_trades', 0)}", flush=True)
        print(f"[COLLECTOR] Total L2 DOM events received: {event_counts.get(f'{alias}_depth', 0)}", flush=True)

        with feature_locks[alias]:
            accumulator = feature_accumulators.pop(alias, None)
            if accumulator is not None:
                row = accumulator.flush_completed(
                    get_ny_timestamp_ns() // 1_000_000_000
                )
                if row is not None:
                    queue_feature_row(alias, row)
        
        # Cleanup caches
        instruments.pop(alias, None)
        volume_delta.pop(alias, None)
        pips_cache.pop(alias, None)
        size_mult_cache.pop(alias, None)
        table_name_cache.pop(alias, None)
        depth_table_name_cache.pop(alias, None)
        depth_sequence.pop(alias, None)
        trade_sequence.pop(alias, None)
        stream_ids.pop(alias, None)
        with book_locks[alias]:
            bids_book.pop(alias, None)
            asks_book.pop(alias, None)
            best_bid_level.pop(alias, None)
            best_ask_level.pop(alias, None)
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
        trade_sequence[alias] += 1
        sequence = trade_sequence[alias]

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
        table_name = table_name_cache[alias]
        
        t_ns = get_ny_timestamp_ns()

        # Resolve BBO for trade record
        with book_locks[alias]:
            bid_level = best_bid_level.get(alias)
            ask_level = best_ask_level.get(alias)
        pip = pips_cache.get(alias, 1.0)
        best_bid = bid_level * pip if bid_level is not None else price
        best_ask = ask_level * pip if ask_level is not None else price

        # Live path first: push to browsers immediately (lowest latency, ~1-10ms).
        ws_push(alias_to_symbol.get(alias, ""), t_ns, price, size, side, best_bid, best_ask)

        # Persistence path: buffered ILP write to QuestDB (historical backfill).
        columns: Dict[str, Any] = {
            "price": price,
            "size": size,
            "is_otc": is_otc,
            "is_exec_start": is_execution_start,
            "is_exec_end": is_execution_end,
            "buy_vol": v_delta["buy_vol"],
            "sell_vol": v_delta["sell_vol"],
            "delta": delta,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "trade_sequence": sequence,
            "schema_version": SCHEMA_VERSION,
        }
        # Order IDs are regular STRING columns, never SYMBOL columns, because
        # their cardinality can be extremely high. Disabled by default to keep
        # the existing storage profile unchanged.
        if STORE_ORDER_IDS:
            if aggressor_order_id is not None:
                columns["aggressor_order_id"] = aggressor_order_id
            if passive_order_id is not None:
                columns["passive_order_id"] = passive_order_id

        send_row_safe(
            table_name,
            symbols={
                "side": side,
                "alias": alias,
                "stream_id": stream_ids.get(alias, COLLECTOR_RUN_ID),
            },
            columns=columns,
            at_ns=t_ns,
        )
        record_feature_event(
            alias,
            L2Event(
                timestamp_ns=t_ns,
                priority=1,
                sequence=sequence,
                kind="T",
                side=side,
                price=price,
                size=size,
                best_bid=best_bid,
                best_ask=best_ask,
                stream_id=stream_ids.get(alias, COLLECTOR_RUN_ID),
            ),
        )

    except Exception as e:
        print(f"[COLLECTOR] ERROR in on_trade: {e}", flush=True)
        traceback.print_exc()


def on_depth(
    addon: Any, alias: str, is_bid: bool, price_level: int, size_level: int
) -> None:
    """Persist one aggregated market-by-price L2/DOM update.

    Bookmap sends every currently visible DOM level immediately after a depth
    subscription (the initial snapshot), then calls this handler for each
    change.  This is intentionally an append-only event log: replaying it in
    timestamp/sequence order recreates the DOM exactly as received.  A
    ``size`` of zero means the level was removed.
    """
    try:
        pips = pips_cache.get(alias, 1.0)
        size_multiplier = size_mult_cache.get(alias, 0.0)
        price = int(price_level) * pips
        size = size_level / size_multiplier if size_multiplier != 0.0 else float(size_level)
        side = "BID" if is_bid else "ASK"

        # Maintain the book using exact integer price levels. Cached BBO values
        # avoid scanning the complete book on every trade; a scan is needed only
        # when the current best level is removed.
        raw_level = int(price_level)
        with book_locks[alias]:
            book = bids_book[alias] if is_bid else asks_book[alias]
            if size == 0.0:
                book.pop(raw_level, None)
            else:
                book[raw_level] = size

            if is_bid:
                current_best = best_bid_level.get(alias)
                if size > 0.0 and (current_best is None or raw_level > current_best):
                    best_bid_level[alias] = raw_level
                elif size == 0.0 and current_best == raw_level:
                    if book:
                        best_bid_level[alias] = max(book)
                    else:
                        best_bid_level.pop(alias, None)
            else:
                current_best = best_ask_level.get(alias)
                if size > 0.0 and (current_best is None or raw_level < current_best):
                    best_ask_level[alias] = raw_level
                elif size == 0.0 and current_best == raw_level:
                    if book:
                        best_ask_level[alias] = min(book)
                    else:
                        best_ask_level.pop(alias, None)

        event_counts[f"{alias}_depth"] += 1
        count = event_counts[f"{alias}_depth"]
        depth_sequence[alias] += 1
        sequence = depth_sequence[alias]

        if count <= 5 or count % 10000 == 0:
            print(
                f"[COLLECTOR] L2 #{count} | {alias} | {side} | "
                f"price={price} size={size} seq={sequence}",
                flush=True,
            )

        t_ns = get_ny_timestamp_ns()
        send_row_safe(
            depth_table_name_cache[alias],
            symbols={
                "side": side,
                "alias": alias,
                "stream_id": stream_ids.get(alias, COLLECTOR_RUN_ID),
            },
            columns={
                # Normalized values are convenient for SQL research.
                "price": price,
                "size": size,
                # Preserve the exact Bookmap values for lossless replay.
                "price_level": int(price_level),
                "size_level": int(size_level),
                # Disambiguates receive order if two events share a timestamp.
                "sequence": sequence,
                "schema_version": SCHEMA_VERSION,
            },
            at_ns=t_ns,
        )
        record_feature_event(
            alias,
            L2Event(
                timestamp_ns=t_ns,
                priority=0,
                sequence=sequence,
                kind="D",
                side=side,
                price=price,
                size=size,
                stream_id=stream_ids.get(alias, COLLECTOR_RUN_ID),
            ),
        )

    except Exception as e:
        print(f"[COLLECTOR] ERROR in on_depth: {e}", flush=True)
        traceback.print_exc()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("[COLLECTOR] Bookmap Trades + L2 DOM Collector - Optimized (QuestDB)", flush=True)
    print(f"[COLLECTOR] QuestDB: {QUESTDB_CONF}", flush=True)
    print(f"[COLLECTOR] Run ID: {COLLECTOR_RUN_ID}", flush=True)
    print(f"[COLLECTOR] Failure spool: {SPOOL_PATH}", flush=True)
    print(f"[COLLECTOR] Store order IDs: {STORE_ORDER_IDS}", flush=True)
    print(f"[COLLECTOR] Mode: ASYNC REAL-TIME TRADES + L2 DOM", flush=True)
    print("=" * 60, flush=True)

    # Warm up when available, but still start the addon during a QuestDB outage:
    # failed rows will be retained in the append-only local spool.
    try:
        get_sender()
    except Exception as exc:
        print(
            f"[COLLECTOR] QuestDB unavailable at startup; rows will spool: {exc}",
            flush=True,
        )
        clear_sender()
    if SPOOL_PATH.exists() and SPOOL_PATH.stat().st_size > 0:
        print(
            f"[COLLECTOR] WARNING: recovery spool contains "
            f"{SPOOL_PATH.stat().st_size} bytes and is never auto-deleted",
            flush=True,
        )

    # Start the single background writer. Bookmap callbacks only enqueue rows,
    # keeping database latency away from the market-data callback path.
    running = True
    writer_thread = threading.Thread(
        target=writer_loop, daemon=True, name="questdb-writer"
    )
    writer_thread.start()
    feature_running = True
    feature_thread = threading.Thread(
        target=feature_flush_loop, daemon=True, name="l2-feature-flusher"
    )
    feature_thread.start()

    # Start the WebSocket live-push server (realtime path to the browser).
    start_ws_server()

    addon = bm.create_addon()

    # Register handlers before subscribing, otherwise Bookmap can emit data
    # before this addon is ready to receive it.
    bm.add_trades_handler(addon, on_trade)
    bm.add_depth_handler(addon, on_depth)

    print("[COLLECTOR] Handlers registered: TRADES, L2 DOM", flush=True)
    print("[COLLECTOR] Waiting for Bookmap to subscribe instruments...", flush=True)
    print("[COLLECTOR] Make sure addon is ENABLED on your ES/NQ charts!", flush=True)

    bm.start_addon(addon, handle_subscribe_instrument, handle_unsubscribe_instrument)
    bm.wait_until_addon_is_turned_off(addon)

    # Stop feature production before allowing the QuestDB writer to drain.
    feature_running = False
    feature_thread.join(timeout=2.0)

    # Stop accepting Bookmap events, drain queued persistence work, then flush.
    running = False
    writer_thread.join(timeout=30.0)
    if writer_thread.is_alive():
        print(
            f"[COLLECTOR] Writer did not drain within 30s; "
            f"{row_queue.qsize()} rows remain queued",
            flush=True,
        )
    close_sender()

    # Summary
    print("[COLLECTOR] === FINAL SUMMARY ===", flush=True)
    for key, cnt in event_counts.items():
        print(f"[COLLECTOR]   {key}: {cnt} events", flush=True)
    print(
        "[COLLECTOR]   persistence: "
        f"persisted={health_counts['rows_persisted']} "
        f"spooled={health_counts['rows_spooled']} "
        f"queue_overflows={health_counts['queue_overflows']} "
        f"spool_errors={health_counts['spool_errors']} "
        f"feature_rows={health_counts['feature_rows_queued']} "
        f"ws_dropped={health_counts['ws_dropped']}",
        flush=True,
    )

    print("[COLLECTOR] Done.", flush=True)
