"""What the canon book would have got, executed the way the live server does.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the tables into the reply
([[paste-results-into-the-reply]]).

WHAT THIS ANSWERS.

Every sealed number in this repository is a backtest fill: the entry is the next
bar's open, the exit is the stop or the target to the digit, and the cost is one
spread constant per symbol. The live account does none of those three things.
This module runs the same decisions down the actual execution path and reports
what is left.

The path, read out of the code that walks it:

    1  the bar closes on the vendor's clock
    2  the vendor publishes it -- dukascopy 78s, binance 2s, databento ~1s
       (`exness_latency.MEASURED_LAG_SECONDS`)
    3  the Rust runtime picks it up on a 500ms poll and the strategy decides
    4  the decision is queued as an MT5 command and `mt5/bridge.py` claims it
       on a 0.25s poll (`BRIDGE_QUEUE_SECONDS` is 2, 3 and 4 together)
    5  `order_send` fills at the broker's own quote, paying that quote's spread

THE EXIT IS A MARKET ORDER AND THAT IS THE FINDING THIS MODULE EXISTS FOR.

There is no broker-side stop or take-profit on this account. The order payload
has nowhere to put one -- `live_trade/src/live/mt5/bridge.rs` sends
`ORDER|id|action|symbol|volume|magic|deviation|ticket|created_at` -- so a stop
is a level the RUNTIME watches, and `family.rs` can only test it when the candle
it is building ROLLS. On a 30-minute sleeve a stop breached at 09:31 is acted on
at 10:00, and then it walks steps 2 to 5 like any other order.

The backtest charges that exit the stop price. The account pays the market one
bar plus one lag plus one queue later. `exit_delay_seconds` is where those three
terms are added up and it is the biggest correction here by a wide margin --
every other leg is measured in seconds and this one starts at a whole bar.

WHAT IS NOT MODELLED, AND WHY NOT.

`MT5_DEVIATION_POINTS` (20) does NOT reject a stale decision. `bridge.py` reads
`symbol_info_tick` at send time and passes THAT as the requested price, so the
deviation only covers the microseconds between reading the quote and the server
filling. It cannot be measured from second-resolution ticks and it is not
pretended to be.

Entry commands DO expire: `march.rs` fails any `long`/`short` older than 30
seconds. That only fires when the bridge is offline or wedged, so simulating it
would mean simulating bridge downtime, which no data here records. It is left
out and named rather than guessed at.

`feed_stale_after` (`max(market_step + 90, 180)`s) triggers `emergency_flatten`,
a market close at whatever is quoted. Under SESSION_ONLY nothing survives its
own session close, so this can only bite on a gap INSIDE a session -- a vendor
outage or a half-day. `FEED_STALE_AFTER_SECONDS` records the threshold.

THE WINDOW IS SEVEN MONTHS AND IT OPENS WHERE THE LAST SYMBOL'S TICKS DO.

Exness serves ticks only from 2026-01, and not from the same moment on every
symbol: btc and ethusd start 2026-01-01 09:30, ukoil not until 2026-01-02 09:00.
`tick_window_start` takes the LATEST of those, so `cells` and `book` both run
2026-01-02 09:00 .. 2026-08-21. That removes the coverage difference BETWEEN
symbols, which is what it is for, and it is the smaller of the two problems.

THE TABLES ARE FULL OF HOLES INSIDE THAT WINDOW AND `priced` IS ONLY ~62%.

Measured 2026-08-31 by `coverage`: usdjpy and ukoil hold a median of 17 hours a
day, ukoil never a single complete day, and 91 gaps longer than an hour with a
median length of 14. A representative one runs Mon 23:59:57 straight to Tue
13:00:01. ethusd and btc are better at 24 median hours but still only two thirds
of days complete.

So a trade whose entry bar falls in a hole keeps the sealed fill and is silently
excluded from the correction. That is safe -- it can never invent a cost -- but
it is NOT unbiased: the holes sit at particular hours, and a family that enters
at those hours is under-corrected relative to one that does not. Every delta in
`cells` and `book` is therefore a LOWER BOUND on the real execution cost, and
the per-sleeve ordering is not trustworthy while coverage differs by sleeve.

THE FIX IS AN IMPORT, NOT A MODEL. `tools/exness_tick_import.py` is what fills
them; nothing in this module can. Re-run it, re-run `precompute`, then check
`coverage` reads near 24 before leaning on a per-sleeve number.

What the window costs even when full: seven months is a short book, and a
monthly Sharpe over eight points is not a number to lean on
([[short-month-series-fakes-monthly-sharpe]]). The window also sits INSIDE the
period the survivor pool was screened on
([[exness-survivor-pool-is-oos-conditioned]]), so everything here is a cost
correction on a known-conditioned sample and never a holdout.

SLIPPAGE IS NOT MEASURABLE HERE AND THE MODULE SAYS SO RATHER THAN GUESSING.

A tick table is what the market QUOTED. Slippage is the gap between the quote a
market order saw and the price it got, which only the account's own deal history
knows. This account holds 66 deals, all inside 2026-08-12..08-16 -- one or two
per symbol across fifteen symbols. `slippage` reports them for what they are: a
four-day sample that can establish an order of magnitude and nothing else.

    py -m sandbox.research.exness_live_execution selftest
    py -m sandbox.research.exness_live_execution coverage  <- read FIRST
    py -m sandbox.research.exness_live_execution precompute
    py -m sandbox.research.exness_live_execution book       <- the portfolio
    py -m sandbox.research.exness_live_execution cells      <- per sleeve, solo
    py -m sandbox.research.exness_live_execution spreads
    py -m sandbox.research.exness_live_execution sleeve
    py -m sandbox.research.exness_live_execution latency
    py -m sandbox.research.exness_live_execution slippage
"""

import argparse
import bisect
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sandbox.research import exness_families as ef

UTC = timezone.utc
OUT_PATH = os.path.join(os.path.dirname(__file__), "exness_live_execution.json")

#: Ticks either side of a bar's open that count as "the spread it crossed".
#:
#: A single quote is too fragile -- one crossed or stale print would decide a
#: trade's cost -- and a whole bar is the wrong question, because the entry
#: happens at the OPEN and the spread thirty minutes later is not what it paid.
#: A minute is long enough to hold several quotes on every symbol here and short
#: enough to still be the open.
ENTRY_WINDOW_SECONDS = 60

#: Below this many quotes in the window, the bar is left on the constant rather
#: than priced from one or two prints -- the same floor `tick_spreads` applies
#: to a whole symbol, for the same reason.
MIN_QUOTES = 3

#: The tick tables begin here on every symbol. Kept as a constant so a study
#: that reports a "tick-priced" return states the window it can possibly cover.
TICK_HISTORY_START = "2026-01-01"

#: WHAT THE VENDOR LAG ACTUALLY BUYS YOU, derived 2026-08-31 from
#: `tools/idk_market_live_data_feeds.py` rather than from the constant's name.
#:
#: `MEASURED_LAG_SECONDS["dukascopy"]` is 78s measured from a minute bar's OWN
#: START (at 16:52:16 the newest GBP/JPY bar was 16:51:00), which is 18s after
#: that bar CLOSED. But the daemon does not read continuously: it ticks on the
#: wall-clock minute plus `TICK_OFFSET_SECONDS` (5s) and fetches a 4-minute
#: tail. So for a 30-minute sleeve whose fill bar opens at T:
#:
#:     T + 18    the deciding slot's last minute, [T-60,T), is published
#:     T +  5    daemon tick -- too early, misses it
#:     T + 65    next daemon tick catches it
#:     T + 67    fetch returns (~1.9s) and the row is written
#:     T + 68    Rust 500ms poll, bridge 0.25s poll, order_send
#:
#: The real delay is therefore ~68s and it is set by the DAEMON'S POLL
#: BOUNDARY, not by Dukascopy's publish lag. This module charges 79s.
#:
#: BOTH LAND ON THE SAME MINUTE AND THAT IS WHY NOTHING CHANGES. `price_by_bar_1m`
#: reads the open of the minute CONTAINING the moment, so 68s and 79s both
#: resolve to T+60, and 1,868s and 1,879s both resolve to T+1,860. Verified by
#: rebuilding every map at both delays: 0 of 11,471 entry bars and 0 of 11,469
#: exit bars differ on usdjpy, and the same on uk100 and xniusd. The BARS feed
#: is insensitive to this question.
#:
#: THE TICKS FEED IS NOT. At one-second resolution the 11s gap is real, so
#: `--source ticks` is mis-specified by that much. It is a control, not the
#: reported path, and it is left alone rather than tuned to match.
#:
#: THE DAEMON'S PRIORITISATION DOES NOT REACH CANON. In-session symbols are
#: polled every tick and the rest every fifth (`INACTIVE_EVERY_TICKS`), which
#: would be a 5-minute lag -- but `is_open` uses the SAME session windows as
#: `exness_families.SESSION` (verified equal for all six Dukascopy symbols) plus
#: a 45-minute margin either side, and every canon sleeve trades inside its own
#: session. No canon entry or exit is ever on the deprioritised path.
#:
#: NOT MODELLED: the hourly `reconcile` pass can REVISE a bar the strategy has
#: already acted on. That is a signal difference rather than a fill difference,
#: and nothing here measures it.

#: Everything between the runtime deciding and MT5 executing, in seconds.
#:
#: NOT MEASURED -- ADDED UP FROM THE CODE THAT DOES IT, and stated here so a
#: later reader can check the arithmetic rather than the number:
#:
#:     0.25s  the Rust runtime polls the store on a 500ms interval
#:            (`live_trade/src/live/portfolio/mod.rs`), so a bar waits half of one
#:     0.13s  `mt5/bridge.py` polls `/bridge/poll` every 0.25s by default, so a
#:            queued command waits half of one
#:     ~0.6s  two HTTP round trips to the backend plus `order_send` itself
#:
#: It is small next to every feed lag here and it is included anyway, because
#: leaving it out is a claim that the queue is free and this is the cheapest
#: possible way to stop making it.
BRIDGE_QUEUE_SECONDS = 1.0

