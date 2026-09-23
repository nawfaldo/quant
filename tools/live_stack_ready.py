#!/usr/bin/env python3
"""Block until one stage of the live stack is actually ready, or time out.

    py tools/live_stack_ready.py terminal|feeds|server|bridge [--timeout 180]

Exit 0 means ready, 1 means it did not become ready in time. `start_live_trade.bat`
is the only caller, and it stops the launch on a non-zero exit rather than
starting the next stage against a dependency that is not there.

WHY NOT JUST SLEEP, AND WHY NOT JUST "IS THE PROCESS UP".

A fixed sleep is wrong in both directions: too short on a cold start, where the
server replays months of warm-up before it answers anything, and pure waste on a
warm one. And a running process is not a ready one -- `terminal64.exe` is up long
before a login completes, the server binds its port before the strategies are
warm, and `bridge.py` is running for a while before its first heartbeat lands.
Both mistakes look identical from the outside: the next stage starts, fails
against a dependency that is not there yet, and the operator reads a stack of
errors that have nothing to do with the real cause.

So every check here asks the QUESTION THE NEXT STAGE WILL ASK, using the same
signal that stage uses:

    terminal  `bridge.py --check` logs into each account and exits. That is the
              repository's own terminal discovery and account lookup, so a pass
              means the bridge will connect when it is started for real.
    feeds     `data/feed_health.json`, which the feed daemon rewrites every five
              seconds. The live runtime already reads this file to tell a quiet
              vendor from a dead feed; here it answers "is the daemon alive".
    server    an HTTP request the frontend makes. It needs the port bound, the
              router up and SQLite open, which is what the bridge needs too.
    bridge    `seen_at` on the heartbeat row, tested with the SAME SQL the live
              runtime uses to decide whether an account is connected. Entries
              are refused while it says no, so anything weaker would report a
              stack that is ready and then refuse every trade.

Nothing here starts, stops, or configures anything. It only waits and reports.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent

#: How fresh the feed daemon's health file has to be.
#:
#: It is rewritten every five seconds, so this is six missed writes. The Rust
#: side uses thirty for the same file (`FEED_HEALTH_FRESH_SECONDS`); matching it
#: means the launcher never reports a feed ready that the runtime would then
#: treat as absent.
FEED_HEALTH_FRESH_SECONDS = 30

#: How recent the bridge heartbeat has to be.
#:
#: THE RUNTIME'S OWN GATE IS FIVE SECONDS (`datetime('now','-5 seconds')` in
#: `live_runtime.rs`), and this is deliberately wider. A launcher that demanded
#: the same five would fail on a heartbeat that arrived four seconds late during
#: start-up, when the account is still settling -- the question here is "has the
#: bridge started reporting at all", not "is it reporting right this instant".
BRIDGE_HEARTBEAT_FRESH_SECONDS = 45

POLL_SECONDS = 2.0


def log(message: str) -> None:
    print(f"[{datetime.now(tz=timezone.utc):%H:%M:%S}] {message}", flush=True)


def python_exe() -> str:
    venv = WORKSPACE / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.is_file() else sys.executable


# --------------------------------------------------------------------------- #
# the checks. each returns (ready, detail-for-the-operator)
# --------------------------------------------------------------------------- #

def check_terminal() -> tuple[bool, str]:
    """Every configured account can log into its terminal.

    Delegated to `bridge.py --check` rather than reimplemented: that is where
    terminal discovery, the per-account terminal map and the account lookup out
    of `app.db` already live, and a second copy here would drift from the thing
    it is supposed to predict.
    """
    try:
        done = subprocess.run(
            [python_exe(), str(WORKSPACE / "mt5" / "bridge.py"), "--check"],
            cwd=str(WORKSPACE), capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "bridge.py --check did not return within 120s"
    except OSError as error:
        return False, f"cannot run bridge.py: {error}"
    if done.returncode == 0:
        return True, (done.stdout or "").strip().splitlines()[-1:][0] if done.stdout.strip() else "connection check passed"
    tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
    return False, tail[-1] if tail else f"exit {done.returncode}"


def check_feeds() -> tuple[bool, str]:
    """The feed daemon is running and its fetch loop is coming back."""
    path = WORKSPACE / "data" / "feed_health.json"
    try:
        health = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "no data/feed_health.json yet"
    wrote_at = health.get("wrote_at")
    if not isinstance(wrote_at, (int, float)):
        return False, "health file has no wrote_at"
    age = time.time() - wrote_at
    if age > FEED_HEALTH_FRESH_SECONDS:
        return False, f"health file is {age:.0f}s old"
    session = ", ".join(health.get("in_session") or []) or "none"
    return True, f"fetch {health.get('fetch_age')}s ago; in session: {session}"


def check_server(port: int) -> tuple[bool, str]:
    """The HTTP API answers, which needs the port, the router and SQLite."""
    url = f"http://127.0.0.1:{port}/api/environments"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            body = response.read(1_000_000)
    except urllib.error.HTTPError as error:
        # A response at all proves the server is up; only 5xx is not ready.
        if error.code < 500:
            return True, f"HTTP {error.code} (up)"
        return False, f"HTTP {error.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return False, f"{type(error).__name__}: {error}"
    try:
        count = len(json.loads(body))
        return True, f"{count} environment(s)"
    except ValueError:
        return True, "responding"


def check_bridge(db: Path) -> tuple[bool, str]:
    """A heartbeat has landed, by the runtime's own definition of connected."""
    if not db.is_file():
        return False, f"no {db}"
    try:
        connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return False, f"cannot open app.db: {error}"
    try:
        row = connection.execute(
            "SELECT account_id, seen_at, "
            f"seen_at >= datetime('now','-{BRIDGE_HEARTBEAT_FRESH_SECONDS} seconds') "
            "FROM mt5_bridge_heartbeats ORDER BY seen_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as error:
        return False, f"cannot read heartbeats: {error}"
    finally:
        connection.close()
    if row is None:
        return False, "no heartbeat row at all"
    account, seen_at, fresh = row
    if not fresh:
        return False, f"newest heartbeat is {seen_at} UTC (stale)"
    return True, f"account {account} last seen {seen_at} UTC"


def check_flat(db: Path) -> tuple[bool, str]:
    """No position is on, so the server can be stopped without abandoning one.

    NOT A STAGE, AND THE ONLY CHECK THAT IS A PRECONDITION FOR STOPPING rather
    than a signal that something has started. It is here because it answers the
    same shape of question against the same database, and because the caller
    that needs it -- `restart_live_server.bat` -- is the caller that needs the
    rest of this file.

    The runtime holds no broker-side stops: every exit is a market order the
    server sends itself, so a position left open across a restart has no
    protection for the whole warm-up replay. Both tables are read because they
    answer different questions and either one alone can be wrong in the
    dangerous direction: `mt5_bridge_positions` is the broker's truth as of the
    last heartbeat but is emptied when the bridge is down, and
    `mt5_strategy_positions` is what the runtime believes it owns but can lag a
    fill. Flat means BOTH say flat.

    A pending execution command counts as not flat. It is an order that has not
    reached the broker yet; stopping the server now means it is either lost or
    filled into a runtime that no longer knows it asked.
    """
    if not db.is_file():
        return False, f"no {db}"
    try:
        connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return False, f"cannot open app.db: {error}"
    try:
        broker = connection.execute(
            "SELECT COUNT(*) FROM mt5_bridge_positions"
        ).fetchone()[0]
        strategy = connection.execute(
            "SELECT COUNT(*) FROM mt5_strategy_positions WHERE status != 'closed'"
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM mt5_execution_commands WHERE status = 'pending'"
        ).fetchone()[0]
    except sqlite3.Error as error:
        # Deliberately NOT treated as flat. An unreadable database is not
        # evidence of an empty account.
        return False, f"cannot read positions: {error}"
    finally:
        connection.close()
    if broker or strategy or pending:
        return False, (
            f"{broker} broker position(s), {strategy} open strategy position(s), "
            f"{pending} pending command(s)"
        )
    return True, "no broker positions, no open strategy positions, no pending commands"


CHECKS = {
    "terminal": ("Exness terminal", lambda args: check_terminal()),
    "feeds": ("market data feeds", lambda args: check_feeds()),
    "server": ("live trading server", lambda args: check_server(args.port)),
    "bridge": ("MT5 execution bridge", lambda args: check_bridge(args.db)),
    "flat": ("a flat account", lambda args: check_flat(args.db)),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=sorted(CHECKS))
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="seconds to wait before giving up (default 180)")
    parser.add_argument("--port", type=int, default=4000,
                        help="the server's HTTP port (default 4000)")
    parser.add_argument("--db", type=Path, default=WORKSPACE / "live_trade" / "app.db",
                        help="application database, matching bridge.py --db")
    args = parser.parse_args()

    label, check = CHECKS[args.stage]
    deadline = time.monotonic() + args.timeout
    log(f"waiting for {label}...")
    detail = "not checked yet"
    attempts = 0
    while True:
        attempts += 1
        ready, detail = check(args)
        if ready:
            log(f"{label} READY -- {detail}")
            return 0
        if time.monotonic() >= deadline:
            break
        # Reported every fifth attempt so a slow start-up shows progress
        # without the wait itself becoming the noisiest thing on the screen.
        if attempts % 5 == 0:
            log(f"  still waiting: {detail}")
        time.sleep(POLL_SECONDS)
    log(f"{label} NOT READY after {args.timeout:.0f}s -- {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
