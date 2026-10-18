"""Backtest the days you traded live, and show every place the two differ.

    py tools/compare_live_backtest.py 2026-10-05                 # one day
    py tools/compare_live_backtest.py 2026-10-05 2026-10-09      # a week
    py tools/compare_live_backtest.py 2026-10-05 --balance 512.40
    py tools/compare_live_backtest.py 2026-10-05 --no-refresh    # skip the MT5 import
    py tools/compare_live_backtest.py 2026-10-12 2026-10-16 --account fundednext

`--account` picks the book: `exness` (default) or `fundednext`. Each runs on its
own sleeves, its own broker minute tables (`exness_*` / `fundednext_*`, from its
own terminal) and its own account balance.

Dates are New York days, inclusive. Run it after the session, with the MT5
terminal open: it first refreshes the broker's own minute tables
(`exness_*_1m`) for the window, because the backtest prices every fill from
them -- without them it falls back to the idealised vendor fill and differs from
live for that reason alone.

WHAT IT RUNS. The Rust backtest (`exness_book`) over the same days, on the same
sleeves that were live (retired ones included, via `EXNESS_RETIRED=1`), starting
from the account balance (`--balance`, default: the bridge's last heartbeat).

WHAT IT COMPARES. Each live trade in `live_trades` is matched to the backtest
trade of the same sleeve on the same candle and side, then:

  * entry price   live engine (vendor) price vs backtest -- the DATA check
  * exit          live exit time and reason vs backtest
  * dollars       the broker's real P&L vs the backtest's modelled P&L

and every trade that exists on only one side is listed with the reason the live
runtime recorded (`live_events`): a refused entry, a block, a ghost, a watchdog
flatten. A healthy day reads all matched, no exits that differ, and dollars
within the spread noise of the fill model.

It reads the database and the store, writes nothing but the broker-table
refresh, and never sends an order.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "live_trade" / "app.db"
SLEEVES = {
    "exness": ROOT / "live_trade" / "src" / "strategies" / "exness" / "oct-12-2026" / "sleeves",
    "fundednext": ROOT / "live_trade" / "src" / "strategies" / "fundednext" / "oct-13-2026" / "sleeves",
}
#: The account each book trades, found by server.
SERVER = {"exness": "Exness%", "fundednext": "FundedNext%"}
BOOK = ROOT / "live_trade" / "target" / "release" / "exness_book.exe"

#: Live exit reasons that ARE the strategy's own exit. Anything else -- a
#: watchdog flatten, a clock flatten, a retry of an exit that failed, a manual
#: close -- is the runtime overriding the strategy, and is reported as a
#: difference even when it happens to land near the backtest's exit.
FORCED_OK = {"strategy", "session_end"}

#: An exit the backtest books ON THE CLOCK -- live's session-overdue close,
#: modelled for the FundedNext book -- lands at its session close + one candle +
#: 10 seconds, the only exit stamp whose seconds are not zero. Live records it
#: as an emergency flatten, and that is the matching exit, not a difference.
OVERDUE_SECONDS = 10
OVERDUE_LIVE = {"emergency_flatten", "session_end_overdue"}

#: The broker importer's key where it differs from the market name: the US500
#: table is filed under the importer's `es` key.
IMPORTER_KEY = {"us500": "es"}


def sleeves(book):
    """`{id: {"display", "market", "bar"}}` read off the book's sleeve files, so
    every sleeve the engine can run is known here, retired ones included."""
    out = {}
    for path in SLEEVES[book].glob("*.rs"):
        text = path.read_text(encoding="utf-8")
        found = {key: re.search(rf'{key}: "([^"]+)"', text) for key in ("display", "id", "market")}
        if not all(found.values()):
            continue
        bar = re.search(r"bar_minutes: (\d+)", text)
        out[found["id"].group(1)] = {
            "display": found["display"].group(1),
            "market": found["market"].group(1),
            "bar": int(bar.group(1)) * 60 if bar else 1800,
        }
    return out


def ny(text):
    """'YYYY-MM-DD HH:MM' (New York wall clock, as the tables store it) -> seconds."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def label(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


