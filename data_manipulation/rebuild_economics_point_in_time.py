#!/usr/bin/env python3
"""Replace revised economics snapshots with values known on each release date.

Sources
-------
* ALFRED's public (no API key) vintage download form for BLS, Census, and
  BEA/FRED series.  Each value is selected from the column for the release
  date stored in ``ts``.
* TradingView's public economic-calendar events for the two copyrighted ISM
  headline indexes that are not distributed by FRED.  Events are required to
  identify the Institute for Supply Management as their source.
* New York Fed Markets API for EFFR original publication values.

``date`` remains the reference period and ``ts`` remains the public release
time.  The script changes values only after complete coverage is proven.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import requests


ROOT = Path(__file__).resolve().parents[1]
ECONOMICS_DIR = ROOT / "parquets" / "economics"
ALFRED_GRAPH = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
TRADINGVIEW_EVENTS = "https://economic-calendar.tradingview.com/events"
NYFED_READ = "https://markets.newyorkfed.org/read"
USER_AGENT = "Quant point-in-time economics rebuilder/1.0"
UTC = timezone.utc
NY = ZoneInfo("America/New_York")
ALFRED_CACHE = Path(tempfile.gettempdir()) / "quant_alfred_vintage_cache"


# symbol: (ALFRED series, units transformation, decimal places)
ALFRED_DIRECT = {
    "nfp_tch": ("PAYEMS", "chg", 0),
    "usurtot": ("UNRATE", "lin", 1),
    "unitedstaavehouear": ("CES0500000003", "pch", 1),
    "unitedstaconpriindcp": ("CPIAUCNS", "lin", 3),
    "unitedstacorconpri": ("CPILFESL", "lin", 3),
    "rstamom": ("RSAFS", "pch", 1),
    "usaretailsalesyoy": ("RSAFS", "pc1", 1),
    "usarsegaam": ("MARTSMPCSM44W72USS", "lin", 1),
    "unitedstacorpcepriin": ("PCEPILFE", "lin", 3),
    "gdp_cqoq": ("A191RL1Q225SBEA", "lin", 1),
    "gdp_cyoy": ("GDPC1", "pc1", 1),
}

CONTROL_COMPONENTS = ("RSAFS", "RSMVPD", "RSGASS", "RSBMGESD", "RSFSDP")
MANUFACTURING_PMI = "napmpmi"
SERVICES_PMI = "unitedstanonmanpmi"
EFFR = "usaeffr"


class SourceError(RuntimeError):
    pass


def request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = session.request(method, url, timeout=75, **kwargs)
            if response.status_code in {403, 429} or response.status_code >= 500:
                raise SourceError(f"{response.status_code} from {response.url}")
            response.raise_for_status()
            return response
        except (requests.RequestException, SourceError) as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(min(60, 5 * (2**attempt)))
    raise SourceError(f"Request failed for {url}: {last_error}")


def load_tables() -> dict[Path, pa.Table]:
    result = {path: pq.read_table(path) for path in sorted(ECONOMICS_DIR.glob("*.parquet"))}
    if not result:
        raise SourceError(f"No Parquets found in {ECONOMICS_DIR}")
    return result


def symbol_of(table: pa.Table) -> str:
    symbols = set(table["symbol"].to_pylist())
    if len(symbols) != 1:
        raise SourceError(f"Expected one symbol, got {symbols}")
    return next(iter(symbols))


def observation_date(reference: date, symbol: str) -> date:
    if symbol.startswith("gdp_"):
        return date(reference.year, reference.month - 2, 1)
    return reference


def alfred_matrix(
    series_id: str,
    units: str,
    release_dates: list[date],
    observation_start: date,
    observation_end: date,
) -> dict[tuple[date, date], float]:
    del units
    selected = sorted(set(release_dates))

    def fetch_vintage(vintage: date) -> tuple[date, dict[date, float]]:
        ALFRED_CACHE.mkdir(parents=True, exist_ok=True)
        cache = ALFRED_CACHE / (
            f"{series_id}_{observation_start}_{observation_end}_{vintage}.csv"
        )
        if cache.exists():
            text = cache.read_text()
        else:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            response = request(
                session,
                "GET",
                ALFRED_GRAPH,
                params={
                    "id": series_id,
                    "cosd": observation_start.isoformat(),
                    # A vintage cannot contain observations from its future;
                    # limiting the range also avoids ALFRED 404s on unusually
                    # large historical releases (notably April 2020 payrolls).
                    "coed": min(observation_end, vintage).isoformat(),
                    "vintage_date": vintage.isoformat(),
                },
            )
            text = response.text
            cache.write_text(text)
        rows = csv.DictReader(io.StringIO(text))
        expected = f"{series_id}_{vintage:%Y%m%d}"
        if rows.fieldnames != ["observation_date", expected]:
            raise SourceError(
                f"ALFRED ignored vintage {vintage} for {series_id}: {rows.fieldnames}"
            )
        values = {}
        for row in rows:
            raw = row[expected].strip()
            if raw not in {"", "."}:
                values[date.fromisoformat(row["observation_date"])] = float(raw)
        return vintage, values

    result: dict[tuple[date, date], float] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_vintage, vintage) for vintage in selected]
        for count, future in enumerate(as_completed(futures), 1):
            vintage, values = future.result()
            result.update({(observed, vintage): value for observed, value in values.items()})
            if count % 20 == 0 or count == len(futures):
                print(
                    f"Fetched ALFRED {series_id} vintages {count}/{len(futures)}",
                    file=sys.stderr,
                    flush=True,
                )
    return result


def fetch_alfred_jobs(tables: dict[Path, pa.Table]) -> dict[str, dict]:
    by_symbol = {symbol_of(table): table for table in tables.values()}
    jobs: dict[str, tuple[set[date], date, date]] = {}

    def add_job(series_id: str, releases: list[date], start: date, end: date) -> None:
        if series_id in jobs:
            old_releases, old_start, old_end = jobs[series_id]
            jobs[series_id] = (old_releases | set(releases), min(old_start, start), max(old_end, end))
        else:
            jobs[series_id] = (set(releases), start, end)

    for symbol, (series_id, units, _places) in ALFRED_DIRECT.items():
        table = by_symbol[symbol]
        releases = [value.date() for value in table["ts"].to_pylist()]
        observations = [observation_date(value, symbol) for value in table["date"].to_pylist()]
        add_job(
            series_id,
            releases,
            min(observations) - timedelta(days=370),
            max(observations),
        )

    control = by_symbol["usarscg"]
    releases = [value.date() for value in control["ts"].to_pylist()]
    observations = control["date"].to_pylist()
    for series_id in CONTROL_COMPONENTS:
        add_job(
            series_id,
            releases,
            min(observations) - timedelta(days=40),
            max(observations),
        )

    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {
            pool.submit(alfred_matrix, sid, "lin", sorted(releases), start, end): sid
            for sid, (releases, start, end) in jobs.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            result[key] = future.result()
            print(f"Completed ALFRED {key}", file=sys.stderr, flush=True)
    return result


def month_before(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def direct_values(symbol: str, table: pa.Table, matrices: dict) -> list[float]:
    series_id, units, places = ALFRED_DIRECT[symbol]
    matrix = matrices[series_id]
    result = []
    for reference, release_ts in zip(table["date"].to_pylist(), table["ts"].to_pylist()):
        observed = observation_date(reference, symbol)
        vintage = release_ts.date()
        key = (observed, vintage)
        if key not in matrix:
            raise SourceError(f"Missing ALFRED value for {symbol}: observation/vintage {key}")
        current = matrix[key]
        if units == "lin":
            value = current
        elif units in {"chg", "pch"}:
            previous_key = (month_before(observed), vintage)
            if previous_key not in matrix:
                raise SourceError(f"Missing prior-period ALFRED value for {symbol}: {previous_key}")
            previous = matrix[previous_key]
            value = current - previous if units == "chg" else (current / previous - 1.0) * 100.0
        elif units == "pc1":
            prior_year_key = (date(observed.year - 1, observed.month, 1), vintage)
            if prior_year_key not in matrix:
                raise SourceError(f"Missing year-ago ALFRED value for {symbol}: {prior_year_key}")
            value = (current / matrix[prior_year_key] - 1.0) * 100.0
        else:
            raise SourceError(f"Unsupported transformation {units}")
        result.append(round(value, places))
    return result


def control_group_values(table: pa.Table, matrices: dict) -> list[float]:
    result = []
    for reference, release_ts in zip(table["date"].to_pylist(), table["ts"].to_pylist()):
        vintage = release_ts.date()

        def control(period: date) -> float:
            values = []
            for series_id in CONTROL_COMPONENTS:
                key = (period, vintage)
                matrix = matrices[series_id]
                if key not in matrix:
                    raise SourceError(f"Missing {series_id} control-group input at {key}")
                values.append(matrix[key])
            total, autos, gas, building, food_service = values
            return total - autos - gas - building - food_service

        current = control(reference)
        previous = control(month_before(reference))
        result.append(round((current / previous - 1.0) * 100.0, 1))
    return result


def fetch_tradingview_pmi(
    start: date, end: date
) -> dict[tuple[str, date], tuple[float, datetime, str]]:
    result: dict[tuple[str, date], tuple[float, datetime, str]] = {}
    windows = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        stop = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
        stop = min(stop, end + timedelta(days=1))
        windows.append((cursor, stop))
        cursor = stop

    def fetch_window(window: tuple[date, date]) -> list[dict]:
        cursor, stop = window
        session = requests.Session()
        session.headers.update(
            {"User-Agent": USER_AGENT, "Origin": "https://www.tradingview.com"}
        )
        return request(
            session,
            "GET",
            TRADINGVIEW_EVENTS,
            params={
                "from": f"{cursor.isoformat()}T00:00:00.000Z",
                "to": f"{stop.isoformat()}T00:00:00.000Z",
                "countries": "US",
            },
        ).json().get("result", [])

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = pool.map(fetch_window, windows)
        for items in batches:
          for item in items:
            title = item.get("title", "")
            if title == "ISM Manufacturing PMI":
                symbol = MANUFACTURING_PMI
            elif title in {"ISM Non-Manufacturing PMI", "ISM Services PMI"}:
                symbol = SERVICES_PMI
            else:
                continue
            if item.get("source") != "Institute for Supply Management":
                raise SourceError(f"Unexpected PMI source: {item}")
            actual = item.get("actual")
            if actual is None:
                continue
            published = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
            period_text = str(item.get("period", "")).strip()[:3].title()
            try:
                month = list(calendar.month_abbr).index(period_text)
            except ValueError as exc:
                raise SourceError(f"Cannot parse PMI period: {item}") from exc
            year = published.year - 1 if month > published.month else published.year
            reference = date(year, month, 1)
            key = (symbol, reference)
            if key in result:
                raise SourceError(f"Duplicate PMI event for {key}")
            result[key] = (round(float(actual), 1), published, str(item["id"]))
    return result


def pmi_values(symbol: str, table: pa.Table, events: dict) -> tuple[list[float], list[datetime]]:
    values = []
    timestamps = []
    for reference in table["date"].to_pylist():
        key = (symbol, reference)
        if key not in events:
            raise SourceError(f"Missing TradingView/ISM event for {key}")
        value, published, _event_id = events[key]
        values.append(value)
        timestamps.append(published)
    return values, timestamps


def fetch_effr(start: date, end: date) -> dict[date, tuple[float, datetime]]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    query = urlencode(
        {
            "productCode": 50,
            "eventCodes": 500,
            "startDt": start.isoformat(),
            "endDt": end.isoformat(),
            "sort": "postDt:1",
            "limit": 5000,
        }
    )
    payload = request(session, "GET", f"{NYFED_READ}?{query}").json()
    result = {}
    for item in payload["data"]:
        detail = json.loads(item["data"])
        effective = date.fromisoformat(item["postDt"])
        published = datetime.fromisoformat(item["origInsertTs"]).replace(tzinfo=NY).astimezone(UTC)
        result[effective] = (float(detail["dailyRate"]), published)
    return result


def replace_column(table: pa.Table, name: str, values: list) -> pa.Table:
    index = table.schema.get_field_index(name)
    field = table.schema.field(index)
    return table.set_column(index, field, pa.array(values, type=field.type))


def attach_metadata(table: pa.Table, source: str, fetched_at: datetime) -> pa.Table:
    metadata = dict(table.schema.metadata or {})
    metadata.update(
        {
            b"point_in_time_values": b"true",
            b"value_vintage": b"as_of_release_date",
            b"availability_column": b"ts",
            b"value_source": source.encode(),
            b"vintage_rebuilt_at": fetched_at.isoformat().encode(),
        }
    )
    return table.replace_schema_metadata(metadata)


def atomic_write(path: Path, table: pa.Table) -> None:
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temp = Path(handle.name)
    handle.close()
    try:
        pq.write_table(table, temp, compression="snappy", version="2.6")
        pq.read_table(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="atomically rewrite all covered files")
    parser.add_argument("--backup-dir", type=Path, help="copy current Parquets here before applying")
    parser.add_argument("--report", type=Path, help="write a JSON provenance/change report")
    args = parser.parse_args()

    tables = load_tables()
    by_symbol = {symbol_of(table): (path, table) for path, table in tables.items()}
    expected = set(ALFRED_DIRECT) | {"usarscg", MANUFACTURING_PMI, SERVICES_PMI, EFFR}
    if set(by_symbol) != expected:
        raise SourceError(f"Unexpected symbol set: {sorted(set(by_symbol) ^ expected)}")

    matrices = fetch_alfred_jobs(tables)
    all_ts = [value for table in tables.values() for value in table["ts"].to_pylist()]
    pmi = fetch_tradingview_pmi(min(all_ts).date(), max(all_ts).date())
    effr_table = by_symbol[EFFR][1]
    effr = fetch_effr(min(effr_table["date"].to_pylist()), max(effr_table["date"].to_pylist()))
    fetched_at = datetime.now(UTC).replace(microsecond=0)

    corrected: dict[Path, pa.Table] = {}
    report = []
    for symbol, (path, table) in sorted(by_symbol.items()):
        old_values = table["value"].to_pylist()
        old_ts = table["ts"].to_pylist()
        timestamps = old_ts
        if symbol in ALFRED_DIRECT:
            values = direct_values(symbol, table, matrices)
            sid, units, _ = ALFRED_DIRECT[symbol]
            source = f"ALFRED:{sid}; units={units}; vintage=ts.date"
        elif symbol == "usarscg":
            values = control_group_values(table, matrices)
            source = "ALFRED:retail control group computed from release-vintage components"
        elif symbol in {MANUFACTURING_PMI, SERVICES_PMI}:
            values, timestamps = pmi_values(symbol, table, pmi)
            source = "TradingView calendar actual; source=Institute for Supply Management"
        else:
            values = []
            for reference, published in zip(table["date"].to_pylist(), table["ts"].to_pylist()):
                if reference not in effr:
                    raise SourceError(f"Missing official EFFR for {reference}")
                value, official_ts = effr[reference]
                if official_ts != published:
                    raise SourceError(f"EFFR timestamp mismatch for {reference}: {published} != {official_ts}")
                values.append(value)
            source = "New York Fed Markets API: dailyRate at origInsertTs"

        if len(values) != table.num_rows or any(value is None for value in values):
            raise SourceError(f"Incomplete values for {symbol}")
        result = replace_column(table, "value", values)
        result = replace_column(result, "ts", timestamps)
        result = replace_column(result, "refreshed_at", [fetched_at] * result.num_rows)
        result = attach_metadata(result, source, fetched_at)
        if result["ts"].to_pylist() != sorted(result["ts"].to_pylist()):
            raise SourceError(f"Unsorted timestamps after rebuilding {symbol}")
        corrected[path] = result
        changes = sum(a != b for a, b in zip(old_values, values))
        timestamp_changes = sum(a != b for a, b in zip(old_ts, timestamps))
        samples = [
            {"date": str(table["date"][i].as_py()), "old": old_values[i], "first_release": values[i]}
            for i in range(table.num_rows)
            if old_values[i] != values[i]
        ][:5]
        report.append(
            {
                "symbol": symbol,
                "rows": table.num_rows,
                "changed_values": changes,
                "changed_timestamps": timestamp_changes,
                "source": source,
                "samples": samples,
            }
        )

    payload = {
        "applied": args.apply,
        "fetched_at": fetched_at.isoformat(),
        "files": report,
    }
    if args.apply:
        if not args.backup_dir:
            raise SourceError("--apply requires --backup-dir")
        args.backup_dir.mkdir(parents=True, exist_ok=False)
        for path in tables:
            shutil.copy2(path, args.backup_dir / path.name)
        for path, table in corrected.items():
            atomic_write(path, table)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
