#!/usr/bin/env python3
"""Scrape options GEX data from InsiderFinance on 30-second OS-clock boundaries.

Saves per-day records to Parquet in the format:
[{date, symbol, spot: [{time, price}], expiry: [{time, date, strike}]}, ...]

Only appends when something on the web page changed:
- spot: appended when the price differs from the last recorded price
- expiry: appended when a new (expiry date, strike) pair appears
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ============================================================================
# SCRAPING
# ============================================================================

def get_ny_timezone() -> Any:
    """Get New York timezone info, compatible with Windows/Linux/macOS without external database requirements."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
        datetime.now(tz)
        return tz
    except Exception:
        class NewYorkTimezone(timezone):
            def __init__(self):
                super().__init__(timedelta(hours=-5), "EST")
            def utcoffset(self, dt: datetime | None) -> timedelta:
                if dt is None:
                    return timedelta(hours=-5)
                year = dt.year
                dst_start = datetime(year, 3, 8, 2, 0)
                dst_start += timedelta(days=(6 - dst_start.weekday()))
                dst_end = datetime(year, 11, 1, 2, 0)
                dst_end += timedelta(days=(6 - dst_end.weekday()))
                naive_dt = dt.replace(tzinfo=None)
                if dst_start <= naive_dt < dst_end:
                    return timedelta(hours=-4)
                return timedelta(hours=-5)
            def tzname(self, dt: datetime | None) -> str:
                return "EDT" if self.utcoffset(dt) == timedelta(hours=-4) else "EST"
            def dst(self, dt: datetime | None) -> timedelta:
                return timedelta(hours=1) if self.utcoffset(dt) == timedelta(hours=-4) else timedelta(0)
        return NewYorkTimezone()

def fetch_insider_gex(symbol: str) -> dict[str, Any] | None:
    """Fetch the page and extract the initialData JSON from __NEXT_DATA__."""
    url = f"https://www.insiderfinance.io/gamma-exposure/{symbol.upper()}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            print(f"[Error] Failed to fetch HTML. Status code: {response.status_code}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[Error] Network request failed: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        print("[Error] __NEXT_DATA__ script tag not found.", file=sys.stderr)
        return None

    try:
        data = json.loads(script.string)
        return data["props"]["pageProps"]["initialData"]
    except Exception as e:
        print(f"[Error] Failed to parse JSON payload: {e}", file=sys.stderr)
        return None

def extract_expiry_strikes(initial_data: dict[str, Any]) -> list[tuple[str, float]]:
    """Extract unique (expiry date, strike) pairs within +/- 30 points of spot."""
    spot = float(initial_data["spot"])
    min_strike = spot - 30.0
    max_strike = spot + 30.0

    pairs: set[tuple[str, float]] = set()
    for opt in initial_data.get("options", []):
        strike = float(opt["strike"])
        if not (min_strike <= strike <= max_strike):
            continue
        expiry = "%d-%02d-%02d" % (opt["expireYear"], opt["expireMonth"], opt["expireDay"])
        pairs.add((expiry, strike))
    return sorted(pairs)

# ============================================================================
# PARQUET STORAGE
# ============================================================================

def load_records(file_path: Path) -> list[dict[str, Any]]:
    """Load existing day records from Parquet, or return an empty list."""
    if not file_path.exists():
        return []
    try:
        df = pd.read_parquet(file_path)
        records = df.to_dict(orient="records")
        for rcd in records:
            rcd["spot"] = [dict(e) for e in rcd["spot"]]
            rcd["expiry"] = [dict(e) for e in rcd["expiry"]]
        return records
    except Exception as e:
        print(f"[Warning] Failed to read existing parquet, starting fresh: {e}", file=sys.stderr)
        return []

def save_records(records: list[dict[str, Any]], file_path: Path) -> bool:
    """Write day records back to Parquet safely via a temporary file."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(records, columns=["date", "symbol", "spot", "expiry"])
        temp_path = file_path.with_name(f"{file_path.name}.tmp")
        df.to_parquet(temp_path, index=False, engine="pyarrow")
        if file_path.exists():
            file_path.unlink()
        temp_path.rename(file_path)
        return True
    except Exception as e:
        print(f"[Error] Failed to write to Parquet: {e}", file=sys.stderr)
        return False

# ============================================================================
# LOOP & PROCESS CYCLE
# ============================================================================

def run_cycle(symbol: str, args: argparse.Namespace) -> None:
    """Scrape the page and append spot/expiry entries when something changed."""
    now = datetime.now(get_ny_timezone())
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    sym = symbol.upper()

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] Fetching GEX data for {sym}...")
    initial_data = fetch_insider_gex(symbol)
    if not initial_data:
        return

    spot = float(initial_data["spot"])
    expiry_strikes = extract_expiry_strikes(initial_data)

    file_path = Path(args.output_dir) / f"{sym}_gex.parquet"
    records = load_records(file_path)

    # Find or create today's record for this symbol
    record = next((r for r in records if r["date"] == date_str and r["symbol"] == sym), None)
    created = False
    if record is None:
        record = {"date": date_str, "symbol": sym, "spot": [], "expiry": []}
        records.append(record)
        created = True

    changed = created

    # Append spot only when the price changed since the last entry
    last_price = record["spot"][-1]["price"] if record["spot"] else None
    if last_price is None or float(last_price) != spot:
        record["spot"].append({"time": time_str, "price": spot})
        changed = True

    # Append only new (expiry date, strike) pairs
    seen = {(e["date"], float(e["strike"])) for e in record["expiry"]}
    new_pairs = [(d, s) for d, s in expiry_strikes if (d, s) not in seen]
    for d, s in new_pairs:
        record["expiry"].append({"time": time_str, "date": d, "strike": s})
    if new_pairs:
        changed = True

    if not changed:
        print(f"No change for {sym}. Skipping save.")
        return

    if save_records(records, file_path):
        print(f"Saved {sym}: spot={spot}, +{len(new_pairs)} new expiry/strike pairs.")

def main() -> int:
    # Force console encoding to UTF-8 on Windows Powershell to prevent print UnicodeEncodeErrors
    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Scrape options GEX data from InsiderFinance on 30-second boundaries and save to Parquet."
    )
    parser.add_argument(
        "--symbol",
        default="SPY",
        help="Ticker to scrape (default: SPY)"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "parquets" / "gex"),
        help="Directory to save the Parquet files (default: parquets/gex)"
    )
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="Run once and exit immediately"
    )

    args = parser.parse_args()

    # Support comma-separated symbols
    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    print("=====================================================")
    print(f"GEX Scraper for {', '.join(symbols)}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Mode: {'Single-run' if args.single_run else 'Continuous (30-second boundaries)'}")
    print("=====================================================")

    if args.single_run:
        for sym in symbols:
            run_cycle(sym, args)
        return 0

    try:
        while True:
            # Sleep until the next 30-second OS-clock boundary (:00 or :30)
            now = time.time()
            time.sleep(30.0 - (now % 30.0))

            for sym in symbols:
                try:
                    run_cycle(sym, args)
                except Exception as e:
                    print(f"[Loop Exception] Cycle failed for {sym} with error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nScraper stopped by user. Exiting...")

    return 0

if __name__ == "__main__":
    sys.exit(main())
