#!/usr/bin/env python3
"""Scrape options GEX data from InsiderFinance on 30-second OS-clock boundaries.

Saves per-day records to Parquet in the format:
[{date, symbol, spot: [{time, price}], expiry: [{time, expiry_date, net_gex, strike}]}, ...]

Only appends when something changed:
- spot: appended when the price differs from the last recorded price
- expiry: net GEX per (expiry_date, strike) within +/- STRIKE_WINDOW of spot,
  appended when the value moves from the last recorded value for that pair

Also writes a compact <SYMBOL>_gex.json snapshot (spot + 0DTE net GEX by strike)
that the March chart's GEX overlay fetches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def extract_header_metrics(html_text: str) -> tuple[float | None, float | None]:
    """Extract zero_gamma and net_gex header metrics directly from InsiderFinance HTML."""
    zg_match = re.search(r"Zero-Gamma Level:.*?\$([0-9.,]+)", html_text, re.IGNORECASE)
    net_match = re.search(r"Net GEX:.*?\$([0-9.,\-+BMK]+)", html_text, re.IGNORECASE)

    zg = float(zg_match.group(1).replace(",", "")) if zg_match else None

    net_val = None
    if net_match:
        raw_net = net_match.group(1).replace(",", "").strip()
        mult = 1.0
        if "B" in raw_net.upper():
            mult = 1e9
            raw_net = raw_net.upper().replace("B", "")
        elif "M" in raw_net.upper():
            mult = 1e6
            raw_net = raw_net.upper().replace("M", "")
        elif "K" in raw_net.upper():
            mult = 1e3
            raw_net = raw_net.upper().replace("K", "")
        try:
            net_val = float(raw_net) * mult
        except ValueError:
            pass

    return zg, net_val


def fetch_insider_gex(symbol: str) -> dict[str, Any] | None:
    """Fetch the page and extract initialData JSON plus header metrics."""
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
        initial_data = data["props"]["pageProps"]["initialData"]
        zg_header, net_header = extract_header_metrics(response.text)
        initial_data["_zero_gamma_header"] = zg_header
        initial_data["_net_gex_header"] = net_header
        return initial_data
    except Exception as e:
        print(f"[Error] Failed to parse JSON payload: {e}", file=sys.stderr)
        return None

# Strikes are kept within +/- this many points of spot.
STRIKE_WINDOW = 30.0

# Dollar-gamma per 1% move: gamma * openInterest * contractMultiplier * spot^2 * 0.01.
# Calls add positive dealer gamma, puts subtract it.
CONTRACT_MULTIPLIER = 100.0


def option_gex(opt: dict[str, Any], spot: float) -> float:
    """Signed dollar gamma exposure of a single option contract (call +, put -)."""
    try:
        oi = float(opt.get("openInterest") or 0.0)
        gamma = float(opt.get("gamma") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if oi <= 0.0 or gamma == 0.0:
        return 0.0
    value = gamma * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
    return value if opt.get("cp") == "C" else -value


def extract_net_gex(initial_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Net GEX per (expiry_date, strike) within +/- STRIKE_WINDOW points of spot."""
    spot = float(initial_data["spot"])
    min_strike = spot - STRIKE_WINDOW
    max_strike = spot + STRIKE_WINDOW

    totals: dict[tuple[str, float], float] = {}
    for opt in initial_data.get("options", []):
        strike = float(opt["strike"])
        if not (min_strike <= strike <= max_strike):
            continue
        expiry = "%d-%02d-%02d" % (opt["expireYear"], opt["expireMonth"], opt["expireDay"])
        key = (expiry, strike)
        totals[key] = totals.get(key, 0.0) + option_gex(opt, spot)

    return [
        {"expiry_date": expiry, "strike": strike, "net_gex": round(net, 2)}
        for (expiry, strike), net in sorted(totals.items())
    ]