#: What the live watchdog calls a dead feed, in seconds.
#:
#: `routing::feed_stale_after` is `max(market_step + 90, 180)` on the MINUTE
#: feed, and when it trips with a position open the runtime sends
#: `emergency_flatten` -- a market close at whatever is quoted, not at any level
#: the strategy chose. A scheduled market close looks identical to a dead feed
#: from inside that check ([[feed-watchdog-fights-the-swing-sleeve]]), which is
#: why it is measured against gaps INSIDE a session and never across one.
FEED_STALE_AFTER_SECONDS = 180

#: How far short of the window's end a symbol's coverage may fall before the
#: window is refused, in days.
#:
#: NOT ZERO, AND NOT GENEROUS. The check exists so a book is not reported over a
#: span its own data cannot cover. But a deciding table that stops twelve hours
#: early loses 0.1% of a twenty-month window -- that is a rounding error, not a
#: coverage failure, and refusing on it stops a whole book run over nothing.
#:
#: The observed gap is bimodal and this sits in the empty middle: de40 and es
#: fall half a day short (their vendors simply had not published the last
#: session yet), while nq, hk50 and the gold crosses fall eight to fourteen days
#: short, which is a genuinely stale feed and must still be refused.
MAX_SHORT_DAYS = 3


def log(message):
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def tick_table(symbol):
    return f"exness_{ef.broker_symbol(symbol).lower()}_ticks"


def has_ticks(symbol):
    return bool(_writer().table_files(tick_table(symbol)))


def _tick_files(symbol):
    """Every file backing the tick table, in time order.

    Through `table_files` rather than a glob, so this reads the compacted
    single file AND a table an interrupted import left as day shards. A glob on
    the directory saw only the second and reported a finished symbol as having
    no ticks at all.
    """
    return _writer().table_files(tick_table(symbol))


