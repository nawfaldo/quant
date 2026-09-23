"""Parquet access and bar loading. Strategy-agnostic.

Mirrors the loaders in `live_trade/src/backtest/prepare.rs` so a replica sees exactly
the rows the Rust engine sees. Everything here is cached on disk after the first
pull because the level-two pull is the slow part of any sweep.

QUESTDB IS GONE. Every table was exported to `data/parquet/` and dropped, so
there is no database to fall back to and no `query()` here any more. All reads go
through `sandbox.parquet_store`, which owns the file layout, the timestamp
decoding and the row-group pruning; this module owns only the shapes the
strategies want.

A cache key must cover *the rows*, not just the query. The level-two bars were
once keyed on `f"l2:{symbol}"` -- a constant -- so extending the history could
not invalidate them, and every sweep kept fitting a stale slice while the Rust
engine read the full table. Keys now carry a fingerprint of each source file
(`_table_fingerprint`), so an import invalidates them by itself.

Market timestamps are New York wall-clock encoded as fake UTC (see AGENT.md).
Bars are plain tuples, indexed by the TS/O/H/L/C/D/DE constants below, because a
sweep touches them millions of times and attribute lookup is not free.
"""
import glob
import hashlib
import json
import os
import pickle
import zlib
from datetime import datetime, timezone

import numpy as np

# `parquet_store` first: it disarms the WMI lookup that hangs pandas on this
# machine, and it has to do so before anything imports pyarrow.
from sandbox import parquet_store as store
import pyarrow.compute as pc

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

# bar tuple indices: timestamp, open, high, low, close, volume delta, depth events
TS, O, H, L, C, D, DE = range(7)

_NS_PER_MINUTE = 60_000_000_000


