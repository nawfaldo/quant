"""Find and fill every missing 1-minute bar in a stored table from Binance.

For a collector that only runs part of the day. `binance_stream_1m.py` heals the
gap in front of it when it starts, but only back as far as ``--max-backfill-days``
and only the single gap at the tail. This walks the *whole* table, finds every
hole, and fills each one — the overnight stretch, weekends the machine was off,
and any minute a disconnect dropped.

It is safe to run at any time, including while the streamer is running: both
write the same nine columns at the same timestamps, and a minute that already
exists is simply not requested.

    # everything missing since the table's first row
    python tools/binance_fill_gaps.py

    # just the last few days, which is the normal daily use
    python tools/binance_fill_gaps.py --days 3

    # see what is missing without writing anything
    python tools/binance_fill_gaps.py --days 30 --dry-run

TIMESTAMPS. Identical convention to the streamer and to
``binance_fetch&import_1m.py``: Binance emits real UTC, this repository stores
exchange-local wall-clock labelled as UTC, and ``btc_1m`` specifically holds New
York wall-clock. All three scripts share ``stored_timestamp_ms``.

WHAT COUNTS AS A GAP. Only whole missing minutes strictly inside the table's
existing span, plus the stretch between its last row and the last *closed*
minute. A minute Binance itself never printed — a genuine exchange outage — is
requested once, comes back empty, and is reported rather than retried forever.

NETWORK. Binance is blocked on this connection unless Warp is on; the preflight
says so instead of hanging.
"""

from __future__ import annotations


import argparse
import json
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from parquet_writer import Sender, TimestampNanos, read_columns


UTC = timezone.utc
MINUTE_MS = 60_000
MINUTE_US = 60_000_000
REST_URL = "https://data-api.binance.vision/api/v3/klines"
REST_LIMIT = 1000

COLUMN_ORDER = (
    "open", "high", "low", "close", "volume",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
)


def log(message: str) -> None:
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def stored_timestamp_ms(actual_ms: int, wall_clock: ZoneInfo) -> int:
    """Real UTC instant -> wall-clock fields reinterpreted as UTC."""
    local = datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp() * 1000)


def actual_timestamp_ms(stored_ms: int, wall_clock: ZoneInfo) -> int:
    """The inverse: stored fake-UTC back to the real instant Binance knows."""
    naive = datetime.fromtimestamp(stored_ms / 1000, tz=UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=wall_clock).timestamp() * 1000)


def preflight(symbol: str) -> None:
    try:
        params = urlencode({"symbol": symbol, "interval": "1m", "limit": 1})
        with urlopen(f"{REST_URL}?{params}", timeout=15) as response:
            json.load(response)
    except Exception as error:  # noqa: BLE001
        raise SystemExit(
            f"cannot reach Binance ({error}).\n"
            "This connection blocks exchange endpoints unless Warp is on — turn "
            "it on and retry before debugging this script."
        ) from error


