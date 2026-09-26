"""What the canon book loses to the feed lag it is never charged for.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the tables into the reply
([[paste-results-into-the-reply]]).

WHAT THIS ANSWERS.

Every sealed result in this repository assumes a bar is in hand the instant it
closes: a sleeve reads bar `i`, and its entry goes on at bar `i+1`'s open. Live
that is false. The canon sleeves decide on VENDOR tables and every vendor
publishes behind the clock -- `idk_market_live_data_feeds` measures Dukascopy at
about 1.3 minutes, which is longer than the bar the sleeve is waiting for. A 1m
Dukascopy sleeve is still waiting for bar `i` when bar `i+1` opens, so the entry
the backtest gives it at `i+1`'s open is an entry it could not have placed.

This is NOT the same question as `exness_broker_fills`. That one holds the
decisions fixed and swaps the prices, and measured execution at nearly free
(+630.8% against +634.3%). This holds the PRICES fixed and moves WHEN the
decision can be acted on. Nothing in the repository has charged for it.

HOW IT IS CHARGED.

`cfd_families.backtest` grew a `decision_lag` argument: a pending entry
becomes fillable at bar `i+1+lag`'s open instead of `i+1`'s. Zero reproduces
every sealed result byte for byte, which `selftest` asserts rather than
assuming. Nothing else moves -- the signal is still READ on bar `i`, the stop
distance and sizing are unchanged, and a lagged entry that runs past its own
session close is dropped exactly as a late signal always was.

THE LAG IS IN BARS, AND THAT IS WHY IT BITES UNEVENLY.

Lag is wall-clock but the loop is bars, so the SAME 1.3 minutes is a whole bar
to a 1m sleeve and nothing at all to a 30m one. `bars_for` does that conversion
per sleeve and it is the only place the two units meet. The honest reading of a
1-bar result on a 30m sleeve is "half an hour late", which is far beyond any
measured feed lag -- it is there as a sensitivity bound, not as a forecast.

    py -m sandbox.research.exness_latency selftest
    py -m sandbox.research.exness_latency measure
    py -m sandbox.research.exness_latency book
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

from sandbox import data
from sandbox.research import cfd_families as ef

UTC = timezone.utc
OUT_PATH = os.path.join(os.path.dirname(__file__), "exness_latency.json")

#: Publish lag in SECONDS, per vendor: how long after a bar CLOSES the vendor
#: will serve it complete.
#:
#: DUKASCOPY WAS 78.0 UNTIL 2026-09-07 AND THAT NUMBER TIMED THE WRONG EVENT.
#: It came from this line in `idk_market_live_data_feeds`: "at 16:52:16 UTC the
#: newest GBP/JPY minute bar was 16:51:00", read as 76 seconds. Two errors
#: compound in it:
#:
#:   1. Dukascopy serves the minute STILL BEING TRADED. Asked at 11:10:48 it
#:      hands back a bar stamped 11:10, forty-eight seconds into it. That is a
#:      PARTIAL bar; `write_rows` has always dropped it, so the daemon never
#:      acted on one. Watching the raw newest index times its appearance, not a
#:      publication.
#:   2. The difference was taken to the bar's OPEN. A publish lag is measured to
#:      its CLOSE, which is what `measure_dukascopy` below does.
#:
#: Measured properly on 2026-09-07 by `_seventh_publish` -- polling gbpjpy every
#: two seconds and filtering to complete bars the way the daemon does -- five
#: consecutive arrivals gave a MEDIAN 0.6s, min 0.0, max 2.4.
#:
#: THE CONSEQUENCE IS THAT EVERY LIVE-EXECUTION FIGURE BUILT ON 78.0 CHARGED
#: SEVENTY-SIX SECONDS OF DRIFT THE ACCOUNT NEVER PAID, so those numbers are a
#: LOWER bound on the book. Re-run `fill_models.exness precompute` after
#: changing this or the maps keep the old delay.
#:
#: Binance is a websocket and closes a minute bar on the minute, so its lag is
#: transport only. Exness M30 is the broker's own terminal and the one feed
#: where the bar and the fill come from the same place.
MEASURED_LAG_SECONDS = {
    "dukascopy": 0.6,
    "binance": 2.0,
    "exness": 5.0,
}

#: Which vendor each canon symbol's DECIDING table comes from. This is the
#: mapping in `idk_market_live_data_feeds` -- duplicated for the same reason
#: that module duplicates `SESSION`, so a research script does not drag a live
#: daemon's imports behind it.
#:
#: `nq` is Databento over Bookmap and does not arrive through that daemon at
#: all; it is a direct exchange feed and the closest thing here to zero lag.
FEED = {
    "audusd": "dukascopy", "eurjpy": "dukascopy", "gbpjpy": "dukascopy",
    "gbpusd": "dukascopy", "usdjpy": "dukascopy", "jp225": "dukascopy",
    "uk100": "dukascopy", "ukoil": "dukascopy",
    "ethusd": "binance", "btc": "binance",
    "xalusd": "exness",
    "nq": "databento",
}

MEASURED_LAG_SECONDS["databento"] = 1.0

#: The lag settings the book is re-run across, in BARS.
#:
#: 0 is the sealed assumption and the control. 1 is what the measured Dukascopy
#: lag actually costs a 1m sleeve. 2 and 3 are not forecasts of any feed -- they
#: are the sensitivity question, "how fast does this decay", which is the part
#: that says whether the book is standing on a knife edge or on a plateau.
LAG_GRID = (0, 1, 2, 3)


def log(message):
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def bars_for(symbol, bar_minutes, seconds=None):
    """Whole bars of delay the feed's publish lag costs `symbol`.

    Rounded UP, because a sleeve that is even one second late for a bar's open
    has missed it and waits for the next. That is a real ceiling effect and not
    a conservatism: at 1m a 78-second Dukascopy lag is two bars, not 1.3.
    """
    vendor = FEED.get(symbol, "dukascopy")
    lag = MEASURED_LAG_SECONDS[vendor] if seconds is None else seconds
    return int(math.ceil(lag / (bar_minutes * 60.0)))


# --------------------------------------------------------------------------- #
# measuring the lag
# --------------------------------------------------------------------------- #


def measure_dukascopy(samples=6, interval=30.0):
    """Observed `now - newest published bar close` for a live Dukascopy pair.

    ONLY AN OPEN MARKET ANSWERS THIS. A shut one serves its last bar forever
    and would report the whole weekend as the publish lag, which is the same
    mistake the weekend spread reading made
    ([[weekend-spreads-are-not-tradeable]]). So the reading is tagged with
    whether the market was open, and a closed one is reported as unmeasurable
    rather than as a number.
    """
    import dukascopy_python as dk
    from dukascopy_python import instruments as ins

    probes = [("gbpjpy", ins.INSTRUMENT_FX_CROSSES_GBP_JPY),
              ("eurjpy", ins.INSTRUMENT_FX_CROSSES_EUR_JPY)]
    out = {}
    for name, instrument in probes:
        readings, last_seen = [], None
        for _ in range(samples):
            now = datetime.now(tz=UTC)
            frame = dk.fetch(instrument, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_BID,
                             now - timedelta(minutes=15), now)
            if frame is None or not len(frame):
                time.sleep(interval)
                continue
            newest = frame.index[-1].to_pydatetime().replace(tzinfo=UTC)
            if last_seen is not None and newest > last_seen:
                # Only a bar that ARRIVED during the watch times the publish
                # delay. The first reading is whatever was already cached and
                # says nothing about how long it took to get there.
                readings.append((now - (newest + timedelta(minutes=1)))
                                .total_seconds())
            last_seen = newest
            time.sleep(interval)
        out[name] = {
            "samples": len(readings),
            "median_seconds": round(statistics.median(readings), 1)
            if readings else None,
            "max_seconds": round(max(readings), 1) if readings else None,
            "note": None if readings else "no new bar arrived -- market shut?",
        }
    return out


def measure_binance(samples=6, interval=20.0):
    """Observed `now - newest closed 1m kline` on the Binance REST endpoint.

    Crypto trades continuously, so unlike Dukascopy this is measurable at any
    hour -- which is also why it is the only feed here whose lag can be checked
    on the weekend the rest of the book is shut.
    """
    import urllib.request

    url = ("https://api.binance.com/api/v3/klines"
           "?symbol=ETHUSDT&interval=1m&limit=3")
    readings, last_seen = [], None
    for _ in range(samples):
        now = datetime.now(tz=UTC)
        try:
            with urllib.request.urlopen(url, timeout=15) as handle:
                rows = json.loads(handle.read())
        except Exception as error:  # noqa: BLE001 - a blocked ISP is normal here
            return {"ethusd": {"samples": 0, "median_seconds": None,
                               "note": f"{type(error).__name__}: {error} "
                                       "(is Warp on? "
                                       "[[isp-blocks-crypto-exchanges]])"}}
        # THE NEWEST BAR WHOSE CLOSE TIME HAS ACTUALLY PASSED, not `rows[-2]`.
        # Assuming a fixed position measured -15.8 seconds on the first run --
        # a bar arriving before it closed, which is not a lag at all but an
        # off-by-one against a forming kline. Testing the close time against
        # the clock cannot be off by one however many bars the endpoint returns.
        past = [row for row in rows if row[6] / 1000.0 <= now.timestamp()]
        if not past:
            time.sleep(interval)
            continue
        closed = datetime.fromtimestamp(past[-1][6] / 1000.0, tz=UTC)
        if last_seen is not None and closed > last_seen:
            readings.append((now - closed).total_seconds())
        last_seen = closed
        time.sleep(interval)
    return {"ethusd": {
        "samples": len(readings),
        "median_seconds": round(statistics.median(readings), 1)
        if readings else None,
        "max_seconds": round(max(readings), 1) if readings else None,
        "note": None if readings else "no bar closed during the watch",
    }}


def measure(samples=6, path=OUT_PATH):
    """Probe every vendor and print the lag each one is actually publishing at."""
    log("probing feeds (each needs a bar to ARRIVE, so this takes minutes)")
    out = {"measured_at": datetime.now(tz=UTC).isoformat(timespec="seconds")}
    try:
        out["binance"] = measure_binance(samples=samples)
    except Exception as error:  # noqa: BLE001
        out["binance"] = {"error": f"{type(error).__name__}: {error}"}
    try:
        out["dukascopy"] = measure_dukascopy(samples=samples)
    except Exception as error:  # noqa: BLE001
        out["dukascopy"] = {"error": f"{type(error).__name__}: {error}"}

    print(f"\n{'feed':12}{'symbol':10}{'median s':>10}{'max s':>9}"
          f"{'samples':>9}  note")
    for vendor in ("binance", "dukascopy"):
        block = out.get(vendor) or {}
        if "error" in block:
            print(f"{vendor:12}{'-':10}{'-':>10}{'-':>9}{'-':>9}  {block['error']}")
            continue
        for name, row in block.items():
            median = row.get("median_seconds")
            top = row.get("max_seconds")
            print(f"{vendor:12}{name:10}"
                  f"{'-' if median is None else f'{median:.1f}':>10}"
                  f"{'-' if top is None else f'{top:.1f}':>9}"
                  f"{row.get('samples', 0):>9}  {row.get('note') or ''}")
    print("\nBaked-in defaults now in use (seconds): "
          + ", ".join(f"{k}={v:g}" for k, v in MEASURED_LAG_SECONDS.items()))
    _merge(path, {"measure": out})
    return out


# --------------------------------------------------------------------------- #
# charging it to the book
# --------------------------------------------------------------------------- #


def _merge(path, block):
    stored = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    stored.update(block)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
    return stored


def _native_rows():
    """`(sleeve, row)` for every canon member that runs through `ef.backtest`.

    The five NQ sleeves are EXTERNAL: they are replayed by `combined_book`
    through `execution.resolve`, not by this loop, so no `decision_lag` reaches
    them. That is the RIGHT answer rather than a hole -- NQ arrives over
    Databento/Bookmap, a direct exchange feed measured near a second, which at a
    30-minute bar is zero bars of delay however it is rounded. It is stated here
    so the gap is a recorded decision and not something a later reader has to
    rediscover from a smaller trade count.
    """
    from sandbox.research import exness_combined_strategies as cs

    out = []
    for sleeve in cs.BOOK:
        if sleeve in cs.EXTERNAL:
            continue
        symbol, family = sleeve.split(":", 1)
        row = _sealed_row(symbol, family)
        if row is None:
            log(f"  {sleeve}: no sealed parameters found -- skipped")
            continue
        out.append((sleeve, row))
    return out


def _sealed_row(symbol, family):
    """This cell's frozen row, read the way `build --members canon` reads it."""
    from sandbox.research import exness_combined_strategies as cs

    for name in sorted(os.listdir(cs.RESULTS)):
        if not (name.startswith("exness_families_")
                and f"_{symbol}_" in name and "null" not in name):
            continue
        with open(os.path.join(cs.RESULTS, name), encoding="utf-8") as handle:
            payload = json.load(handle)
        cell = (payload.get("families") or {}).get(family)
        if cell:
            return {"symbol": symbol, "family": family,
                    "params": cs._retuple(cell["params"])}
    return None


