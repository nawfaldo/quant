"""Build fingerprinted causal top-10 liquidity-line research caches.

The raw tables are read read-only. Output is one gzip JSON cache
per source/session under ``sandbox/.cache`` so interrupted builds resume at
the next missing day without mutating market data.
"""

from __future__ import annotations


import argparse
import gzip
import hashlib
import heapq
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tools.build_nq_l2_features_1s import (
    MAX_PARALLEL_REPLAY_WORKERS,
    depth_events,
    require_wal_drained,
    seed_book,
    table_exists,
    table_rows,
    trade_events,
)
from tools import parquet_writer as pw
from sandbox import data
from sandbox.liquidity_lines import LineAccumulator, SCHEMA_VERSION, compact


@dataclass(frozen=True)
class CacheJob:
    day: date
    source: str
    fingerprint: str
    tick_size: float
    seed_ticks: int


def cache_path(job):
    digest = hashlib.sha1(
        f"v{SCHEMA_VERSION}:{job.source}:{job.day}:{job.fingerprint}".encode()
    ).hexdigest()[:12]
    return Path(data.CACHE_DIR) / (
        f"nq_lines_v{SCHEMA_VERSION}_{job.source}_{job.day}.{digest}.json.gz"
    )


def replay_day(job):
    path = cache_path(job)
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        return str(job.day), len(payload["rows"]), str(path), True

    start = job.day.isoformat()
    end = (job.day + timedelta(days=1)).isoformat()
    depth_table = f"{job.source}_nq_depth"
    tick_table = f"{job.source}_nq_ticks"
    accumulator = LineAccumulator(job.tick_size, levels=10)
    seeded = seed_book(
        depth_table, start, accumulator, job.tick_size, job.seed_ticks,
    )
    rows = []
    with ExitStack() as stack:
        depth = stack.enter_context(table_rows(
            depth_table,
            ["side", "price", "size", "sequence", "stream_id"],
            start,
            end,
        ))
        ticks = stack.enter_context(table_rows(
            tick_table,
            ["side", "price", "size", "trade_sequence", "best_bid", "best_ask",
             "stream_id"],
            start,
            end,
        ))
        for event in heapq.merge(depth_events(depth), trade_events(ticks)):
            rows.extend(compact(job.source, row) for row in accumulator.on_event(event))
        final = accumulator.finish()
        if final is not None:
            rows.append(compact(job.source, final))

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
        json.dump({
            "schema": SCHEMA_VERSION,
            "source": job.source,
            "day": start,
            "fingerprint": job.fingerprint,
            "seeded_levels": seeded,
            "rows": rows,
        }, handle, separators=(",", ":"))
    temporary.replace(path)
    return start, len(rows), str(path), False


def source_fingerprint(source):
    """`min|max|rows` per raw table -- the cache key's data component.

    Read from the Parquet footer, so fingerprinting the five-billion-row depth
    table costs a footer read rather than an aggregate over every event.
    """
    parts = []
    for kind in ("depth", "ticks"):
        table = f"{source}_nq_{kind}"
        span = pw.table_span(table)
        if span is None:
            raise RuntimeError(f"{table} is empty")
        parts.append(f"{table}:{span[0]}|{span[1]}|{span[2]}")
    return ";".join(parts)


def source_days(source, from_day, to_day):
    """Session list from the already-fingerprinted minute feature cache.

    Enumerating days directly from the event tables forces an avoidable scan of
    hundreds of millions of raw rows before replay can begin.
    """
    features = data.load_cached_l2_features("nq")
    days = {
        datetime.fromtimestamp(ts, tz=timezone.utc).date()
        for ts, row in features.items()
        if row.get("source") == source
    }
    return sorted(day for day in days if from_day <= day <= to_day)


def build(args):
    sources = ("dbento", "bm") if args.source == "both" else (args.source,)
    start, end = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    jobs = []
    for source in sources:
        for kind in ("depth", "ticks"):
            table = f"{source}_nq_{kind}"
            if not table_exists(table):
                raise RuntimeError(f"missing {table}")
            require_wal_drained(table)
        fingerprint = source_fingerprint(source)
        jobs.extend(CacheJob(day, source, fingerprint,
                             args.tick_size, args.seed_ticks)
                    for day in source_days(source, start, end))

    workers = min(args.workers, MAX_PARALLEL_REPLAY_WORKERS, max(1, len(jobs)))
    print(f"replaying {len(jobs)} source-sessions with {workers} workers", flush=True)
    completed = cached = rows = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(replay_day, job): job for job in jobs}
        for future in as_completed(futures):
            day, count, path, was_cached = future.result()
            completed += 1
            cached += int(was_cached)
            rows += count
            print(f"{completed}/{len(jobs)} {futures[future].source} {day}: "
                  f"{count:,} seconds{' cached' if was_cached else ''}", flush=True)
    print(f"complete: {rows:,} rows, {cached}/{len(jobs)} sessions reused", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("dbento", "bm", "both"), default="dbento")
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--seed-ticks", type=int, default=200)
    raise SystemExit(build(parser.parse_args()))


if __name__ == "__main__":
    main()