def _cached(name, key, build):
    """`build()`'s result, memoised on disk under `name` for as long as `key` holds."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    bin_path = os.path.join(CACHE_DIR, f"{name}.{digest}.bin")
    json_path = os.path.join(CACHE_DIR, f"{name}.{digest}.json")

    if os.path.exists(bin_path):
        try:
            with open(bin_path, "rb") as f:
                return pickle.loads(zlib.decompress(f.read()))
        except Exception:
            pass

    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                val = json.load(f)
            # Silently upgrade to .bin in background
            try:
                staged = f"{bin_path}.{os.getpid()}.tmp"
                with open(staged, "wb") as f:
                    f.write(zlib.compress(pickle.dumps(val, protocol=5), level=1))
                os.replace(staged, bin_path)
            except Exception:
                pass
            return val
        except Exception:
            pass

    value = build()
    staged = f"{bin_path}.{os.getpid()}.tmp"
    with open(staged, "wb") as f:
        f.write(zlib.compress(pickle.dumps(value, protocol=5), level=1))
    try:
        os.replace(staged, bin_path)
    except OSError:
        try:
            os.remove(staged)
        except OSError:
            pass
    _drop_stale(name, bin_path)
    return value


def _drop_stale(name, keep):
    """Delete superseded entries for `name`; they are dead weight once the key moves."""
    for entry in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, entry)
        if entry.startswith(f"{name}.") and (entry.endswith(".bin") or entry.endswith(".json")) and path != keep:
            try:
                os.remove(path)
            except OSError:
                pass


def _newest_cache(name):
    """The newest fingerprinted cache entry for `name`, in either on-disk form."""
    matches = glob.glob(os.path.join(CACHE_DIR, f"{name}.*.bin"))
    matches += glob.glob(os.path.join(CACHE_DIR, f"{name}.*.json"))
    if not matches:
        return None
    path = max(matches, key=os.path.getmtime)
    if path.endswith(".bin"):
        with open(path, "rb") as handle:
            return pickle.loads(zlib.decompress(handle.read()))
    with open(path) as handle:
        return json.load(handle)


def _table_fingerprint(tables):
    """`rows|bytes|mtime` per table -- the part of a cache key that tracks the data."""
    return store.fingerprints(tables)


def _minute_seconds(stamps_ns):
    """Epoch seconds of the minute each nanosecond timestamp falls in."""
    return (stamps_ns // _NS_PER_MINUTE * 60).astype(np.int64)


def _group_starts(keys):
    """Start index of each run of equal values in an already-grouped array."""
    if not len(keys):
        return np.empty(0, dtype=np.int64)
    return np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))


def _signed_size(chunk, sizes):
    """`size` on a BUY, `-size` on a SELL, zero otherwise.

    The comparison stays inside Arrow. `side` is a string column of up to a
    hundred million rows and `to_pylist()` on it builds that many Python strings
    -- several gigabytes on a machine that has sixteen, to answer a question
    worth one bit per row.
    """

    buys = pc.equal(chunk["side"], "BUY").to_numpy(zero_copy_only=False)
    sells = pc.equal(chunk["side"], "SELL").to_numpy(zero_copy_only=False)
    return np.where(buys, sizes, np.where(sells, -sizes, 0.0))


def _order_flow_source(prefix, symbol):
    """One order-flow source, matching load_order_flow_source in prepare.rs.

    Trades become a minute bar of first/max/min/last price plus signed size, and
    the depth table contributes only its event count for the minute. Both feeds
    carry nanosecond timestamps, so one minute rule serves both -- the SQL used
    to need two because QuestDB's `SAMPLE BY` and `date_trunc` disagreed about
    which one could be applied to a non-designated timestamp.

    STREAMED RATHER THAN MATERIALISED. `dbento_nq_ticks` is ninety-six million
    rows; holding its price, size and side columns at once is gigabytes for a
    result that is a hundred thousand minute bars. Row groups arrive in time
    order, so a minute is merged into the accumulator with "first price wins,
    last price wins" and the answer is the same as one pass over the sorted
    whole.
    """
    table = f"{prefix}_{symbol}_ticks"
    if not store.has_table(table):
        return {}

    out = {}
    for stamps, chunk in store.iter_batches(
        table, columns=["price", "size", "side"]
    ):
        sizes = store._floats(chunk["size"])
        traded = sizes > 0
        if not traded.any():
            continue
        signed = _signed_size(chunk, sizes)[traded]
        prices = store._floats(chunk["price"])[traded]
        minutes = _minute_seconds(stamps[traded])

        order = np.argsort(minutes, kind="stable")
        minutes, prices, signed = minutes[order], prices[order], signed[order]

        starts = _group_starts(minutes)
        ends = np.append(starts[1:], len(minutes))
        for minute, first, high, low, last, delta in zip(
            minutes[starts].tolist(),
            prices[starts].tolist(),
            np.maximum.reduceat(prices, starts).tolist(),
            np.minimum.reduceat(prices, starts).tolist(),
            prices[ends - 1].tolist(),
            np.add.reduceat(signed, starts).tolist(),
        ):
            row = out.get(minute)
            if row is None:
                out[minute] = [minute, first, high, low, last, delta, 0]
            else:
                row[H] = max(row[H], high)
                row[L] = min(row[L], low)
                row[C] = last
                row[D] += delta

    depth_table = f"{prefix}_{symbol}_depth"
    if store.has_table(depth_table):
        for minute, count in _depth_counts(depth_table).items():
            row = out.get(minute)
            if row is not None:
                row[DE] = count
    return out


def _depth_counts(table):
    """`{minute: events}` for a depth table, streamed a row group at a time.

    `dbento_nq_depth` is five billion rows. Only its timestamp column is read and
    only one row group is resident at a time, so the count costs a column scan
    rather than a materialised table.
    """
    counts = {}
    for stamps, _ in store.iter_batches(table, columns=["timestamp"]):
        minutes, sizes = np.unique(_minute_seconds(stamps), return_counts=True)
        for minute, size in zip(minutes.tolist(), sizes.tolist()):
            counts[minute] = counts.get(minute, 0) + size
    return counts


def _order_flow_tables(symbol):
    return [f"{prefix}_{symbol}_{kind}"
            for prefix in ("dbento", "bm") for kind in ("ticks", "depth")]


def load_level_two_bars(symbol="nq", use_cache=True):
    """Level-two minute bars: dbento first, bookmap overwrites overlapping minutes."""
    def build():
        merged = {}
        merged.update(_order_flow_source("dbento", symbol))
        merged.update(_order_flow_source("bm", symbol))
        return [merged[k] for k in sorted(merged)]

    if not use_cache:
        return build()
    key = f"l2:{symbol}:{_table_fingerprint(_order_flow_tables(symbol))}"
    return _cached(f"{symbol}_l2_bars", key, build)


def load_cached_level_two_bars(symbol="nq", use_cache=True):
    """Read the newest existing L2 bar cache without rescanning.

    Raw S4 replay already pins and reports its exact input caches. Its research
    loop must not re-scan the roughly billion-row depth table merely to
    rediscover a bar cache built earlier in the same data state.
    """
    cached = _newest_cache(f"{symbol}_l2_bars")
    if cached is None:
        raise SystemExit(f"no fingerprinted {symbol} L2 bar cache under {CACHE_DIR}")
    return cached


def load_ohlcv_bars(symbol="nq", use_cache=True):
    """`<symbol>_1m` OHLCV minute bars, with no order-flow columns (delta 0)."""
    table = f"{symbol}_1m"

    def build():
        return [[ts, o, h, l, c, 0.0, 0]
                for ts, o, h, l, c, _ in store.read_bars(table, bar_minutes=1)]

    if not use_cache:
        return build()
    return _cached(f"{symbol}_ohlcv_bars",
                   f"ohlcv:v2:{table}:{_table_fingerprint([table])}", build)


#: order-flow columns, in the order `attach_l2_features` selects them
L2_FIELDS = (
    "midprice", "spread", "microprice", "top1_imbalance", "top5_imbalance",
    "top10_imbalance", "bid_add_volume", "bid_cancel_volume", "ask_add_volume",
    "ask_cancel_volume", "aggressive_buy_volume", "aggressive_sell_volume",
    "trade_delta", "price_change", "delta_per_tick", "executed_at_bid",
    "executed_at_ask", "bid_replenishment", "ask_replenishment",
    "bid_depth_distance", "ask_depth_distance", "depth_weighted_distance",
    "trade_count", "depth_event_count", "book_valid",
)

OPTIONAL_L2_FIELDS = (
    "bid_standing_depth",
    "ask_standing_depth",
)

#: snapshot columns take the minute's last value; flow columns are summed
_L2_LAST = {"midprice", "spread", "microprice", "top1_imbalance", "top5_imbalance",
            "top10_imbalance", "bid_depth_distance", "ask_depth_distance",
            "depth_weighted_distance", "bid_standing_depth", "ask_standing_depth",
            "book_valid"}


def load_l2_features(symbol="nq", use_cache=True):
    """`{ts: {field: value}}` minute features, mirroring attach_l2_features.

    Same rollup the Rust loader uses -- `last()` on snapshot columns, `sum()` on
    flow columns, dbento first with bookmap overwriting overlapping minutes.
    Minutes with no feature row are simply absent; a bar without one gets the
    default `OrderFlowFeatures` in Rust, i.e. `book_valid` false.

    `replenishment_score` is derived here rather than read, exactly as
    `prepare.rs` derives it from the two summed replenishment columns. Note that
    a *summed* `delta_per_tick` is meaningless (see AGENT-level notes on the
    aggregation trap); build minute ratios from summed inputs instead.
    """
    table = f"{symbol}_l2_features_1s"
    available = set(store.column_names(table))
    fields = L2_FIELDS + tuple(
        name for name in OPTIONAL_L2_FIELDS if name in available
    )

    def build():
        return _rollup_l2_features(table, fields)

    if use_cache:
        # v3 is the Parquet rollup. It retains the source alongside each row, as
        # v2 did: existing strategies ignore it, but source-aware normalization
        # (such as LWI) needs it because the two collectors can have materially
        # different feature distributions.
        key = f"l2f:v3:{','.join(fields)}:{_table_fingerprint([table])}"
        rows = _cached(f"{symbol}_l2_features", key, build)
    else:
        rows = build()

    return _decode_l2_rows(rows, fields)


def _rollup_l2_features(table, fields):
    """`[[source, [ts_us, *values]]]` -- one row per source per traded minute.

    The table is read ONCE and split by source afterwards. Reading it per source
    would decode the whole eight-million-row file twice to keep half of it each
    time, and the two collectors interleave rather than partition by date.
    """
    stamps, chunk = store.scan(table, columns=["source", *fields])
    if not len(stamps):
        return []
    values = {name: store._floats(chunk[name]) for name in fields}

    rows = []
    for source in ("dbento", "bm"):
        selected = np.flatnonzero(
            pc.equal(chunk["source"], source).to_numpy(zero_copy_only=False))
        if not len(selected):
            continue
        selected = selected[np.argsort(stamps[selected], kind="stable")]
        minutes = _minute_seconds(stamps[selected])
        starts = _group_starts(minutes)
        ends = np.append(starts[1:], len(minutes))

        columns = []
        for name in fields:
            column = values[name][selected]
            columns.append(column[ends - 1] if name in _L2_LAST
                           else np.add.reduceat(column, starts))

        for index, minute in enumerate(minutes[starts].tolist()):
            rows.append([source, [minute * 1_000_000,
                                  *(column[index].item() for column in columns)]])
    return rows


def _decode_l2_rows(rows, fields):
    out = {}
    for source, row in rows:
        record = {}
        for name, value in zip(fields, row[1:]):
            record[name] = bool(value) if name == "book_valid" else float(value or 0.0)
        for name in OPTIONAL_L2_FIELDS:
            record.setdefault(name, 0.0)
        record["source"] = source
        total = record["bid_replenishment"] + record["ask_replenishment"]
        record["replenishment_score"] = (
            (record["bid_replenishment"] - record["ask_replenishment"]) / total
            if total > 0 else 0.0)
        out[int(row[0]) // 1_000_000] = record
    return out


def load_cached_l2_features(symbol="nq"):
    """Read the newest existing feature cache without re-reading the source."""
    rows = _newest_cache(f"{symbol}_l2_features")
    if rows is None:
        raise SystemExit(f"no fingerprinted {symbol} L2 feature cache under {CACHE_DIR}")
    if not rows:
        return {}
    width = len(rows[0][1]) - 1
    fields = L2_FIELDS + OPTIONAL_L2_FIELDS[: max(0, width - len(L2_FIELDS))]
    return _decode_l2_rows(rows, fields)


#: Footprint conventions. Both are the defaults every retail footprint platform
#: ships with (ATAS, Quantower, Sierra), so a strategy that varies the *run
#: length* is varying the axis practitioners actually vary, while the ratio and
#: the noise floor stay where the published setups assume them.
IMBALANCE_RATIO = 3.0
IMBALANCE_MIN_VOLUME = 4.0
NQ_TICK = 0.25


def _footprint_minute(ladder, tick):
    """Reduce one minute's price ladder to the footprint facts strategies use.

    `ladder` is `{price: [ask_volume, bid_volume]}`, where "ask volume" is
    volume that traded at the offer (aggressive buying) and "bid volume" is
    volume that traded at the bid (aggressive selling).

    Two published patterns come out of this:

    *Unfinished auction* -- at the extreme of a bar, a finished auction leaves
    one side empty: the last buyer up there got filled and nobody sold back.
    When *both* sides printed at the extreme the auction never resolved, and
    that level is widely traded as a magnet the market returns to.

    *Stacked imbalance* -- the comparison is diagonal, ask at P against bid at
    P-tick, because those are the two sides of the same mini-auction. Three or
    more in a row on one side is the standard continuation trigger.
    """
    prices = sorted(ladder)
    if not prices:
        return None
    high, low = prices[-1], prices[0]
    ask_high, bid_high = ladder[high]
    ask_low, bid_low = ladder[low]

    stacked_buy = stacked_sell = 0
    run_buy = run_sell = 0
    for price in prices:
        ask, bid = ladder[price]
        bid_below = ladder.get(round(price - tick, 4), (0.0, 0.0))[1]
        ask_above = ladder.get(round(price + tick, 4), (0.0, 0.0))[0]
        buy_side = ask >= IMBALANCE_MIN_VOLUME and ask >= IMBALANCE_RATIO * bid_below
        sell_side = bid >= IMBALANCE_MIN_VOLUME and bid >= IMBALANCE_RATIO * ask_above
        run_buy = run_buy + 1 if buy_side else 0
        run_sell = run_sell + 1 if sell_side else 0
        stacked_buy = max(stacked_buy, run_buy)
        stacked_sell = max(stacked_sell, run_sell)

    poc = max(prices, key=lambda p: ladder[p][0] + ladder[p][1])
    return {
        # Both sides traded at the extreme: the auction there is unfinished.
        "unfinished_high": ask_high > 0 and bid_high > 0,
        "unfinished_low": ask_low > 0 and bid_low > 0,
        "stacked_buy": stacked_buy,
        "stacked_sell": stacked_sell,
        "poc": poc,
        "delta_at_high": ask_high - bid_high,
        "delta_at_low": ask_low - bid_low,
        "levels": len(prices),
    }


def load_footprint_features(symbol="nq", tick=NQ_TICK, use_cache=True):
    """`{ts: {...}}` per-minute footprint facts, built from the raw tick table.

    Read one row group at a time and reduced on the way in. The full ladder is
    roughly a hundred million rows over the sample and there is no reason to keep
    it: every published footprint pattern this repo implements is a small
    summary of one minute's ladder, so only the summary is cached.
    """
    table = f"dbento_{symbol}_ticks"

    def build():
        ladders = {}
        for stamps, chunk in store.iter_batches(
            table, columns=["price", "size", "side"]
        ):
            sizes = store._floats(chunk["size"])
            traded = sizes > 0
            if not traded.any():
                continue
            minutes = _minute_seconds(stamps[traded])
            prices = np.round(store._floats(chunk["price"])[traded], 4)

            buys = pc.equal(chunk["side"], "BUY").to_numpy(zero_copy_only=False)[traded]
            sizes = sizes[traded]

            # Reduce the row group to its distinct (minute, price) cells before
            # anything touches Python, so the loop below runs once per ladder
            # level rather than once per trade.
            cells, inverse = np.unique(
                np.stack((minutes, prices)), axis=1, return_inverse=True
            )
            ask = np.bincount(inverse, weights=np.where(buys, sizes, 0.0),
                              minlength=cells.shape[1])
            bid = np.bincount(inverse, weights=np.where(buys, 0.0, sizes),
                              minlength=cells.shape[1])
            for minute, price, ask_volume, bid_volume in zip(
                cells[0].astype(np.int64).tolist(), cells[1].tolist(),
                ask.tolist(), bid.tolist()
            ):
                level = ladders.setdefault(minute, {}).setdefault(price, [0.0, 0.0])
                level[0] += ask_volume
                level[1] += bid_volume

        out = {}
        for minute, ladder in ladders.items():
            summary = _footprint_minute(ladder, tick)
            if summary is not None:
                out[str(minute)] = summary
        return out

    if not use_cache:
        return {int(k): v for k, v in build().items()}
    key = (f"fp:v2:{tick}:{IMBALANCE_RATIO}:{IMBALANCE_MIN_VOLUME}:"
           f"{_table_fingerprint([table])}")
    return {int(k): v for k, v in _cached(f"{symbol}_footprint", key, build).items()}


BAR_SOURCES = {
    "level_two": load_level_two_bars,
    "cached_level_two": load_cached_level_two_bars,
    "ohlcv": load_ohlcv_bars,
}


def load_bars(source="level_two", symbol="nq", use_cache=True):
    """Bars for a strategy's declared `bars` source."""
    if source not in BAR_SOURCES:
        raise ValueError(f"unknown bar source {source!r}; expected one of {sorted(BAR_SOURCES)}")
    return BAR_SOURCES[source](symbol, use_cache)