def _writer():
    """`tools/parquet_writer` on the path, whatever the caller's cwd is."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    tools = os.path.join(root, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import parquet_writer  # noqa: PLC0415

    return parquet_writer


#: ONE symbol's quotes, kept between calls. USTEC alone is 46.6M rows -- about
#: 1.1 GB across stamps, spreads and mids -- and `latency` asks for the same
#: symbol once per delay while `sleeve` asks once per sleeve. Reloading it each
#: time was the whole runtime. One entry, not a dict: holding two of these at
#: once is 2 GB for no gain, since every caller works through the symbols in
#: order.
_QUOTE_CACHE = {"symbol": None, "stamps": None, "spreads": None, "mids": None}


def _quotes(symbol):
    """`(stamps, spread_bp, mid)` for `symbol`, read at most once in a row."""
    if _QUOTE_CACHE["symbol"] != symbol:
        stamps, spreads, mids = _read_quotes(symbol)
        _QUOTE_CACHE.update(symbol=symbol, stamps=stamps, spreads=spreads,
                            mids=mids)
    return (_QUOTE_CACHE["stamps"], _QUOTE_CACHE["spreads"],
            _QUOTE_CACHE["mids"])


def load_quotes(symbol):
    """`(stamps, spread_bp)` for every stored quote, oldest first."""
    stamps, spreads, _mids = _quotes(symbol)
    return stamps, spreads


def release_quotes():
    """Drop the cached arrays. Call once a symbol's maps have been derived.

    Holding one symbol is 200-500 MB and the derived maps are a few thousand
    floats, so once they exist the arrays are dead weight in a long-running
    process.

    DO NOT re-add the claim that this was needed because the external NQ sleeves
    hold `dbento_nq_ticks` in memory. They do not: `sandbox/data.py` streams that
    table through `iter_batches` and disk-caches the reduction, exactly so it is
    never materialised. That explanation was asserted from the row count without
    checking and is false. The cause of the slow book runs on 2026-08-31 was
    never established.
    """
    _QUOTE_CACHE.update(symbol=None, stamps=None, spreads=None, mids=None)


def _read_quotes(symbol):
    """`(stamps, spread_bp, mid)` off disk.

    Returned as flat parallel arrays rather than rows: the only thing done with
    them is a bisect and a slice median, and at sixty million ticks the row
    objects are the entire memory cost.
    """
    files = _tick_files(symbol)
    if not files:
        return [], [], []

    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    stamps, spreads, mids = [], [], []
    for path in files:
        block = pq.read_table(str(path), columns=["timestamp", "bid", "ask"])
        bid = block.column("bid").to_numpy()
        ask = block.column("ask").to_numpy()
        mid = (bid + ask) / 2.0
        # A crossed or zeroed quote is a feed artefact, not a tradeable spread.
        good = (bid > 0) & (ask >= bid) & (mid > 0)
        # PARSED INSIDE ARROW, NOT THROUGH `to_pylist`. The store keeps
        # timestamps as ISO TEXT, and materialising them as Python strings costs
        # minutes and gigabytes per symbol at twelve million rows -- run across
        # nine symbols at once it looked like a hang. `strptime` does the same
        # work in C over the Arrow buffer.
        # A CAST, not `strptime`: this build's strptime rejects the store's
        # microsecond strings at every unit, while the plain string ->
        # timestamp cast parses ISO8601 with a fraction natively. It must be
        # `us` -- `s` and `ms` both refuse a six-digit fraction. Floored to
        # whole seconds here; that is all any caller bisects on.
        when = block.column("timestamp").cast(pa.timestamp("us"))
        when = when.cast(pa.int64()).to_numpy(zero_copy_only=False) // 1_000_000
        stamps.append(when[good])
        spreads.append(1e4 * (ask[good] - bid[good]) / mid[good])
        mids.append(mid[good])
    if not stamps:
        return [], [], []
    return (np.concatenate(stamps), np.concatenate(spreads),
            np.concatenate(mids))


def clock_offset(symbol):
    """Seconds to SUBTRACT from a bar stamp to reach the broker's own clock.

    THE ASIAN MARKETS ARE STORED ON TWO CLOCKS AND THE MAPS READ BOTH. `all_bars`
    applies `SHIFT_HOURS` to jp225, hk50 and aus200 so that one cash session
    lands inside one calendar day instead of being cut in half by New York
    midnight -- their bar stamps are therefore six hours AHEAD of New York.
    Every broker table is written in plain New York wall clock, like every other
    table in the store ([[questdb-stores-ny-wall-clock]]).

    KEYED ON ONE AND READ FROM THE OTHER, EVERY LOOKUP LANDED SIX HOURS AWAY
    AND STILL SUCCEEDED, because these CFDs quote around the clock -- so there
    was no missing row, no gap, and no error. Measured 2026-09-20 by correlating
    each vendor 30m open-to-open return against the broker's return over the
    same instants: jp225 read -0.007 as keyed and +0.228 at -6h, hk50 +0.015 as
    keyed and +0.991 at -6h, while unshifted uk100 read +0.734 at zero and
    nothing anywhere else. The offset scan picks out exactly `SHIFT_HOURS` on
    every shifted symbol and exactly zero on every other.

    WHAT IT COST BEFORE IT WAS FOUND: a jp225 cell scored +348% in sample and
    +93% out of it with the live model BEATING the sealed one by 246 points,
    which is backwards -- a late market exit cannot pay. That was a
    six-hour-displaced price series behaving like a random one that happened to
    flatter the sample.
    """
    return int(ef.INSTRUMENTS.get(symbol, {}).get("shift_hours", 0) or 0) * 3600


def bar_seconds(bars):
    """The sleeve's bar length, read off the bars rather than configured.

    The exit delay below is `one whole bar` plus the lag, so this number is
    load-bearing and a wrong constant would be silent. Taken as the SHORTEST
    positive step in the series: every longer gap is a session break, a weekend
    or a holiday, and a mean or a median over those would report a 30-minute bar
    as an hour and a half.
    """
    steps = {b[ef.TS] - a[ef.TS] for a, b in zip(bars, bars[1:])}
    steps = [step for step in steps if step > 0]
    return min(steps) if steps else 0


def exit_delay_seconds(bars, feed_lag, queue=BRIDGE_QUEUE_SECONDS):
    """How long after a bar OPENS the live account is actually out of it.

    THE WHOLE BAR IS THE FIRST TERM AND IT IS THE BIGGEST ONE. A backtest sees
    `low <= stop` and books the stop price during the bar. The live runtime
    accumulates minutes into `self.building` and evaluates the exit only when
    the slot ROLLS (`family.rs`), so on a 30-minute sleeve the stop that was
    breached at 09:31 is not acted on until 10:00 -- and then it is a market
    order, so the feed lag and the bridge queue follow it.

    The exit is therefore priced `bar + lag + queue` after the bar's own open,
    which is where `price_by_bar` puts it. There is no argument for a shorter
    delay while the account carries no broker-side stop.
    """
    return bar_seconds(bars) + feed_lag + queue


def spread_by_bar(symbol, bar_stamps, window=ENTRY_WINDOW_SECONDS):
    """`{bar_ts: spread_bp}` at each bar's OPEN, for the bars ticks cover.

    A bar the ticks do not reach is simply absent, which is what makes the
    engine fall back to the constant for it. Absence is the honest signal here
    and a zero would be a free trade.
    """
    import numpy as np

    stamps, spreads = load_quotes(symbol)
    if len(stamps) == 0:
        return {}
    window = int(round(window))          # see `price_by_bar`: a float upcasts
    # The KEY stays the bar's own stamp -- that is what `backtest` looks up --
    # and only the LOOKUP moves onto the broker's clock. See `clock_offset`.
    offset = clock_offset(symbol)
    out = {}
    for ts in bar_stamps:
        at = int(ts) - offset
        lo = np.searchsorted(stamps, at, side="left")
        hi = np.searchsorted(stamps, at + window, side="left")
        if hi - lo < MIN_QUOTES:
            continue
        out[int(ts)] = float(np.median(spreads[lo:hi]))
    return out


def price_by_bar(symbol, bar_opens, delay_seconds):
    """`{bar_ts: entry}` -- the bar's own open, moved by what the ticks did.

    THIS IS WHAT MAKES THE LATENCY QUESTION ANSWERABLE AT ALL. `decision_lag`
    moves an entry by whole bars, so at the 30-minute canon bar it can charge 0
    minutes or 30 and nothing between -- and every measured feed lag is between.
    Here the entry is moved by exactly what the market did in those 78 seconds.

    A RATIO, NOT THE TICK PRICE ITSELF, AND THIS IS NOT A DETAIL. Every sleeve
    DECIDES on a vendor table and the ticks are the BROKER's. On ukoil those
    are Dukascopy Brent against Exness UKOIL, and on the index CFDs a future
    against a cash CFD -- different instruments at different levels, which
    `exness_broker_fills` refuses to mix for exactly this reason. Substituting
    the broker's absolute mid as the entry put the fill at one price level while
    the stop, the target and the exit stayed at another: it read
    `ukoil:xma_cross` at +69.6% against a sealed +28.6% and inverted
    `jp225:break_retest` from +11.1% to -28.7%. Applying only the RATIO over the
    delay window cancels the level difference, leaves the instrument mismatch
    where it was, and makes the delay the one thing that changed.

    It also makes `+0s` an exact control rather than an approximate one: with
    the same tick anchoring both ends the ratio is 1.0 and the sealed return is
    reproduced to the digit, so any movement in the table is the delay.

    The MID is used rather than the ask. The spread is charged separately and
    once, by `cost_price`; taking the ask here as well would bill it twice.
    """
    import numpy as np

    stamps, _spreads, mids = _quotes(symbol)
    if len(stamps) == 0:
        return {}
    # AN INT, ALWAYS. `stamps` is int64, and `searchsorted` handed a float
    # scalar upcasts the WHOLE array to float64 on every call -- eight million
    # elements per bar, eighty thousand bars. `MEASURED_LAG_SECONDS` stores
    # floats (78.0, 1.8), so passing one straight through turned a two-second
    # reduction into an unbounded hang. This was the cause of every stalled run
    # on 2026-08-31; nothing about memory or NQ was involved.
    delay_seconds = int(round(delay_seconds))
    offset = clock_offset(symbol)        # see `clock_offset`
    out = {}
    for ts, open_price in bar_opens.items():
        if not open_price:
            continue
        at = int(ts) - offset
        # The last quote AT OR BEFORE each end. Taking the next one instead
        # would price the fill on information that had not printed yet, which is
        # the same look-ahead [[entry-must-be-next-bar-open]] records.
        anchor = np.searchsorted(stamps, at, side="right") - 1
        moved = np.searchsorted(stamps, at + delay_seconds, side="right") - 1
        if anchor < 0 or moved < 0:
            continue
        # BOTH ends must be fresh. A stale anchor is the previous session's
        # last print, and a ratio against it is a gap, not a delay.
        if (at - stamps[anchor] > ENTRY_WINDOW_SECONDS
                or at + delay_seconds - stamps[moved] > ENTRY_WINDOW_SECONDS):
            continue
        base = mids[anchor]
        if base <= 0:
            continue
        out[int(ts)] = float(open_price * mids[moved] / base)
    return out


#: Which series the maps are built from. `bars` is the default and the one to
#: use; `ticks` is kept as the control that proved it.
#:
#: THE BAR FEED WON ON COVERAGE AND LOST NOTHING ON ACCURACY. Measured
#: 2026-08-31 over 105,000 matched minutes on usdjpy, ethusd and btc, the M1
#: `spread` column against the median of the ticks inside the same minute:
#: ratio 1.00x on all three, correlation 0.956 / 1.000 / 0.994. MT5's per-bar
#: spread IS the tick median, so nothing is given up by reading it instead --
#: while `exness_<broker>_1m` covers every minute of 2025-2026 and the tick
#: tables cover about 62% of trades ([[exness-tick-tables-have-daily-holes]]).
FEEDS = ("bars", "ticks")
DEFAULT_FEED = "bars"

#: One symbol's minute bars, kept between calls, for the same reason
#: `_QUOTE_CACHE` holds one symbol's quotes.
_MINUTE_CACHE = {"symbol": None, "stamps": None, "opens": None, "spreads": None}


def _point_size(symbol):
    """The broker's point for `symbol`, which the M1 spread column is counted in.

    `spread` is an INTEGER COUNT OF POINTS, not a price and not basis points, so
    it means nothing without this. Read from the terminal rather than derived
    from the quote's decimal places: a 3-digit JPY cross and a 5-digit major
    both exist here and guessing from `close` gets one of them wrong.

    OFF THE SPEC SNAPSHOT FIRST. `resolve` already carries `point` from
    `exness_pro_specs.json`, where `swaps` recorded it, and reading it there
    means a map can be built on a machine with no terminal running -- which is
    what `exness_families.live_fills` does on every sweep. The terminal is
    opened only for a symbol the snapshot predates.

    Cached on the instrument spec so the terminal is opened once per process
    rather than once per symbol.
    """
    spec = ef.INSTRUMENTS.get(symbol) or {}
    if spec.get("_point"):
        return spec["_point"]
    if spec.get("point"):
        return float(spec["point"])
    import MetaTrader5 as mt5  # noqa: PLC0415

    started = mt5.initialize()
    try:
        info = mt5.symbol_info(ef.broker_symbol(symbol))
        if info is None:
            raise SystemExit(
                f"{symbol}: the terminal does not quote "
                f"{ef.broker_symbol(symbol)}, so its M1 spread cannot be scaled")
        point = float(info.point)
    finally:
        if started:
            mt5.shutdown()
    if spec:
        spec["_point"] = point
    return point


def minute_table(symbol):
    # One definition, in `exness_families`, because `resolve` refuses a symbol
    # this table does not cover and the two names must be the same string.
    return ef.broker_minute_table(symbol)


def has_minutes(symbol):
    return bool(_writer().table_files(minute_table(symbol)))


def _read_minutes(symbol):
    """`(stamps, open, spread_bp)` off the broker's own one-minute bars.

    THE STAMPS NEED NO CONVERSION AND THAT IS NOT LUCK. `exness_import_1m`
    writes New York wall clock relabelled UTC, exactly as every other bar table
    in the store does ([[questdb-stores-ny-wall-clock]]), so these line up with
    `bar[ef.TS]` directly. A tick table is on the same convention, which is why
    both feeds can share every caller below.

    `spread` is converted to basis points here and nowhere else, so no caller
    has to know it was stored in points.
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    files = _writer().table_files(minute_table(symbol))
    if not files:
        return [], [], []
    point = _point_size(symbol)
    stamps, opens, spreads = [], [], []
    for path in files:
        block = pq.read_table(str(path),
                              columns=["timestamp", "open", "close", "spread"])
        op = block.column("open").to_numpy()
        close = block.column("close").to_numpy()
        raw = block.column("spread").to_numpy().astype("float64")
        good = (op > 0) & (close > 0) & (raw > 0)
        # Parsed inside Arrow for the reason `_read_quotes` gives, but NOT the
        # same cast. Two traps, both hit on 2026-08-31:
        #
        #   `strptime` returns null for every row on this build, at every unit
        #   and every format string. It fails silently, so the maps came out
        #   with zero priced bars and no error.
        #
        #   These strings carry a trailing `Z` where the tick tables' do not,
        #   and a bare `timestamp("us")` cast rejects them with "expected no
        #   zone offset". It must be `timestamp("us", tz="UTC")`.
        when = block.column("timestamp").cast(pa.timestamp("us", tz="UTC"))
        when = when.cast(pa.int64()).to_numpy(zero_copy_only=False) // 1_000_000
        fresh = good
        stamps.append(when[fresh])
        opens.append(op[fresh])
        # Against the bar's own close, so a spread is expressed relative to the
        # level it was quoted at rather than to some later one.
        spreads.append(1e4 * raw[fresh] * point / close[fresh])
    if not stamps:
        return [], [], []
    order = np.argsort(np.concatenate(stamps), kind="stable")
    return (np.concatenate(stamps)[order], np.concatenate(opens)[order],
            np.concatenate(spreads)[order])


def _minutes(symbol):
    if _MINUTE_CACHE["symbol"] != symbol:
        stamps, opens, spreads = _read_minutes(symbol)
        _MINUTE_CACHE.update(symbol=symbol, stamps=stamps, opens=opens,
                             spreads=spreads)
    return (_MINUTE_CACHE["stamps"], _MINUTE_CACHE["opens"],
            _MINUTE_CACHE["spreads"])


def release_minutes():
    _MINUTE_CACHE.update(symbol=None, stamps=None, opens=None, spreads=None)


def spread_by_bar_1m(symbol, bar_stamps):
    """`{bar_ts: spread_bp}` from the M1 bar the entry opens in.

    ONE BAR, NOT A MEDIAN OVER A WINDOW. The tick path takes the median of the
    quotes in the entry minute because a single print can be crossed or stale.
    The M1 column already IS that median -- that is what the 1.00x/0.956-1.000
    validation established -- so taking a further median over neighbouring
    minutes would smooth a number that is not noisy and would reach across the
    minute the entry actually happened in.
    """
    import numpy as np

    stamps, _opens, spreads = _minutes(symbol)
    if not len(stamps):
        return {}
    offset = clock_offset(symbol)        # see `clock_offset`
    out = {}
    for ts in bar_stamps:
        at = int(ts) - offset
        index = np.searchsorted(stamps, at, side="right") - 1
        # The bar must BE the entry minute, not merely the last one before it.
        # A stale bar means the market was not quoting and the entry did not
        # happen at that spread.
        if index < 0 or at - stamps[index] >= 60:
            continue
        out[int(ts)] = float(spreads[index])
    return out


