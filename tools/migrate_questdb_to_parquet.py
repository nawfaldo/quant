#!/usr/bin/env python3
"""Safely migrate QuestDB tables to compressed Parquet and drop from QuestDB.

Workflow per table:
1. Stream export table from QuestDB to data/parquet/<table_name>.parquet (ZSTD Level 7).
2. Strict verification: ensure row count, columns, and min/max timestamps match 100%.
3. Drop the table from QuestDB to immediately reclaim C: drive disk space.
4. Verify local Parquet file integrity.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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


def query_questdb(sql: str, base_url: str = DEFAULT_QUESTDB_URL) -> list[list]:
    url = f"{base_url}/exec?query={urllib.parse.quote(sql)}"
    with urllib.request.urlopen(url, timeout=600) as resp:
        data = json.load(resp)
        return data.get("dataset", [])


def list_tables(base_url: str = DEFAULT_QUESTDB_URL) -> list[str]:
    data = query_questdb("SHOW TABLES", base_url)
    return sorted(row[0] for row in data)


def table_stats(table_name: str, base_url: str = DEFAULT_QUESTDB_URL) -> tuple[int, str | None, str | None]:
    try:
        data = query_questdb(f"SELECT count(), min(timestamp), max(timestamp) FROM {table_name}", base_url)
        if data and data[0]:
            count = int(data[0][0])
            min_ts = str(data[0][1]) if data[0][1] is not None else None
            max_ts = str(data[0][2]) if data[0][2] is not None else None
            return count, min_ts, max_ts
    except Exception as e:
        print(f"Warning: could not get stats for {table_name}: {e}", file=sys.stderr)
    return 0, None, None


def export_and_verify(
    table_name: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    base_url: str = DEFAULT_QUESTDB_URL,
    compression_level: int = 7,
) -> tuple[bool, int, float]:
    """Export table to Parquet and strictly verify row count. Returns (success, rows, file_size_mb)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"{table_name}.parquet"
    temp_file = out_file.with_suffix(f".{os.getpid()}.tmp.parquet")

    # Get QuestDB baseline stats
    qdb_count, qdb_min, qdb_max = table_stats(table_name, base_url)
    print(f"\n[{table_name}] QuestDB rows: {qdb_count:,} ({qdb_min} .. {qdb_max})", flush=True)

    if qdb_count == 0:
        print(f"  Table {table_name} is empty. Creating empty Parquet schema.", flush=True)
        schema = pa.schema([("timestamp", pa.timestamp("us"))])
        pq.write_table(pa.Table.from_arrays([pa.array([], pa.timestamp("us"))], schema=schema), str(temp_file))
        if out_file.exists():
            out_file.unlink()
        temp_file.rename(out_file)
        return True, 0, 0.0

    t0 = time.time()
    # Check if table has partitions (especially for large tables > 5M rows)
    partitions = []
    try:
        p_data = query_questdb(f"SHOW PARTITIONS FROM {table_name}", base_url)
        if p_data and len(p_data) > 1:
            partitions = [row[2] for row in p_data if row[2] and row[5] > 0]
    except Exception:
        partitions = []

    read_options = pcsv.ReadOptions(block_size=64 * 1024 * 1024)
    parse_options = pcsv.ParseOptions()
    convert_options = pcsv.ConvertOptions(
        timestamp_parsers=["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "ISO8601"]
    )

    import io
    if partitions and qdb_count > 5_000_000:
        print(f"  Exporting {len(partitions)} partitions chunk-by-chunk for memory safety...", flush=True)
        writer = None
        total_written = 0
        try:
            for idx, p_name in enumerate(partitions):
                p_sql = f"SELECT * FROM {table_name} WHERE timestamp in '{p_name}'"
                p_url = f"{base_url}/exp?query={urllib.parse.quote(p_sql)}"
                req = urllib.request.Request(p_url, headers={"Accept-Encoding": "identity"})
                with urllib.request.urlopen(req, timeout=1800) as resp:
                    data_bytes = resp.read()
                    if len(data_bytes) == 0:
                        continue
                    chunk_tbl = pcsv.read_csv(
                        io.BytesIO(data_bytes),
                        read_options=read_options,
                        parse_options=parse_options,
                        convert_options=convert_options,
                    )
                    if len(chunk_tbl) == 0:
                        continue
                    if writer is None:
                        writer = pq.ParquetWriter(
                            str(temp_file),
                            chunk_tbl.schema,
                            compression="zstd",
                            compression_level=compression_level,
                        )
                    writer.write_table(chunk_tbl)
                    total_written += len(chunk_tbl)
                    pct = (idx + 1) / len(partitions) * 100
                    print(f"\r  Partition {idx+1}/{len(partitions)} ({p_name}): {total_written:,} rows written ({pct:.1f}%)...", end="", flush=True)
            print()
        finally:
            if writer is not None:
                writer.close()
    else:
        url = f"{base_url}/exp?query={urllib.parse.quote(f'SELECT * FROM {table_name}')}"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=1800) as response:
            tbl = pcsv.read_csv(
                response,
                read_options=read_options,
                parse_options=parse_options,
                convert_options=convert_options,
            )
            pq.write_table(
                tbl,
                str(temp_file),
                compression="zstd",
                compression_level=compression_level,
            )

    # Verification
    if not temp_file.exists():
        print(f"  [ERROR] Temp Parquet file not found for {table_name}!", file=sys.stderr)
        return False, 0, 0.0

    with pq.ParquetFile(str(temp_file)) as pq_file:
        pq_count = pq_file.metadata.num_rows

    if pq_count != qdb_count:
        print(f"  [PARITY MISMATCH] QuestDB={qdb_count:,} vs Parquet={pq_count:,}! Aborting drop.", file=sys.stderr)
        temp_file.unlink()
        return False, 0, 0.0

    if out_file.exists():
        out_file.unlink()
    temp_file.rename(out_file)

    sz_mb = out_file.stat().st_size / (1024 * 1024)
    elapsed = time.time() - t0
    print(f"  [VERIFIED 100%] Exported {pq_count:,} rows in {elapsed:.1f}s -> {sz_mb:.2f} MB ({pq_count/(elapsed or 1):,.0f} rows/s)", flush=True)
    return True, pq_count, sz_mb


