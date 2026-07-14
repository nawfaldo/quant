"""Incrementally import a configurable Yahoo futures universe into QuestDB.

Daily tables use the repository convention ``<prefix>_1d`` and ET calendar
dates stored as fake-UTC midnight timestamps. Existing tables are never
dropped. QuestDB WAL deduplication makes the trailing overlap an upsert.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx
import pandas as pd

import config

LOGGER = logging.getLogger("fetch_futures_daily")
DEFAULT_REGISTRY = Path(__file__).with_name("futures_universe.json")
PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TICKER_RE = re.compile(r"^[A-Z0-9=.^-]+$")
EXPECTED_COLUMNS = {
    "open": "DOUBLE",
    "high": "DOUBLE",
    "low": "DOUBLE",
    "close": "DOUBLE",
    "volume": "LONG",
    "timestamp": "TIMESTAMP",
}


VALID_SOURCES = frozenset({"yahoo", "external"})


@dataclass(frozen=True)
class Symbol:
    ticker: str
    prefix: str
    asset_class: str
    source: str = "yahoo"

    @property
    def table(self) -> str:
        return f"{self.prefix}_1d"

    @property
    def externally_sourced(self) -> bool:
        # es_1d/nq_1d hold back-adjusted CFD bars that Yahoo's front-month =F
        # series would silently upsert over, at a different price level.
        return self.source == "external"


@dataclass
class SyncResult:
    symbol: Symbol
    status: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    checksum: str = ""
    detail: str = ""


def load_registry(path: Path) -> list[Symbol]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported futures registry schema")
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise ValueError("registry must contain a non-empty symbols list")

    symbols: list[Symbol] = []
    seen_tickers: set[str] = set()
    seen_prefixes: set[str] = set()
    for raw in raw_symbols:
        ticker = str(raw.get("ticker", "")).strip().upper()
        prefix = str(raw.get("prefix", "")).strip().lower()
        asset_class = str(raw.get("asset_class", "")).strip().lower()
        source = str(raw.get("source", "yahoo")).strip().lower()
        if not TICKER_RE.fullmatch(ticker):
            raise ValueError(f"invalid Yahoo ticker: {ticker!r}")
        if not PREFIX_RE.fullmatch(prefix):
            raise ValueError(f"invalid QuestDB prefix: {prefix!r}")
        if not asset_class:
            raise ValueError(f"missing asset_class for {ticker}")
        if source not in VALID_SOURCES:
            raise ValueError(f"invalid source for {ticker}: {source!r}")
        if ticker in seen_tickers or prefix in seen_prefixes:
            raise ValueError(f"duplicate ticker or prefix: {ticker}/{prefix}")
        seen_tickers.add(ticker)
        seen_prefixes.add(prefix)
        symbols.append(Symbol(ticker, prefix, asset_class, source))
    return symbols


class QuestDB:
    def __init__(self) -> None:
        self.http = f"http://{config.QUESTDB_HOST}:{config.QUESTDB_HTTP_PORT}"

    def query(self, sql: str, timeout: float = 120.0) -> dict:
        response = httpx.get(f"{self.http}/exec", params={"query": sql}, timeout=timeout)
        if response.is_error:
            raise RuntimeError(f"QuestDB {response.status_code}: {response.text}")
        return response.json()

    def table_metadata(self, table: str) -> dict | None:
        result = self.query(
            "SELECT table_name, walEnabled, dedup, designatedTimestamp "
            f"FROM tables() WHERE table_name = '{table}'"
        )
        rows = result.get("dataset", [])
        if not rows:
            return None
        return dict(zip(("table", "wal", "dedup", "timestamp"), rows[0]))

    def table_columns(self, table: str) -> dict[str, str]:
        result = self.query(f"SHOW COLUMNS FROM {table}")
        return {str(row[0]).lower(): str(row[1]).upper() for row in result.get("dataset", [])}

    def duplicate_dates(self, table: str) -> int:
        result = self.query(
            "SELECT count() FROM ("
            f"SELECT timestamp, count() c FROM {table} GROUP BY timestamp"
            ") WHERE c > 1"
        )
        rows = result.get("dataset", [])
        return int(rows[0][0]) if rows else 0

    def ensure_table(self, table: str, *, dry_run: bool = False) -> bool:
        metadata = self.table_metadata(table)
        if metadata is None:
            if dry_run:
                return False
            self.query(
                f"CREATE TABLE {table} ("
                "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume LONG, "
                "timestamp TIMESTAMP"
                # One bar per day, so PARTITION BY DAY would create a partition per
                # row (~6.5k dirs/table) — every full scan then opens them all and
                # memory-maps each. YEAR keeps a daily table to a few dozen partitions.
                ") TIMESTAMP(timestamp) PARTITION BY YEAR WAL "
                "DEDUP UPSERT KEYS(timestamp)"
            )
            return True

        if not metadata["wal"] or metadata["timestamp"] != "timestamp":
            raise RuntimeError(f"{table} must be WAL-enabled with designated timestamp")
        columns = self.table_columns(table)
        for name, expected in EXPECTED_COLUMNS.items():
            if columns.get(name) != expected:
                raise RuntimeError(
                    f"{table}.{name} has type {columns.get(name)!r}; expected {expected}"
                )
        if not metadata["dedup"]:
            duplicates = self.duplicate_dates(table)
            if duplicates:
                raise RuntimeError(f"{table} contains {duplicates} duplicate dates")
            if not dry_run:
                self.query(f"ALTER TABLE {table} DEDUP ENABLE UPSERT KEYS(timestamp)")
        return True

    def max_timestamp(self, table: str) -> pd.Timestamp | None:
        result = self.query(f"SELECT max(timestamp) FROM {table}")
        rows = result.get("dataset", [])
        if not rows or rows[0][0] is None:
            return None
        return pd.Timestamp(rows[0][0]).tz_localize(None)

    def existing_dates(self, table: str, start: pd.Timestamp, end: pd.Timestamp) -> set[pd.Timestamp]:
        result = self.query(
            f"SELECT timestamp FROM {table} WHERE timestamp >= '{start.isoformat()}' "
            f"AND timestamp <= '{end.isoformat()}'"
        )
        return {pd.Timestamp(row[0]).tz_localize(None) for row in result.get("dataset", [])}

    def write_rows(self, table: str, frame: pd.DataFrame) -> None:
        sock = socket.create_connection(
            (config.QUESTDB_HOST, config.QUESTDB_ILP_PORT), timeout=30.0
        )
        try:
            batch: list[str] = []
            for timestamp, row in frame.iterrows():
                nanos = int(pd.Timestamp(timestamp).value)
                batch.append(
                    f"{table} open={row['Open']:.12g},high={row['High']:.12g},"
                    f"low={row['Low']:.12g},close={row['Close']:.12g},"
                    f"volume={int(row['Volume'])}i {nanos}\n"
                )
                if len(batch) == 1000:
                    sock.sendall("".join(batch).encode("utf-8"))
                    batch.clear()
            if batch:
                sock.sendall("".join(batch).encode("utf-8"))
        finally:
            sock.close()

    def ensure_runs_table(self) -> None:
        if self.table_metadata("data_collection_runs") is not None:
            return
        self.query(
            "CREATE TABLE data_collection_runs ("
            "run_timestamp TIMESTAMP, source VARCHAR, ticker VARCHAR, table_name VARCHAR, "
            "status VARCHAR, fetched LONG, inserted LONG, updated LONG, rejected LONG, "
            "checksum VARCHAR, detail VARCHAR"
            ") TIMESTAMP(run_timestamp) PARTITION BY MONTH WAL"
        )

    def record(self, result: SyncResult) -> None:
        self.ensure_runs_table()

        def quoted(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        now = datetime.now(timezone.utc).isoformat()
        self.query(
            "INSERT INTO data_collection_runs VALUES ("
            f"'{now}', 'yahoo', {quoted(result.symbol.ticker)}, "
            f"{quoted(result.symbol.table)}, {quoted(result.status)}, {result.fetched}, "
            f"{result.inserted}, {result.updated}, {result.rejected}, "
            f"{quoted(result.checksum)}, {quoted(result.detail[:1000])})"
        )


def flatten_yahoo_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy()
        frame.columns = [str(column[0]) for column in frame.columns]
    return frame


def normalize_yahoo(frame: pd.DataFrame, now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]), 0
    frame = flatten_yahoo_columns(frame).copy()
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Yahoo response missing columns: {sorted(missing)}")

    numeric = ["Open", "High", "Low", "Close", "Volume"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    if "Adj Close" in frame.columns:
        adjusted = pd.to_numeric(frame["Adj Close"], errors="coerce")
        factor = adjusted / frame["Close"]
        valid_factor = factor.notna() & (factor > 0) & factor.map(math.isfinite)
        for column in ("Open", "High", "Low", "Close"):
            frame.loc[valid_factor, column] *= factor[valid_factor]

    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_convert("America/New_York").tz_localize(None)
    index = index.normalize()
    frame.index = index
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()

    now = now or pd.Timestamp.now(tz="America/New_York")
    today = now.tz_localize(None).normalize()
    if now.hour < 17:
        frame = frame[frame.index < today]
    else:
        frame = frame[frame.index <= today]

    finite = frame[["Open", "High", "Low", "Close"]].apply(
        lambda col: col.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    ).all(axis=1)
    valid = (
        finite
        & (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        & (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
        & frame["Volume"].notna()
        & (frame["Volume"] >= 0)
    )
    rejected = int((~valid).sum())
    frame = frame.loc[valid, numeric]
    frame["Volume"] = frame["Volume"].round().astype("int64")
    return frame, rejected


def fetch_yahoo(symbol: Symbol, start: pd.Timestamp | None, attempts: int = 3) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is not installed; run pip install -r requirements.txt") from exc

    for attempt in range(attempts):
        try:
            ticker = yf.Ticker(symbol.ticker)
            kwargs: dict[str, object] = {
                "interval": "1d",
                "auto_adjust": False,
                "actions": False,
                "repair": True,
            }
            if start is None:
                kwargs["period"] = "max"
            else:
                kwargs["start"] = start.strftime("%Y-%m-%d")
            return ticker.history(**kwargs)
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def checksum_frame(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    values = frame.to_csv(index=True, date_format="%Y-%m-%d").encode("utf-8")
    return hashlib.sha256(values).hexdigest()


def select_symbols(symbols: Iterable[Symbol], requested: list[str]) -> list[Symbol]:
    if not requested:
        return list(symbols)
    wanted = {value.upper() for value in requested}
    selected = [s for s in symbols if s.ticker.upper() in wanted or s.prefix.upper() in wanted]
    found = {s.ticker.upper() for s in selected} | {s.prefix.upper() for s in selected}
    missing = [value for value in wanted if value not in found]
    if missing:
        raise ValueError(f"unknown symbols: {', '.join(sorted(missing))}")
    return selected


def sync_symbol(
    quest: QuestDB,
    symbol: Symbol,
    *,
    overlap_days: int,
    missing_only: bool,
    dry_run: bool,
) -> SyncResult:
    if symbol.externally_sourced:
        return SyncResult(symbol, "skipped", detail="externally sourced; Yahoo import disabled")

    exists = quest.table_metadata(symbol.table) is not None
    if missing_only and exists:
        return SyncResult(symbol, "skipped", detail="table already exists")

    quest.ensure_table(symbol.table, dry_run=dry_run)
    maximum = quest.max_timestamp(symbol.table) if exists else None
    start = maximum - pd.Timedelta(days=overlap_days) if maximum is not None else None
    raw = fetch_yahoo(symbol, start)
    frame, rejected = normalize_yahoo(raw)
    result = SyncResult(
        symbol,
        "ok",
        fetched=len(frame),
        rejected=rejected,
        checksum=checksum_frame(frame),
    )
    if frame.empty:
        result.status = "failed"
        result.detail = "Yahoo returned no valid complete daily rows"
        return result

    existing = (
        quest.existing_dates(symbol.table, frame.index.min(), frame.index.max())
        if exists
        else set()
    )
    result.updated = sum(timestamp in existing for timestamp in frame.index)
    result.inserted = len(frame) - result.updated
    if not dry_run:
        quest.write_rows(symbol.table, frame)
    else:
        result.detail = "dry run"
    return result


def validate(quest: QuestDB, symbols: list[Symbol]) -> int:
    failures = 0
    for symbol in symbols:
        try:
            exists = quest.ensure_table(symbol.table, dry_run=True)
            if not exists:
                LOGGER.warning("%-6s missing", symbol.table)
                failures += 1
            else:
                LOGGER.info("%-6s valid", symbol.table)
        except Exception as exc:
            failures += 1
            LOGGER.error("%-6s invalid: %s", symbol.table, exc)
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "validate"), nargs="?", default="sync")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--overlap-days", type=int, default=7)
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)
    if args.overlap_days < 0:
        raise SystemExit("--overlap-days must be non-negative")
    symbols = select_symbols(load_registry(args.registry), args.symbols)
    quest = QuestDB()
    if args.command == "validate":
        return 1 if validate(quest, symbols) else 0

    failures = 0
    for symbol in symbols:
        result: SyncResult
        try:
            result = sync_symbol(
                quest,
                symbol,
                overlap_days=args.overlap_days,
                missing_only=args.missing_only,
                dry_run=args.dry_run,
            )
            failures += result.status == "failed"
        except Exception as exc:
            LOGGER.exception("%s failed", symbol.ticker)
            result = SyncResult(symbol, "failed", detail=str(exc))
            failures += 1
        if not args.dry_run:
            try:
                quest.record(result)
            except Exception as exc:
                LOGGER.error("could not record %s import metadata: %s", symbol.ticker, exc)
        LOGGER.info(
            "%-5s %-8s fetched=%d inserted=%d updated=%d rejected=%d %s",
            symbol.prefix,
            result.status,
            result.fetched,
            result.inserted,
            result.updated,
            result.rejected,
            result.detail,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