def calculate_zero_gamma(initial_data: dict[str, Any]) -> float | None:
    """Get zero_gamma directly from InsiderFinance header data."""
    zg_header = initial_data.get("_zero_gamma_header")
    if zg_header is not None:
        return zg_header

    # Fallback to sum of strike net GEX if header is missing
    spot = float(initial_data.get("spot") or 0.0)
    options = initial_data.get("options", [])
    if not options or spot <= 0.0:
        return None

    strike_net: dict[float, float] = {}
    for opt in options:
        try:
            stk = float(opt["strike"])
            oi = float(opt.get("openInterest") or 0.0)
            gamma = float(opt.get("gamma") or 0.0)
        except (TypeError, ValueError, KeyError):
            continue

        if oi > 0.0 and gamma != 0.0:
            val = gamma * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
            net = val if opt.get("cp") == "C" else -val
            strike_net[stk] = strike_net.get(stk, 0.0) + net

    return zg_header


def extract_gamma_levels_eod(initial_data: dict[str, Any], date_str: str) -> list[dict[str, Any]]:
    """Extract key EOD gamma levels:
    - zero_gamma: Price level where cumulative net GEX crosses 0 (flip price)
    - gex_1, gex_2, gex_3: Top 3 strikes by absolute net GEX across all expirations
    - high_volume: Strike with highest total open interest across all expirations
    - call_resistance_0dte: 0DTE strike with highest Call GEX overall
    - put_support_0dte: 0DTE strike with highest Put GEX overall
    """
    spot = float(initial_data.get("spot") or 0.0)
    options = initial_data.get("options", [])
    if not options or spot <= 0.0:
        return []

    zero_gamma_price = calculate_zero_gamma(initial_data)

    strike_net_gex: dict[float, float] = {}
    strike_total_oi: dict[float, float] = {}

    expiries_set: set[str] = set()
    for opt in options:
        try:
            stk = float(opt["strike"])
            exp = "%d-%02d-%02d" % (opt["expireYear"], opt["expireMonth"], opt["expireDay"])
            expiries_set.add(exp)
            oi = float(opt.get("openInterest") or 0.0)
            gamma = float(opt.get("gamma") or 0.0)
        except (TypeError, ValueError, KeyError):
            continue

        strike_total_oi[stk] = strike_total_oi.get(stk, 0.0) + oi
        if oi > 0.0 and gamma != 0.0:
            val = gamma * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
            net = val if opt.get("cp") == "C" else -val
            strike_net_gex[stk] = strike_net_gex.get(stk, 0.0) + net

    # Identify 0DTE (nearest future/today expiry)
    expiries = sorted(expiries_set)
    future = [d for d in expiries if d >= date_str]
    target_0dte = future[0] if future else (expiries[0] if expiries else date_str)

    call_gex_0dte: dict[float, float] = {}
    put_gex_0dte: dict[float, float] = {}

    for opt in options:
        try:
            exp = "%d-%02d-%02d" % (opt["expireYear"], opt["expireMonth"], opt["expireDay"])
            if exp != target_0dte:
                continue
            stk = float(opt["strike"])
            oi = float(opt.get("openInterest") or 0.0)
            gamma = float(opt.get("gamma") or 0.0)
        except (TypeError, ValueError, KeyError):
            continue

        if oi > 0.0 and gamma > 0.0:
            gex_val = gamma * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
            if opt.get("cp") == "C":
                call_gex_0dte[stk] = call_gex_0dte.get(stk, 0.0) + gex_val
            elif opt.get("cp") == "P":
                put_gex_0dte[stk] = put_gex_0dte.get(stk, 0.0) + gex_val

    results: list[dict[str, Any]] = []

    # Zero Gamma Level
    if zero_gamma_price is not None:
        results.append({"level_name": "zero_gamma", "strike": zero_gamma_price, "gex": 0.0})

    # Top 3 strikes by absolute net GEX
    sorted_by_abs_gex = sorted(strike_net_gex.items(), key=lambda x: abs(x[1]), reverse=True)
    if len(sorted_by_abs_gex) > 0:
        results.append({"level_name": "gex_1", "strike": sorted_by_abs_gex[0][0], "gex": round(sorted_by_abs_gex[0][1], 2)})
    if len(sorted_by_abs_gex) > 1:
        results.append({"level_name": "gex_2", "strike": sorted_by_abs_gex[1][0], "gex": round(sorted_by_abs_gex[1][1], 2)})
    if len(sorted_by_abs_gex) > 2:
        results.append({"level_name": "gex_3", "strike": sorted_by_abs_gex[2][0], "gex": round(sorted_by_abs_gex[2][1], 2)})

    # high_volume: max total OI strike
    if strike_total_oi:
        top_oi = max(strike_total_oi.items(), key=lambda x: x[1])
        results.append({"level_name": "high_volume", "strike": top_oi[0], "gex": round(top_oi[1], 2)})

    # call_resistance_0dte: largest 0DTE call GEX strike overall
    if call_gex_0dte:
        top_call = max(call_gex_0dte.items(), key=lambda x: x[1])
        results.append({"level_name": "call_resistance_0dte", "strike": top_call[0], "gex": round(top_call[1], 2)})

    # put_support_0dte: largest 0DTE put GEX strike overall
    if put_gex_0dte:
        top_put = max(put_gex_0dte.items(), key=lambda x: x[1])
        results.append({"level_name": "put_support_0dte", "strike": top_put[0], "gex": round(top_put[1], 2)})

    return results

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
            spot_val = rcd.get("spot")
            rcd["spot"] = [dict(e) for e in spot_val] if isinstance(spot_val, (list, tuple)) else []
            exp_val = rcd.get("expiry")
            rcd["expiry"] = [dict(e) for e in exp_val] if isinstance(exp_val, (list, tuple)) else []
            gl_val = rcd.get("gamma_levels")
            rcd["gamma_levels"] = [dict(e) for e in gl_val] if isinstance(gl_val, (list, tuple)) else []
            zg_val = rcd.get("zero_gamma")
            rcd["zero_gamma"] = [dict(e) for e in zg_val] if isinstance(zg_val, (list, tuple)) else []
        return records
    except Exception as e:
        print(f"[Warning] Failed to read existing parquet, starting fresh: {e}", file=sys.stderr)
        return []