def price_by_bar_1m(symbol, bar_opens, delay_seconds):
    """`{bar_ts: entry}` -- the vendor open moved by what the BROKER did.

    A RATIO, FOR THE REASON `price_by_bar` GIVES AT LENGTH, and it is not
    optional just because these are the broker's own bars. The sleeve decides on
    a vendor table and is filled at Exness; on ukoil those are Dukascopy Brent
    against Exness UKOIL and on the index CFDs a future against a cash CFD.
    Substituting the broker's absolute price put the fill at one level while the
    stop and target stayed at another, which read `ukoil:xma_cross` at +69.6%
    against a sealed +28.6%. The ratio cancels the level and leaves the delay.

    NEVER LOOKS AHEAD. Both ends read the OPEN of the minute CONTAINING the
    moment, which is the price at or before it -- the same rule
    [[entry-must-be-next-bar-open]] records. The cost is that the delay is
    rounded down to a whole minute: 79s is charged as 60s and 1,879s as 1,860s.
    That is ~24% of the entry delay and ~1% of the exit delay, and the exit is
    where the effect lives.
    """
    import numpy as np

    stamps, opens, _spreads = _minutes(symbol)
    if not len(stamps):
        return {}
    delay_seconds = int(round(delay_seconds))
    offset = clock_offset(symbol)        # see `clock_offset`
    out = {}
    for ts, open_price in bar_opens.items():
        if not open_price:
            continue
        at = int(ts) - offset
        anchor = np.searchsorted(stamps, at, side="right") - 1
        moved = np.searchsorted(stamps, at + delay_seconds, side="right") - 1
        if anchor < 0 or moved < 0:
            continue
        # BOTH ends must be the minute itself. A stale one is the previous
        # session's last bar and a ratio against it is a gap, not a delay.
        if (at - stamps[anchor] >= 60
                or at + delay_seconds - stamps[moved] >= 60):
            continue
        base = opens[anchor]
        if base <= 0:
            continue
        out[int(ts)] = float(open_price * opens[moved] / base)
    return out


def spread_map(symbol, bar_stamps, feed=DEFAULT_FEED):
    """`spread_by_bar` on whichever feed is selected."""
    if feed == "bars":
        return spread_by_bar_1m(symbol, bar_stamps)
    return spread_by_bar(symbol, bar_stamps)


def price_map(symbol, bar_opens, delay_seconds, feed=DEFAULT_FEED):
    """`price_by_bar` on whichever feed is selected."""
    if feed == "bars":
        return price_by_bar_1m(symbol, bar_opens, delay_seconds)
    return price_by_bar(symbol, bar_opens, delay_seconds)


def has_feed(symbol, feed=DEFAULT_FEED):
    return has_minutes(symbol) if feed == "bars" else has_ticks(symbol)


def release_feed(feed=DEFAULT_FEED):
    release_minutes() if feed == "bars" else release_quotes()


#: Where `precompute` writes the per-symbol maps the book reads.
MAPS_PATH = os.path.join(os.path.dirname(__file__), "exness_live_execution_maps.json")


def precompute(path=MAPS_PATH, source=DEFAULT_FEED):
    """Write every canon symbol's spread and entry-price map to one small file.

    WHY THIS IS A SEPARATE PROCESS AND NOT A FUNCTION CALL. Reducing a tick
    table takes about five seconds a symbol measured standalone, but book runs
    that did it in-process were unusably slow on 2026-08-31 and the reason was
    never established. Splitting it out made them finish. That is the whole
    justification -- an empirical one, not a diagnosis.

    So the source series is read HERE, reduced to a few thousand floats per
    symbol, and the book reads only the reduction.

    `source` picks that series. `bars` -- the default -- reads
    `exness_<broker>_1m`, which covers every minute of 2025-2026 and carries the
    broker's own per-minute spread. `ticks` reads `exness_<broker>_ticks`, which
    is where the method was validated and where it stays as a control; it starts
    2026-01 and holds only about 62% of the trades
    ([[exness-tick-tables-have-daily-holes]]).
    """
    from sandbox.research import exness_latency as lat

    out = {}
    for symbol in _screenable_symbols(source):
        if not has_feed(symbol, source):
            log(f"{symbol}: no {source} table -- left on the constant")
            continue
        ef.resolve(symbol, allow_stale=True)
        bars = _bars_for(symbol)
        feed = lat.FEED.get(symbol, "dukascopy")
        seconds = lat.MEASURED_LAG_SECONDS[feed]
        stamps = [bar[ef.TS] for bar in bars]
        spreads = spread_map(symbol, stamps, source)
        opens = {bar[ef.TS]: bar[ef.O] for bar in bars}
        entries = price_map(symbol, opens, seconds + BRIDGE_QUEUE_SECONDS,
                            source)
        # THE SAME FUNCTION AT A LONGER DELAY, deliberately. An exit is not a
        # different kind of event from an entry on this account -- both are
        # market orders on the same queue -- so modelling it with a second
        # mechanism would be inventing a difference that is not there. The only
        # thing that differs is WHEN, and that is one argument.
        exits = price_map(symbol, opens, exit_delay_seconds(bars, seconds),
                          source)
        release_feed(source)
        out[symbol] = {
            "feed": feed, "source": source, "lag_seconds": seconds,
            "queue_seconds": BRIDGE_QUEUE_SECONDS,
            "bar_seconds": bar_seconds(bars),
            "exit_delay_seconds": exit_delay_seconds(bars, seconds),
            "bars": len(bars),
            # JSON keys are strings; the reader casts back to int.
            "spread_bp": {str(k): round(v, 6) for k, v in spreads.items()},
            "entry": {str(k): v for k, v in entries.items()},
            "exit": {str(k): v for k, v in exits.items()},
        }
        log(f"{symbol}: {feed} +{seconds:g}s -- {len(spreads):,} priced, "
            f"{len(entries):,} refilled, {len(exits):,} exit-repriced "
            f"of {len(bars):,} bars")

    path = maps_path(source)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    log(f"{len(out)} symbols -> {path} "
        f"({os.path.getsize(path) / 1e6:.1f} MB)")
    return out


def _bars_for(symbol):
    """The book's own bar list for `symbol`, without importing its caches."""
    from sandbox.research import exness_combined_strategies as cs

    bars, _ctx = cs._context(symbol)
    return bars


def maps_path(source=DEFAULT_FEED):
    """A separate maps file per feed, so the two can never be confused.

    Writing both to one path made the last `precompute` decide what every later
    `book` meant, with nothing in the output naming which. The file name is the
    record.
    """
    if source not in FEEDS:
        raise SystemExit(f"unknown source {source!r}; known: {list(FEEDS)}")
    return MAPS_PATH.replace(".json", f"_{source}.json")


def load_maps(path=None, source=DEFAULT_FEED):
    """`{symbol: (spread_by_ts, entry_by_ts, exit_by_ts)}`, or `{}`.

    `exit` may be missing on a file written before exits were repriced; it
    reads as an empty map rather than an error, so an old maps file still runs
    and simply charges the idealised exit it always did.
    """
    path = maps_path(source) if path is None else path
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        stored = json.load(handle)
    return {
        symbol: ({int(k): v for k, v in row["spread_bp"].items()},
                 {int(k): v for k, v in row["entry"].items()},
                 {int(k): v for k, v in row.get("exit", {}).items()})
        for symbol, row in stored.items()
    }


def latency(delays=(0, 78, 300), path=OUT_PATH):
    """Each sleeve re-priced at its entry bar's open PLUS a real publish lag.

    The companion to `exness_latency book`, at the resolution that question
    actually has. 0 is the control and must reproduce the sealed return where
    the ticks reach; 78 is the measured Dukascopy lag; 300 is five minutes, far
    beyond any feed here, as the shape check.

    ONLY THE 2026 TAIL MOVES. Entries before the tick history keep the bar's
    open, so a small delta is not evidence the book is robust -- it is mostly
    evidence that most of the window could not be repriced. `priced` is the
    column to read first.
    """
    from sandbox.research import exness_combined_strategies as cs
    from sandbox.research import exness_latency as lat

    rows = {}
    header = "".join(f"{f'+{d}s':>11}" for d in delays)
    print(f"{'sleeve':30}{'trades':>8}{'priced':>8}{header}")
    for name, row in lat._native_rows():
        symbol = row["symbol"]
        if not has_ticks(symbol):
            print(f"{name:30}  no tick table")
            continue
        ef.resolve(symbol, allow_stale=True)
        bars, ctx = cs._context(symbol)
        common = dict(lo=ef.IS_END, hi=ef.OOS_END, include_trades=True,
                      fill_bars=cs._fill_bars(symbol, bars))
        base = ef.backtest(row["family"], bars, ctx, row["params"], **common)
        # The OPEN each entry actually filled at, keyed by its bar stamp -- off
        # the fill feed where there is one, since that is the price the engine
        # paid and the price the ratio has to be applied to.
        fills = cs._fill_bars(symbol, bars) or bars
        open_of = {bar[ef.TS]: bar[ef.O] for bar in fills}
        entries = {t["entry_ts"]: open_of.get(t["entry_ts"])
                   for t in base["trade_log"]}
        cells, priced = [], 0
        for delay in delays:
            at = price_by_bar(symbol, entries, delay)
            priced = max(priced, len(at))
            run = ef.backtest(row["family"], bars, ctx, row["params"],
                              entry_prices=at, **common)
            cells.append(run["return_pct"])
        rows[name] = {"trades": base["trades"], "priced_entries": priced,
                      "delays": list(delays), "return_pct": cells}
        body = "".join(f"{value:>10.1f}%" for value in cells)
        print(f"{name:30}{base['trades']:>8}{priced:>8}{body}")
    print(f"\n+0s is the control: it re-prices the entry at the last quote "
          f"before the bar's\n  own open, so it should sit on the sealed "
          f"return wherever `priced` is high.")
    _merge(path, {"latency": rows})
    return rows


