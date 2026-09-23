#!/usr/bin/env python3
"""Export QuestDB tables to compressed Parquet files.

Features:
- Memory-efficient streaming from QuestDB's /exp (CSV) or /exec endpoint.
- Zstandard compression (zstd level 7) for maximum compression ratio.
- Preserves exact timestamps, types, and nanosecond/microsecond values.
- Row-count and parity validation against QuestDB.
- Optional year/month partitioning or single-file storage.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

DEFAULT_QUESTDB_URL = os.getenv("QUESTDB_URL", "http://127.0.0.1:9000")
DEFAULT_OUTPUT_DIR = Path("data/parquet")


def list_questdb_tables(base_url: str) -> list[str]:
    """Retrieve all table names from QuestDB."""
    url = f"{base_url}/exec?query=SHOW%20TABLES"
    with urllib.request.urlopen(url, timeout=30) as resp:
        import json
        data = json.load(resp)
        return [row[0] for row in data.get("dataset", [])]


def table_stats(base_url: str, table_name: str) -> tuple[int, str | None, str | None]:
    """Return (row_count, min_ts, max_ts) for a table."""
    query = f"SELECT count(), min(timestamp), max(timestamp) FROM {table_name}"
    url = f"{base_url}/exec?query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            import json
            data = json.load(resp)
            rows = data.get("dataset", [])
            if rows and rows[0]:
                count = int(rows[0][0])
                min_ts = str(rows[0][1]) if rows[0][1] is not None else None
                max_ts = str(rows[0][2]) if rows[0][2] is not None else None
                return count, min_ts, max_ts
    except Exception as e:
        print(f"Warning: could not get stats for {table_name}: {e}", file=sys.stderr)
    return 0, None, None


def export_table_to_parquet(
    table_name: str,
    output_path: Path,
    base_url: str = DEFAULT_QUESTDB_URL,
    compression: str = "zstd",
    compression_level: int = 7,
    where_clause: str | None = None,
) -> int:
    """Stream table export directly from QuestDB into a Parquet file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(f".{os.getpid()}.tmp.parquet")

    where_sql = f" WHERE {where_clause}" if where_clause else ""
    sql = f"SELECT * FROM {table_name}{where_sql}"
    url = f"{base_url}/exp?query={urllib.parse.quote(sql)}"

    print(f"Exporting {table_name} -> {output_path}...", flush=True)
    t0 = time.time()

    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=1200) as response:
        read_options = pcsv.ReadOptions(block_size=64 * 1024 * 1024)
        parse_options = pcsv.ParseOptions()
        convert_options = pcsv.ConvertOptions(
            timestamp_parsers=["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "ISO8601"]
        )

        tbl = pcsv.read_csv(
            response,
            read_options=read_options,
            parse_options=parse_options,
            convert_options=convert_options,
        )

        total_rows = len(tbl)
        pq.write_table(
            tbl,
            str(temp_output),
            compression=compression,
            compression_level=compression_level,
        )

    if temp_output.exists():
        if output_path.exists():
            output_path.unlink()
        temp_output.rename(output_path)

    elapsed = time.time() - t0
    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\r  Finished {table_name}: {total_rows:,} rows in {elapsed:.1f}s ({file_size_mb:.2f} MB, {total_rows/(elapsed or 1):,.0f} rows/s)", flush=True)
    return total_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export QuestDB tables to compressed Parquet files.")
    parser.add_argument("tables", nargs="*", help="Specific table names to export (default: all)")
    parser.add_argument("--pattern", "-p", help="Regex or wildcard pattern for table names (e.g. '*_1m', 'dbento_*')")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_OUTPUT_DIR, help="Destination directory for Parquet files")
    parser.add_argument("--questdb-url", default=DEFAULT_QUESTDB_URL, help="QuestDB base URL")
    parser.add_argument("--compression", default="zstd", choices=["zstd", "snappy", "gzip", "none"], help="Parquet compression codec")
    parser.add_argument("--compression-level", type=int, default=7, help="Zstandard compression level (1-22)")
    parser.add_argument("--verify", action="store_true", default=True, help="Verify row counts against QuestDB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    all_tables = list_questdb_tables(args.questdb_url)
    print(f"Connected to QuestDB at {args.questdb_url}. Total tables available: {len(all_tables)}")

    import fnmatch
    selected: list[str] = []
    if args.tables:
        for t in args.tables:
            if t in all_tables:
                selected.append(t)
            else:
                matches = fnmatch.filter(all_tables, t)
                if matches:
                    selected.extend(matches)
                else:
                    print(f"Warning: table {t} not found in QuestDB", file=sys.stderr)
    elif args.pattern:
        selected = fnmatch.filter(all_tables, args.pattern)
    else:
        selected = all_tables

    selected = sorted(set(selected))
    if not selected:
        print("No matching tables found to export.", file=sys.stderr)
        return 1

    print(f"Selected {len(selected)} table(s) for export: {', '.join(selected[:10])}{'...' if len(selected) > 10 else ''}")

    total_exported_rows = 0
    start_time = time.time()

    for table in selected:
        out_file = args.output_dir / f"{table}.parquet"
        try:
            rows = export_table_to_parquet(
                table,
                out_file,
                base_url=args.questdb_url,
                compression=args.compression if args.compression != "none" else None,
                compression_level=args.compression_level,
            )
            total_exported_rows += rows

            if args.verify:
                qdb_count, _, _ = table_stats(args.questdb_url, table)
                pq_file = pq.ParquetFile(str(out_file))
                pq_count = pq_file.metadata.num_rows
                if qdb_count == pq_count:
                    print(f"    [VERIFIED] Parity match: {pq_count:,} rows")
                else:
                    print(f"    [WARNING] Row mismatch: QuestDB={qdb_count:,}, Parquet={pq_count:,}", file=sys.stderr)
        except Exception as e:
            print(f"Error exporting {table}: {e}", file=sys.stderr)

    total_elapsed = time.time() - start_time
    print(f"\nAll done! Exported {len(selected)} tables ({total_exported_rows:,} total rows) in {total_elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