def write_frontend_json(
    sym: str,
    date_str: str,
    spot: float,
    zero_gamma: float | None,
    net_gex_levels: list[dict[str, Any]],
    gamma_levels: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    """Write the compact snapshot the March GEX overlay fetches (<SYM>_gex.json)."""
    if net_gex_levels:
        strike_totals: dict[float, float] = {}
        for lvl in net_gex_levels:
            stk = float(lvl["strike"])
            strike_totals[stk] = strike_totals.get(stk, 0.0) + float(lvl["net_gex"])
        levels = [
            {"strike": stk, "net_gex": round(net, 2)}
            for stk, net in sorted(strike_totals.items())
        ]
    else:
        levels = []

    payload = {
        "symbol": sym,
        "date": date_str,
        "spot_price": spot,
        "zero_gamma": zero_gamma,
        "expiry_date": "all",
        "levels": levels,
        "gamma_levels": gamma_levels,
    }
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{sym}_gex.json"
        temp_path = json_path.with_name(f"{json_path.name}.tmp")
        temp_path.write_text(json.dumps(payload), encoding="utf-8")
        temp_path.replace(json_path)
    except Exception as e:
        print(f"[Error] Failed to write frontend JSON for {sym}: {e}", file=sys.stderr)


def save_records(records: list[dict[str, Any]], file_path: Path) -> bool:
    """Write day records back to Parquet safely via a temporary file."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(records, columns=["date", "symbol", "spot", "expiry", "gamma_levels", "zero_gamma"])
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
    """Scrape the page and append spot/expiry/gamma_levels/zero_gamma entries when something changed."""
    now = datetime.now(get_ny_timezone())
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    sym = symbol.upper()

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] Fetching GEX data for {sym}...")
    initial_data = fetch_insider_gex(symbol)
    if not initial_data:
        return

    spot = float(initial_data["spot"])
    zero_gamma_price = calculate_zero_gamma(initial_data)
    net_gex_levels = extract_net_gex(initial_data)
    gamma_levels = extract_gamma_levels_eod(initial_data, date_str)

    file_path = Path(args.output_dir) / f"{sym}_gex.parquet"
    records = load_records(file_path)

    # Migrate legacy record format if needed
    for rec in records:
        if rec.get("expiry") and "net_gex" not in rec["expiry"][0]:
            rec["expiry"] = []
        if "gamma_levels" not in rec:
            rec["gamma_levels"] = []
        if "zero_gamma" not in rec:
            rec["zero_gamma"] = []

    # Find or create today's record for this symbol
    record = next((r for r in records if r["date"] == date_str and r["symbol"] == sym), None)
    created = False
    if record is None:
        record = {
            "date": date_str,
            "symbol": sym,
            "spot": [],
            "expiry": [],
            "gamma_levels": [],
            "zero_gamma": [],
        }
        records.append(record)
        created = True
    else:
        for k in ["gamma_levels", "zero_gamma"]:
            if k not in record:
                record[k] = []

    changed = created

    # Append spot only when the price changed
    last_price = record["spot"][-1]["price"] if record["spot"] else None
    if last_price is None or float(last_price) != spot:
        record["spot"].append({"time": time_str, "price": spot})
        changed = True

    # Append zero_gamma only when it changed
    if zero_gamma_price is not None:
        last_zg = record["zero_gamma"][-1]["price"] if record["zero_gamma"] else None
        if last_zg is None or float(last_zg) != zero_gamma_price:
            record["zero_gamma"].append({"time": time_str, "price": zero_gamma_price})
            changed = True

    # Append net_gex levels per (expiry_date, strike) only when it moved
    last_net: dict[tuple[str, float], float] = {}
    for e in record["expiry"]:
        last_net[(e["expiry_date"], float(e["strike"]))] = float(e["net_gex"])
    for level in net_gex_levels:
        key = (level["expiry_date"], float(level["strike"]))
        if last_net.get(key) != level["net_gex"]:
            record["expiry"].append(
                {
                    "time": time_str,
                    "expiry_date": level["expiry_date"],
                    "net_gex": level["net_gex"],
                    "strike": level["strike"],
                }
            )
            changed = True

    # Append gamma_levels per level_name only when the strike moved
    last_gamma_strike: dict[str, float] = {}
    for g in record.get("gamma_levels", []):
        last_gamma_strike[g["level_name"]] = float(g["strike"])
    for gl in gamma_levels:
        lvl_name = gl["level_name"]
        if last_gamma_strike.get(lvl_name) != gl["strike"]:
            record["gamma_levels"].append(
                {
                    "time": time_str,
                    "level_name": lvl_name,
                    "strike": gl["strike"],
                    "gex": gl["gex"],
                }
            )
            changed = True

    # Always refresh the frontend snapshot JSON
    write_frontend_json(
        sym,
        date_str,
        spot,
        zero_gamma_price,
        net_gex_levels,
        gamma_levels,
        Path(args.output_dir),
    )

    if not changed:
        print(f"No change for {sym}. Skipping parquet save.")
        return

    if save_records(records, file_path):
        print(f"Saved {sym}: spot={spot}, {len(net_gex_levels)} net-GEX levels, {len(gamma_levels)} EOD gamma levels.")

def main() -> int:
    # Force console encoding to UTF-8 on Windows Powershell to prevent print UnicodeEncodeErrors
    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Scrape options GEX data from InsiderFinance on customizable boundaries (default 1s) and save to Parquet."
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
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5.0)"
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
    print(f"Mode: {'Single-run' if args.single_run else f'Continuous ({args.interval}s interval)'}")
    print("=====================================================")

    def safe_run_cycle(sym: str) -> None:
        try:
            run_cycle(sym, args)
        except Exception as e:
            import traceback
            print(f"[Loop Exception] Cycle failed for {sym}: {e}", file=sys.stderr)
            traceback.print_exc()

    if args.single_run:
        with ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as executor:
            futures = [executor.submit(safe_run_cycle, sym) for sym in symbols]
            for f in as_completed(futures):
                f.result()
        return 0

    try:
        while True:
            interval = max(0.1, args.interval)
            now = time.time()
            time.sleep(max(0.01, interval - (now % interval)))

            with ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as executor:
                futures = [executor.submit(safe_run_cycle, sym) for sym in symbols]
                for f in as_completed(futures):
                    f.result()
    except KeyboardInterrupt:
        print("\nScraper stopped by user. Exiting...")

    return 0

if __name__ == "__main__":
    sys.exit(main())
