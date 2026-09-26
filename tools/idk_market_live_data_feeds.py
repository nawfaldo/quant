#!/usr/bin/env python3
"""Keep the canon book's vendor tables live: Dukascopy and Binance.

    py tools/idk_market_live_data_feeds.py

That is the whole interface. No arguments, no modes. It catches every table up
from its own last minute, then keeps them within about a minute and a half of
real time until you stop it.

WHY THE VENDOR TABLES. Every canon sleeve DECIDES on a vendor table --
`gbpjpy_1m` is Dukascopy, `ethusd_1m` is Binance -- and is EXECUTED at Exness.
Measured, filling at broker prices is nearly free (+630.8% against +634.3%) but
re-deciding on them is not (+506.0%). So the vendor series is what has to be
current, and nothing here reads a broker feed.

HOW FAST IT CAN POSSIBLY BE. Dukascopy's own publish lag is 1.3 minutes: at
16:52:16 UTC the newest GBP/JPY minute bar was 16:51:00. That is the floor and
no polling arrangement beats it. Binance is a websocket and is genuinely live.

TWO FEEDS, ONE PROCESS. Dukascopy is polled once a wall-clock minute (it has no
stream); Binance is a streaming child this supervises and restarts. There is no
broker leg any more: `xalusd:gated_fade` was the one sleeve whose SIGNAL came
from Exness and it left the book on 2026-08-29, so every canon table is now a
vendor table and the operator's rule -- no sleeve decides on broker prices --
holds without an exception.

WHAT IT FEEDS IS CHECKED AGAINST THE REGISTRY, NOT TRUSTED. The table below is
hand-written, because a market-data daemon must not depend on the research
module or on MetaTrader to start. `canon_markets` reads the already-built
`exness_live_book` binary instead -- the same registry
`activate_canonical_live.py` copies its rows from -- and REFUSES to start if a
canon market is missing here. That direction is the dangerous one: a sleeve
deciding on a table nobody is updating looks perfectly healthy.

CLOUDFLARE WARP MUST BE ON. dukascopy.com is DNS-blocked on this network. The
preflight refuses to start rather than let it be diagnosed from timeouts while
Binance carries on working and it looks like a partial outage.

IT IS MEANT TO BE RUN UNDER `run_idk_market_feeds.bat`, WHICH RESTARTS IT. Being
down is the one state this must never settle into quietly, because the server's
live safety watchdog flattens every open position after 180 seconds without a
bar and the sleeve is left flat mid-signal. So the exit codes below are an
interface, a stray console interrupt is refused rather than obeyed
(`CONFIRM_STOP_SECONDS`), a wedged loop restarts itself (`STALL_RESTART_SECONDS`),
and every line printed here is also appended to `logs/idk_market_feeds-<day>.log`
so a stall at 11:32 is still diagnosable at midnight.

This script reads market data only. It never sends, changes, or closes an order.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

import dukascopy_python as _dk
import requests
from dukascopy_python import instruments as _ins
from requests.adapters import HTTPAdapter

from parquet_writer import Sender, TimestampNanos, max_timestamp_seconds


UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent

#: Instruments come from the library's OWN constants, never written as strings.
#:
#: NEVER READ AN INSTRUMENT NAME OFF THE PARQUET FILENAMES in
#: the Dukascopy month cache (`parquet_writer.VENDOR_DIR`). Those are written by the importer's `safe_name()`,
#: which rewrites `/` and `-` to `_`, so `GBP/JPY` is archived as `GBP_JPY_...`
#: and `E_Futsee-100` as `E_Futsee_100_...`. Feeding those back fetches nothing
#: and fails INVISIBLY: `dukascopy_python` raises a bare `TypeError`, the month
#: importer's retry loop swallows it, and the run exits 0 saying "already up to
#: date" over a table days stale. Six of these eight were wrong that way once;
#: the only two that worked were the two whose names survive `safe_name`.
#: Resolving through `instruments` turns a library rename into an
#: AttributeError at import instead of a table that quietly stops updating.
#:
#: The tuple is `(instrument, table, session)` where session is the REAL NEW
#: YORK window in minutes past midnight, and may WRAP past midnight. A closed
#: market prints no new bars, so polling one is a request that cannot return
#: anything -- averaged over the day only about three of these are open at once.
#:
#: NEW YORK, NOT THE SHIFTED CLOCK, and the two are not the same table.
#: `cfd_families.SESSION` stores JP225 as `(60, 480)` because `all_bars` has
#: ALREADY added its six-hour shift; in real New York terms that session is
#: 19:00-02:00, which wraps. Copying the shifted pair in read JP225 as open
#: 01:00-08:00 -- the seven hours it is shut -- and deprioritised through the
#: seven it actually trades. It cost freshness rather than correctness, because
#: the hourly reconciliation ignores sessions entirely, but it meant the one
#: Asian market in the book was the one least likely to be current.
#:
#: The session is duplicated from the research module on purpose: importing it
#: would make a market-data daemon depend on the Exness spec snapshot and
#: MetaTrader, and a stale spec would then stop the feed. `canon_markets` checks
#: the MEMBERSHIP against the registry instead, which needs neither.
DUKASCOPY = {
    "audusd": (_ins.INSTRUMENT_FX_MAJORS_AUD_USD, "audusd_1m", (60, 840)),
    "eurjpy": (_ins.INSTRUMENT_FX_CROSSES_EUR_JPY, "eurjpy_1m", (60, 870)),
    "gbpjpy": (_ins.INSTRUMENT_FX_CROSSES_GBP_JPY, "gbpjpy_1m", (0, 810)),
    "usdjpy": (_ins.INSTRUMENT_FX_MAJORS_USD_JPY, "usdjpy_1m", (0, 780)),
    # "jp225" LEFT 2026-09-23 with every jp225 sleeve. Its wrapping-session
    # handling in `is_open` stays, in case it is re-seated.
    "ukoil": (_ins.INSTRUMENT_CMD_ENERGY_E_BRENT, "ukoil_1m", (540, 870)),
}

#: ETHUSD is the whole crypto leg now.
#:
#: BTCUSDT WAS FED HERE AND IS NOT ANY MORE. It was required while
#: `ethusd:idio_break` was in the book -- that cell asked whether ETHUSD broke
#: its own channel while BTC stayed inside its own, so BTC had to be fetched and
#: carried onto ETHUSD's bars as `Bar::benchmark`. The decay screen removed the
#: cell on 2026-09-03 and `benchmark_markets()` is empty, so nothing in the live
#: runtime reads `btc_1m`. It is `canon_markets` that keeps this honest: the
#: registry reports every market that must be fed INCLUDING a benchmark, so
#: re-seating a benchmarked sleeve fails the preflight here rather than running
#: a cell that silently never fires.
BINANCE = {"ethusd": ("ETHUSDT", "ethusd_1m")}

#: Markets the canon has held that this script deliberately does not feed, so a
#: reader looking for one finds the reason rather than an omission.
NOT_FED = {
    "nq": "barred as a symbol 2026-09-03; its level-two feed and nq_1m came "
          "over Bookmap and its own importers, never this script",
    "xalusd": "xalusd:gated_fade left the book 2026-08-29; it was the one "
              "broker-sourced signal, because Dukascopy carries no aluminium "
              "(COPPER, XPD, XPT) and Binance is a crypto exchange",
    "hk50": "dropped 2026-09-07 on data quality -- 6% of weekdays carried no "
            "30-minute bar at all",
    "gbpusd": "gbpusd:obv_break dropped 2026-09-03 by the decay screen",
    "uk100": "uk100:gated_fade dropped 2026-09-03 by the decay screen",
    "btc": "fed only while ethusd:idio_break needed it as a benchmark",
}

#: THE LOOP CHASES THE BAR; IT DOES NOT SAMPLE ON A TIMER.
#:
#: It used to fire once a wall-clock minute at a fixed offset past, on the
#: reasoning that one bar exists per minute so one fetch per minute is enough.
#: That is true of the COUNT and false of the TIMING: both sides were locked to
#: the minute -- the vendor publishes at a near-fixed offset past it and the
#: daemon polled at a fixed offset past it -- so whatever gap the two offsets
#: left was paid on EVERY bar, deterministically, rather than occasionally.
#:
#: Chasing removes the coupling. The daemon aims at the offset it has actually
#: observed instead of one chosen in advance, so a vendor that moves its publish
#: time is tracked rather than missed by a fixed amount forever.
#:
#: So the daemon now PROBES one in-session symbol every `CHASE_INTERVAL_SECONDS`
#: through a window, and fans out to the full pass the moment the probe returns
#: a row. Latency becomes the probe interval rather than the remainder of a
#: minute, and the cost is a handful of one-symbol fetches instead of one --
#: the probe's own rows are written, so none of it is wasted work.
#:
#: `TICK_OFFSET_SECONDS` is the FLOOR of the chase window: there is no point
#: asking before the vendor could possibly have published.
#:
#: ONE SECOND, BECAUSE THE PUBLISH DELAY IS MEASURED AT 0.6s. It was 5.0 while
#: the delay was believed to be 78s, where a floor that low would only have
#: burned fetches. Now it BINDS: the estimator clamps to it below, so the daemon
#: converged to a first probe at :05 and an arrival at :06.8 -- four seconds
#: after a bar that had been sitting there since :00.6. A fetch costs ~1.9s, so
#: probing from :01 lands the bar at about :03 instead.
#:
#: It is not zero. A bar cannot exist before its own close, and asking at :00.0
#: would spend one whole fetch per minute on a certainty.
TICK_OFFSET_SECONDS = 1.0

#: How often the probe re-asks while chasing. A Dukascopy fetch costs ~1.9s, so
#: this is the interval BETWEEN attempts and the real cadence is a little wider.
#: Two seconds puts the arrival inside about four seconds of the publish without
#: making the daemon a hot loop.
CHASE_INTERVAL_SECONDS = 2.0

#: How far BEFORE the minute boundary the window opens, in seconds.
#:
#: THE FIRST ATTEMPT IS THERE TO OPEN THE SOCKET, NOT TO FIND THE BAR. A cold
#: request to `freeserv.dukascopy.com` costs 0.80-1.11s from here, almost all of
#: it TCP and TLS setup to Switzerland; on a connection already open the same
#: request is 0.174-0.188s. Measured 2026-09-08, the socket survives at least 6s
#: of idle and is gone by 10 -- too short to hold between minutes, which is why
#: the daemon used to pay the handshake every window, but far longer than the
#: second or so needed to span the publish.
#:
#: So the window now opens just before the boundary. That attempt cannot find
#: the bar -- the vendor publishes ~0.6s AFTER it -- and is not meant to: it
#: leaves a warm connection for the attempt that can, which then costs 0.18s
#: rather than 0.85s.
PREWARM_LEAD_SECONDS = 0.8

#: The retry gap while the bar is expected imminently, in seconds.
#:
#: `CHASE_INTERVAL_SECONDS` is the patient one, for a vendor running late. Two
#: seconds is far too coarse across the publish itself: it would leave the warm
#: socket idle through the exact moment the bar becomes available.
CHASE_FAST_INTERVAL_SECONDS = 0.3

#: How many fast attempts before dropping back to patient single-symbol probing.
#:
#: BOUNDS THE REQUEST RATE ON A BAD MINUTE. A fast attempt fans out over every
#: due symbol, so leaving it fast for the whole 50-second window would multiply
#: the request count by the symbol count on every minute the vendor is late.
#: Four covers the boundary plus a second of slack; past that the minute is
#: already lost and patience is cheaper than volume.
CHASE_FAST_ATTEMPTS = 4

#: Where the chase starts, in seconds past the minute the bar closed on. Set
#: from the observed arrival once one has been seen -- see `_publish_offset` --
#: and floored at `TICK_OFFSET_SECONDS` so a bad estimate cannot make the daemon
#: poll from the top of the minute.
CHASE_LEAD_SECONDS = 6.0

#: Give up chasing this minute at this point and wait for the next bar. A vendor
#: outage must not turn into a tight retry loop, and the hourly reconciliation
#: is what recovers anything missed.
CHASE_DEADLINE_SECONDS = 50.0

#: Seed for the expected arrival, in seconds past the minute. Dukascopy's own
#: publish delay is the floor and no polling arrangement beats it; this is only
#: where the FIRST chase begins, and it is replaced by measurement immediately.
#:
#: THE 78s WAS SETTLED AND IT WAS WRONG. It timed the appearance of the minute
#: STILL BEING TRADED -- Dukascopy serves that partial bar, so asking at 11:10:48
#: hands back a bar stamped 11:10 -- and measuring it to the bar's OPEN reads as
#: 78 seconds. `write_rows` has always dropped partial bars, so the daemon never
#: saw one; only the measurement did. `_seventh_publish` filtered to complete
#: bars the way `write_rows` does and got a MEDIAN 0.6s, max 2.4s.
#:
#: The seed is left high anyway. It is a cold-start guess, arrivals step it DOWN
#: immediately, and the daemon walked 20 -> 15.8 -> 11.5 -> 7.4 -> 6.8 in four
#: minutes on its first live run. Seeding it at the measured figure would save
#: those four minutes and spend wasted probes on any symbol slower than gbpjpy,
#: which is every symbol that has not been measured.
PUBLISH_OFFSET_SEED = 20.0

#: How much history each tick asks for. Only the NEW minute is ever written --
#: `write_rows` drops everything at or before the table's maximum -- but the
#: window has to span the publish lag plus a missed tick or two, or a bar that
#: arrives late is never picked up. A fetch costs ~1.9s at any width, so this
#: is free.
#:
#: IT MUST COVER `INACTIVE_EVERY_TICKS`, AND AT 4 AGAINST 5 IT DID NOT. An
#: out-of-session symbol is swept every fifth minute and each sweep asked for
#: four minutes, so exactly one minute in five was never requested by anybody.
#: The hole is PERMANENT: `dukascopy_fetch&import.py` imports
#: `bars[bars.index > latest]`, so the hourly reconciliation appends and never
#: fills an interior gap. On 2026-09-09 that left 90 missing minutes in
#: `ukoil_1m` and 138 in `jp225_1m` -- every one of them at `minute % 5 == 1`,
#: which is the signature -- and cost live trades: the server's market cursor
#: waits `LATE_BAR_GRACE_SECONDS` (300) on a hole, and while it is behind its
#: own watermark EVERY bar it hands a strategy is flagged as a stale catch-up
#: bar, which refuses entries. `ethusd_pullback` lost its 14:31 entry that way.
#:
#: Eight covers the five-minute sweep with three minutes of slack for a late
#: publish, and costs nothing: the fetch is handshake-bound, not payload-bound.
TAIL_MINUTES = 8

#: OUT-OF-SESSION SYMBOLS ARE DEPRIORITISED, NOT DROPPED. A closed market
#: prints almost nothing, so asking every minute is waste -- but it does still
#: print (pre-market, late prints, a session boundary this table's window gets
#: slightly wrong), and skipping it entirely leaves those bars to the hourly
#: reconciliation. So the in-session symbols go every tick and the rest go
#: every fifth, which keeps them current without competing for the minute.
INACTIVE_EVERY_TICKS = 5

#: Slack either side of a session. The 30-minute bar CONTAINING the open or the
#: close still needs the minutes just outside it, and Dukascopy runs 1.3 minutes
#: behind, so a hard edge would clip both ends of every session.
SESSION_MARGIN_MINUTES = 45

#: Reconciliation against the vendor's monthly archive. The tail keeps the table
#: CURRENT; this keeps it CORRECT.
DEEP_PASS_SECONDS = 3600.0

#: Catch-up is chunked: one request spanning months is a long connection that
#: restarts from the beginning if it drops, and Dukascopy drops often enough.
CATCHUP_CHUNK_DAYS = 14

WORKERS = 16
RESTART_SECONDS = 30.0

#: How long every in-session market may print nothing before this process
#: RESTARTS ITSELF rather than sit there looking healthy.
#:
#: THE SERVER FLATTENS THE BOOK AT 180 SECONDS. `feed_stale_after` in
#: `live_trade/src/live/portfolio/routing.rs` is `step + 90`, so a one-minute market
#: that stops advancing for three minutes has every open position on it closed
#: by the live safety watchdog and the sleeve is left flat mid-signal. On
#: 2026-09-08 that fired at 11:35:00Z against `eurjpy_gated_orb`,
#: `usdjpy_kendall` and `usdjpy_volume_thrust` -- and the tables have NO GAP at
#: that minute, so whatever wedged the daemon was in the ARRIVAL of the bars
#: rather than their content, and the hourly reconciliation later backfilled it
#: invisibly. The console was the only record and it was gone by the evening.
#:
#: 135 seconds is deliberately INSIDE the server's 180. A restart re-runs
#: `catch_up`, which writes the missing minutes in one request, so the feed can
#: advance again before the flatten rather than after it.
#:
#: EVERY IN-SESSION MARKET, NOT ONE. A single symbol printing nothing is a
#: holiday, a thin hour, or `is_open` being slightly wrong at a session edge --
#: none of which a restart fixes. All of them at once is either a vendor outage
#: or a wedged process, and restarting is right for both.
STALL_RESTART_SECONDS = 135.0

#: How long the second Ctrl+C has to arrive for a console interrupt to count.
#:
#: A STRAY CTRL+C USED TO KILL THE BOOK'S ONLY DATA FEED. Windows consoles send
#: SIGINT for Ctrl+C when nothing is selected, which is the same keystroke that
#: copies a selection -- so reading the log out of the window is one slip away
#: from stopping it, and on 2026-09-08 that is exactly how the feed died at
#: 12:48:18Z with `-NoExit` leaving a window that still looked alive.
#:
#: SIGTERM is untouched. It is never sent by a fingertip.
CONFIRM_STOP_SECONDS = 5.0

#: Exit codes, read by `run_idk_market_feeds.bat`. The supervisor restarts on
#: anything it does not recognise, so only a DELIBERATE stop and a refusal that
#: cannot fix itself are listed here.
EXIT_STOPPED = 0        #: operator asked twice; stay down
EXIT_RETRYABLE = 1      #: preflight failed on something that recovers (DNS/Warp)
EXIT_REFUSED = 3        #: canon needs a market nothing here feeds; stay down
EXIT_STALLED = 4        #: the stall watchdog fired; restart

#: Where this process keeps its own log, beside the console.
#:
#: THE CONSOLE WAS THE ONLY COPY. Diagnosing the 11:35 flatten above needed the
#: daemon's log for 11:32-11:35 and there was none: the window had scrolled and
#: the process had been restarted. One file per UTC day, appended, and `*.log`
#: is already ignored by git.
LOG_DIR = WORKSPACE / "logs"

_log_lock = Lock()
_log_handle = None
_log_day = None


def log(message):
    now = datetime.now(tz=UTC)
    print(f"[{now:%H:%M:%S}] {message}", flush=True)
    global _log_handle, _log_day
    with _log_lock:
        day = f"{now:%Y-%m-%d}"
        if day != _log_day:
            if _log_handle is not None:
                try:
                    _log_handle.close()
                except OSError:
                    pass
                _log_handle = None
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _log_handle = open(LOG_DIR / f"idk_market_feeds-{day}.log",
                                   "a", encoding="utf-8")
            except OSError:
                # A log that cannot be opened must never stop the feed.
                _log_handle = None
            _log_day = day
        if _log_handle is not None:
            try:
                _log_handle.write(f"{now:%Y-%m-%dT%H:%M:%S}Z {message}\n")
                _log_handle.flush()
            except OSError:
                pass


def canon_markets():
    """Every market the canon book must have fed, or `None` if unknown.

    READ FROM THE ALREADY-BUILT BINARY, NEVER BUILT HERE. `exness_live_book`
    reports `known_markets()` plus any benchmark market, which is the same
    registry `activate_canonical_live.py` copies its live rows from. Shelling
    out to `cargo run` instead would make a market-data daemon wait on a Rust
    build before it fed anything, so an absent binary answers `None` and the
    check is skipped with a warning rather than blocking the feed.
    """
    for name in ("exness_live_book.exe", "exness_live_book"):
        binary = WORKSPACE / "live_trade" / "target" / "release" / name
        if binary.is_file():
            break
    else:
        return None
    try:
        done = subprocess.run([str(binary)], capture_output=True, text=True,
                              timeout=60, cwd=str(WORKSPACE))
        if done.returncode != 0:
            return None
        payload = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    markets = payload.get("markets")
    if not isinstance(markets, list) or not markets:
        return None
    return payload.get("book_id", "?"), set(markets)


def check_against_canon(fed):
    """Refuse to start if a canon market has no feed here.

    THE TWO DIRECTIONS ARE NOT SYMMETRIC. Feeding a table the book no longer
    trades is waste and is reported; NOT feeding one it does trade is a sleeve
    deciding on stale prices, which looks perfectly healthy from every screen
    the operator has. So the first is a line in the log and the second stops
    the process.
    """
    answer = canon_markets()
    if answer is None:
        log("canon check SKIPPED -- build live_trade/target/release/exness_live_book "
            "to have this verified")
        return True
    book, wanted = answer
    log(f"canon:     {book}")
    missing = sorted(wanted - fed)
    extra = sorted(fed - wanted)
    if extra:
        log(f"feeding    {', '.join(extra)} -- not in the canon, harmless but wasted")
    if missing:
        log(f"REFUSING TO START: canon needs {', '.join(missing)} and nothing here feeds it")
        return False
    return True


def python_exe():
    venv = WORKSPACE / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.is_file() else sys.executable


# --------------------------------------------------------------------------- #
# timestamps: this repository stores New York wall clock relabelled as UTC
# --------------------------------------------------------------------------- #

def stored_ns(instant):
    """Real UTC instant -> New York wall clock relabelled UTC, in nanoseconds."""
    local = instant.astimezone(NEW_YORK)
    return int(local.replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def actual_seconds(stored):
    """The inverse.

    Needed because the catch-up range starts at what the TABLE holds -- wall
    clock -- while Dukascopy is asked for real instants. Skipping this does not
    raise; it shifts every window by 4-5 hours and silently misses one end.
    """
    naive = datetime.fromtimestamp(stored, tz=UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=NEW_YORK).timestamp())


# --------------------------------------------------------------------------- #
# QuestDB
# --------------------------------------------------------------------------- #

TABLE_MAX_CACHE: dict[str, int] = {}


def table_max_seconds(table: str, force_query: bool = False):
    """Newest stored timestamp in `table` as epoch seconds, or None if empty."""
    if not force_query and table in TABLE_MAX_CACHE:
        return TABLE_MAX_CACHE[table]
    try:
        val = max_timestamp_seconds(table)
    except OSError:
        return None
    if val is not None:
        TABLE_MAX_CACHE[table] = val
    return val


def write_rows(table, frame, newer_than):
    """Send `frame` to `table`, skipping anything at or before `newer_than`.

    THE VENDOR TABLES HAVE NO DEDUP KEY. The Parquet writer replaces a row at a
    timestamp it already holds, so a resend no longer duplicates -- but this
    filter is kept and still load-bearing for the reason below, and it also
    keeps the write cheap by not rewriting a shard for rows already stored.

    THE MINUTE IN PROGRESS IS DROPPED, and that is not optional. Dukascopy will
    hand back the CURRENT minute mid-flight: asked at 17:50:49 it returned a
    bar stamped 17:50, forty-nine seconds into it. Storing that would be
    permanent, because the filter above then treats the partial bar as the
    table's maximum and skips the completed version when it appears. A partial
    bar is indistinguishable from a real one afterwards -- wrong high, wrong
    low, wrong close, wrong volume -- so it must never be written in the first
    place.
    """
    if frame is None or frame.empty:
        return 0
    minute_now = int(datetime.now(tz=UTC).timestamp()) // 60 * 60
    sender = Sender()
    sender.establish()
    written = 0
    max_written = 0
    try:
        for stamp, row in frame.iterrows():
            instant = stamp.to_pydatetime()
            if int(instant.timestamp()) >= minute_now:
                continue                      # the minute still being traded
            at = stored_ns(instant)
            sec = at // 1_000_000_000
            if newer_than is not None and sec <= newer_than:
                continue
            sender.row(table, symbols=None, columns={
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"])}, at=TimestampNanos(at))
            written += 1
            if sec > max_written:
                max_written = sec
        sender.flush()
        if max_written > 0:
            TABLE_MAX_CACHE[table] = max(TABLE_MAX_CACHE.get(table, 0), max_written)
        if written:
            note_rows()
    finally:
        try:
            sender.close()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
    return written


# --------------------------------------------------------------------------- #
# Dukascopy
# --------------------------------------------------------------------------- #

#: How long one Dukascopy HTTP call may take before it is abandoned.
#:
#: THE LIBRARY PASSES NO TIMEOUT AT ALL, so a stalled socket blocks the chase
#: thread until the OS gives up -- indistinguishable from a slow vendor, and the
#: reason a wedged fetch used to look like a quiet market. The session below is
#: the only place a default can be applied without editing the package.
FETCH_TIMEOUT_SECONDS = 8.0


class _KeepAlive(requests.Session):
    """A pooled session with a default timeout, standing in for `requests`.

    THE HANDSHAKE WAS MOST OF THE FETCH. `dukascopy_python._fetch` calls the
    module-level `requests.get`, which opens a new TCP connection and negotiates
    TLS every time -- three to four round trips to Switzerland before a byte of
    data moves. Measured from here 2026-09-08, the same request costs 0.80-1.11s
    cold and 0.18s on a connection already open.

    `_stream` issues TWO of those per `fetch`: one that returns the window and
    one more that has to come back past the end before it will stop. So a
    four-minute tail of three rows cost ~1.7s, and at least half of that is
    recovered the moment the second request reuses the first's connection --
    whatever the vendor does with the socket between minutes.

    Substituted onto the package rather than patched into it, because
    `_fetch` resolves `requests.get` through the module attribute at call time.
    """

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", FETCH_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


def _install_keepalive():
    """Point the vendor library's `requests` at one pooled session."""
    session = _KeepAlive()
    # One connection per worker, so a fan-out never queues on the pool or
    # evicts a connection another thread is about to reuse.
    adapter = HTTPAdapter(pool_connections=WORKERS, pool_maxsize=WORKERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _dk.requests = session
    return session


_SESSION = _install_keepalive()


def fetch(instrument, start, stop):
    return _dk.fetch(instrument, _dk.INTERVAL_MIN_1, _dk.OFFER_SIDE_BID, start, stop)


def is_open(symbol, now=None):
    """Whether `symbol` is inside its own session, margin included.

    THE WINDOW MAY WRAP. JP225 trades 19:00-02:00 New York, so `opened` is
    greater than `closed` and a plain `opened <= m <= closed` is empty -- the
    market would read as shut every minute of the week and never be polled on a
    live tick at all.

    THE WEEK WRAPS WITH IT. A wrapping session belongs to the day it OPENS on,
    so JP225's Monday session starts Sunday evening and its Friday session ends
    Saturday morning. Refusing every Saturday and Sunday minute outright, which
    is right for an unshifted market, would cut the open off one end of a
    wrapping week and the close off the other.
    """
    now = now or datetime.now(tz=NEW_YORK)
    opened, closed = DUKASCOPY[symbol][2]
    minute = now.hour * 60 + now.minute
    lower = opened - SESSION_MARGIN_MINUTES
    upper = closed + SESSION_MARGIN_MINUTES
    if lower <= upper:
        return now.weekday() < 5 and lower <= minute <= upper
    if minute >= lower:
        # The EVENING leg opens the following day's session, so it runs Sunday
        # through Thursday -- never Friday, which would open a Saturday in Tokyo.
        return now.weekday() in (6, 0, 1, 2, 3)
    if minute <= upper:
        # The MORNING leg closes the session that opened last evening: Monday to
        # Friday, and never Saturday.
        return now.weekday() <= 4
    # BETWEEN THE TWO LEGS THE MARKET IS SHUT, and this arm is why the wrapping
    # branch needs three cases rather than two. Falling through to the morning
    # test alone read every minute below the evening open as in-session, so
    # JP225 reported open around the clock Monday to Thursday.
    return False


def _fetch_catchup_chunk(args):
    instrument, table, cursor, stop, newest = args
    return write_rows(table, fetch(instrument, cursor, stop), newest)


def catch_up(symbol):
    """Fill `symbol` from its table's last minute to now. Returns rows written."""
    instrument, table, _ = DUKASCOPY[symbol]
    newest = table_max_seconds(table, force_query=True)
    if newest is None:
        # Empty table. That is a bootstrap, not a catch-up: walking years of
        # archive over one live connection is the monthly importer's job.
        log(f"{table} is EMPTY -- bootstrap it with dukascopy_fetch&import.py")
        return 0
    now = datetime.now(tz=UTC)
    cursor = datetime.fromtimestamp(actual_seconds(newest), tz=UTC) + timedelta(minutes=1)
    if cursor >= now:
        return 0
    chunks = []
    while cursor < now:
        stop = min(cursor + timedelta(days=CATCHUP_CHUNK_DAYS), now)
        chunks.append((instrument, table, cursor, stop, newest))
        cursor = stop
    if len(chunks) == 1:
        return _fetch_catchup_chunk(chunks[0])
    written = 0
    with ThreadPoolExecutor(max_workers=min(len(chunks), 16)) as pool:
        for rows in pool.map(_fetch_catchup_chunk, chunks):
            written += rows
    return written


def tail(symbol):
    """Fetch the newest minutes for `symbol` and store whatever is new."""
    instrument, table, _ = DUKASCOPY[symbol]
    now = datetime.now(tz=UTC)
    frame = fetch(instrument, now - timedelta(minutes=TAIL_MINUTES), now)
    # AFTER the fetch, BEFORE the write, and unconditional on the row count.
    # This is the daemon's liveness signal, and what it has to prove is that
    # the loop came back -- not that the vendor had anything to say.
    note_fetch()
    return write_rows(table, frame, table_max_seconds(table))


#: Running estimate of when the vendor publishes, in seconds past the minute the
#: bar closed on. Learned from arrivals, so the daemon needs no baked constant.
#:
#: OBSERVABILITY ONLY SINCE THE WINDOW MOVED. It used to schedule the wake-up,
#: which is why it tracks the fast end rather than the average. `PREWARM_LEAD_SECONDS`
#: now opens the window before the boundary unconditionally -- there is nothing
#: left to tune, because the first attempt is meant to be in flight when the
#: vendor publishes rather than to arrive after it. The estimate is still logged,
#: since a drift in it is the first sign the vendor's timing has changed.
_publish_offset = PUBLISH_OFFSET_SEED


def _observe_arrival(seconds_past):
    """Fold one observed arrival into the running estimate.

    A SLOW EWMA UP, AN IMMEDIATE STEP DOWN. Arriving later than expected is
    usually one slow fetch and should barely move the estimate; arriving EARLIER
    proves the vendor can publish by then, and there is no reason to keep
    waiting past a time already demonstrated. So the estimate tracks the fast
    end rather than the average, which is what keeps the chase window tight.
    """
    global _publish_offset
    if seconds_past < _publish_offset:
        _publish_offset = seconds_past
    else:
        _publish_offset += 0.2 * (seconds_past - _publish_offset)
    _publish_offset = min(max(_publish_offset, TICK_OFFSET_SECONDS),
                          CHASE_DEADLINE_SECONDS)


def sleep_to_chase_window(stop):
    """Block until the next minute's chase window opens, or until `stop`.

    The window opens `CHASE_LEAD_SECONDS` before the expected arrival, floored
    at `TICK_OFFSET_SECONDS`, so the first probe lands just before the vendor
    publishes rather than long after.
    """
    now = time.time()
    # BEFORE the boundary, not after it. The first attempt's job is to open the
    # connection the next one will use; see `PREWARM_LEAD_SECONDS`.
    target = (int(now // 60) + 1) * 60 - PREWARM_LEAD_SECONDS
    # IN SLICES, BECAUSE A SIGNAL HANDLER ONLY RUNS IN THE MAIN THREAD AT A
    # BYTECODE BOUNDARY. This is the one wait long enough to matter: blocked in
    # it, a Ctrl+Break was not seen for 59 seconds, which made "press again
    # within five" impossible to satisfy and a SIGTERM look ignored. Ctrl+C is
    # the exception -- CPython arms an event that cuts the wait short -- and
    # relying on that for every signal is what this stops doing.
    remaining = max(0.05, target - now)
    while remaining > 0 and not stop.wait(min(1.0, remaining)):
        remaining -= 1.0


def chase(stop, probe, due):
    """Probe one symbol until the bar lands, then fetch the rest.

    Returns `(rows, seconds_past_minute, attempts)`; `seconds_past_minute` is
    `None` if the window closed without the vendor serving anything.

    THE FIRST ATTEMPT FANS OUT; A MISS FALLS BACK TO ONE PROBE.

    Every Dukascopy table comes from the same publisher, so one instrument does
    answer "has this minute been published" for all of them -- but answering it
    first and fetching the others afterwards makes every symbol except the probe
    wait a whole second round trip. The probe lands around :02.9 (0.6s vendor
    publish, ~2.3s fetch) and the rest at ~:04.8, and five of the six markets
    this book trades live are in that second group.

    The window is entered at `TICK_OFFSET_SECONDS` and the vendor publishes
    inside a second, so the first attempt is expected to succeed -- and on that
    attempt a fan-out costs exactly what probe-then-rest costs: one request per
    symbol. It is only a MISS that would multiply the request rate, so a miss
    drops back to probing one symbol at `CHASE_INTERVAL_SECONDS` and fetches the
    others once it lands, which is what this always used to do.
    """
    attempts, total = 0, 0
    while not stop.is_set():
        past = time.time() % 60.0
        if attempts and past > CHASE_DEADLINE_SECONDS:
            return total, None, attempts
        attempts += 1
        if attempts <= CHASE_FAST_ATTEMPTS:
            # Fan out while the bar is due. The first of these is the one that
            # pays the handshake and is expected to come back empty; the rest
            # ride its connection and cost a fifth as much.
            rows, _failed = parallel(tail, due, "tail")
            total += rows
            if rows:
                return total, time.time() % 60.0, attempts
            if stop.wait(CHASE_FAST_INTERVAL_SECONDS):
                break
            continue
        try:
            got = tail(probe)
        except Exception as error:  # noqa: BLE001 - one probe, not the loop
            log(f"probe {probe}: {type(error).__name__}: {error}")
            got = 0
        total += got
        if got:
            landed = time.time() % 60.0
            rest = [s for s in due if s != probe]
            if rest:
                rows, _failed = parallel(tail, rest, "tail")
                total += rows
            return total, landed, attempts
        if stop.wait(CHASE_INTERVAL_SECONDS):
            break
    return total, None, attempts


def parallel(work, symbols, label):
    """Run `work` over `symbols` in a pool. One failure never costs the rest."""
    total, failed = 0, []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(work, s): s for s in symbols}
        for future, symbol in futures.items():
            try:
                res = future.result()
                if isinstance(res, (int, float)):
                    total += res
            except Exception as error:  # noqa: BLE001 - one symbol, not all
                log(f"{label} {symbol}: {type(error).__name__}: {error}")
                failed.append(symbol)
    return total, failed


def _reconcile_symbol(symbol):
    now = datetime.now(tz=UTC)
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    instrument, table, _ = DUKASCOPY[symbol]
    try:
        done = subprocess.run(
            [python_exe(), str(HERE / "dukascopy_fetch&import.py"),
             "--instrument", instrument, "--table", table,
             "--start", f"{year}-{month:02d}",
             "--end", f"{now.year}-{now.month:02d}",
             "--timezone", "America/New_York"],
            cwd=str(WORKSPACE), capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        log(f"reconcile {symbol}: timed out")
        return
    out = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0 or any(
            m in out for m in ("FAILED", "month(s) missing", "nothing downloaded")):
        log(f"reconcile {symbol}: FAILED (retried next hour)")


def reconcile(symbols):
    """Replay the vendor's monthly archive across all symbols in parallel."""
    parallel(_reconcile_symbol, symbols, "reconcile")


# --------------------------------------------------------------------------- #
# Binance
# --------------------------------------------------------------------------- #

class ChildFeed:
    """A streaming child process, restarted if it dies."""

    def __init__(self, name, command):
        self.name = name
        self.command = command
        self.process = None
        self.started = 0.0

    def ensure_running(self):
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            # Not instantly: a tight respawn loop against a rejecting endpoint
            # is worse than being down and visible.
            if time.monotonic() - self.started < RESTART_SECONDS:
                return
            log(f"{self.name} exited ({self.process.returncode}); restarting")
        self.process = subprocess.Popen(
            self.command, cwd=str(WORKSPACE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1)
        self.started = time.monotonic()
        # ITS OUTPUT WENT TO THE CONSOLE AND NOWHERE ELSE. The child inherited
        # the window, so half the feed -- every ETHUSD minute, every reconnect,
        # every backfill -- was missing from the log file that exists to make a
        # stall diagnosable afterwards. Reading it on a thread and re-logging it
        # also means a full pipe can never block the child.
        Thread(target=self._drain, args=(self.process,), daemon=True).start()
        log(f"{self.name} running (pid {self.process.pid})")

    def _drain(self, process):
        try:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    log(f"[{self.name}] {line}")
        except Exception:  # noqa: BLE001 - the child exiting closes the pipe
            pass

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()


# --------------------------------------------------------------------------- #
# the daemon's own liveness
# --------------------------------------------------------------------------- #

#: When a fetch last CAME BACK, on the monotonic clock, and when a row was last
#: stored. The first is the liveness signal; the second is only for the log.
#:
#: IT USED TO WATCH THE ROWS AND THAT WAS THE WRONG SIGNAL. A vendor that
#: serves nothing and a daemon that has wedged look identical from the rows, and
#: only one of them is worth restarting. On 2026-09-09 Dukascopy went quiet
#: twice -- "no bar inside the window after 24 probe(s)", which is
#: twenty-four fetches that COMPLETED and returned nothing -- and both times
#: this restarted a perfectly healthy process. Neither restart helped: the
#: catch-up wrote one row between them, because the vendor had nothing to give.
#: One of them cost a real trade, by killing the Binance child mid-minute and
#: losing ETHUSD's 14:24, which then froze the server's cursor on the hole and
#: made it refuse `ethusd_pullback`'s 14:31 entry as a stale catch-up bar.
#:
#: A COMPLETED FETCH IS THE PROOF THE LOOP IS ALIVE, empty or not. It means the
#: thread ran, the socket answered and `write_rows` returned -- which is exactly
#: what a wedge stops. A vendor outage is then left alone: it is the server's
#: feed watchdog that must handle it, and flattening on a feed that genuinely
#: stopped publishing is the right answer rather than a bug to route around.
_last_fetch_at = time.monotonic()
_last_row_at = time.monotonic()


def note_fetch():
    global _last_fetch_at
    _last_fetch_at = time.monotonic()


#: Where the daemon publishes its own health for the live runtime to read.
#:
#: THE SERVER CANNOT TELL A QUIET VENDOR FROM A DEAD FEED, AND IT FLATTENS THE
#: BOOK OVER THE DIFFERENCE. `feed_stale_after` gives a one-minute market 180
#: seconds without a bar before the live safety watchdog closes every position
#: on it, and from the store alone "no new rows" has exactly one appearance
#: whatever the cause. On 2026-09-09 Dukascopy published nothing for three
#: minutes -- while this daemon was fetching normally and getting empty answers
#: back -- and `eurjpy_gated_orb` was flattened for it.
#:
#: So the one fact the server cannot derive is written here: the loop ran, and
#: the vendor answered. A stale or missing file is not an error, it is the
#: absence of evidence, and the server keeps its short fuse. Only a FRESH file
#: buys the longer one.
#:
#: Beside the store rather than in `logs/`, because the server already resolves
#: `data/` and resolving a second tree would need another environment variable.
HEALTH_FILE = WORKSPACE / "data" / "feed_health.json"


def publish_health():
    """Write the liveness the live runtime cannot observe for itself.

    Best effort in both directions: a write that fails must never stop the
    feed, and the server treats an unreadable file as "no evidence" rather
    than as a fault.
    """
    now = time.time()
    monotonic = time.monotonic()
    payload = {
        "wrote_at": int(now),
        # Seconds since a Dukascopy fetch last CAME BACK, empty or not. The
        # server reads this as "the daemon is alive and the vendor is
        # answering", which is the case it must not flatten on.
        "fetch_age": round(max(0.0, monotonic - _last_fetch_at), 1),
        # Seconds since a row was last stored. Reported for an operator reading
        # the file; the server does not key anything on it, because a quiet
        # market makes it large for entirely healthy reasons.
        "row_age": round(max(0.0, monotonic - _last_row_at), 1),
        "in_session": sorted(s for s in DUKASCOPY if is_open(s)),
    }
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        staged = HEALTH_FILE.with_suffix(f".{os.getpid()}.tmp")
        staged.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(staged, HEALTH_FILE)
    except OSError:
        pass


def note_rows():
    global _last_row_at
    _last_row_at = time.monotonic()


def stall_watchdog(stop, feeds):
    """Restart the process if the fetch loop stops coming back at all.

    ITS OWN THREAD, BECAUSE THE THING IT WATCHES FOR IS A WEDGED MAIN LOOP. A
    check written into the loop cannot report the loop not running, and a fetch
    that never returns is exactly the failure the server sees as a dead feed.
    `parallel` waits on its futures without a deadline, so one thread stuck
    below the HTTP timeout -- inside the vendor library, inside a shard rewrite
    -- holds the whole minute and nothing here would ever notice.

    IT EXITS RATHER THAN TRIES TO RECOVER. There is no way to unwedge another
    thread from here, and `os._exit` is what reaches the supervisor: a clean
    `stop.set()` would be read by the loop that has stopped reading. The
    Binance child is terminated first so a restart does not leave two streamers
    on the same table -- which is why this must not fire on a vendor outage:
    the restart is not free, it costs the crypto leg the minute in flight.
    """
    while not stop.wait(5.0):
        # ON THIS THREAD, because it must keep being written while the main loop
        # is inside a fetch -- that is most of a minute, and a health file that
        # went stale during normal work would tell the server the opposite of
        # the truth.
        publish_health()
        if not any(is_open(s) for s in DUKASCOPY):
            # Every market shut, so nothing is being fetched on a live tick and
            # silence proves nothing. Keep both clocks fresh.
            note_fetch()
            note_rows()
            continue
        idle = time.monotonic() - _last_fetch_at
        if idle < STALL_RESTART_SECONDS:
            continue
        quiet = time.monotonic() - _last_row_at
        log(f"STALLED: no fetch has returned for {idle:.0f}s (last row {quiet:.0f}s "
            "ago) -- the loop is wedged, restarting")
        for feed in feeds:
            try:
                feed.stop()
            except Exception as error:  # noqa: BLE001 - shutdown is best effort
                log(f"{feed.name}: {type(error).__name__}: {error}")
        os._exit(EXIT_STALLED)


# --------------------------------------------------------------------------- #

def binance_feed():
    pairs = [BINANCE[s] for s in sorted(BINANCE)]
    return ChildFeed("binance stream", [
        python_exe(), str(HERE / "binance_stream_1m.py"),
        "--symbol", *[p[0] for p in pairs],
        "--table", *[p[1] for p in pairs],
        "--timezone", "America/New_York", "--max-backfill-days", "7"])


def main():
    symbols = sorted(DUKASCOPY)
    log(f"dukascopy: {', '.join(symbols)}")
    log(f"binance:   {', '.join(sorted(BINANCE))}")
    for symbol, why in NOT_FED.items():
        log(f"not fed:   {symbol} -- {why}")
    if not check_against_canon(set(DUKASCOPY) | set(BINANCE)):
        # NOT RETRYABLE. A market the canon trades and nothing here feeds is a
        # book change, not an outage, so the supervisor is told to stay down
        # rather than reprint this once every ten seconds all night.
        return EXIT_REFUSED

    try:
        socket.setdefaulttimeout(5.0)
        socket.getaddrinfo("datafeed.dukascopy.com", 443)
    except OSError:
        log("datafeed.dukascopy.com does not resolve -- turn Cloudflare Warp on")
        return EXIT_RETRYABLE
    finally:
        socket.setdefaulttimeout(None)

    stop = Event()
    interrupts = []

    def interrupted(*_):
        """Ask twice. See `CONFIRM_STOP_SECONDS`."""
        now = time.monotonic()
        interrupts[:] = [at for at in interrupts if now - at < CONFIRM_STOP_SECONDS]
        interrupts.append(now)
        if len(interrupts) < 2:
            log(f"interrupt IGNORED -- press Ctrl+C again within "
                f"{CONFIRM_STOP_SECONDS:.0f}s to stop the feed")
            return
        log("second interrupt -- stopping the feed")
        stop.set()

    # SIGBREAK IS GUARDED TOO. Ctrl+Break is one key away from Ctrl+C on the
    # same console and Python's default for it is to die where it stands --
    # no `stopped` line, no child terminated, no record of why.
    for name, handler in (("SIGINT", interrupted),
                          ("SIGBREAK", interrupted),
                          ("SIGTERM", lambda *_: stop.set())):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):
                pass

    # START THE STREAM BEFORE THE CATCH-UP, not inside the loop. It backfills on
    # startup, so launching it first lets that happen while the Dukascopy
    # catch-up runs instead of after it -- previously Binance sat idle until the
    # first tick, a minute or more into the run.
    feeds = [binance_feed()]
    for feed in feeds:
        feed.ensure_running()

    # Armed before the catch-up, which is itself a place this can wedge.
    Thread(target=stall_watchdog, args=(stop, feeds), daemon=True).start()

    # CATCH UP the Dukascopy tables. This process has just been off for an
    # unknown length of time and the tail window is ten minutes, so a longer gap
    # would never be closed by the loop alone. Sized by each table's OWN last
    # minute, so an hour of downtime costs one request and a month a handful.
    log("catching up each table from its last minute")
    rows, failed = parallel(catch_up, symbols, "catchup")
    log(f"caught up {rows:,} row(s)" + (f"; failed {failed}" if failed else ""))

    last_deep = time.monotonic()
    last_open = None
    tick = 0
    try:
        while not stop.is_set():
            sleep_to_chase_window(stop)
            if stop.is_set():
                break
            tick += 1
            for feed in feeds:
                feed.ensure_running()

            # In-session symbols every minute; the rest every fifth. Ordered
            # so the open markets are submitted to the pool first -- with
            # sixteen workers they all start at once anyway, but the order is
            # what makes "priority" true rather than decorative if the pool is
            # ever narrowed.
            live = [s for s in symbols if is_open(s)]
            if tick % INACTIVE_EVERY_TICKS == 0:
                due = live + [s for s in symbols if s not in live]
            else:
                due = live
            if live != last_open:
                log(f"in session: {', '.join(live) or 'none'}")
                last_open = live

            if due:
                # The probe must be a symbol that is actually printing, or it
                # can never land and the whole window is spent waiting on a shut
                # market. In-session symbols are ordered first; if none is open,
                # there is nothing to chase and the pass is a plain sweep.
                if live:
                    rows, landed, tries = chase(stop, live[0], due)
                    if landed is not None:
                        _observe_arrival(landed)
                        log(f"+{rows} bar(s) from {len(due)} symbol(s) at "
                            f":{landed:04.1f} after {tries} probe(s); "
                            f"expecting :{_publish_offset:04.1f}")
                    elif tries:
                        log(f"no bar inside the window after {tries} probe(s) "
                            f"-- expecting :{_publish_offset:04.1f}")
                else:
                    rows, _ = parallel(tail, due, "tail")
                    if rows:
                        log(f"+{rows} bar(s) from {len(due)} symbol(s)")

            if time.monotonic() - last_deep >= DEEP_PASS_SECONDS:
                log("hourly reconciliation")
                reconcile(symbols)
                last_deep = time.monotonic()
    finally:
        for feed in feeds:
            feed.stop()
        log("stopped")
    return EXIT_STOPPED


if __name__ == "__main__":
    raise SystemExit(main())
