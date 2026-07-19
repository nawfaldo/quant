#!/usr/bin/env python3
"""Make economic Parquets use honest data-availability timestamps.

Sources:
  * BLS annual release schedules (via a text proxy because bls.gov blocks bots)
  * Census annual economic-indicator calendars
  * BEA release pages listed in the BEA releases sitemap
  * ISM's documented first/third-business-day schedule
  * New York Fed Markets API (exact original EFFR insert timestamp)

The existing ``date`` column remains the reference period for monthly and
quarterly series.  For EFFR it is corrected to the daily effective date.

Run ``rebuild_economics_point_in_time.py`` after this script when source values
are revised snapshots; that companion replaces them with release-date vintages.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from bs4 import BeautifulSoup


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
ECONOMICS_DIR = ROOT / "parquets" / "economics"
USER_AGENT = "Quant economic release timestamp updater/1.0"
MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}

EMPLOYMENT_SYMBOLS = {"nfp_tch", "usurtot", "unitedstaavehouear"}
CPI_SYMBOLS = {"unitedstaconpriindcp", "unitedstacorconpri"}
RETAIL_SYMBOLS = {"rstamom", "usaretailsalesyoy", "usarscg", "usarsegaam"}
PCE_SYMBOLS = {"unitedstacorpcepriin"}
GDP_SYMBOLS = {"gdp_cqoq", "gdp_cyoy"}
ISM_MANUFACTURING_SYMBOLS = {"napmpmi"}
ISM_SERVICES_SYMBOLS = {"unitedstanonmanpmi"}
EFFR_SYMBOLS = {"usaeffr"}


def get_text(url: str, timeout: int = 60) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def period(month_name: str, year: str) -> date:
    return date(int(year), MONTHS[month_name.strip().lower()], 1)


def eastern_to_utc(day: date, clock: str) -> datetime:
    local = datetime.combine(day, datetime.strptime(clock.strip(), "%I:%M %p").time(), NY)
    return local.astimezone(UTC)


def fetch_bls() -> dict[str, dict[date, datetime]]:
    result: dict[str, dict[date, datetime]] = {"employment": {}, "cpi": {}}
    row = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*\*\*"
        r"(Employment Situation|Consumer Price Index)\*\*\s+for\s+"
        r"([A-Za-z]+)\s+(\d{4})\s*\|"
    )
    for year in range(2020, 2027):
        url = f"https://r.jina.ai/https://www.bls.gov/schedule/{year}/"
        for line in get_text(url).splitlines():
            match = row.match(line)
            if not match:
                continue
            release_day = datetime.strptime(match.group(1).strip(), "%A, %B %d, %Y").date()
            release_ts = eastern_to_utc(release_day, match.group(2).strip())
            key = "employment" if match.group(3) == "Employment Situation" else "cpi"
            result[key][period(match.group(4), match.group(5))] = release_ts
    # The October 2025 establishment-survey figures were first published with
    # November's Employment Situation on December 16 after the shutdown.  BLS
    # issued no standalone October report (and collected no October CPS data).
    october_2025 = date(2025, 10, 1)
    november_2025 = date(2025, 11, 1)
    if october_2025 not in result["employment"] and november_2025 in result["employment"]:
        result["employment"][october_2025] = result["employment"][november_2025]
    return result


def fetch_census_retail() -> dict[date, datetime]:
    result: dict[date, datetime] = {}
    for year in range(2020, 2027):
        url = f"https://www.census.gov/economic-indicators/calendar-listview-{year}.html"
        soup = BeautifulSoup(get_text(url), "html.parser")
        for tr in soup.select("tr"):
            fields = list(tr.stripped_strings)
            if not fields or fields[0] != "Advance Monthly Sales for Retail and Food Services":
                continue
            if len(fields) < 4:
                raise RuntimeError(f"Unexpected Census row: {fields!r}")
            release_day = datetime.strptime(fields[1], "%B %d, %Y").date()
            reference = datetime.strptime(fields[3], "%B %Y").date()
            result[reference] = eastern_to_utc(release_day, fields[2].upper())
    # Census maintains the post-shutdown and current dates on the dedicated
    # Monthly Retail Trade schedule rather than the 2026 indicator calendar.
    schedule = BeautifulSoup(
        get_text("https://www.census.gov/retail/release_schedule.html"), "html.parser"
    )
    tables = schedule.select("table")
    if not tables:
        raise RuntimeError("Census retail release schedule has no tables")
    for tr in tables[0].select("tr")[1:]:
        fields = list(tr.stripped_strings)
        if len(fields) < 2 or "announced" in fields[1].lower():
            continue
        reference = datetime.strptime(fields[0], "%B %Y").date()
        release_day = datetime.strptime(fields[1], "%B %d, %Y").date()
        result[reference] = eastern_to_utc(release_day, "8:30 AM")
    return result


def parse_bea_release(url: str) -> tuple[str, date, datetime] | None:
    soup = BeautifulSoup(get_text(url), "html.parser")
    headings = [node.get_text(" ", strip=True) for node in soup.select("h1")]
    title = next((text for text in headings if text != "News Release"), "")
    lower_title = title.lower()
    is_monthly_pio = (
        "data update" not in lower_title
        and re.search(r"Personal Income and Outlays.*\d{4}", title, re.I)
    )
    is_advance_gdp = (
        ("gross domestic product" in lower_title or lower_title.startswith("gdp"))
        and ("advance estimate" in lower_title or "initial estimate" in lower_title)
    )
    if not is_monthly_pio and not is_advance_gdp:
        return None
    release_node = soup.select_one(".field--name-field-release-date")
    if not title or release_node is None:
        return None
    release_text = release_node.get_text(" ", strip=True).upper().replace(".", "")
    match = re.search(
        r"(?:AT|:)\s+(\d{1,2}:\d{2})\s+(AM|PM)\s*,?\s*(?:EST|EDT|ET),?\s*"
        r"(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),?\s*"
        r"([A-Z]+\s+\d{1,2},\s+\d{4})",
        release_text,
    )
    if not match:
        raise RuntimeError(f"Cannot parse BEA release field at {url}: {release_text!r}")
    release_day = datetime.strptime(match.group(3).title(), "%B %d, %Y").date()
    release_ts = eastern_to_utc(release_day, f"{match.group(1)} {match.group(2)}")
    return title, release_day, release_ts


def fetch_bea() -> dict[str, dict[date, datetime]]:
    sitemap = ElementTree.fromstring(get_text("https://www.bea.gov/releases/sitemap.xml"))
    urls = [node.text for node in sitemap.iter() if node.tag.endswith("loc") and node.text]
    candidates = [
        url
        for url in urls
        if re.search(r"/news/20(?:20|21|22|23|24|25|26)/", url)
        and (
            "personal-income-and-outlays" in url
            or (
                "gross-domestic-product" in url
                and ("advance-estimate" in url or "initial-estimate" in url)
            )
        )
    ]
    candidates.extend(
        [
            "https://www.bea.gov/news/2026/gdp-advance-estimate-4th-quarter-and-year-2025",
            "https://www.bea.gov/news/2026/gdp-advance-estimate-1st-quarter-2026",
        ]
    )
    candidates = sorted(set(candidates))
    parsed = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(parse_bea_release, url): url for url in candidates}
        for future in as_completed(futures):
            item = future.result()
            if item:
                parsed.append(item)

    result: dict[str, dict[date, datetime]] = {"pce": {}, "gdp": {}}
    pio_pattern = re.compile(r"Personal Income and Outlays(?:,|:)\s*([A-Za-z]+)\s+(\d{4})", re.I)
    combined_pio_pattern = re.compile(
        r"Personal Income and Outlays(?:,|:)\s*([A-Za-z]+)\s+and\s+([A-Za-z]+)\s+(\d{4})",
        re.I,
    )
    gdp_pattern = re.compile(r"([1-4])(?:st|nd|rd|th) Quarter(?: and Year)?\s+(\d{4})", re.I)
    for title, _release_day, release_ts in parsed:
        combined_pio = combined_pio_pattern.search(title)
        pio = pio_pattern.search(title)
        if combined_pio:
            result["pce"][period(combined_pio.group(1), combined_pio.group(3))] = release_ts
            result["pce"][period(combined_pio.group(2), combined_pio.group(3))] = release_ts
        elif pio:
            result["pce"][period(pio.group(1), pio.group(2))] = release_ts
        normalized_title = title
        for word, ordinal in {
            "First": "1st",
            "Second": "2nd",
            "Third": "3rd",
            "Fourth": "4th",
        }.items():
            normalized_title = re.sub(rf"\b{word}\b", ordinal, normalized_title, flags=re.I)
        gdp = gdp_pattern.search(normalized_title)
        if gdp:
            quarter_month = int(gdp.group(1)) * 3
            result["gdp"][date(int(gdp.group(2)), quarter_month, 1)] = release_ts
    return result


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    day = date(year, month, 1)
    day += timedelta(days=(weekday - day.weekday()) % 7 + 7 * (n - 1))
    return day


def last_weekday(year: int, month: int, weekday: int) -> date:
    day = date(year, month, calendar.monthrange(year, month)[1])
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def ism_holidays(year: int) -> set[date]:
    holidays = {
        observed(date(year, 1, 1)),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        last_weekday(year, 5, 0),
        observed(date(year, 7, 4)),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 10, 0, 2),
        observed(date(year, 11, 11)),
        nth_weekday(year, 11, 3, 4),
        observed(date(year, 12, 25)),
        easter_sunday(year) - timedelta(days=2),
    }
    if year >= 2021:
        holidays.add(observed(date(year, 6, 19)))

    # ISM takes one additional New Year holiday before January reports.
    cursor = observed(date(year, 1, 1)) + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    holidays.add(cursor)
    return holidays


def ism_release(reference: date, business_day_number: int) -> datetime:
    if reference.month == 12:
        year, month = reference.year + 1, 1
    else:
        year, month = reference.year, reference.month + 1
    holidays = ism_holidays(year)
    business_days = []
    day = date(year, month, 1)
    while len(business_days) < business_day_number:
        if day.weekday() < 5 and day not in holidays:
            business_days.append(day)
        day += timedelta(days=1)
    return eastern_to_utc(business_days[-1], "10:00 AM")


def fetch_effr(start: date, end: date) -> tuple[dict[date, datetime], dict[date, float]]:
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
    payload = json.loads(get_text(f"https://markets.newyorkfed.org/read?{query}"))
    timestamps: dict[date, datetime] = {}
    values: dict[date, float] = {}
    for item in payload["data"]:
        effective = date.fromisoformat(item["postDt"])
        local_insert = datetime.fromisoformat(item["origInsertTs"])
        detail = json.loads(item["data"])
        timestamps[effective] = local_insert.replace(tzinfo=NY).astimezone(UTC)
        values[effective] = float(detail["dailyRate"])
    return timestamps, values


def load_tables() -> dict[Path, pa.Table]:
    return {path: pq.read_table(path) for path in sorted(ECONOMICS_DIR.glob("*.parquet"))}


def build_sources(tables: dict[Path, pa.Table]) -> dict[str, object]:
    all_dates = []
    for table in tables.values():
        all_dates.extend(value for value in table.column("date").to_pylist() if value)
        all_dates.extend(value.date() for value in table.column("ts").to_pylist() if value)
    start, end = min(all_dates), max(all_dates)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            "bls": pool.submit(fetch_bls),
            "retail": pool.submit(fetch_census_retail),
            "bea": pool.submit(fetch_bea),
            "effr": pool.submit(fetch_effr, start, end),
        }
        result = {name: future.result() for name, future in jobs.items()}
    result["effr"], result["effr_values"] = result["effr"]
    return result


def replace_column(table: pa.Table, name: str, values: list, typ: pa.DataType) -> pa.Table:
    index = table.schema.get_field_index(name)
    field = table.schema.field(index)
    array = pa.array(values, type=typ)
    return table.set_column(index, pa.field(name, typ, nullable=field.nullable), array)


def corrected_table(
    path: Path,
    table: pa.Table,
    sources: dict[str, object],
) -> tuple[pa.Table, dict]:
    symbols = set(table.column("symbol").to_pylist())
    if len(symbols) != 1:
        raise RuntimeError(f"Expected one symbol in {path}, got {symbols}")
    symbol = next(iter(symbols))
    dates = table.column("date").to_pylist()
    old_ts = table.column("ts").to_pylist()
    keep = [True] * table.num_rows
    new_dates = list(dates)

    if symbol in EMPLOYMENT_SYMBOLS:
        mapping = sources["bls"]["employment"]
    elif symbol in CPI_SYMBOLS:
        mapping = sources["bls"]["cpi"]
    elif symbol in RETAIL_SYMBOLS:
        mapping = sources["retail"]
    elif symbol in PCE_SYMBOLS:
        mapping = sources["bea"]["pce"]
    elif symbol in GDP_SYMBOLS:
        mapping = sources["bea"]["gdp"]
    elif symbol in ISM_MANUFACTURING_SYMBOLS:
        mapping = {reference: ism_release(reference, 1) for reference in dates}
    elif symbol in ISM_SERVICES_SYMBOLS:
        mapping = {reference: ism_release(reference, 3) for reference in dates}
    elif symbol in EFFR_SYMBOLS:
        mapping = sources["effr"]
        effective_dates = dates if len(set(dates)) > table.num_rows * 0.9 else [value.date() for value in old_ts]
        keep = [effective in mapping for effective in effective_dates]
        new_dates = effective_dates
    else:
        raise RuntimeError(f"No release-time source configured for {symbol}")

    missing = sorted({reference for reference, include in zip(new_dates, keep) if include and reference not in mapping})
    if missing:
        raise RuntimeError(f"Missing {len(missing)} release timestamps for {symbol}: {missing[:12]}")

    indices = [index for index, include in enumerate(keep) if include]
    result = table.take(pa.array(indices, type=pa.int64())) if len(indices) != table.num_rows else table
    result_dates = [new_dates[index] for index in indices]
    release_ts = [mapping[new_dates[index]] for index in indices]
    result = replace_column(result, "date", result_dates, pa.date32())

    availability_mode = "original_release"
    availability_ts = release_ts

    result = replace_column(result, "ts", availability_ts, pa.timestamp("ms", tz="UTC"))

    if availability_ts != sorted(availability_ts):
        raise RuntimeError(f"Availability timestamps are not sorted for {symbol}")
    if any(ts.date() < reference for ts, reference in zip(release_ts, result_dates)):
        raise RuntimeError(f"Release precedes reference period for {symbol}")
    return result, {
        "symbol": symbol,
        "before": table.num_rows,
        "after": result.num_rows,
        "dropped": table.num_rows - result.num_rows,
        "simultaneous_releases": len(release_ts) - len(set(release_ts)),
        "availability_mode": availability_mode,
        "first_ts": availability_ts[0].isoformat(),
        "last_ts": availability_ts[-1].isoformat(),
    }


def atomic_write(path: Path, table: pa.Table) -> None:
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False)
    temp_path = Path(handle.name)
    handle.close()
    try:
        pq.write_table(table, temp_path, compression="snappy", version="2.6")
        pq.read_table(temp_path)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="rewrite Parquet files atomically")
    parser.add_argument("--backup-dir", type=Path, help="copy originals here before applying")
    args = parser.parse_args()

    tables = load_tables()
    if not tables:
        raise SystemExit(f"No Parquet files found in {ECONOMICS_DIR}")
    sources = build_sources(tables)
    corrected = {}
    report = []
    for path, table in tables.items():
        corrected[path], item = corrected_table(path, table, sources)
        report.append(item)

    if args.apply:
        if args.backup_dir:
            args.backup_dir.mkdir(parents=True, exist_ok=False)
            for path in tables:
                shutil.copy2(path, args.backup_dir / path.name)
        for path, table in corrected.items():
            atomic_write(path, table)

    print(json.dumps({"applied": args.apply, "files": report}, indent=2))


if __name__ == "__main__":
    main()