def selftest():
    """Assert `decision_lag=0` is the sealed loop and that 1 actually moves it.

    THE FIRST HALF IS THE IMPORTANT ONE. A lag switch that quietly changed the
    zero case would restate every published number in the repository as a side
    effect, and the change would be invisible because the control had moved with
    it. So the assertion is that the DEFAULT call and an explicit
    `decision_lag=0` are identical, field for field, on every canon sleeve.
    """
    from sandbox.research import exness_combined_strategies as cs

    checked = failed = inert = 0
    print(f"{'sleeve':32}{'trades@0':>10}{'trades@1':>10}"
          f"{'return@0':>11}{'return@1':>11}  verdict")
    for sleeve, row in _native_rows():
        # `INSTRUMENTS` is populated by `resolve`, exactly as `build` does it
        # before touching a context. Stale spreads are accepted because this
        # test compares a sleeve against ITSELF -- the cost constant cancels.
        ef.resolve(row["symbol"], allow_stale=True)
        bars, ctx = cs._context(row["symbol"])
        common = dict(lo=ef.IS_END, hi=ef.OOS_END,
                      fill_bars=cs._fill_bars(row["symbol"], bars))
        base = ef.backtest(row["family"], bars, ctx, row["params"], **common)
        same = ef.backtest(row["family"], bars, ctx, row["params"],
                           decision_lag=0, **common)
        moved = ef.backtest(row["family"], bars, ctx, row["params"],
                            decision_lag=1, **common)
        checked += 1
        if base != same:
            failed += 1
            verdict = "BROKEN: lag 0 is not the sealed loop"
        elif base == moved:
            inert += 1
            verdict = "inert (lag 1 changed nothing)"
        else:
            verdict = "ok"
        print(f"{sleeve:32}{base['trades']:>10}{moved['trades']:>10}"
              f"{base['return_pct']:>10.1f}%{moved['return_pct']:>10.1f}%"
              f"  {verdict}")
    print(f"\n{checked} sleeves checked, {failed} broken, {inert} inert")
    return failed == 0