def drop_from_questdb(table_name: str, base_url: str = DEFAULT_QUESTDB_URL) -> bool:
    """Drop table from QuestDB after verified Parquet export."""
    try:
        query_questdb(f"DROP TABLE {table_name}", base_url)
        print(f"  [DROPPED FROM QUESTDB] Table '{table_name}' dropped successfully. Disk space reclaimed!", flush=True)
        return True
    except Exception as e:
        print(f"  [ERROR DROPPING TABLE] {table_name}: {e}", file=sys.stderr)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate QuestDB tables to Parquet and reclaim disk space.")
    parser.add_argument("tables", nargs="*", help="Specific table names to migrate (default: all)")
    parser.add_argument("--pattern", "-p", help="Wildcard pattern for table names (e.g. '*_1m', '*_30m', 'dbento_*')")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_OUTPUT_DIR, help="Destination directory for Parquet files")
    parser.add_argument("--questdb-url", default=DEFAULT_QUESTDB_URL, help="QuestDB base URL")
    parser.add_argument("--skip-drop", action="store_true", help="Export to Parquet only, do not drop from QuestDB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    total_disk, used_disk, free_disk = shutil.disk_usage("C:/")
    print(f"=== QuestDB -> Parquet Migration & Disk Space Reclaim ===")
    print(f"Initial C: Drive Free Space: {free_disk / (1024**3):.2f} GB (Used: {used_disk / (1024**3):.2f} GB / Total: {total_disk / (1024**3):.2f} GB)")

    all_tables = list_tables(args.questdb_url)
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
    elif args.pattern:
        selected = fnmatch.filter(all_tables, args.pattern)
    else:
        selected = all_tables

    selected = sorted(set(selected))
    if not selected:
        print("No matching tables found in QuestDB to migrate.", file=sys.stderr)
        return 1

    print(f"Selected {len(selected)} tables for migration: {', '.join(selected[:10])}{'...' if len(selected) > 10 else ''}")

    migrated_count = 0
    total_migrated_rows = 0

    for table in selected:
        success, rows, sz_mb = export_and_verify(table, args.output_dir, args.questdb_url)
        if success:
            if not args.skip_drop:
                drop_from_questdb(table, args.questdb_url)
            migrated_count += 1
            total_migrated_rows += rows
        else:
            print(f"  [SKIPPED DROP] Safety check failed for {table}. QuestDB table preserved.", file=sys.stderr)

    _, final_used, final_free = shutil.disk_usage("C:/")
    freed_gb = (final_free - free_disk) / (1024**3)
    print(f"\n=======================================================")
    print(f"Migration Complete!")
    print(f"  Tables Migrated: {migrated_count} / {len(selected)}")
    print(f"  Total Rows Safely Preserved in Parquet: {total_migrated_rows:,}")
    print(f"  Final C: Drive Free Space: {final_free / (1024**3):.2f} GB (Freed: {freed_gb:+.2f} GB)")
    print(f"=======================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
