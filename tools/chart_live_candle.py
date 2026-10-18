#!/usr/bin/env python3
"""Publish the forming Dukascopy / Binance candle for the web chart.

    py tools/chart_live_candle.py

FOR THE WEB CHART ONLY. The live trading stack never reads what this writes:
the feeds daemon (`idk_market_live_data_feeds.py`) stays the only writer of the
Parquet tables, and it writes closed bars only. A forming bar in a table would
be a strategy reading the future's open as this minute's close.

Follows the web app: while a chart is open on a symbol, the web app lists it
in `data/live/watch.json` (with a heartbeat), and this script publishes only
those symbols. For each it writes `data/live/<table>.live.json` -- the
previous candle and the forming one -- replaced atomically. The web app ignores
the file once it is a little old and falls back to the stored bars.

    binance     BTCUSD ETHUSD ETHBTC, REST klines every `BINANCE_POLL_SECONDS`
    dukascopy   everything in `DUKASCOPY`, bid ticks every `DUKASCOPY_POLL_SECONDS`

DUKASCOPY IS POLLED GENTLY because the trading feed daemon uses the same
vendor from the same address, and Dukascopy throttles a busy client with 503s
and then connect timeouts. A failing symbol backs off for `BACKOFF_SECONDS`.

Each Dukascopy instrument was checked 2026-10-14 against its stored table, to
the digit, on the last three stored minutes. `aus200_1m` and `xngusd_1m` did
NOT match `AUS.IDX/AUD` / `GAS.CMD/USD`, so they are left out rather than
drawing a live candle from a different series than the history.

Times use the store's convention: New York wall clock written as if it were
UTC, so the chart lines up with the Parquet bars.

CLOUDFLARE WARP MUST BE ON: dukascopy.com and Binance are blocked on this
network without it.
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import dukascopy_python as _dk
import requests

NEW_YORK = ZoneInfo("America/New_York")
LIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "live"
WATCH_PATH = LIVE_DIR / "watch.json"
WATCH_STALE_SECONDS = 30  # the web app refreshes the file every few seconds

BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_POLL_SECONDS = 1.0
DUKASCOPY_POLL_SECONDS = 3.0
BACKOFF_SECONDS = 30.0
FETCH_TIMEOUT_SECONDS = 8.0
LOOP_SECONDS = 0.25

# Chart symbol -> (Binance symbol, table).
BINANCE = {
    "BTCUSD": ("BTCUSDT", "btc_1m"),
    "ETHUSD": ("ETHUSDT", "ethusd_1m"),
    "ETHBTC": ("ETHBTC", "ethbtc_1m"),
}

# Chart symbol -> Dukascopy instrument; the table is `<symbol>_1m`.
DUKASCOPY = {
    **{s: f"{s}.US/USD" for s in (
        "AAPL", "AMD", "AMZN", "AVGO", "CVX", "GOOGL", "GS", "JPM", "MSFT",
        "MU", "NVDA", "ORCL", "PLTR", "TSLA", "TSM")},
    "AUDJPY": "AUD/JPY", "AUDUSD": "AUD/USD", "EURJPY": "EUR/JPY",
    "EURUSD": "EUR/USD", "GBPJPY": "GBP/JPY", "GBPUSD": "GBP/USD",
    "USDCAD": "USD/CAD", "USDJPY": "USD/JPY",
    "DE40": "E_DAAX", "FR40": "FRA.IDX/EUR", "HK50": "HKG.IDX/HKD",
    "JP225": "JPN.IDX/JPY", "STOXX50": "EUS.IDX/EUR", "UK100": "E_Futsee-100",
    "US500": "E_SandP-500", "USTEC": "E_NQ-100",
    "UKOIL": "E_Brent", "USOIL": "E_Light",
    "XAGAUD": "XAG/AUD", "XAGEUR": "XAG/EUR", "XAGGBP": "XAG/GBP",
    "XAGUSD": "XAG/USD", "XAUAUD": "XAU/AUD", "XAUEUR": "XAU/EUR",
    "XAUGBP": "XAU/GBP", "XCUUSD": "COPPER.CMD/USD", "XPDUSD": "XPD.CMD/USD",
    "XPTUSD": "XPT.CMD/USD",
}


class _Session(requests.Session):
    """Keep-alive with a default timeout: the vendor library passes none, so a
    stalled socket would otherwise block the loop forever."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", FETCH_TIMEOUT_SECONDS)
        return super().request(*args, **kwargs)