def book(lags=LAG_GRID, path=OUT_PATH):
    """Re-run `build --members canon` once per lag, in its own process.

    ONE SUBPROCESS PER LAG, and it is not paranoia: `sleeve_trades` memoises on
    a key that does not mention the lag, so a second lag in the same process
    would be served the first one's trades and the whole study would report that
    latency is free.
    """
    import subprocess

    rows = []
    for lag in lags:
        env = dict(os.environ, EXNESS_DECISION_LAG_BARS=str(int(lag)))
        log(f"canon book at decision_lag={lag} bars "
            f"({lag * 30} minutes at the 30m canon bar)")
        proc = subprocess.run(
            [sys.executable, "-m", "sandbox.research.exness_combined_strategies",
             "build", "--members", "canon"],
            env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            log(f"  lag {lag} FAILED rc={proc.returncode}")
            print(proc.stdout[-4000:])
            print(proc.stderr[-4000:], file=sys.stderr)
            rows.append({"lag_bars": lag, "error": f"rc={proc.returncode}"})
            continue
        with open(cs_out_path(), encoding="utf-8") as handle:
            payload = json.load(handle)
        block = payload["book"]
        rows.append({
            "lag_bars": lag,
            "lag_minutes": lag * payload.get("bar_minutes", 30),
            "return_pct": block["return_pct"],
            "mtm_dd_pct": block["mtm_dd_pct"],
            "closed_dd_pct": block["max_dd_pct"],
            "trades": block["trades"],
            "final": block["final"],
            "monthly_sharpe": payload.get("monthly_sharpe"),
            "by_sleeve": block.get("by_sleeve"),
        })
        log(f"  lag {lag}: {block['return_pct']:+.1f}%  "
            f"MTM dd {block['mtm_dd_pct']:.1f}%  {block['trades']} trades")

    _report(rows)
    _merge(path, {"book": rows, "lag_grid": list(lags)})
    return 0


def cs_out_path():
    from sandbox.research import exness_combined_strategies as cs
    return cs.OUT_PATH


def _report(rows):
    base = next((r for r in rows if r.get("lag_bars") == 0
                 and "error" not in r), None)
    print(f"\n{'lag':>5}{'minutes':>9}{'return':>11}{'MTM dd':>9}"
          f"{'closed dd':>11}{'trades':>8}{'mSharpe':>9}{'vs lag 0':>10}")
    for row in rows:
        if "error" in row:
            print(f"{row['lag_bars']:>5}{'':>9}  {row['error']}")
            continue
        delta = ("-" if base is None
                 else f"{row['return_pct'] - base['return_pct']:+.1f}pp")
        sharpe = row.get("monthly_sharpe")
        print(f"{row['lag_bars']:>5}{row['lag_minutes']:>9}"
              f"{row['return_pct']:>10.1f}%{row['mtm_dd_pct']:>8.1f}%"
              f"{row['closed_dd_pct']:>10.1f}%{row['trades']:>8}"
              f"{'-' if sharpe is None else f'{sharpe:.2f}':>9}{delta:>10}")

    if base and len(rows) > 1:
        print("\nPer-sleeve contribution, in percent of the book's return "
              "([[show-per-sleeve-detail-every-time]])")
        names = sorted(base.get("by_sleeve") or {})
        live = [r for r in rows if "error" not in r]
        header = "".join(("lag %d" % r["lag_bars"]).rjust(12) for r in live)
        print(f"{'sleeve':32}{header}")
        for name in names:
            cells = ""
            for row in rows:
                if "error" in row:
                    continue
                value = (row.get("by_sleeve") or {}).get(name)
                cells += f"{'-':>12}" if value is None else f"{_pnl(value):>12,.0f}"
            print(f"{name:32}{cells}")


def _pnl(value):
    if isinstance(value, dict):
        for key in ("pnl", "net", "total"):
            if key in value:
                return value[key]
        return float("nan")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="lag 0 must reproduce the sealed loop")
    probe = sub.add_parser("measure", help="probe each vendor's publish lag")
    probe.add_argument("--samples", type=int, default=6)
    run = sub.add_parser("book", help="re-run the canon book across LAG_GRID")
    run.add_argument("--lags", type=int, nargs="+", default=list(LAG_GRID))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "selftest":
        return 0 if selftest() else 1
    if args.command == "measure":
        measure(samples=args.samples)
        return 0
    if args.command == "book":
        return book(args.lags)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