def live_rows(db, lo, hi):
    # The broker-trade table was `environment_trades` until the accounts
    # refactor; the server renames it on its first start. Read whichever exists.
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    table, column = (("account_trades", "account_trade_id") if "account_trades" in names
                     else ("environment_trades", "environment_trade_id"))
    trades = db.execute(
        "SELECT l.id, l.strategy_name, l.side, l.contract, l.engine_entry_price, "
        "l.engine_close_price, l.mt5_entry_price, l.mt5_close_price, l.engine_open_time, "
        "l.engine_close_time, l.exit_reason, e.mt5pnl "
        "FROM live_trades l "
        "LEFT JOIN mt5_execution_commands c ON c.trade_id=l.id AND c.action IN ('long','short') "
        f"LEFT JOIN {table} e ON e.id=c.{column} "
        "WHERE l.engine_open_time>=? AND l.engine_open_time<? ORDER BY l.engine_open_time",
        (lo, hi)).fetchall()
    keys = ("id", "strategy", "side", "lots", "entry", "exit", "mt5_entry", "mt5_exit",
            "opened", "closed", "reason", "pnl")
    out = []
    for row in trades:
        trade = dict(zip(keys, row))
        trade["opened_ts"] = ny(trade["opened"])
        trade["closed_ts"] = ny(trade["closed"]) if trade["closed"] else None
        out.append(trade)
    events = db.execute(
        "SELECT bar_time, strategy, kind, detail FROM live_events "
        "WHERE bar_time>=? AND bar_time<? ORDER BY id", (lo, hi)).fetchall()
    return out, [dict(zip(("at", "strategy", "kind", "detail"), e)) for e in events if e[0]]


def account_id(db, book):
    rows = [r[0] for r in db.execute("SELECT id FROM mt5_accounts WHERE server LIKE ? AND EXISTS ("
                                     "SELECT 1 FROM mt5_account_strategies s WHERE s.account_id="
                                     "mt5_accounts.id AND s.active=1)", (SERVER[book],))]
    if len(rows) != 1:
        sys.exit(f"expected one active {book} account, found {rows}")
    return rows[0]


def heartbeat_balance(db, account):
    row = db.execute("SELECT balance FROM mt5_bridge_heartbeats WHERE account_id=? "
                     "ORDER BY seen_at DESC LIMIT 1", (account,)).fetchone()
    return float(row[0]) if row and row[0] else 500.0


def refresh_broker_tables(book, markets, first, last):
    keys = sorted({IMPORTER_KEY.get(m, m) for m in markets})
    print(f"refreshing {book} minute tables for {', '.join(keys)} ({first} .. {last}) ...", flush=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "exness_import_1m.py"), "--broker", book,
         "--symbols", *keys,
         "--from-date", first, "--to-date", last],
        cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        print("  REFRESH FAILED -- fills fall back to the idealised model:\n    "
              + "\n    ".join(tail))


def backtest(ids, first, last, balance):
    with tempfile.TemporaryDirectory() as tmp:
        dump = os.path.join(tmp, "trades.json")
        env = dict(os.environ, EXNESS_RETIRED="1", DUMP_TRADES=dump)
        done = subprocess.run([str(BOOK), first, last, f"{balance:.2f}", ",".join(sorted(ids))],
                              cwd=ROOT / "live_trade", env=env, capture_output=True, text=True)
        if done.returncode != 0:
            sys.exit(f"backtest failed:\n{done.stderr or done.stdout}")
        trades = json.load(open(dump, encoding="utf-8")) if os.path.exists(dump) else []
    coverage = re.search(r"EXNESS LIVE FILLS\s+entries ([\d.]+)%\s+exits ([\d.]+)%", done.stdout)
    return trades, coverage.groups() if coverage else None