def bar_range(source="level_two", symbol="nq"):
    """`(from, to)` ISO dates covering the bars a replica actually loads.

    Read off the bars rather than hardcoded, so `validation.py` always asks the
    engine for the same span the replica saw. Both ends are inclusive, matching
    the engine's `timestamp < dateadd('d',1,to)`.
    """
    bars = load_bars(source, symbol)
    if not bars:
        raise SystemExit(f"no {source} bars for {symbol}; is data/parquet populated?")
    day = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    return day(bars[0][TS]), day(bars[-1][TS])


SESSION_CLOSE_HOUR = 17  # last hour before the maintenance break (18:00 = next session)


def load_session_closes(symbol="nq"):
    """(day index, close) per session, oldest first.

    Mirrors load_session_closes in prepare.rs. The level-two feed is RTH-only, so
    daily trend indicators read the OHLCV table for the whole run rather than
    deriving closes from the traded bars.

    DERIVED FROM THE MINUTE TABLE, not from `<symbol>_1h`. The hourly tables were
    QuestDB `SAMPLE BY` views and were never exported, so the hour is rebuilt
    here; the filter is the same one -- only hours before the maintenance break
    count, so the 18:00 reopen belongs to the next session rather than closing
    this one.
    """
    table = f"{symbol}_1m"
    if not store.has_table(table):
        return []

    def build():
        hourly = store.read_bars(table, bar_minutes=60)
        closes = {}
        for ts, _open, _high, _low, close, _volume in hourly:
            if ts % 86_400 // 3600 < SESSION_CLOSE_HOUR:
                closes[ts // 86_400] = close
        return [[day, close] for day, close in sorted(closes.items())]

    rows = _cached(f"{symbol}_session_closes",
                   f"closes:v1:{SESSION_CLOSE_HOUR}:{_table_fingerprint([table])}",
                   build)
    return [(int(day), float(close)) for day, close in rows]


def load_hourly_vix(use_cache=True):
    """`[(ts, close)]` from `vix_1h`, oldest first. Mirrors attach_hourly_vix.

    THE TABLE IS NOT IN THE STORE. `vix_1h` was never exported out of QuestDB, so
    this returns nothing and `vix_series` yields the "no reading" value of 0.0
    for every bar -- which is what the Rust loader does for a bar with no VIX
    print. It is deliberately not an error: a dozen research modules and two live
    strategy replicas ask for VIX as one input among many, and the alternative to
    a no-reading series is those modules failing to import.
    """
    if not store.has_table("vix_1h"):
        return []

    def build():
        return [[ts, close] for ts, _o, _h, _l, close, _v
                in store.read_bars("vix_1h", bar_minutes=60)]

    if not use_cache:
        return build()
    return _cached("vix_1h", f"vix:v2:{_table_fingerprint(['vix_1h'])}", build)


def vix_series(bars, vix=None):
    """VIX aligned to `bars`: the last close at or before each bar, else 0.0.

    A step function, exactly as `attach_hourly_vix` in prepare.rs builds it --
    the hourly close is only known once its hour has passed, so a bar never sees
    a VIX print stamped after it.
    """
    values = vix if vix is not None else load_hourly_vix()
    out = [0.0] * len(bars)
    index = 0
    for i, bar in enumerate(bars):
        while index + 1 < len(values) and values[index + 1][0] <= bar[TS]:
            index += 1
        if index < len(values) and values[index][0] <= bar[TS]:
            out[i] = values[index][1]
    return out


def session_ranges(bars):
    """`[(day, high, low, close)]` per session from the traded bars, oldest first."""
    out = []
    day = None
    for bar in bars:
        current = bar[TS] // 86_400
        if current != day:
            out.append([current, bar[H], bar[L], bar[C]])
            day = current
        else:
            row = out[-1]
            row[1] = max(row[1], bar[H])
            row[2] = min(row[2], bar[L])
            row[3] = bar[C]
    return [tuple(row) for row in out]


def atr_by_day(bars, days=20):
    """`{day: ATR}` in points, averaged over the `days` sessions that closed
    strictly *before* that day.

    Point-in-time by construction: a session's own range is withheld until the
    next day begins, the same discipline `sma_gate` applies to closes. Days with
    fewer than `days` completed sessions are absent, and callers treat an absent
    day as "no reading" rather than as zero volatility.
    """
    sessions = session_ranges(bars)
    ranges = []
    out = {}
    previous_close = None
    for day, high, low, close in sessions:
        if len(ranges) >= days:
            out[day] = sum(ranges[-days:]) / days
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close),
                             abs(low - previous_close))
        ranges.append(true_range)
        previous_close = close
    return out


def sma_gate(session_closes, days):
    """Build `allowed(day, price)` for an N-session SMA trend gate.

    Returns a callable that is True while `price` is below the average of the N
    sessions that closed strictly before `day` -- a session's own close is
    withheld until the next day begins. Fewer than N closed sessions (warm-up)
    leaves the gate open, matching the Rust filter.
    """
    closes = sorted(session_closes or [])
    if days <= 0 or not closes:
        return lambda day, price: True

    state = {"closed": 0}

    def allowed(day, price):
        closed = state["closed"]
        while closed < len(closes) and closes[closed][0] < day:
            closed += 1
        state["closed"] = closed
        if closed < days:
            return True
        return price < sum(c for _, c in closes[closed - days:closed]) / days

    return allowed
