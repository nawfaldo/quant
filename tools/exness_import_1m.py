#!/usr/bin/env python3
"""Import Exness MT5 one-minute bars into the store as `exness_<symbol>_1m`.

WHY THIS EXISTS. Every canon sleeve signals off a vendor table (`gbpjpy_1m`
from Dukascopy, `ethusd_1m` from Binance, `nq_1m` from Databento) but the book
is executed at Exness. Those two price series are not the same series: a
different venue, a different aggregation, a different set of gaps. This
importer lands the broker's OWN bars beside the vendor's so a sleeve can be
signalled on one and filled on the other, and the difference measured rather
than assumed.

TIMEZONE. Bar tables in this repository hold NEW YORK WALL CLOCK relabelled as
UTC (see AGENT.md). The MetaTrader5 Python API returns a genuine UTC epoch --
verified against `gbpjpy_1m` by scanning the hourly offset that minimises the
mean absolute close difference, which bottoms sharply at +4h in August, exactly
New York's DST offset (0.005 there against 0.10 at either neighbour). So the
stored timestamp is the UTC instant read in New York and then relabelled UTC,
which is what `actual_to_stored_ns` does and what `exness_stream_ohlcv.py`
already writes.

MAX BARS, AND WHY THIS WALKS IN MONTHS. MetaTrader caps every history call at
the terminal's "Max bars in chart" setting. At the default 100,000 a YEAR of M1
is over the cap, and an over-cap request does not come back truncated -- it
returns "Invalid params" and NOTHING. Asked for a year at that setting, US500
returned 0 rows on 2026-09-20 while the same symbol served 29,974 bars for a
month-sized window.

That is why the request is a MONTH and never a year. A month of M1 is about
30,000 bars, which is inside the default cap, so this importer runs against a
terminal nobody has reconfigured -- and, on a machine where that terminal is
carrying live orders, without a restart to change a setting.

Raising `[Charts] MaxBars` to 2147483647 still helps: it is one request per
month either way, but the terminal caches more and the settle loop converges
sooner. It is no longer a prerequisite.

This importer reads market data only. It never sends, changes, or closes an
MT5 order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as _mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)

from parquet_writer import VENDOR_DIR, Sender, TimestampNanos, compact, table_span


UTC = timezone.utc
DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

#: The eleven instruments the canon book trades, as Exness names them. The keys
#: are the repository's symbols, so `nq` -> `USTEC` matches `BROKER_ALIAS` in
#: `sandbox/research/exness_families.py` and the table lands as
#: `exness_ustec_1m`.
CANON = {
    # RENAMED FROM `nq` 2026-09-03. The table this lands is `exness_ustec_1m`
    # either way -- it is named for the BROKER symbol -- but keying it `nq` was
    # wrong twice over: `nq` is now barred as a repository symbol
    # ([[nq-is-barred-ustec-is-the-replacement]]), and `nq_1m` is the
    # back-adjusted futures continuum, which is NOT the series this table holds.
    "ustec": "USTEC",
    "usdjpy": "USDJPY",
    "audusd": "AUDUSD",
    "jp225": "JP225",
    "ethusd": "ETHUSD",
    "gbpjpy": "GBPJPY",
    "gbpusd": "GBPUSD",
    "ukoil": "UKOIL",
    "eurjpy": "EURJPY",
    "xalusd": "XALUSD",
    "uk100": "UK100",
    # ADDED 2026-08-31, and the reason is worth recording: this map had drifted
    # from the book. `btc` and `xniusd` are canon sleeves that were missing
    # here, so a default run imported ten of the twelve symbols the book needs
    # and said nothing about the two it skipped. Checked against
    # `exness_families.broker_symbol`, which is the authority.
    "btc": "BTCUSD",
    "xniusd": "XNIUSD",
    # The rest of the research pool, added 2026-08-31 so a candidate can be
    # screened on live fills rather than skipped for want of a table. `es` and
    # `de40` are the two whose broker name does NOT follow the repository name
    # -- US500 and DE30 -- which is exactly why this map is resolved through
    # `exness_families.broker_symbol` and never guessed.
    "es": "US500",
    "de40": "DE30",
    "hk50": "HK50",
    "xauaud": "XAUAUD",
    "xaueur": "XAUEUR",
    "xaugbp": "XAUGBP",
    # ADDED 2026-09-03. `usoil` was the ONLY symbol in the research pool with no
    # broker table, so its sleeves were the only ones a live-execution study
    # could not correct: they kept the constant spread and the vendor bar open
    # while every other sleeve paid a measured per-minute spread and a late
    # fill. Two of them were candidates for canon at the time.
    "usoil": "USOIL",
    # ADDED 2026-09-22. ETHBTC is the one symbol in the widened search with no
    # broker table at all, so it was the only candidate a live-execution study
    # could not price. Its contract size is 100, not 1
    # ([[ethbtc-live-size-is-100x-the-contract-size]]).
    "ethbtc": "ETHBTC",
}

#: A year chunk is requested until two consecutive calls agree, because the
#: first call only ASKS the server for that history and returns whatever is
#: cached at the moment it is asked. Without the settle loop a deep year
#: silently imports a fraction of itself and looks like a thin market.
SETTLE_ATTEMPTS = 12
SETTLE_SECONDS = 4.0

#: How many empty years in a row end the walk backwards. One is not enough: a
#: CFD can be quoted, dropped for a year, and quoted again.
EMPTY_YEAR_LIMIT = 2

#: How far back the walk goes by default. Exness serves FX M1 back to 1999 and
#: the walk will happily take all of it, but the canon comparison only ever
#: reads the study window, so the deep years are download time spent on rows
#: nothing queries. Pass `--first-year` to go further back.
EARLIEST_YEAR = 2020


def log(message):
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def safe_table(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table name: {value!r}")
    return value


def actual_to_stored_ns(actual_seconds, wall_clock):
    """Real UTC instant -> New York wall clock reinterpreted as UTC."""
    local = datetime.fromtimestamp(actual_seconds, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def ensure_table(table):
    """Nothing to create. A shard appears when the first row is written.

    KEPT AS A NO-OP because what it used to declare still holds: a re-run over
    the same year must not double every bar. QuestDB needed an explicit
    `DEDUP UPSERT KEYS(timestamp)` for that, since ILP has no upsert without
    one; the Parquet writer replaces a row at a timestamp it already holds, so
    the guarantee is now a property of the writer rather than of the schema.
    """


def month_windows(start, stop):
    """`[start, stop)` cut into calendar months, oldest first.

    THE UNIT THE TERMINAL WILL ACTUALLY SERVE. See the MAX BARS note: a year of
    M1 is over the default cap and comes back as nothing at all, so the window
    has to be small enough to answer. A calendar month rather than a fixed 28
    days so the chunk boundaries are stable across re-runs and a re-import asks
    for the same windows it asked for last time.
    """
    edge = datetime(start.year, start.month, 1, tzinfo=UTC)
    while edge < stop:
        following = (datetime(edge.year + 1, 1, 1, tzinfo=UTC)
                     if edge.month == 12
                     else datetime(edge.year, edge.month + 1, 1, tzinfo=UTC))
        lo, hi = max(edge, start), min(following, stop)
        if lo < hi:
            yield lo, hi
        edge = following


def settled_rates(symbol, start, stop):
    """M1 bars in `[start, stop)`, waited on until the terminal stops growing them.

    THE RANGE IS RE-CHECKED IN PYTHON BECAUSE MT5 DOES NOT HONOUR IT. Asked for
    a year the server has no history for, `copy_rates_range` does not return an
    empty array -- it returns ONE bar, the same stale bar every time, dated
    outside the window entirely (asking for 2016-06-01..2016-06-08 returned a
    single 2026-05-11 bar). Left alone that is two bugs: the walk backwards
    never sees an empty year so it grinds through to 1999, and a fabricated row
    is written over a real one, since DEDUP upserts on the timestamp. So every
    row is filtered against the window that was actually asked for.
    """
    lo, hi = int(start.timestamp()), int(stop.timestamp())
    previous = -1
    rows = []
    for _ in range(SETTLE_ATTEMPTS):
        fetched = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, stop)
        # `None` IS AN ERROR AND WAS READ AS AN EMPTY MARKET. A refused call and
        # a year the broker never quoted both used to arrive here as zero rows,
        # so an over-cap request tripped `EMPTY_YEAR_LIMIT` and the run reported
        # "no bars" and exited 0 over a table it had not touched. Invalid params
        # is the cap (see MAX BARS) and it is raised rather than absorbed;
        # anything else is logged and left to the settle loop.
        if fetched is None:
            code, text = mt5.last_error()
            if code == -2:
                raise RuntimeError(
                    f"{symbol}: the terminal refused {start:%Y-%m-%d}.."
                    f"{stop:%Y-%m-%d} as too large ({text}). That window is "
                    f"over `[Charts] MaxBars`; this importer asks a month at a "
                    f"time, so a refusal here means the cap is set below one "
                    f"month of M1.")
            log(f"  {symbol} {start:%Y-%m}: {text} -- retrying")
            time.sleep(SETTLE_SECONDS)
            continue
        rows = [row for row in fetched if lo <= int(row["time"]) < hi]
        if len(rows) == previous:
            return rows
        previous = len(rows)
        time.sleep(SETTLE_SECONDS)
    return rows


class Writer:
    """One reconnecting QuestDB ILP connection, shared by every symbol."""

    def __init__(self, wall_clock):
        self.wall_clock = wall_clock
        self.sender = None
        self.written = 0

    def connect(self):
        if self.sender is None:
            self.sender = Sender()
            self.sender.establish()
        return self.sender

    def write(self, table, rows, cutoff_seconds):
        sender = self.connect()
        written = 0
        try:
            for row in rows:
                seconds = int(row["time"])
                if seconds >= cutoff_seconds:
                    continue
                sender.row(
                    table,
                    symbols=None,
                    columns={
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["tick_volume"]),
                        "spread": int(row["spread"]),
                        "real_volume": int(row["real_volume"]),
                    },
                    at=TimestampNanos(actual_to_stored_ns(seconds, self.wall_clock)),
                )
                written += 1
            sender.flush()
        except Exception:
            self.close()
            raise
        self.written += written
        return written

    def close(self):
        if self.sender is None:
            return
        try:
            self.sender.flush()
            self.sender.close()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
        self.sender = None


def settled_span(table):
    """`(min, max, count)` for `table`.

    NO LONGER POLLED, and the reason it was is worth keeping. A QuestDB
    `count()` taken the instant after a flush UNDERSTATED the table -- GBPJPY
    reported 2,407,400 rows against 2,480,404 written, and the two agreed
    exactly a minute later ([[questdb-update-read-lag]]) -- so this re-read
    until the count stopped moving. A Parquet shard is renamed into place
    complete, so the first read is already the settled one.
    """
    return table_span(table) or (None, None, 0)


def import_symbol(key, broker, writer, cutoff, first_year, table_prefix):
    table = safe_table(f"{table_prefix}{broker.lower()}_1m")
    if not mt5.symbol_select(broker, True):
        raise RuntimeError(f"cannot select {broker}: {mt5.last_error()}")
    ensure_table(table)

    cutoff_seconds = int(cutoff.timestamp())
    total, empty_run = 0, 0
    for year in range(cutoff.year, first_year - 1, -1):
        start = datetime(year, 1, 1, tzinfo=UTC)
        stop = min(datetime(year + 1, 1, 1, tzinfo=UTC), cutoff)
        if stop <= start:
            continue
        # ONE MONTH PER REQUEST, WRITTEN AS IT ARRIVES. The year is still the
        # unit the walk backwards counts in -- a year with no month in it is
        # what ends it -- but nothing asks the terminal for a year any more.
        # Writing per month also means an interrupted deep import keeps what it
        # already fetched instead of losing the year it was in the middle of.
        written = 0
        for edge, following in month_windows(start, stop):
            rows = settled_rates(broker, edge, following)
            if rows:
                written += writer.write(table, rows, cutoff_seconds)
        if written == 0:
            empty_run += 1
            log(f"  {broker} {year}: no bars")
            if empty_run >= EMPTY_YEAR_LIMIT:
                break
            continue
        empty_run = 0
        total += written
        log(f"  {broker} {year}: {written:,} bars -> {table} (running {total:,})")

    # COMPACTED BEFORE THE SPAN IS READ, so the reported row count is the one a
    # reader will actually see. The writer lands a shard per New York day, which
    # is right for a collector that must survive a crash and wrong for a bulk
    # import: 2025-2026 is 513 shards a symbol, and every consumer then opens
    # 513 files to answer one question. `flush` first -- the shards have to be
    # on disk before they can be folded.
    writer.close()
    rows = compact(table)
    if rows:
        log(f"  {table}: compacted to one file, {rows:,} rows")

    span = settled_span(table)
    return {"symbol": key, "broker": broker, "table": table,
            "written": total, "first_row": str(span[0])[:19],
            "last_row": str(span[1])[:19], "rows": span[2]}


def parse_args():
    two_days_ago = date.today() - timedelta(days=2)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", default=sorted(CANON),
                        help="repository symbols; default is the canon eleven")
    parser.add_argument("--to-date", default=two_days_ago.isoformat(),
                        help="last INCLUSIVE New York date to import "
                             "(default: two days ago)")
    parser.add_argument("--first-year", type=int, default=EARLIEST_YEAR)
    parser.add_argument("--terminal", type=Path, default=DEFAULT_TERMINAL)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--table-prefix", default="exness_")
    parser.add_argument("--report", type=Path,
                        default=VENDOR_DIR / "exness" / "import_1m_report.json")
    return parser.parse_args()


def main():
    args = parse_args()
    wall_clock = ZoneInfo(args.timezone)
    unknown = [s for s in args.symbols if s.lower() not in CANON]
    if unknown:
        print(f"unknown symbols: {unknown}; known: {sorted(CANON)}", file=sys.stderr)
        return 2

    # The cutoff is a NEW YORK date, so it is converted back to the real UTC
    # instant MT5 timestamps are expressed in before it filters anything.
    cutoff_ny = datetime.fromisoformat(args.to_date) + timedelta(days=1)
    cutoff = cutoff_ny.replace(tzinfo=wall_clock).astimezone(UTC)

    if not mt5.initialize(path=str(args.terminal)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    writer = Writer(wall_clock)
    report = []
    try:
        terminal, account = mt5.terminal_info(), mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account is unavailable: {mt5.last_error()}")
        log(f"account {account.login} on {account.server}, "
            f"maxbars {terminal.maxbars:,} (read-only feed)")
        # NOT A WARNING ANY MORE, because the request is a month and a month of
        # M1 fits inside even the 100,000 default. It used to say deep history
        # "will be truncated", which was wrong in the worse direction: an
        # over-cap request returns nothing at all rather than a truncated
        # answer. Raising the cap still helps the terminal cache more, so it is
        # reported and no longer asked for -- changing it means restarting a
        # terminal that may be carrying live orders.
        if terminal.maxbars <= 1_000_000:
            log(f"maxbars {terminal.maxbars:,} is the default; requests are "
                f"one month each and fit inside it. Raising [Charts] MaxBars "
                f"only makes the terminal cache more.")
        log(f"cutoff {cutoff.isoformat()} UTC "
            f"(New York bars through {args.to_date} 23:59)")

        for key in args.symbols:
            key = key.lower()
            log(f"{key} -> {CANON[key]}")
            try:
                summary = import_symbol(key, CANON[key], writer, cutoff,
                                        args.first_year, args.table_prefix)
            except Exception as error:  # noqa: BLE001 - one symbol must not
                # cost the other ten; an unquoted CFD is a normal outcome.
                log(f"{key}: {type(error).__name__}: {error}")
                summary = {"symbol": key, "broker": CANON[key],
                           "error": f"{type(error).__name__}: {error}"}
            report.append(summary)
            log(f"{key}: {summary}")
    finally:
        writer.close()
        mt5.shutdown()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    log(f"wrote {writer.written:,} bars; report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
