#!/usr/bin/env python3
"""Batch runner to fetch and import daily candle data for a CSV list of stocks into QuestDB year-by-year."""

from __future__ import annotations

import argparse
import csv
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch daily candle fetch & import into QuestDB, processed year-by-year."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Path to CSV containing stock codes",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2018,
        help="Starting year (default: 2018)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Ending year (default: current year)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay in seconds between fetching symbols (default: 2.0)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    csv_path = args.csv.expanduser()
    if not csv_path.is_file():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    # Read stock codes from CSV
    stock_codes: list[str] = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("Kode Saham", "").strip()
            if code:
                stock_codes.append(code)

    if not stock_codes:
        print("Error: No stock codes found in CSV file.", file=sys.stderr)
        return 1

    now = datetime.now()
    current_year = now.year
    start_year = args.start_year
    end_year = args.end_year if args.end_year is not None else current_year

    if start_year > end_year:
        print(f"Error: start_year ({start_year}) cannot be greater than end_year ({end_year}).", file=sys.stderr)
        return 1

    script_dir = Path(__file__).parent
    daily_script = script_dir / "yfinance_fetch&import_daily.py"
    if not daily_script.is_file():
        print(f"Error: Daily fetch script not found: {daily_script}", file=sys.stderr)
        return 1

    print("==================================================")
    print(f"Starting year-by-year daily import for {len(stock_codes)} stocks...")
    print(f"Year range: {start_year} to {end_year}")
    print("==================================================\n")

    overall_failed: list[tuple[int, str, str]] = []

    for year in range(start_year, end_year + 1):
        year_short = year % 100
        start_date_str = f"01/01/{year_short:02d}"

        if year < current_year:
            end_date_str = f"31/12/{year_short:02d}"
        else:
            end_date_str = now.strftime("%d/%m/%y")

        date_range = f"{start_date_str}-{end_date_str}"

        print("--------------------------------------------------")
        print(f"--- Processing Year {year} ({date_range}) for {len(stock_codes)} stocks ---")
        print("--------------------------------------------------\n")

        successful: list[str] = []
        failed: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []

        for idx, code in enumerate(stock_codes, 1):
            symbol = f"{code.upper()}.JK"
            table_prefix = code.lower()
            print(f"[{year}] [{idx}/{len(stock_codes)}] Fetching & importing {symbol} ({date_range})...")

            cmd = [
                sys.executable,
                str(daily_script),
                "--symbol", symbol,
                "--table-prefix", table_prefix,
                "--timezone", "Asia/Jakarta",
                "--date", date_range,
            ]

            res = subprocess.run(cmd, capture_output=True, text=True)
            err_msg = (res.stderr.strip() or res.stdout.strip())
            is_no_data = (
                "YFPricesMissingError" in err_msg
                or "no daily candles" in err_msg
                or "no price data found" in err_msg
                or "Data doesn't exist" in err_msg
            )

            if res.returncode == 0:
                print(f"✅ [{year}] [{idx}/{len(stock_codes)}] {symbol} completed successfully.")
                successful.append(symbol)
            elif is_no_data:
                print(f"⚠️ [{year}] [{idx}/{len(stock_codes)}] {symbol} skipped (no data / pre-IPO for {year}).")
                skipped.append((symbol, "No data / pre-IPO"))
            else:
                short_err = err_msg.split('\n')[0][:120]
                print(f"❌ [{year}] [{idx}/{len(stock_codes)}] {symbol} failed: {short_err}")
                failed.append((symbol, err_msg))
                overall_failed.append((year, symbol, err_msg))

            if idx < len(stock_codes):
                time.sleep(args.delay)
            print()

        skipped_info = f", {len(skipped)} skipped (pre-IPO)" if skipped else ""
        print(f"--- Year {year} Summary: {len(successful)} successful{skipped_info}, {len(failed)} failed out of {len(stock_codes)} stocks. ---\n")

    print("==================================================")
    print(f"All years ({start_year}-{end_year}) completed.")
    print("==================================================")
    if overall_failed:
        print(f"\nFailed entries ({len(overall_failed)} total):")
        for yr, sym, err in overall_failed:
            print(f" - [{yr}] {sym}: {err}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