# --------------------------------------------------------------------------- #
# the distribution
# --------------------------------------------------------------------------- #


def spreads(symbols=None, path=OUT_PATH):
    """The tick spread distribution per symbol, against the charged constant."""
    import numpy as np

    symbols = symbols or _canon_symbols()
    rows = {}
    print(f"{'symbol':10}{'charged':>9}{'median':>9}{'p75':>8}{'p90':>8}"
          f"{'p99':>8}{'max':>9}{'quotes':>12}{'p90/const':>11}")
    for symbol in symbols:
        if not has_ticks(symbol):
            print(f"{symbol:10}  no tick table -- run tools/exness_tick_import.py")
            continue
        ef.resolve(symbol, allow_stale=True)
        charged = ef.INSTRUMENTS[symbol]["spread_bp"] or 0.0
        _stamps, values = load_quotes(symbol)
        if len(values) == 0:
            continue
        quantiles = np.percentile(values, [50, 75, 90, 99])
        row = {
            "charged_bp": charged,
            "median_bp": float(quantiles[0]), "p75_bp": float(quantiles[1]),
            "p90_bp": float(quantiles[2]), "p99_bp": float(quantiles[3]),
            "max_bp": float(values.max()), "quotes": int(len(values)),
        }
        rows[symbol] = row
        ratio = row["p90_bp"] / charged if charged else float("nan")
        print(f"{symbol:10}{charged:>9.3f}{row['median_bp']:>9.3f}"
              f"{row['p75_bp']:>8.3f}{row['p90_bp']:>8.3f}{row['p99_bp']:>8.3f}"
              f"{row['max_bp']:>9.2f}{row['quotes']:>12,}{ratio:>10.1f}x")
    print("\nAll hours, every stored quote. `sleeve` is the number that "
          "matters:\n  a symbol's distribution is not what its sleeve pays, "
          "because a sleeve\n  only trades at its own entries.")
    _merge(path, {"spreads": rows})
    return rows


# --------------------------------------------------------------------------- #
# what each sleeve actually pays
# --------------------------------------------------------------------------- #


def sleeve(path=OUT_PATH):
    """Entry-time spread per canon sleeve, against the constant it is charged.

    THIS IS THE ONE THAT DECIDES WHETHER THE COST MODEL IS WRONG. A symbol whose
    p90 is five times its median tells you nothing on its own -- the sleeve may
    never trade there. Only the spread at the sleeve's OWN entry timestamps does.
    """
    from sandbox.research import exness_combined_strategies as cs
    from sandbox.research import exness_latency as lat

    rows = {}
    print(f"{'sleeve':32}{'trades':>8}{'priced':>8}{'charged':>9}"
          f"{'at entry':>10}{'ratio':>8}{'p90 entry':>11}")
    for name, row in lat._native_rows():
        symbol = row["symbol"]
        if not has_ticks(symbol):
            print(f"{name:32}  no tick table")
            continue
        ef.resolve(symbol, allow_stale=True)
        charged = ef.INSTRUMENTS[symbol]["spread_bp"] or 0.0
        bars, ctx = cs._context(symbol)
        result = ef.backtest(row["family"], bars, ctx, row["params"],
                             lo=ef.IS_END, hi=ef.OOS_END, include_trades=True,
                             fill_bars=cs._fill_bars(symbol, bars))
        entries = [t["entry_ts"] for t in result["trade_log"]]
        if not entries:
            continue
        at_entry = spread_by_bar(symbol, sorted(set(entries)))
        priced = [at_entry[ts] for ts in entries if ts in at_entry]
        if not priced:
            print(f"{name:32}{len(entries):>8}{0:>8}  "
                  f"no entry falls inside the tick window")
            continue
        median = statistics.median(priced)
        p90 = statistics.quantiles(priced, n=10)[-1] if len(priced) > 9 else max(priced)
        rows[name] = {"trades": len(entries), "priced": len(priced),
                      "charged_bp": charged, "entry_median_bp": median,
                      "entry_p90_bp": p90}
        ratio = median / charged if charged else float("nan")
        print(f"{name:32}{len(entries):>8}{len(priced):>8}{charged:>9.3f}"
              f"{median:>10.3f}{ratio:>7.2f}x{p90:>11.3f}")
    print(f"\n`priced` is how many entries fall after {TICK_HISTORY_START}; the "
          "rest keep the\n  constant. A ratio above 1.0 means the book is "
          "undercharging that sleeve.")
    _merge(path, {"sleeve": rows})
    return rows


# --------------------------------------------------------------------------- #
# the book, tick-priced
# --------------------------------------------------------------------------- #


def book(path=OUT_PATH, source=DEFAULT_FEED):
    """The canon book on ONE shared balance, run twice: as sealed, and as live.

    THIS IS THE PORTFOLIO RUN AND `cells` IS NOT. `cells` prices each sleeve
    against its own private account, which cannot see the three things that
    decide what this book actually does: lots are recomputed at entry from the
    SHARED equity, a request under the broker minimum is refused outright
    ([[four-hundred-dollars-selects-the-sleeves-for-you]]), and drawdown is
    marked to market on open positions rather than booked at close
    ([[engine-drawdown-is-mark-to-market]]). A sum of standalone returns hides
    half of it ([[blend-model-understates-portfolio-drawdown]]).

    So this delegates to `exness_combined_strategies.build` -- the same function
    `build --members canon` calls, at the same risk scale, the same $400, the
    same gross cap -- and runs it twice with nothing different but
    `TICK_COSTS`:

        sealed   constant spread, vendor bar open in, idealised stop out
        live     tick spread at the entry, tick price one lag+queue after the
                 open in, tick price one BAR plus lag+queue after the exit bar
                 opened out

    THE SECOND RUN IS NOT A COST ADJUSTMENT ON THE FIRST. Lot sizes are derived
    from the running balance, so a worse fill early makes every later sleeve
    smaller, and refusals move: a sleeve that could afford the broker minimum in
    the sealed run may not in the live one. That is why the two are separate
    replays and not one replay with a subtracted number.

    Needs the maps on disk first::

        py -m sandbox.research.exness_live_execution precompute
        py -m sandbox.research.exness_live_execution book
    """
    from sandbox.research import exness_combined_strategies as cs

    lo = tick_window_start(source=source)
    print(_window_banner(lo, source), flush=True)
    # `combined_strategies` loads whichever maps file is on disk under the name
    # it knows, so the selected feed is staged there rather than passed. Without
    # this a `--source ticks` book would silently replay the bars maps.
    cs.MAPS_OVERRIDE = maps_path(source)
    # THE WINDOW IS MOVED BY REBINDING `ef.IS_END`, not by an argument, because
    # `build` has none: it reaches `sleeve_trades` and `replay` through two
    # layers that both default to `ef.IS_END`, and passing it down would mean
    # threading a parameter through the canon book path for the sake of this
    # module. Restored in a `finally` so an interrupted run cannot leave a
    # later `build --members canon` silently running a seven-month book.
    sealed_is_end = ef.IS_END

    runs = {}
    try:
        ef.IS_END = lo
        runs = _book_runs(cs)
    finally:
        ef.IS_END = sealed_is_end

    sealed = runs["sealed"]["book"]
    live = runs["live"]["book"]
    solo_sealed = runs["sealed"]["standalone"]
    solo_live = runs["live"]["standalone"]

    print(f"\n{'=' * 78}\nSEALED / LIVE   {_stamp(lo)} .. "
          f"{_stamp(ef.OOS_END)} UTC\n{'=' * 78}")
    print(f"  {'':24}{'sealed':>13}{'live':>13}{'delta':>13}")
    for field, label, unit in (("return_pct", "return", "%"),
                               ("mtm_dd_pct", "MTM dd  <- read this", "%"),
                               ("max_dd_pct", "closed dd", "%"),
                               ("final", "final equity", ""),
                               ("trades", "trades", "")):
        a, b = sealed.get(field), live.get(field)
        if a is None or b is None:
            continue
        print(f"  {label:24}{a:>12,.1f}{unit:1}{b:>12,.1f}{unit:1}"
              f"{b - a:>+13,.1f}")
    print(f"  {'below broker minimum':24}"
          f"{sum(sealed['below_broker_minimum'].values()):>13,}"
          f"{sum(live['below_broker_minimum'].values()):>13,}")

    # PER SLEEVE, ALWAYS. A book total moves for reasons that belong to one
    # member, and the total alone cannot say which
    # ([[show-per-sleeve-detail-every-time]]).
    print(f"\n  {'sleeve':30}{'solo seal':>10}{'solo live':>10}"
          f"{'bk seal':>10}{'bk live':>10}{'exec $':>10}")
    rows = {}
    for name in cs.BOOK:
        a = sealed["by_sleeve"].get(name, {})
        b = live["by_sleeve"].get(name, {})
        sa = solo_sealed.get(name, {}).get("return_pct", float("nan"))
        sb = solo_live.get(name, {}).get("return_pct", float("nan"))
        rows[name] = {"solo_sealed_pct": sa, "solo_live_pct": sb,
                      "book_sealed_pnl": a.get("pnl"),
                      "book_live_pnl": b.get("pnl"),
                      "dd_event_share": b.get("dd_event_share")}
        print(f"  {name:30}{sa:>9.1f}%{sb:>9.1f}%"
              f"{a.get('pnl', 0.0):>10,.0f}{b.get('pnl', 0.0):>10,.0f}"
              f"{b.get('pnl', 0.0) - a.get('pnl', 0.0):>+10,.0f}")

    print("\n  Every trade in this window is repriced, so the delta is "
          "execution and nothing\n  else. The EXTERNAL nq sleeves move anyway, "
          "and that is the shared balance\n  working rather than a leak: they "
          "read no map, but they size against equity\n  the other sleeves have "
          "already spent.")
    print("  The window sits INSIDE the period the survivor pool was screened "
          "on, so this\n  is a cost correction and never a holdout "
          "([[exness-survivor-pool-is-oos-conditioned]]).")
    keep = ("return_pct", "mtm_dd_pct", "max_dd_pct", "final", "trades")
    _merge(path, {f"book_{source}": {"window": [_stamp(lo), _stamp(ef.OOS_END)],
                           "source": source,
                           "sealed": {k: sealed.get(k) for k in keep},
                           "live": {k: live.get(k) for k in keep},
                           "by_sleeve": rows}})
    return runs