def existing_minutes(table: str, since_stored_ms: int | None) -> tuple[set[int], int | None, int | None]:
    """`(stored minutes present, first, last)` for the scanned window."""
    start = None if since_stored_ms is None else since_stored_ms * 1_000_000
    _, rows = read_columns(table, [], start, None)
    present = {row[0] // 1_000_000 for row in rows}
    if not present:
        return present, None, None
    return present, min(present), max(present)


def find_gaps(present: set[int], first_ms: int, last_ms: int) -> list[tuple[int, int]]:
    """Contiguous `[start, end)` runs of stored minutes missing in `[first, last]`."""
    gaps = []
    run_start = None
    minute = first_ms
    while minute <= last_ms:
        if minute in present:
            if run_start is not None:
                gaps.append((run_start, minute))
                run_start = None
        elif run_start is None:
            run_start = minute
        minute += MINUTE_MS
    if run_start is not None:
        gaps.append((run_start, last_ms + MINUTE_MS))
    return gaps


def fetch(symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, dict]]:
    """Closed klines in the real-UTC range `[start_ms, end_ms)`, oldest first."""
    out = []
    cursor = start_ms
    while cursor < end_ms:
        params = urlencode({
            "symbol": symbol, "interval": "1m",
            "startTime": cursor, "endTime": end_ms - 1, "limit": REST_LIMIT,
        })
        with urlopen(f"{REST_URL}?{params}", timeout=60) as response:
            batch = json.load(response)
        if not batch:
            break
        for values in batch:
            open_ms = int(values[0])
            if open_ms + MINUTE_MS > end_ms:
                continue
            out.append((open_ms, {
                "open": float(values[1]), "high": float(values[2]),
                "low": float(values[3]), "close": float(values[4]),
                "volume": float(values[5]), "quote_volume": float(values[7]),
                "trades": int(values[8]), "taker_buy_base": float(values[9]),
                "taker_buy_quote": float(values[10]),
            }))
        cursor = int(batch[-1][0]) + MINUTE_MS
        if len(batch) < REST_LIMIT:
            break
        time.sleep(0.1)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--table", default="btc_1m")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument(
        "--days", type=int, default=0,
        help="only scan the last N days (0 = the whole table)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="report the gaps and write nothing")
    args = parser.parse_args()

    wall_clock = ZoneInfo(args.timezone)
    preflight(args.symbol)

    now_actual = int(time.time() * 1000) // MINUTE_MS * MINUTE_MS
    since = None
    if args.days > 0:
        since = stored_timestamp_ms(now_actual - args.days * 86_400_000, wall_clock)

    log(f"scanning {args.table} for missing minutes...")
    present, first, last = existing_minutes(args.table, since)
    if first is None:
        raise SystemExit(f"{args.table} has no rows in the scanned window; "
                         "use binance_fetch&import_1m.py to seed it first")

    # The tail: everything between the newest row and the last closed minute.
    last_closed_stored = stored_timestamp_ms(now_actual - MINUTE_MS, wall_clock)
    horizon = max(last, last_closed_stored)
    gaps = find_gaps(present, first, horizon)

    span = (horizon - first) // MINUTE_MS + 1
    missing = sum((end - start) // MINUTE_MS for start, end in gaps)
    log(f"{len(present):,} of {span:,} minutes present; "
        f"{missing:,} missing across {len(gaps)} gap(s)")
    if not gaps:
        log("nothing to fill")
        return

    for start, end in gaps[:20]:
        a = datetime.fromtimestamp(start / 1000, tz=UTC)
        b = datetime.fromtimestamp((end - MINUTE_MS) / 1000, tz=UTC)
        log(f"  gap {(end - start) // MINUTE_MS:>6} min  "
            f"{a:%Y-%m-%d %H:%M} .. {b:%Y-%m-%d %H:%M} (stored)")
    if len(gaps) > 20:
        log(f"  ... and {len(gaps) - 20} more")
    if args.dry_run:
        log("dry run; nothing written")
        return

    sender = Sender()
    sender.establish()
    written = unavailable = 0
    try:
        for start, end in gaps:
            rows = fetch(args.symbol,
                         actual_timestamp_ms(start, wall_clock),
                         actual_timestamp_ms(end, wall_clock))
            got = {stored_timestamp_ms(open_ms, wall_clock) for open_ms, _ in rows}
            wanted = {start + i * MINUTE_MS for i in range((end - start) // MINUTE_MS)}
            unavailable += len(wanted - got)
            for open_ms, columns in rows:
                stored_ms = stored_timestamp_ms(open_ms, wall_clock)
                if stored_ms in present:
                    continue
                sender.row(
                    args.table,
                    columns={name: columns[name] for name in COLUMN_ORDER},
                    at=TimestampNanos(stored_ms * 1_000_000),
                )
                written += 1
            sender.flush()
    finally:
        sender.close()

    log(f"wrote {written:,} row(s)")
    if unavailable:
        log(f"{unavailable:,} minute(s) were never printed by Binance "
            "(exchange downtime); they stay missing and will be reported again")


if __name__ == "__main__":
    main()
