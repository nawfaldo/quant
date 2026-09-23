"""Imports an MetaTrader 5 tick export (``<DATE> <TIME> <BID> <ASK> ...``) into
the Parquet store.

MT5 exports the terminal's server clock. The weekly session boundary in this
account's export sits at 23:00 in winter and 22:00 in summer while the New York
event behind it (the 18:00 Sunday open) does not move, which places the export
on UTC with no DST of its own. Every other table in this repository stores New
York wall-clock encoded as UTC, so the offset is applied here rather than left
for each reader to rediscover.

    py tools/mt5_tick_import.py Downloads/USTEC_....csv --table ustec_tick
"""


from __future__ import annotations

# --------------------------------------------------------------------------- #
# NOT PORTED TO THE PARQUET STORE -- see tools/NOT_PORTED.md
#
# QuestDB is retired. Every table was exported to `data/parquet/` and dropped,
# so the database this script connects to is empty and no reader looks at it.
# Left to run, it would report success and write rows nobody reads.
#
# Everything below this guard is the untouched original and is what a port
# should preserve: the vendor API handling, the clock conversions, the gap
# rules. `tools/parquet_writer.py` is the write path; `exness_import_1m.py` is
# the smallest worked example of the change.
# --------------------------------------------------------------------------- #
# Behind `__name__` so the module stays IMPORTABLE. Several of these hold pure
# helpers that working code depends on -- `sandbox/liquidity_lines.py` imports
# `FeatureAccumulator` and `FeatureRow` out of the L2 builder -- and a
# module-level refusal took five passing tests down with it.
if __name__ == "__main__":
    raise SystemExit(
        (__doc__ or "").splitlines()[0]
        + "\n\nThis importer still writes to QuestDB, which is retired and empty."
        + "\nSee tools/NOT_PORTED.md for what porting it involves."
    )

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from parquet_writer import Sender, TimestampNanos

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
EXPECTED_HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="MT5 tick export")
    parser.add_argument("--table", default="ustec_tick")
    parser.add_argument("--symbol", default="USTEC")
    parser.add_argument(
        "--conf",
        default="http::addr=localhost:9000;",
        help="QuestDB ingress configuration string",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="stop after N rows (0 = all)"
    )
    return parser.parse_args()


class OffsetCache:
    """UTC -> New York offset, memoised per UTC hour.

    A per-day cache would be wrong on the two DST changeover days. The
    transitions land exactly on a UTC hour boundary, so keying by hour is exact
    and still collapses ~44 million rows onto a few thousand lookups.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], timedelta] = {}

    def for_row(self, date: str, hour: str) -> timedelta:
        key = (date, hour)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        year, month, day = (int(part) for part in date.split("."))
        moment = datetime(year, month, day, int(hour), tzinfo=UTC)
        offset = moment.astimezone(NEW_YORK).utcoffset()
        if offset is None:
            raise RuntimeError(f"no New York offset for {date} {hour}")
        self._cache[key] = offset
        return offset


def main() -> int:
    args = parse_args()
    if not args.csv.is_file():
        print(f"not found: {args.csv}", file=sys.stderr)
        return 1

    offsets = OffsetCache()
    sent = 0
    skipped = 0
    first_ts: str | None = None
    last_ts: str | None = None

    with args.csv.open("r", encoding="latin-1", newline="") as handle:
        header = handle.readline()
        if not header.startswith(EXPECTED_HEADER):
            print(f"unexpected header: {header.strip()!r}", file=sys.stderr)
            return 1
        # `dedup=False`: a tick feed prints many quotes in the same
        # millisecond and each one is a distinct quote.
        with Sender(dedup=False) as sender:
            for line in handle:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) < 4:
                    skipped += 1
                    continue
                date, clock, bid_text, ask_text = fields[0], fields[1], fields[2], fields[3]
                # A flags=2 tick moves one side only and leaves the other blank.
                # Those rows are still real quotes, so they are kept with the
                # absent side null rather than dropped.
                if not bid_text and not ask_text:
                    skipped += 1
                    continue
                try:
                    columns: dict[str, float] = {}
                    if bid_text:
                        columns["bid"] = float(bid_text)
                    if ask_text:
                        columns["ask"] = float(ask_text)
                    hour, minute, rest = clock.split(":")
                    second, _, millis = rest.partition(".")
                    year, month, day = (int(part) for part in date.split("."))
                    moment = datetime(
                        year,
                        month,
                        day,
                        int(hour),
                        int(minute),
                        int(second),
                        int((millis or "0").ljust(3, "0")[:3]) * 1000,
                        tzinfo=UTC,
                    )
                except ValueError:
                    skipped += 1
                    continue
                # New York wall clock re-encoded as UTC, the repository's
                # convention, so this table lines up with the Bookmap tables.
                wall_clock = moment + offsets.for_row(date, hour)
                nanos = int((wall_clock - EPOCH).total_seconds() * 1_000) * 1_000_000
                sender.row(
                    args.table,
                    symbols={"symbol": args.symbol},
                    columns=columns,
                    at=TimestampNanos(nanos),
                )
                sent += 1
                if first_ts is None:
                    first_ts = wall_clock.strftime("%Y-%m-%d %H:%M:%S")
                last_ts = wall_clock.strftime("%Y-%m-%d %H:%M:%S")
                if sent % 1_000_000 == 0:
                    print(f"  {sent:,} rows ... {last_ts}", flush=True)
                if args.limit and sent >= args.limit:
                    break
            sender.flush()

    print(f"sent {sent:,} rows into {args.table} (skipped {skipped:,})")
    print(f"New York wall-clock range: {first_ts} .. {last_ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