def explain_missing(events, strategy, start, bar):
    """What the live runtime recorded for `strategy` around a backtest entry."""
    near = [e for e in events if e["strategy"] == strategy
            and start <= ny(e["at"]) <= start + bar + 30 * 60
            and e["kind"] in ("entry_refused", "blocked", "entry_rejected", "transient_skip",
                              "replay_phantom_skipped", "deferred_entry_expired")]
    if near:
        e = near[0]
        return f"{e['kind']} at {e['at'][11:]}: {e['detail'] or '-'}"
    return "no live record at that time (runtime not running, or sleeve not active)"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("first", help="first New York day, YYYY-MM-DD")
    parser.add_argument("last", nargs="?", help="last New York day (default: first)")
    parser.add_argument("--balance", type=float, default=None,
                        help="backtest starting balance (default: last MT5 heartbeat)")
    parser.add_argument("--no-refresh", action="store_true",
                        help="do not refresh the broker minute tables first")
    parser.add_argument("--account", choices=sorted(SLEEVES), default="exness",
                        help="which book and account to compare (default: exness)")
    parser.add_argument("--all", action="store_true",
                        help="backtest every ACTIVE sleeve of the account, live-stamped or not")
    args = parser.parse_args()
    first = args.first
    last = args.last or first
    lo = f"{first} 00:00"
    hi = (date.fromisoformat(last) + timedelta(days=1)).isoformat() + " 00:00"

    registry = sleeves(args.account)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    account = account_id(db, args.account)
    live, events = live_rows(db, lo, hi)
    # This book's rows only: the two accounts' sleeve ids never collide
    # (FundedNext's carry `fn_`), so the registry is the filter.
    live = [t for t in live if t["strategy"] in registry]
    events = [e for e in events if e["strategy"] in registry]
    # Active rows count only once the runtime has stamped them live, and only
    # from that day: a sleeve activated after the window did not trade it.
    active = {strategy for strategy, started in db.execute(
        "SELECT strategy, live_started_at FROM mt5_account_strategies WHERE active=1 "
        "AND account_id=?", (account,))
        if args.all or (started and started < ny(hi))}
    balance = args.balance if args.balance is not None else heartbeat_balance(db, account)
    db.close()

    ids = ({t["strategy"] for t in live} | {e["strategy"] for e in events} | active) & set(registry)
    if len(ids) < 2:
        ids |= set(sorted(set(registry) - ids)[: 2 - len(ids)])
    markets = {registry[i]["market"] for i in ids}
    if not args.no_refresh:
        refresh_broker_tables(args.account, markets, first, last)
    bt, coverage = backtest(ids, first, last, balance)
    by_display = {v["display"]: k for k, v in registry.items()}

    # ---- match -----------------------------------------------------------
    backtest_by = defaultdict(list)
    for t in bt:
        backtest_by[by_display.get(t["s"], t["s"])].append(t)
    live_by = defaultdict(list)
    for t in live:
        live_by[t["strategy"]].append(t)
    matched, only_bt, only_live = [], [], []
    for sid in sorted(set(backtest_by) | set(live_by)):
        bar = registry.get(sid, {}).get("bar", 1800)
        pool = list(live_by.get(sid, []))
        for b in sorted(backtest_by.get(sid, []), key=lambda t: t["et"]):
            side = "long" if b["side"] > 0 else "short"
            hit = next((l for l in pool if l["side"] == side
                        and 0 <= l["opened_ts"] - b["et"] <= bar + 300), None)
            if hit is None:
                only_bt.append((sid, b))
            else:
                pool.remove(hit)
                matched.append((sid, b, hit))
        only_live.extend((sid, l) for l in pool)

    # ---- report ----------------------------------------------------------
    print(f"\nLIVE vs BACKTEST   {args.account} account {account}   {first} .. {last} (New York)"
          f"   backtest from ${balance:,.2f}")
    if coverage:
        print(f"backtest fills priced from the broker table: entries {coverage[0]}%, exits {coverage[1]}%"
              + ("" if float(coverage[0]) > 95 else "   <-- refresh the broker tables"))
    print(f"\n{'sleeve':34s} {'backtest':>14s} {'live':>14s}  matched")
    tot = [0, 0.0, 0, 0.0, 0]
    for sid in sorted(set(backtest_by) | set(live_by)):
        b, l = backtest_by.get(sid, []), live_by.get(sid, [])
        bp = sum(t["pnl"] for t in b)
        lp = sum(t["pnl"] or 0.0 for t in l)
        m = sum(1 for s, *_ in matched if s == sid)
        tot = [tot[0] + len(b), tot[1] + bp, tot[2] + len(l), tot[3] + lp, tot[4] + m]
        print(f"{sid:34s} {len(b):3d} {bp:+9.2f}  {len(l):3d} {lp:+9.2f}  {m:4d}")
    print(f"{'TOTAL':34s} {tot[0]:3d} {tot[1]:+9.2f}  {tot[2]:3d} {tot[3]:+9.2f}  {tot[4]:4d}")

    print("\nMATCHED TRADES   (entry px: backtest vs live engine; exit: backtest vs live)")
    exit_diffs = []
    for sid, b, l in sorted(matched, key=lambda m: m[1]["et"]):
        bar = registry.get(sid, {}).get("bar", 1800)
        overdue = b["xt"] % 60 == OVERDUE_SECONDS
        # A strategy exit is stamped on its candle and filled one candle later;
        # the overdue close is stamped at the moment it fires.
        bt_exit = b["xt"] if overdue else b["xt"] + bar
        reasons = OVERDUE_LIVE if overdue else FORCED_OK
        if overdue:
            # Live stamps its clock-driven close at the market's LAST BAR + 1
            # minute (`forced_flatten`), not at the moment it fired, so any
            # emergency flatten up to that moment, the same day, is this exit.
            same_exit = (l["closed_ts"] is not None and 0 <= bt_exit - l["closed_ts"] <= 86_400
                         and (l["reason"] or "") in reasons)
        else:
            same_exit = (l["closed_ts"] is not None and abs(l["closed_ts"] - bt_exit) <= 180
                         and (l["reason"] or "") in reasons)
        price_gap = l["entry"] - b["raw"]
        flags = []
        if abs(price_gap) > 1e-6 * max(1.0, abs(b["raw"])):
            flags.append(f"DATA entry {price_gap:+.5g}")
        if not same_exit:
            flags.append(f"EXIT {l['reason'] or 'open'}")
            exit_diffs.append((sid, b, l, bt_exit))
        print(f"  {sid:32s} {label(b['et'])} {('L' if b['side'] > 0 else 'S')}  "
              f"px {b['raw']:.5g}/{l['entry']:.5g}  exit {label(bt_exit)}/"
              f"{label(l['closed_ts']) if l['closed_ts'] else 'open':11s}  "
              f"${b['pnl']:+.2f}/{(l['pnl'] or 0.0):+.2f}  {'; '.join(flags)}")

    if exit_diffs:
        print("\nEXITS THAT DIFFER")
        for sid, b, l, bt_exit in exit_diffs:
            print(f"  {sid:32s} {label(b['et'])}  backtest {label(bt_exit)}, live "
                  f"{label(l['closed_ts']) if l['closed_ts'] else 'still open'} ({l['reason'] or '-'})")
    if only_bt:
        print("\nONLY IN BACKTEST   (live did not trade it)")
        for sid, b in sorted(only_bt, key=lambda x: x[1]["et"]):
            bar = registry.get(sid, {}).get("bar", 1800)
            print(f"  {sid:32s} {label(b['et'])} {('long' if b['side'] > 0 else 'short'):5s} "
                  f"${b['pnl']:+.2f}  -> {explain_missing(events, sid, b['et'], bar)}")
    if only_live:
        print("\nONLY IN LIVE   (the backtest did not trade it)")
        for sid, l in sorted(only_live, key=lambda x: x[1]["opened_ts"]):
            print(f"  {sid:32s} {l['opened'][5:]} {l['side']:5s} ${(l['pnl'] or 0.0):+.2f}  "
                  f"exit {l['reason'] or 'open'}")
    if not (exit_diffs or only_bt or only_live):
        print("\nevery trade matched, with the same exits")


if __name__ == "__main__":
    main()