SESSION = _Session()
_dk.requests = SESSION  # `_dk._fetch` resolves `requests.get` at call time


def log(message: str) -> None:
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def stored_seconds(instant: datetime) -> int:
    """Real instant -> New York wall clock relabelled UTC."""
    return int(instant.astimezone(NEW_YORK).replace(tzinfo=UTC).timestamp())


def write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def watched_symbols() -> set[str]:
    """Symbols the web app currently shows, if its heartbeat is recent."""
    try:
        watch = json.loads(WATCH_PATH.read_text(encoding="utf-8"))
        if time.time() - float(watch.get("updated", 0)) > WATCH_STALE_SECONDS:
            return set()
        return {str(s).upper() for s in watch.get("symbols", []) if str(s).isalnum()}
    except (OSError, ValueError, TypeError):
        return set()


def binance_bars(symbol: str) -> list[dict]:
    """The previous and the forming 1-minute kline."""
    reply = SESSION.get(BINANCE_URL, params={"symbol": symbol, "interval": "1m", "limit": 2})
    reply.raise_for_status()
    return [
        {
            "time": stored_seconds(datetime.fromtimestamp(k[0] / 1000, tz=UTC)),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]),
        }
        for k in reply.json()
    ]


def dukascopy_bars(instrument: str) -> list[dict]:
    """The previous and the forming 1-minute bid bar, built from bid ticks.

    FROM TICKS, NOT THE 1-MINUTE ENDPOINT. That endpoint does serve the minute
    still being traded, but as a snapshot: measured 2026-10-14 on E_NQ-100,
    polled every 3s, its forming bar kept one close for 40+ seconds and the next
    minute had not appeared 24s after it opened. The tick endpoint's newest
    tick was under a second old on every poll, at ~0.2s a request."""
    now = datetime.now(tz=UTC)
    start = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    ticks = _dk.fetch(instrument, _dk.INTERVAL_TICK, _dk.OFFER_SIDE_BID, start, now, max_retries=0)
    bars: list[dict] = []
    for ts, bid in ticks["bidPrice"].items():
        minute = stored_seconds(ts.to_pydatetime().replace(second=0, microsecond=0))
        price = float(bid)
        if bars and bars[-1]["time"] == minute:
            bar = bars[-1]
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
        else:
            bars.append({"time": minute, "open": price, "high": price, "low": price, "close": price})
    return bars[-2:]


class Feed:
    """One symbol's live file."""

    def __init__(self, symbol: str):
        if symbol in BINANCE:
            source, table = BINANCE[symbol]
            self.fetch = lambda: binance_bars(source)
            self.every = BINANCE_POLL_SECONDS
        else:
            instrument = DUKASCOPY[symbol]
            table = f"{symbol.lower()}_1m"
            self.fetch = lambda: dukascopy_bars(instrument)
            self.every = DUKASCOPY_POLL_SECONDS
        self.symbol = symbol
        self.path = LIVE_DIR / f"{table}.live.json"
        self.due = 0.0
        self.last = ""

    def poll(self) -> None:
        try:
            bars = self.fetch()
        except Exception as error:  # noqa: BLE001 - back off, then retry
            log(f"{self.symbol}: fetch failed, retrying in {BACKOFF_SECONDS:.0f}s: {repr(error)[:160]}")
            self.due = time.monotonic() + BACKOFF_SECONDS
            return
        self.due = time.monotonic() + self.every
        if not bars:
            return  # market closed: nothing forming
        live = json.dumps(bars, separators=(",", ":"))
        if live != self.last:
            write_json(self.path, {"bars": bars})
            self.last = live


def main() -> int:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    stop = Event()

    def request_stop(*_: object) -> None:
        log("shutting down")
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), request_stop)
            except (ValueError, OSError):
                pass

    log(f"publishing the web chart's forming candles to {LIVE_DIR}")
    feeds: dict[str, Feed] = {}
    while not stop.is_set():
        wanted = {s for s in watched_symbols() if s in BINANCE or s in DUKASCOPY}
        for symbol in wanted - feeds.keys():
            feeds[symbol] = Feed(symbol)
            log(f"now publishing {symbol}")
        for symbol in feeds.keys() - wanted:
            del feeds[symbol]
            log(f"stopped publishing {symbol}")
        now = time.monotonic()
        for feed in feeds.values():
            if now >= feed.due:
                feed.poll()
        stop.wait(LOOP_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
