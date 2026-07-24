#!/usr/bin/env python3
"""Quote and download retained Databento historical files.

Downloads are always stored below ``data_manipulation/databento`` and are
never removed by this script. A batch job is only submitted with --submit,
after the metadata quote passes --max-cost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db
from databento.common.error import BentoClientError


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "databento"

NEW_YORK = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
# Safety bound so a bad --end never spins the backward scan forever.
MAX_SCAN_DAYS = 500


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quote or download Databento MBP-10 data")
    parser.add_argument("--symbol", default="NQ.c.0")
    parser.add_argument("--start", help="inclusive ISO-8601 UTC timestamp (optional in --rth budget scan)")
    parser.add_argument("--end", required=True, help="exclusive ISO-8601 UTC timestamp")
    parser.add_argument("--max-cost", type=float, default=125.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--submit", action="store_true", help="submit the billable batch job")
    parser.add_argument("--job-id", help="download an existing job without submitting another")
    parser.add_argument(
        "--rth",
        action="store_true",
        help="fetch New York regular trading hours (09:30-16:00 ET) only, one request per trading day",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=4,
        help="parallel download threads while RTH batch jobs finish building",
    )
    return parser.parse_args()


def parse_day(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def rth_window_utc(day: date) -> tuple[str, str]:
    """Return the [start, end) UTC ISO strings for one New York RTH session."""
    open_utc = datetime.combine(day, RTH_OPEN, NEW_YORK).astimezone(timezone.utc)
    close_utc = datetime.combine(day, RTH_CLOSE, NEW_YORK).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return open_utc.strftime(fmt), close_utc.strftime(fmt)


def rth_days(start_day: date, end_day: date) -> list[date]:
    """Weekday sessions in [start_day, end_day). Holidays cost ~nothing and are harmless."""
    days: list[date] = []
    day = start_day
    while day < end_day:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def cost_for(client: db.Historical, base: dict, start: str, end: str) -> float:
    return float(client.metadata.get_cost(**base, start=start, end=end))


def downloaded_days(output: Path) -> set[str]:
    """Return YYYYMMDD keys already present on disk (e.g. glbx-mdp3-20260304.mbp-10.dbn.zst)."""
    keys: set[str] = set()
    for path in output.rglob("*.dbn.zst"):
        match = re.search(r"(\d{8})", path.name)
        if match:
            keys.add(match.group(1))
    return keys


def is_no_data(error: BentoClientError) -> bool:
    """A market holiday returns 422 no-data from submit_job even though get_cost is 0."""
    text = str(error).lower()
    return "no_data" in text or "no data was found" in text


def existing_rth_jobs(client: db.Historical, base: dict, wanted: dict[str, date]) -> dict[str, str]:
    """Map a window-start ISO string -> live job id for jobs already submitted.

    Lets a re-run reuse (and download) a queued/processing/done job instead of
    paying to submit the same RTH day twice. Failed/expired jobs are ignored.
    """
    reuse: dict[str, str] = {}
    for job in client.batch.list_jobs():
        if job.get("dataset") != base["dataset"] or job.get("schema") != base["schema"]:
            continue
        if job.get("state") in {"expired", "failed"}:
            continue
        # Job starts arrive as '2026-03-18T13:30:00.000000000Z'; our window keys
        # are 'YYYY-MM-DDTHH:MM:SS'. Compare on the first 19 chars only.
        key = str(job.get("start") or "")[:19]
        if key in wanted and key not in reuse:
            reuse[key] = job["id"]
    return reuse


def wait_for_job(client: db.Historical, job_id: str) -> dict:
    while True:
        job = client.batch.get_job_details(job_id)
        state = job["state"]
        print(f"Job {job_id}: {state} ({job.get('progress')}%)", flush=True)
        if state == "done":
            return job
        if state in {"expired", "failed"}:
            raise RuntimeError(f"Databento job ended in state {state}: {job}")
        time.sleep(5)


def run_rth(client: db.Historical, args: argparse.Namespace, base: dict) -> int:
    """Quote/submit New York RTH-only data, one request per trading day.

    With --start: fetch every RTH session in [start, end).
    Without --start: walk backward from --end, accreting whole RTH sessions until
    the running total would reach --max-cost, and report the earliest reachable day.
    """
    end_day = parse_day(args.end)
    if args.start:
        days = rth_days(parse_day(args.start), end_day)
    else:
        days = []
        day = end_day - timedelta(days=1)
        scanned = 0
        total = 0.0
        while scanned < MAX_SCAN_DAYS:
            scanned += 1
            if day.weekday() < 5:
                start, end = rth_window_utc(day)
                cost = cost_for(client, base, start, end)
                if total + cost >= args.max_cost:
                    break
                total += cost
                days.append(day)
            day -= timedelta(days=1)
        days.reverse()

    if not days:
        raise SystemExit("No trading days selected; check --start/--end.")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    have = downloaded_days(output)
    todo = [d for d in days if d.strftime("%Y%m%d") not in have]
    skipped = len(days) - len(todo)
    if skipped:
        print(f"Skipping {skipped} session(s) already present on disk.", flush=True)
    if not todo:
        print("Every requested RTH session is already downloaded; nothing to do.")
        return 0

    # Zero-cost days are market holidays (no RTH session); drop them so submit
    # never trips over a 422 no-data response.
    sessions = []
    holidays = 0
    total = 0.0
    for d in todo:
        start, end = rth_window_utc(d)
        cost = cost_for(client, base, start, end)
        if cost <= 0:
            holidays += 1
            continue
        sessions.append((d, start, end))
        total += cost
    if holidays:
        print(f"Skipping {holidays} market holiday(s) with no RTH data.", flush=True)
    if not sessions:
        print("No billable RTH sessions remain; nothing to do.")
        return 0
    print(
        json.dumps(
            {
                "mode": "rth",
                "trading_days": len(sessions),
                "already_on_disk": skipped,
                "holidays_skipped": holidays,
                "earliest_session": sessions[0][0].isoformat(),
                "latest_session": sessions[-1][0].isoformat(),
                "estimated_cost_usd": round(total, 2),
                "avg_cost_per_day_usd": round(total / len(sessions), 4),
                "rth_window_et": "09:30-16:00 America/New_York",
            },
            indent=2,
        )
    )
    if total >= args.max_cost:
        raise SystemExit(
            f"Refusing request: ${total:.2f} is not below the ${args.max_cost:.2f} cap"
        )
    if not args.submit:
        print("Quote only. Pass --submit to create the billable per-day batch jobs.")
        return 0

    # Reuse any job already submitted for these RTH windows so a re-run downloads
    # it instead of paying to submit the same day again.
    reuse = existing_rth_jobs(client, base, {start: d for d, start, _ in sessions})

    # Pipeline: submit every remaining job up front so Databento builds them in
    # parallel, then poll and download each as soon as it is ready.
    pending: dict[str, date] = {}
    for d, start, end in sessions:
        if start in reuse:
            job_id = reuse[start]
            pending[job_id] = d
            print(f"[{d.isoformat()}] reusing existing job {job_id}", flush=True)
            continue
        try:
            job = client.batch.submit_job(
                **base,
                start=start,
                end=end,
                encoding="dbn",
                compression="zstd",
                split_duration="day",
                delivery="download",
            )
        except BentoClientError as error:
            if is_no_data(error):
                print(f"[{d.isoformat()}] no data (holiday); skipping", flush=True)
                continue
            raise
        pending[job["id"]] = d
        print(f"[{d.isoformat()}] submitted job {job['id']}", flush=True)

    def fetch(job_id: str, day: date) -> tuple[date, int]:
        paths = client.batch.download(job_id=job_id, output_dir=output)
        return day, len(paths)

    downloads: dict[Future, str] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.download_workers)) as pool:
        while pending or downloads:
            for job_id in list(pending):
                state = client.batch.get_job_details(job_id)["state"]
                if state == "done":
                    day = pending.pop(job_id)
                    downloads[pool.submit(fetch, job_id, day)] = job_id
                elif state in {"expired", "failed"}:
                    day = pending.pop(job_id)
                    print(f"[{day.isoformat()}] job {job_id} ended {state}", flush=True)
            for future in [f for f in downloads if f.done()]:
                downloads.pop(future)
                day, count = future.result()
                completed += 1
                print(
                    f"[{day.isoformat()}] retained {count} files "
                    f"({completed}/{len(sessions)})",
                    flush=True,
                )
            if pending or downloads:
                time.sleep(5)
    print(f"Retained {completed} of {len(sessions)} RTH sessions under {output}")
    return 0


def main() -> int:
    args = parse_args()
    load_env()
    key = os.getenv("DATABENTO_API_KEY")
    if not key:
        raise SystemExit("DATABENTO_API_KEY is missing from data_manipulation/.env")
    client = db.Historical(key)
    base = {
        "dataset": "GLBX.MDP3",
        "symbols": args.symbol,
        "schema": "mbp-10",
        "stype_in": "continuous",
    }

    if args.rth:
        if args.job_id:
            raise SystemExit("--job-id is not supported with --rth (one job per day)")
        return run_rth(client, args, base)

    if not args.start:
        raise SystemExit("--start is required unless using --rth")
    request = {**base, "start": args.start, "end": args.end}
    cost = client.metadata.get_cost(**request)
    size = client.metadata.get_billable_size(**request)
    print(json.dumps({"estimated_cost_usd": cost, "billable_bytes": size, **request}, indent=2))
    if cost >= args.max_cost:
        raise SystemExit(
            f"Refusing request: ${cost:.2f} is not below the ${args.max_cost:.2f} cap"
        )

    if args.job_id:
        job_id = args.job_id
    elif args.submit:
        job = client.batch.submit_job(
            **request,
            encoding="dbn",
            compression="zstd",
            split_duration="day",
            delivery="download",
        )
        job_id = job["id"]
        print(f"Submitted billable job {job_id} at estimated cost ${cost:.2f}", flush=True)
    else:
        print("Quote only. Pass --submit to create the billable batch job.")
        return 0

    wait_for_job(client, job_id)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = client.batch.download(job_id=job_id, output_dir=output)
    print(f"Retained {len(paths)} files under {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