def _book_runs(cs):
    """The canon book twice: as sealed, and as the account actually fills it."""
    runs = {}
    for label, tick_costs in (("sealed", False), ("live", True)):
        note = ("tick spreads, lagged entries, market exits" if tick_costs
                else "constant spread, vendor opens, idealised exits")
        # The engine's broker-held-stop branch stays off. Nothing here asks for
        # it, and leaving it unset would inherit whatever the last caller in
        # this process wanted.
        cs.BROKER_STOPS = False
        print(f"\n{'=' * 78}\n{label.upper()} BOOK   ({note})\n{'=' * 78}",
              flush=True)
        # Set exactly as `main()` sets them for `build --members canon`. Named
        # rather than passed because they are module globals there, and a book
        # run that misses one is a different book that still prints.
        cs.TICK_COSTS = tick_costs
        cs.FORCE_MINIMUM_LOT = True
        cs.UNCAPPED = True
        cs.GLOBAL_SIZING_CAP = 1500.0
        cs.MAX_DD_CONCENTRATION = None
        cs.FAIR_CAP = False
        # The sleeve memo keys on `TICK_COSTS`, so the two runs cannot collide
        # there -- but the maps cache does not, and a stale one would silently
        # hand the sealed run the live prices.
        cs._TICK_COST_CACHE.clear()
        runs[label] = cs.build(
            len(cs.BOOK), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
            risk_scale=cs.CANON_RISK_SCALE, gross_cap=cs.CANON_GROSS_CAP,
            initial=cs.CANON_INITIAL, members_exact=cs.BOOK)

    return runs


# --------------------------------------------------------------------------- #
# per sleeve, standalone
# --------------------------------------------------------------------------- #


def cells(path=OUT_PATH, source=DEFAULT_FEED):
    """The vendor fill, the broker fill, and the fill this account can get.

    The signal is the vendor file in all three and never moves. Only the
    execution does::

        cell 1   entry at the vendor bar's open, exit at the idealised
                 `min(open, stop)`, both charged the constant spread. This is
                 canon exactly as it ships, and it is what every sealed number
                 in the repository is.
        cell 4   entry at the Exness tick quote the feed's own publish lag plus
                 the bridge queue after that open, charged the tick-measured
                 spread at that moment. The EXIT is still idealised.
        cell 5   cell 4, and the exit is a market order too -- priced one whole
                 bar plus the same lag and queue after the bar it fired on,
                 because that is when the runtime could send it.

    CELL 5 IS THE ONE TO READ AND CELL 4 IS ONLY THERE TO SPLIT THE CAUSE.
    Without the pair, a fall from 1 to 5 cannot be attributed: it could be the
    entry arriving late or the exit arriving late, and those have different
    fixes. The entry leg is bounded by seconds; the exit leg is bounded by a
    whole bar, which on the 30-minute canon sleeves is 30 minutes of unmanaged
    price after the stop was already breached.

    WHY THE EXIT LEG EXISTS AT ALL. There is no broker-side stop or take-profit
    on this account. `live_trade/src/live/mt5/bridge.rs` sends
    `ORDER|id|action|symbol|volume|magic|deviation|ticket|created_at` and the
    payload has nowhere to put one, so the stop is a level the RUNTIME watches.
    `family.rs` accumulates minutes into a candle and tests that level only when
    the candle rolls -- and then books the same idealised price this backtest
    does, which is the price the bar says and not the price the account got.

    Lag is per FEED, never one number for all: dukascopy 78s, binance 2s,
    databento ~1s, plus `BRIDGE_QUEUE_SECONDS` on every one of them.

    THE WINDOW OPENS WHERE THE LAST SYMBOL'S TICKS DO (`tick_window_start`), so
    `priced` should equal `trades` on every row and a shortfall is a thin bar
    rather than a hole in the history. It closes at `ef.OOS_END`, which every
    tick table already covers.

    Standalone per sleeve. It is NOT the shared-balance book, and the sum at the
    bottom is a sum of standalone returns, which understates portfolio drawdown
    ([[blend-model-understates-portfolio-drawdown]]). Run `book` for the
    portfolio.

        py -m sandbox.research.exness_live_execution cells
    """
    from sandbox.research import exness_combined_strategies as cs
    from sandbox.research import exness_latency as lat

    lo = tick_window_start(source=source)
    print(_window_banner(lo, source), flush=True)
    head = ("%-30s %-10s %5s %6s %7s %7s %11s %11s %11s %9s"
            % ("sleeve", "feed", "lag", "exit", "trades", "priced",
               "CELL1 vend", "CELL4 entry", "CELL5 live", "delta"))
    print(head, flush=True)
    print("-" * len(head), flush=True)

    rows, one, four, five = {}, 0.0, 0.0, 0.0
    for name, row in _all_rows():
        if row is None:
            # An EXTERNAL sleeve. Printed rather than dropped: its absence used
            # to be invisible, and a reader counting rows would conclude the
            # book had twenty and not twenty-five.
            print("%-30s %-10s %5s %6s %7s %7s %11s %11s %11s %9s"
                  % (name, "databento", "~1", "-", "-", "-", "external",
                     "external", "external", "-"), flush=True)
            continue
        symbol, family = row["symbol"], row["family"]
        ef.resolve(symbol, allow_stale=True)
        bars, ctx = cs._context(symbol)
        common = dict(lo=lo, hi=ef.OOS_END, include_trades=True,
                      fill_bars=cs._fill_bars(symbol, bars))
        cell1 = ef.backtest(family, bars, ctx, row["params"], **common)
        if not has_feed(symbol, source):
            print("%-30s %-10s %5s %6s %7d %7s %10.1f%% %11s %11s %9s"
                  % (name, "-", "-", "-", cell1["trades"], "-",
                     cell1["return_pct"], f"no {source}", f"no {source}", "-"),
                  flush=True)
            continue

        feed = lat.FEED.get(symbol, "dukascopy")
        seconds = lat.MEASURED_LAG_SECONDS[feed]
        out_delay = exit_delay_seconds(bars, seconds)
        stamps = [b[ef.TS] for b in bars]
        opens = {b[ef.TS]: b[ef.O] for b in bars}
        spreads = spread_map(symbol, stamps, source)
        entries = price_map(symbol, opens, seconds + BRIDGE_QUEUE_SECONDS,
                            source)
        exits = price_map(symbol, opens, out_delay, source)
        release_feed(source)
        cell4 = ef.backtest(family, bars, ctx, row["params"],
                            tick_spreads=spreads, entry_prices=entries,
                            **common)
        cell5 = ef.backtest(family, bars, ctx, row["params"],
                            tick_spreads=spreads, entry_prices=entries,
                            exit_prices=exits, **common)
        one += cell1["return_pct"]
        four += cell4["return_pct"]
        five += cell5["return_pct"]
        rows[name] = {"feed": feed, "source": source, "lag_seconds": seconds,
                      "exit_delay_seconds": out_delay,
                      "bar_seconds": bar_seconds(bars),
                      "trades": cell1["trades"],
                      # TRADES repriced, not covered BARS. The column used to
                      # print `len(entries)` -- 3,090 next to a 200-trade sleeve
                      # -- which reads as a coverage ratio and is not one.
                      "priced": sum(1 for t in cell1["trade_log"]
                                    if t["entry_ts"] in entries),
                      "exit_priced": sum(1 for t in cell1["trade_log"]
                                         if t["exit_ts"] in exits),
                      "covered_bars": len(entries),
                      "cell1_return_pct": cell1["return_pct"],
                      "cell4_return_pct": cell4["return_pct"],
                      "cell5_return_pct": cell5["return_pct"],
                      "cell1_max_dd_pct": cell1["max_dd_pct"],
                      "cell5_max_dd_pct": cell5["max_dd_pct"]}
        print("%-30s %-10s %5.0f %6.0f %7d %7d %10.1f%% %10.1f%% %10.1f%% %8.1fpp"
              % (name, feed, seconds, out_delay, cell1["trades"],
                 rows[name]["priced"],
                 cell1["return_pct"], cell4["return_pct"], cell5["return_pct"],
                 cell5["return_pct"] - cell1["return_pct"]), flush=True)

    print("-" * len(head), flush=True)
    print("%-30s %-10s %5s %6s %7s %7s %10.1f%% %10.1f%% %10.1f%% %8.1fpp"
          % ("SUM (tick-covered)", "", "", "", "", "", one, four, five,
             five - one), flush=True)
    print("\n`priced` is TRADES repriced. On the BARS feed it should sit on "
          "`trades`;\n  a shortfall there is a minute the broker did not "
          "quote. On TICKS it runs\n  near 62% because those tables have "
          "holes, and the remainder keeps the\n  sealed fill, which makes "
          "that column's delta a lower bound.", flush=True)
    print("\n`exit` is seconds from the exit bar's OPEN to the market "
          "order landing:\n  one whole bar, plus the feed lag, plus the "
          "bridge queue. It is large\n  because the account holds no "
          "broker-side stop, not because the feed is slow.", flush=True)
    _merge(path, {f"cells_{source}": rows,
                  f"cells_sum_{source}": {"cell1": one, "cell4": four,
                                          "cell5": five}})
    return rows


# --------------------------------------------------------------------------- #
# what the account's own fills say
# --------------------------------------------------------------------------- #


