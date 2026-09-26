#!/usr/bin/env python3
"""Import a broker's MT5 one-minute bars into the store as `<broker>_<symbol>_1m`.

`--broker exness` (the default) lands `exness_<symbol>_1m`; `--broker
fundednext` lands `fundednext_<symbol>_1m` from the FundedNext terminal. The
table is named for the BROKER's symbol, so FundedNext's NDX100 is
`fundednext_ndx100_1m`. Everything below was written for Exness and holds for
both, except the clock -- see FUNDEDNEXT CLOCK.

FUNDEDNEXT CLOCK. FundedNext's MT5 epoch is SERVER time, not UTC: the server
sits at New York + 7h all year (UTC+3 in the US summer, UTC+2 in winter), so the
stored New York wall clock is the raw stamp minus seven hours, with no DST
arithmetic -- EXCEPT before 2024-07-08 12:37 server, when the server kept
Central European time: New York + 6, and + 5 in the weeks each spring and
autumn when the US and EU clocks disagree (`FUNDEDNEXT_OFFSET_EXCEPTIONS`).
Never calibrate against an `exness_*` table: Exness FX is itself an hour early
in every EU winter before 2024-04. Use Dukascopy FX, Databento indices, or a
clock-anchored event such as the 08:30 New York data spike.

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
FUNDEDNEXT_TERMINAL = Path(r"C:\Program Files\FundedNext MT5 Terminal\terminal64.exe")

#: Hours the FundedNext server clock runs ahead of New York wall clock.
FUNDEDNEXT_SERVER_AHEAD_OF_NY_HOURS = 7

#: FundedNext's currency pairs. Kept because the class matters for commission
#: in `cfd_families`; the clock no longer depends on it.
FUNDEDNEXT_FX = {"AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
                 "USDCAD", "USDJPY"}

#: `(first, end, hours, scope)` in SERVER time where the lead was not seven.
#: ONE SWITCH: every symbol was New York + 6 until 2024-07-08 12:37 server and
#: + 7 since. Before it the server kept Central European time, so in the US/EU
#: DST gap weeks of 2020-2022 the lead was five -- found on AUDUSD and AUDJPY,
#: the only FundedNext M1 that old, at day resolution against Dukascopy. The
#: 2022-10 and 2023 gap weeks read six. Later rows override earlier ones. That edge is a mid-week forward jump -- stamps go 12:37 then
#: 13:37 -- so both bars land on 06:37 New York and the writer keeps the later.
#:
#: Checked 2026-09-24 against clock-correct references only: Dukascopy FX
#: (day-level, 2020-2026, AUDUSD/AUDJPY/GBPJPY/USDJPY/EURUSD), Databento
#: `es_1m`/`nq_1m` for the indices, Exness indices and oils from 2022-07, and
#: the 17:00 New York daily break for the metals. An earlier version of this
#: table held six scoped windows because it was calibrated against `exness_*`
#: FX, which is itself an hour early in every EU winter before 2024-04
#: ([[exness-history-is-an-hour-early-in-eu-winter]]).
FUNDEDNEXT_OFFSET_EXCEPTIONS = (
    (datetime(2000, 1, 1, tzinfo=timezone.utc),
     datetime(2024, 7, 8, 13, tzinfo=timezone.utc), 6, "all"),
    (datetime(2020, 3, 7, tzinfo=timezone.utc),
     datetime(2020, 3, 28, tzinfo=timezone.utc), 5, "all"),
    (datetime(2020, 10, 24, tzinfo=timezone.utc),
     datetime(2020, 10, 31, tzinfo=timezone.utc), 5, "all"),
    (datetime(2021, 3, 13, tzinfo=timezone.utc),
     datetime(2021, 3, 27, tzinfo=timezone.utc), 5, "all"),
    (datetime(2021, 10, 30, tzinfo=timezone.utc),
     datetime(2021, 11, 6, tzinfo=timezone.utc), 5, "all"),
    (datetime(2022, 3, 12, tzinfo=timezone.utc),
     datetime(2022, 3, 26, tzinfo=timezone.utc), 5, "all"),
)

#: The eleven instruments the canon book trades, as Exness names them. The keys
#: are the repository's symbols, so `nq` -> `USTEC` matches `BROKER_ALIAS` in
#: `sandbox/research/cfd_families.py` and the table lands as
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
    # `cfd_families.broker_symbol`, which is the authority.
    "btc": "BTCUSD",
    "xniusd": "XNIUSD",
    # The rest of the research pool, added 2026-08-31 so a candidate can be
    # screened on live fills rather than skipped for want of a table. `es` and
    # `de40` are the two whose broker name does NOT follow the repository name
    # -- US500 and DE30 -- which is exactly why this map is resolved through
    # `cfd_families.broker_symbol` and never guessed.
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
    # ADDED 2026-09-23 for the `fastbar` 5m sweep. Each has a 1m vendor table
    # and was refused only for want of a broker table to price its fills.
    "fr40": "FR40",
    "stoxx50": "STOXX50",
    "aus200": "AUS200",
    "eurusd": "EURUSD",
    "usdcad": "USDCAD",
    "audjpy": "AUDJPY",
}

#: The research symbols FundedNext quotes, as FundedNext names them. Probed
#: 2026-09-24 from `symbols_get` (67 symbols): no crypto, no XAL/XNI, no gold
#: crosses. The indices and oils do not follow the Exness names.
FUNDEDNEXT = {
    "ustec": "NDX100",
    "es": "SPX500",
    "de40": "GER30",
    "fr40": "FRA40",
    "stoxx50": "EUSTX50",
    "uk100": "UK100",
    "jp225": "JP225",
    "hk50": "HK50",
    "aus200": "AUS200",
    "ukoil": "UKOUSD",
    "usoil": "USOUSD",
    "usdjpy": "USDJPY",
    "audusd": "AUDUSD",
    "gbpjpy": "GBPJPY",
    "gbpusd": "GBPUSD",
    "eurjpy": "EURJPY",
    "eurusd": "EURUSD",
    "usdcad": "USDCAD",
    "audjpy": "AUDJPY",
    "xagusd": "XAGUSD",
    "xptusd": "XPTUSD",
    "xauusd": "XAUUSD",
}

#: Everything that differs between brokers. `clock` says what the terminal's
#: epoch is: `utc` is a true UTC instant (Exness), `ny_plus_7` is server time
#: seven hours ahead of New York (FundedNext). Each broker gets its own IPC
#: mutex: the lock exists for processes sharing ONE terminal, and a FundedNext
#: import must never queue behind the live Exness bridge.
BROKERS = {
    "exness": {"terminal": DEFAULT_TERMINAL, "symbols": CANON,
               "prefix": "exness_", "clock": "utc", "mutex": None},
    "fundednext": {"terminal": FUNDEDNEXT_TERMINAL, "symbols": FUNDEDNEXT,
                   "prefix": "fundednext_", "clock": "ny_plus_7",
                   "mutex": r"Local\QuantMetaTrader5IPC_fundednext"},
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


def server_to_stored_ns(server_seconds, fx=True):
    """FundedNext server stamp -> New York wall clock reinterpreted as UTC."""
    hours = FUNDEDNEXT_SERVER_AHEAD_OF_NY_HOURS
    for first, end, lead, scope in FUNDEDNEXT_OFFSET_EXCEPTIONS:
        if scope != "all" and (scope == "fx") != fx:
            continue
        if first.timestamp() <= server_seconds < end.timestamp():
            hours = lead
    return (server_seconds - hours * 3600) * 1_000_000_000


def to_stored_ns(seconds, wall_clock, clock, fx=True):
    if clock == "ny_plus_7":
        return server_to_stored_ns(seconds, fx)
    return actual_to_stored_ns(seconds, wall_clock)


def to_actual_seconds(seconds, clock, fx=True):
    """A terminal stamp as a true UTC instant, for comparing with the cutoff."""
    if clock != "ny_plus_7":
        return seconds
    stored = datetime.fromtimestamp(server_to_stored_ns(seconds, fx) // 10**9, tz=UTC)
    return int(stored.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())


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

    def __init__(self, wall_clock, clock="utc"):
        self.wall_clock = wall_clock
        self.clock = clock
        self.sender = None
        self.written = 0

    def connect(self):
        if self.sender is None:
            self.sender = Sender()
            self.sender.establish()
        return self.sender

    def write(self, table, rows, cutoff_seconds, fx=True):
        sender = self.connect()
        written = 0
        try:
            for row in rows:
                seconds = int(row["time"])
                if to_actual_seconds(seconds, self.clock, fx) >= cutoff_seconds:
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
                    at=TimestampNanos(to_stored_ns(seconds, self.wall_clock,
                                                   self.clock, fx)),
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
                written += writer.write(table, rows, cutoff_seconds,
                                        broker in FUNDEDNEXT_FX)
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
    parser.add_argument("--broker", choices=sorted(BROKERS), default="exness")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="repository symbols; default is every symbol in "
                             "the broker's map")
    parser.add_argument("--to-date", default=two_days_ago.isoformat(),
                        help="last INCLUSIVE New York date to import "
                             "(default: two days ago)")
    parser.add_argument("--first-year", type=int, default=EARLIEST_YEAR)
    parser.add_argument("--terminal", type=Path, default=None,
                        help="default: the broker's own terminal")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--table-prefix", default=None, help="default: <broker>_")
    parser.add_argument("--report", type=Path, default=None,
                        help="default: <vendor dir>/<broker>/import_1m_report.json")
    args = parser.parse_args()
    profile = BROKERS[args.broker]
    args.symbol_map = profile["symbols"]
    args.clock = profile["clock"]
    args.symbols = args.symbols or sorted(args.symbol_map)
    args.terminal = args.terminal or profile["terminal"]
    args.table_prefix = args.table_prefix or profile["prefix"]
    args.report = args.report or VENDOR_DIR / args.broker / "import_1m_report.json"
    if profile["mutex"]:
        from ipc_lock import _NamedMutex  # noqa: PLC0415

        mt5._mutex = _NamedMutex(profile["mutex"])
    return args


def main():
    args = parse_args()
    wall_clock = ZoneInfo(args.timezone)
    symbol_map = args.symbol_map
    unknown = [s for s in args.symbols if s.lower() not in symbol_map]
    if unknown:
        print(f"unknown symbols for {args.broker}: {unknown}; "
              f"known: {sorted(symbol_map)}", file=sys.stderr)
        return 2

    # The cutoff is a NEW YORK date, so it is converted back to the real UTC
    # instant MT5 timestamps are expressed in before it filters anything.
    cutoff_ny = datetime.fromisoformat(args.to_date) + timedelta(days=1)
    cutoff = cutoff_ny.replace(tzinfo=wall_clock).astimezone(UTC)

    if not mt5.initialize(path=str(args.terminal)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    writer = Writer(wall_clock, args.clock)
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
            log(f"{key} -> {symbol_map[key]}")
            try:
                summary = import_symbol(key, symbol_map[key], writer, cutoff,
                                        args.first_year, args.table_prefix)
            except Exception as error:  # noqa: BLE001 - one symbol must not
                # cost the other ten; an unquoted CFD is a normal outcome.
                log(f"{key}: {type(error).__name__}: {error}")
                summary = {"symbol": key, "broker": symbol_map[key],
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
