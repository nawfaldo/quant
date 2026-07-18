#!/usr/bin/env python3
"""Scrape options GEX data from InsiderFinance on a 1-minute cycle and save to Parquet."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ============================================================================
# SCRAPING & DATA PROCESSING
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

def generate_gex_flat(initial_data: dict[str, Any]) -> pd.DataFrame | None:
    """Process raw option contracts into a flat GEX DataFrame (Strikes x Expiries) within +/- 30 points of spot."""
    spot = float(initial_data["spot"])
    options = initial_data.get("options", [])
    if not options:
        return None
        
    # Filter options to +/- 30 points from spot
    min_strike = spot - 30.0
    max_strike = spot + 30.0
    
    # Group contracts by (strike, expiry)
    records = {}
    for opt in options:
        strike = float(opt["strike"])
        if not (min_strike <= strike <= max_strike):
            continue
            
        expiry = "%d-%02d-%02d" % (opt["expireYear"], opt["expireMonth"], opt["expireDay"])
        cp = opt["cp"]
        gamma = float(opt["gamma"])
        oi = float(opt["openInterest"])
        
        key = (strike, expiry)
        if key not in records:
            records[key] = {
                "expiry": expiry,
                "strike": strike,
                "call_oi": 0.0,
                "put_oi": 0.0,
                "call_gamma": 0.0,
                "put_gamma": 0.0,
            }
            
        if cp == "C":
            records[key]["call_oi"] = oi
            records[key]["call_gamma"] = gamma
        else:
            records[key]["put_oi"] = oi
            records[key]["put_gamma"] = gamma
            
    # Calculate GEX USD Notional (1% move) for each cell
    flat_data = []
    for rcd in records.values():
        call_gex_shares = rcd["call_gamma"] * rcd["call_oi"] * 100.0
        put_gex_shares = rcd["put_gamma"] * rcd["put_oi"] * 100.0
        net_gex_shares = call_gex_shares - put_gex_shares
        net_gex_usd_1pct = net_gex_shares * spot * spot * 0.01
        
        flat_data.append({
            "expiry": rcd["expiry"],
            "strike": rcd["strike"],
            "net_gex": net_gex_usd_1pct,
        })
        
    return pd.DataFrame(flat_data)

# ============================================================================
# PARQUET INTEGRATION
# ============================================================================

def write_to_parquet(
    symbol: str,
    spot: float,
    df: pd.DataFrame,
    timestamp: datetime,
    output_dir: Path
) -> bool:
    """Write or append option-level GEX data to a Parquet file."""
    try:
        # Filter and construct the rows
        clean_df = df.dropna(subset=["net_gex"]).copy()
        if clean_df.empty:
            return True
            
        clean_df["timestamp"] = timestamp
        clean_df["symbol"] = symbol.upper()
        clean_df["spot"] = spot
        
        # Ensure correct column types
        clean_df["timestamp"] = pd.to_datetime(clean_df["timestamp"])
        clean_df["symbol"] = clean_df["symbol"].astype(str)
        clean_df["expiry"] = clean_df["expiry"].astype(str)
        clean_df["spot"] = clean_df["spot"].astype(float)
        clean_df["strike"] = clean_df["strike"].astype(float)
        clean_df["net_gex"] = clean_df["net_gex"].astype(float)
        
        # Reorder columns
        columns_order = ["timestamp", "symbol", "expiry", "spot", "strike", "net_gex"]
        clean_df = clean_df[columns_order]
        
        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{symbol.upper()}_gex.parquet"
        
        if file_path.exists():
            # Read existing parquet file
            try:
                existing_df = pd.read_parquet(file_path)
                # Concatenate existing and new data
                combined_df = pd.concat([existing_df, clean_df], ignore_index=True)
                # Drop duplicates if any (based on key identifiers)
                combined_df = combined_df.drop_duplicates(
                    subset=["timestamp", "symbol", "expiry", "strike"],
                    keep="last"
                )
            except Exception as e:
                print(f"[Warning] Failed to read existing parquet, overwriting: {e}", file=sys.stderr)
                combined_df = clean_df
        else:
            combined_df = clean_df
            
        # Write to parquet safely using a temporary file first
        temp_path = file_path.with_name(f"{file_path.name}.tmp")
        combined_df.to_parquet(temp_path, index=False, engine="pyarrow")
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

def run_cycle(symbol: str, last_processed: dict[str, str], args: argparse.Namespace) -> None:
    """Scrape and save GEX data to Parquet, ensuring no minute overlap."""
    timestamp = datetime.now(get_ny_timezone())
    
    # Align to minute boundary to prevent overlap
    timestamp_minute = timestamp.replace(second=0, microsecond=0)
    current_minute_str = timestamp_minute.strftime("%Y-%m-%d %H:%M")
    
    # Avoid duplicate writes for the same clock minute
    if last_processed.get(symbol.upper()) == current_minute_str:
        print(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}] Already processed {symbol.upper()} for minute {current_minute_str}. Skipping to avoid overlap.")
        return
        
    print(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}] Fetching GEX data for {symbol.upper()}...")
    initial_data = fetch_insider_gex(symbol)
    if not initial_data:
        return
        
    flat_df = generate_gex_flat(initial_data)
    if flat_df is None or flat_df.empty:
        print("[Error] No options data found to compute GEX.", file=sys.stderr)
        return
        
    spot = float(initial_data["spot"])
    
    print(f"Saving {len(flat_df)} GEX rows to Parquet for timestamp {current_minute_str}...")
    output_dir = Path(args.output_dir)
    success = write_to_parquet(symbol, spot, flat_df, timestamp_minute, output_dir)
    if success:
        last_processed[symbol.upper()] = current_minute_str
        print(f"Successfully saved GEX data for {symbol.upper()}.")

def main() -> int:
    # Force console encoding to UTF-8 on Windows Powershell to prevent print UnicodeEncodeErrors
    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
            
    parser = argparse.ArgumentParser(
        description="Scrape options GEX data from InsiderFinance on a 1-minute cycle and save to Parquet."
    )
    parser.add_argument(
        "--symbol",
        default="SPY",
        help="Ticker to scrape (default: SPY)"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "data"),
        help="Directory to save the Parquet files (default: data_manipulation/data)"
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
    print(f"Mode: {'Single-run' if args.single_run else 'Continuous (1-minute cycle)'}")
    print("=====================================================")
    
    last_processed: dict[str, str] = {}
    
    if args.single_run:
        for sym in symbols:
            run_cycle(sym, last_processed, args)
        return 0
        
    try:
        while True:
            # Sleep until the next clock-minute boundary
            now = time.time()
            sleep_time = 60.0 - (now % 60.0)
            time.sleep(sleep_time)
            
            for sym in symbols:
                try:
                    run_cycle(sym, last_processed, args)
                except Exception as e:
                    print(f"[Loop Exception] Cycle failed for {sym} with error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nScraper stopped by user. Exiting...")
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