def slippage(path=OUT_PATH):
    """Every executed deal on this account, against the quote at its own second.

    THE SAMPLE IS THE FINDING. Sixty-six deals over four days is enough to say
    whether commission is charged at all and roughly what spread the account
    crosses. It is not enough to estimate slippage per symbol, and this reports
    the count on every row so that nobody reads it as though it were.

    WHAT IT CAN ESTABLISH. `pro-commission-is-measured-zero` was measured this
    way: a minimum-lot round trip booked 0.0000 on both deals, so Pro's entire
    cost is the spread. `commission` and `swap` are summed straight off the deal
    records and are facts about the account rather than estimates.

    WHAT IT CANNOT. True slippage is `deal.price` minus the quote the order was
    sent at, and only the terminal knows the second one. What is compared here
    is `deal.price` against the tick table's own quote at the same second, which
    mixes slippage with the gap between MT5's clock and the store's. A `null`
    `quote_spread_bp` means no tick table covers that broker symbol at all.

        py -m sandbox.research.exness_live_execution slippage
    """
    try:
        import MetaTrader5 as mt5  # noqa: PLC0415
    except ImportError:
        raise SystemExit("MetaTrader5 is not installed on this interpreter")

    if not mt5.initialize():
        print(f"MT5 initialization failed: {mt5.last_error()}",
              file=sys.stderr)
        raise SystemExit(1)
    try:
        deals = mt5.history_deals_get(
            datetime.now(tz=UTC) - timedelta(days=30), datetime.now(tz=UTC))
    finally:
        mt5.shutdown()
    if not deals:
        raise SystemExit("no deals in the last 30 days on this account")

    by_broker = defaultdict(list)
    for deal in deals:
        by_broker[deal.symbol].append(deal)

    rows = {}
    print(f"{'broker':10}{'deals':>7}{'commission':>12}{'swap':>10}"
          f"{'quote bp':>10}  {'first':19}  {'last':19}")
    for broker, group in sorted(by_broker.items()):
        # `broker_symbol` maps the study's name onto the terminal's, so the
        # reverse lookup is the only way back to a tick table from a deal.
        symbol = next((s for s in _canon_symbols()
                       if ef.broker_symbol(s).upper() == broker.upper()), None)
        spread = None
        if symbol is not None and has_ticks(symbol):
            stamps, values = load_quotes(symbol)
            if len(stamps):
                import numpy as np  # noqa: PLC0415

                at = []
                for deal in group:
                    index = np.searchsorted(stamps, int(deal.time),
                                            side="right") - 1
                    if index >= 0 and int(deal.time) - stamps[index] <= 60:
                        at.append(float(values[index]))
                spread = round(statistics.fmean(at), 4) if at else None
            release_quotes()
        times = sorted(datetime.fromtimestamp(int(d.time), tz=UTC)
                       for d in group)
        row = {"deals": len(group),
               "commission": round(sum(float(d.commission) for d in group), 4),
               "swap": round(sum(float(d.swap) for d in group), 4),
               "quote_spread_bp": spread,
               "first": times[0].strftime("%Y-%m-%dT%H:%M:%S"),
               "last": times[-1].strftime("%Y-%m-%dT%H:%M:%S")}
        rows[broker] = row
        print(f"{broker:10}{row['deals']:>7}{row['commission']:>12.4f}"
              f"{row['swap']:>10.4f}"
              f"{'-' if spread is None else format(spread, '.4f'):>10}  "
              f"{row['first']:19}  {row['last']:19}")
    print(f"\n{len(deals)} deals total. Commission is the number that has "
          "been checked and\n  held at zero; the spread column is an order of "
          "magnitude, not a measurement.")
    _merge(path, {"slippage": rows})
    return rows


# --------------------------------------------------------------------------- #
# the window every command runs in
# --------------------------------------------------------------------------- #


def coverage(path=OUT_PATH, source=DEFAULT_FEED):
    """How much of each day the tick tables actually hold. READ THIS FIRST.

    EVERY OTHER TABLE IN THIS MODULE IS CONDITIONAL ON THIS ONE. A bar the ticks
    do not reach keeps the sealed fill, so a symbol with holes reports a smaller
    execution cost than it has -- not because the execution is cheap but because
    most of its trades were never repriced. `cells` prints `priced` for the same
    reason; this says WHY the number is what it is.

    `hours/day` is the median count of distinct hours holding at least one
    quote. 24 is a complete day on a 24-hour symbol. `full days` counts days at
    22 or more, which allows for a thin hour without calling the day broken.
    `gaps>1h` counts holes inside the window and `median gap` is how long they
    run -- a median near 14 hours is a table that stops overnight and resumes
    the following afternoon, which is what usdjpy does.

    A LOW NUMBER IS AN IMPORT PROBLEM AND NOT A MARKET ONE. Weekend closes are
    excluded by counting only days that hold any quote at all, so what is left
    is the vendor's own gaps in what was fetched. `tools/exness_tick_import.py`
    is the fix.

        py -m sandbox.research.exness_live_execution coverage
    """
    import numpy as np

    lo = tick_window_start(source=source)
    print(_window_banner(lo, source))
    print(f"\n{'symbol':10}{'days':>7}{'hours/day':>11}{'full days':>11}"
          f"{'share':>8}{'gaps>1h':>9}{'median gap':>12}")
    rows = {}
    for symbol in _mapped_symbols():
        if not has_feed(symbol, source):
            print(f"{symbol:10}  no {source} table")
            continue
        if source == "bars":
            stamps = _minutes(symbol)[0]
        else:
            stamps = _quotes(symbol)[0]
        stamps = np.asarray(stamps)
        stamps = stamps[stamps >= lo]
        if not len(stamps):
            release_feed(source)
            continue
        # One bucket per (day, hour) that holds a quote. Counted over the whole
        # array rather than a sample: a sample can miss a thin hour and report a
        # broken day as whole, which is the error this table exists to avoid.
        day = stamps // 86400
        hour = stamps % 86400 // 3600
        seen = {}
        for d, h in zip(day.tolist(), hour.tolist()):
            seen.setdefault(d, set()).add(h)
        per_day = sorted(len(v) for v in seen.values())
        full = sum(1 for v in per_day if v >= 22)
        step = np.diff(stamps)
        big = step[step > 3600]
        median_gap = float(np.median(big)) / 3600 if len(big) else 0.0
        rows[symbol] = {"days": len(seen),
                        "median_hours_per_day": per_day[len(per_day) // 2],
                        "full_days": full,
                        "full_share": round(full / len(seen), 3),
                        "gaps_over_1h": int(len(big)),
                        "median_gap_hours": round(median_gap, 2)}
        print(f"{symbol:10}{len(seen):>7}"
              f"{rows[symbol]['median_hours_per_day']:>11}{full:>11}"
              f"{100 * rows[symbol]['full_share']:>7.0f}%{len(big):>9}"
              f"{median_gap:>11.1f}h")
        release_feed(source)
    print("\n24 hours a day and a 100% share is a table that can price every "
          "trade.\n  Anything less means `cells` and `book` repriced only "
          "part of the book and\n  their deltas are lower bounds. Fix with "
          "`tools/exness_tick_import.py`, then\n  re-run `precompute`.")
    _merge(path, {f"coverage_{source}": rows})
    return rows


def map_spans(path=None, source=DEFAULT_FEED):
    """`{symbol: (first, last)}` covered stamp, over bars ALL THREE maps reach.

    The span is an intersection with the STUDY bars, not a property of the
    broker table: a map only holds a stamp the deciding series also has. So a
    stale vendor table shows up here as a short span even though
    `exness_<broker>_1m` runs to the window's end -- which is what `nq` (vendor
    ends 2026-08-07), `hk50` and the three gold crosses do.
    """
    out = {}
    for symbol, (spreads, entries, exits) in load_maps(path, source).items():
        covered = set(spreads) & set(entries) & set(exits)
        if covered:
            out[symbol] = (min(covered), max(covered))
    return out


def tick_window_start(path=None, source=DEFAULT_FEED, symbols=None):
    """The first stamp at which EVERY mapped canon symbol has tick coverage.

    THE LATEST START WINS, NOT THE EARLIEST. The tables do not begin together --
    btc and ethusd open 2026-01-01 09:30, ukoil not until 2026-01-02 09:00 --
    and a window opened at the earliest of them runs ukoil on the vendor open
    and the constant spread for a day and a half while everything else is
    already tick-priced. The sealed and live columns would then differ by which
    symbols happened to be covered as much as by the execution, which is not a
    comparison anyone can read.

    So the window opens where the last symbol does. Inside it every sleeve is
    repriced on every trade, and the difference between the two runs is
    execution and nothing else.

    THE END NEEDS NO SUCH CLAMP AND IS CHECKED RATHER THAN ASSUMED. Coverage
    runs to 2026-08-27/28 on every symbol while `ef.OOS_END` is 2026-08-21, so
    the window closes inside the tick history already. `selftest` is not the
    place for that check because it is a property of the maps, not the engine;
    it is asserted here, where the window is chosen.
    """
    # IMPORTED FOR ITS SIDE EFFECT, and that is worth stating plainly:
    # `exness_combined_strategies` rebinds `ef.OOS_END` to `CANON_DATA_END` at
    # import time. Without this, the window a command reports depends on whether
    # some other function happened to import it first -- `coverage` printed
    # 2026-08-16 while `cells` printed 2026-08-21 for the same window.
    from sandbox.research import exness_combined_strategies as cs  # noqa: F401

    spans = map_spans(path, source)
    if not spans:
        raise SystemExit(
            f"no {source} maps on disk -- run `py -m "
            f"sandbox.research.exness_live_execution precompute "
            f"--source {source}` first")
    # SCOPED TO THE CALLER'S OWN SYMBOLS. `precompute` now builds maps for the
    # whole candidate pool, not just the book, and a screening-only symbol with
    # a stale vendor table must not move -- or veto -- the window of a book that
    # never prices it. Default is the book's own symbols, so `book` and `cells`
    # are unchanged.
    if symbols is None:
        symbols = _mapped_symbols()
    spans = {k: v for k, v in spans.items() if k in set(symbols)}
    starts = {k: v[0] for k, v in spans.items()}
    ends = {k: v[1] for k, v in spans.items()}
    if not starts:
        raise SystemExit("tick maps hold no fully covered bar on any symbol")
    start = max(starts.values())
    latest = min(ends.values())
    short_by = (ef.OOS_END - latest) / 86400.0
    if short_by > MAX_SHORT_DAYS:
        raise SystemExit(
            f"coverage ends {_stamp(latest)} on {min(ends, key=ends.get)}, "
            f"{short_by:.1f} days before the window closes at "
            f"{_stamp(ef.OOS_END)} -- catch that VENDOR table up before "
            "trusting a comparison over it")
    if short_by > 0:
        # Named rather than silent: the run is allowed, and the reader is told
        # which symbol is thin at the end and by how much.
        log(f"{min(ends, key=ends.get)} covers only to {_stamp(latest)}, "
            f"{short_by * 24:.0f}h short of the window -- within tolerance")
    return start


def _stamp(ts):
    return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M")


def _window_banner(start, source=DEFAULT_FEED):
    """One line naming the window, the feed, and the symbol that chose it."""
    firsts = {k: v[0] for k, v in map_spans(source=source).items()
              if k in set(_mapped_symbols())}
    last = max(firsts, key=firsts.get)
    return (f"WINDOW {_stamp(start)} .. {_stamp(ef.OOS_END)} UTC on the "
            f"{source.upper()} feed -- opened where the last symbol's history "
            f"begins ({last})")


# --------------------------------------------------------------------------- #
# controls
# --------------------------------------------------------------------------- #


def selftest():
    """Assert the exit plumbing changes nothing it was not asked to change.

    TWO CONTROLS, AND THE OBVIOUS THIRD ONE DOES NOT EXIST.

        absent    `exit_prices=None` and `exit_prices={}` must reproduce the
                  sealed run object for object. Every result in the repository
                  was produced without this argument, so if it is not inert when
                  omitted, nothing published before it is readable.
        identity  a map holding, for each realised exit, the price that run
                  ALREADY paid must reproduce the sealed return to the digit.
                  This is the one that tests the wiring rather than the default:
                  it proves the override reaches the settle block, is keyed on
                  the bar the exit fires on, and is spent on every reason.

    THERE IS NO `+0s` EXIT CONTROL and it would be wrong to add one. `latency`
    can use `+0s` because an entry at zero delay is a real thing -- the order
    goes out at the bar's open, which is where the backtest already puts it. An
    exit at zero delay is not: the stop was breached somewhere INSIDE the bar,
    and the runtime cannot know that until the bar has closed. Repricing an exit
    at the bar's own open would fill it on information that had not printed yet,
    which is the look-ahead [[entry-must-be-next-bar-open]] records. One whole
    bar is the physical floor, which is why `exit_delay_seconds` starts there.

        py -m sandbox.research.exness_live_execution selftest
    py -m sandbox.research.exness_live_execution coverage  <- read FIRST
    """
    from sandbox.research import exness_combined_strategies as cs
    from sandbox.research import exness_latency as lat

    failed = 0
    print(f"{'sleeve':32}{'trades':>8}{'sealed':>11}{'identity':>11}"
          f"{'absent':>9}{'verdict':>10}")
    for name, row in lat._native_rows():
        symbol = row["symbol"]
        ef.resolve(symbol, allow_stale=True)
        bars, ctx = cs._context(symbol)
        common = dict(lo=ef.IS_END, hi=ef.OOS_END, include_trades=True,
                      fill_bars=cs._fill_bars(symbol, bars))
        base = ef.backtest(row["family"], bars, ctx, row["params"], **common)
        absent = (ef.backtest(row["family"], bars, ctx, row["params"],
                              exit_prices=None, **common) == base
                  and ef.backtest(row["family"], bars, ctx, row["params"],
                                  exit_prices={}, **common) == base)
        # The price each exit actually paid, recovered from the log: `gross` is
        # `side * (price - entry)`, so the fill is `entry + side * gross`. There
        # is no other record of it -- the trade log stores the entry and the
        # move, never the exit price itself.
        identity = {t["exit_ts"]: t["entry"] + t["side"] * t["gross"]
                    for t in base["trade_log"]}
        same = ef.backtest(row["family"], bars, ctx, row["params"],
                           exit_prices=identity, **common)
        matched = (same["trades"] == base["trades"]
                   and abs(same["return_pct"] - base["return_pct"]) < 1e-9)
        verdict = "ok" if (absent and matched) else "BROKEN"
        failed += verdict == "BROKEN"
        print(f"{name:32}{base['trades']:>8}{base['return_pct']:>10.4f}%"
              f"{same['return_pct']:>10.4f}%{str(absent):>9}{verdict:>10}")
    print(f"\n{failed} broken of {len(lat._native_rows())}")
    return failed


def _all_rows():
    """`(sleeve, row)` for EVERY canon member, `row=None` for the external ones.

    `exness_latency._native_rows` drops the five EXTERNAL sleeves silently
    because it cannot run them through `ef.backtest`. That is the right
    behaviour for it and the wrong output here: a table of twenty rows for a
    twenty-five sleeve book reads as a complete book, and a reader counting
    them has no way to see the five that are missing. So they are yielded with
    no row and printed as `external`.

    Their exclusion from the ENTRY leg costs nothing measurable -- NQ arrives
    over Databento, a direct exchange feed about a second behind, which against
    a 30-minute bar is zero. The EXIT leg is a different matter and they are not
    exempt from it, but they are replayed by `combined_book` on level-two data,
    so `book` is where they are charged and `cells` is not.
    """
    from sandbox.research import exness_combined_strategies as cs
    from sandbox.research import exness_latency as lat

    native = dict(lat._native_rows())
    return [(sleeve, native.get(sleeve)) for sleeve in cs.BOOK]


def _canon_symbols():
    from sandbox.research import exness_combined_strategies as cs

    return sorted({s.split(":", 1)[0] for s in cs.BOOK})


def _screenable_symbols(source=DEFAULT_FEED):
    """Every symbol a map can be built for -- the BOOK's and the POOL's.

    WIDER THAN `_mapped_symbols` ON PURPOSE. That one answers "which symbols
    does the canon book read a map for", which is the right question for a book
    run and the wrong one for a screen: a candidate that is not seated still has
    to be priced before anyone can argue for seating it, and eight of them were
    silently skipped for want of a table.

    `nq` is included here and excluded there. Its five canon sleeves are
    EXTERNAL and are replayed by `combined_book` on level-two data through a
    path that never consults these maps, so building one cannot change the book
    -- but a NON-external nq candidate in the pool can now be screened, which
    it could not before.

    Symbols with no table are dropped by the caller rather than here, so the
    log names each one instead of the list quietly being shorter.
    """
    from sandbox.research import exness_combined_strategies as cs

    wanted = set(_mapped_symbols())
    wanted.update(s.split(":", 1)[0] for s in cs.BOOK)
    try:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            wanted.update(row["symbol"] for row in cs.candidates())
    except Exception as error:  # noqa: BLE001 - a pool that cannot be built
        # must not stop the book's own symbols being priced.
        log(f"candidate pool unavailable ({error}); book symbols only")
    return sorted(s for s in wanted if has_feed(s, source))


def _mapped_symbols():
    """Canon symbols that actually READ a tick map -- the non-external ones.

    `nq` is excluded and it is the expensive one: 46.6 million quotes, and every
    NQ sleeve is EXTERNAL, replayed by `combined_book` on level-two data through
    a path that never consults these maps. Building it was pure cost. Databento
    runs about a second behind anyway, which at a 30-minute bar rounds to no
    delay at all, so the exclusion loses nothing measurable.
    """
    from sandbox.research import exness_combined_strategies as cs

    return sorted({sleeve.split(":", 1)[0] for sleeve in cs.BOOK
                   if sleeve not in cs.EXTERNAL})


def _merge(path, block):
    stored = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    stored.update(block)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
    return stored


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("coverage",
                   help="how much of each day the tick tables hold; every "
                        "other table here is conditional on it")
    sub.add_parser("selftest",
                   help="assert exit repricing is inert when absent and exact "
                        "when handed the prices the run already paid")
    sub.add_parser("cells",
                   help="per sleeve, standalone: sealed fill vs late entry vs "
                        "late entry AND market exit")
    sub.add_parser("precompute",
                   help="write the per-symbol spread/entry/exit maps the "
                        "book reads")
    sub.add_parser("spreads", help="tick spread distribution per symbol")
    sub.add_parser("sleeve", help="entry-time spread per canon sleeve")
    sub.add_parser("book",
                   help="the shared-balance canon book, sealed against live")
    sub.add_parser("slippage", help="this account's executed deals")
    late = sub.add_parser("latency",
                          help="entries re-priced N SECONDS after the open")
    late.add_argument("--delays", type=int, nargs="+", default=[0, 78, 300])
    for name in ("precompute", "cells", "book", "coverage"):
        sub.choices[name].add_argument(
            "--source", choices=FEEDS, default=DEFAULT_FEED,
            help="which broker series to price from; `bars` is "
                 "exness_<broker>_1m and covers 2025-2026, `ticks` is the "
                 "control it was validated against")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "latency":
        latency(delays=tuple(args.delays))
        return 0
    if args.command in ("precompute", "cells", "book", "coverage"):
        {"precompute": precompute, "cells": cells, "book": book,
         "coverage": coverage}[args.command](source=args.source)
        return 0
    {"spreads": spreads, "sleeve": sleeve, "slippage": slippage,
     "selftest": selftest}[args.command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
