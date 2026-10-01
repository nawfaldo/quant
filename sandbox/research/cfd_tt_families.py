"""Strategies taken from trading creators' videos, scored like `cfd_families`.

WHAT THIS IS. `cfd_families` with its own family registry. Each family is one
strategy lifted from one creator's video, grouped by creator (`GROUPS`), and
every family carries a grid of its own settings -- indicator values, entry
style, exit style -- searched at 5m, 15m and 30m. Everything else is
`cfd_families` unchanged and imported, not copied:

    cost       Exness Pro 416209807: spread only, commission measured 0.0000,
               spread read from the broker's own minute table at entry
    fills      next-bar-open entry moved by the feed lag + bridge queue; exits
               are late market orders (no broker-side stop on this account)
    session    RTH only: every entry AND every exit inside the symbol's own
               cash session (`SESSION_ONLY`, window `rth`), one trade a day
    split      in sample to 2025-01-01, out of sample 2025-01-01..2026-08-16
    gates      the same `passes` / `choose` selection, t-stat ranked

Pro is pinned: `--broker` is refused, and results seal as
`exness_tt_families_<symbol>_<bar>.json`, so they never overwrite a
`cfd_families` seal.

    python -m sandbox.research.cfd_tt_families budget
    python -m sandbox.research.cfd_tt_families select
    python -m sandbox.research.cfd_tt_families validate
    python -m sandbox.research.cfd_tt_families select --symbols us500 --bar-minutes 15

With no `--symbols`, the run covers `DEFAULT_SYMBOLS`; with no
`--bar-minutes` it covers 5, 15 and 30; with no `--workers` it uses every
core. Every other flag is `cfd_families`'.

FULL POWER. `lean_context` replaces `cfd_families.context` with the dozen
arrays these families and the engine actually read: 554 MB a worker on FX at
5m against 1,763, so the pool is 12 wide instead of 2. The memory reserve is
2 GB (`EXNESS_MEMORY_RESERVE_GB`), and `fit_workers` still caps the pool to
what is free.

CREATORS. Each creator is a module and a group: `tt_luxalgo` (group `luxalgo`,
50 families from all 607 @luxalgo posts; post-to-family map in
`results/tiktok/luxalgo_strategy_notes.md`). Shared swing/FVG/session/level
features live in `tt_features`. A creator family's `exit_mode` may also be
`level*` (a price level the signal names) or `signal` (its own exit rule).

THE ONE ENGINE CHANGE. A family here may return `(side, stop_distance)` rather
than a bare side, so a stop can sit at a candle's extreme or at an ATR multiple
rather than at `cfd_families`' `stop_day x daily range`. Lots are sized on that
distance, so each trade still risks 1.5%, and `rr_N` targets are N times it.
"""
from __future__ import annotations

import os
import sys

# Pinned before `cfd_families` binds its broker profile at import, so this
# process and every spawned worker price Exness Pro.
if any(a == "--broker" or a.startswith("--broker=") for a in sys.argv[1:]):
    raise SystemExit("cfd_tt_families is priced on Exness Pro only; "
                     "drop --broker")
os.environ["CFD_BROKER"] = "exness"
#: Full power by default: every core (see `main`), and 2 GB kept back for the
#: OS instead of `cfd_families`' 4. `fit_workers` still drops workers if the
#: contexts would not fit in what is free.
os.environ.setdefault("EXNESS_MEMORY_RESERVE_GB", "2")
#: The holding skip (`cfd_families._hold_scan`): verified 2026-09-28 to give
#: byte-identical results on 192 cells (eurusd 5m, de40 15m), ~20% faster.
os.environ.setdefault("EXNESS_HOLD_SKIP", "1")

import numpy                                                     # noqa: E402

from sandbox.research import cfd_families as cf                  # noqa: E402
from sandbox.research import es_strategy_research as es          # noqa: E402

TS, O, H, L, C, V = range(6)

#: US500 and USTEC, every non-US index, the FX pairs, and BTC/ETH.
DEFAULT_SYMBOLS = (
    "us500", "ustec",
    "de40", "fr40", "stoxx50", "uk100", "aus200", "hk50", "jp225",
    "eurusd", "usdjpy", "gbpusd", "audjpy", "audusd", "eurjpy", "gbpjpy",
    "usdcad",
    "btc", "ethusd",
)
DEFAULT_BARS = "5,15,30"


# --------------------------------------------------------------------------- #
# cached series. Built once per worker context and kept on it, since every
# cell of a family reads the same arrays.
# --------------------------------------------------------------------------- #

def _bar_ema(bars, ctx, period):
    key = ("tt_ema", period)
    out = ctx.get(key)
    if out is None:
        out = es.ema([b[C] for b in bars], period)
        ctx[key] = out
    return out


def _bar_atr(bars, ctx, period):
    key = ("tt_atr", period)
    out = ctx.get(key)
    if out is None:
        out = es.average_true_range(bars, periods=period)
        ctx[key] = out
    return out


# --------------------------------------------------------------------------- #
# luxalgo
# --------------------------------------------------------------------------- #

def luxalgo_manipulation_signal(index, bars, ctx, params, _state):
    """LuxAlgo, "Market Candle Strategy" (TikTok, video 7663213906797595935).

    The video's rules. A candle trades beyond the previous candle's low, then
    closes back above it and engulfs it: price "manipulated" lower before going
    higher. Buy, stop under the sweep low, target 2R, only above the 200 EMA.
    Shorts are the mirror. They also tried a 5x ATR stop at 3R, requiring the
    prior candles to point the same way, and on 5m fading the whole signal.

    Axes:
      engulf       `range`: the close clears the previous candle's high (low);
                   `body`: it only clears the previous candle's body
      prior_bars   0 = any; N = the N candles before the setup all closed in
                   the trade's direction (the video's "all bullish" option)
      ema          bar EMA the close must be on the trade's side of; 0 = off
      stop         `sweep`: the setup candle's far extreme; `atr_K`: K x
                   ATR(14) bars
      direction    `follow` trades the pattern; `fade` takes the other side
                   (the video's 5m flip). A faded `sweep` stop is the whole
                   setup candle's range, since the near extreme sits at the
                   close and would be a stop of almost nothing.

    The previous candle must be from the same session: under RTH-only bars
    the bar before the open is yesterday's close, and a "sweep" of it is the
    overnight gap, not the pattern.
    """
    n = params["prior_bars"]
    if index < max(1, n):
        return None
    day_of = ctx["day"]
    if day_of[index - max(1, n)] != day_of[index]:
        return None
    bar, prev = bars[index], bars[index - 1]
    if cf._late(ctx, bar, params):
        return None
    if params["engulf"] == "range":
        top, bottom = prev[H], prev[L]
    else:
        top, bottom = max(prev[O], prev[C]), min(prev[O], prev[C])
    if bar[L] < prev[L] and bar[C] > top and bar[C] > bar[O]:
        side = 1
    elif bar[H] > prev[H] and bar[C] < bottom and bar[C] < bar[O]:
        side = -1
    else:
        return None
    for k in range(1, n + 1):
        before = bars[index - k]
        if side * (before[C] - before[O]) <= 0:
            return None
    if params["ema"]:
        reference = _bar_ema(bars, ctx, params["ema"])[index]
        if side * (bar[C] - reference) <= 0:
            return None
    stop = params["stop"]
    if stop == "sweep":
        if params["direction"] == "follow":
            distance = bar[C] - bar[L] if side == 1 else bar[H] - bar[C]
        else:
            distance = bar[H] - bar[L]
    else:
        atr = _bar_atr(bars, ctx, 14)[index]
        if atr is None:
            return None
        distance = float(stop[4:]) * atr
    if not distance > 0:
        return None
    return (-side if params["direction"] == "fade" else side), distance


def _arrays(bars, ctx):
    """OHLC and the day as numpy arrays, cached on the context."""
    out = ctx.get("tt_arrays")
    if out is None:
        a = numpy.asarray(bars, dtype=float)
        out = {"o": a[:, O], "h": a[:, H], "l": a[:, L], "c": a[:, C],
               "day": numpy.asarray(ctx["day"], dtype=numpy.int64),
               "minute": numpy.asarray(ctx["minute"], dtype=numpy.int64)}
        ctx["tt_arrays"] = out
    return out


def luxalgo_manipulation_fires(bars, ctx, params, start):
    """Every bar from `start` where `luxalgo_manipulation_signal` can fire,
    computed in one numpy pass instead of one Python call per bar.

    A SUPERSET, never a subset: it skips the stop-distance test (which needs
    `stop`, an axis the firing cache is shared across). That is safe because
    `backtest` calls the real signal again on every bar listed here and acts
    only on what it returns; the list only tells it which bars to skip.
    """
    a = _arrays(bars, ctx)
    o, h, lo, c, day = a["o"], a["h"], a["l"], a["c"], a["day"]
    size = len(c)
    n = params["prior_bars"]
    back = max(1, n)
    ok = numpy.zeros(size, dtype=bool)
    if size <= back:
        return []
    i = numpy.arange(back, size)
    ok[back:] = day[i - back] == day[i]
    if not ctx["daily"]:
        ok &= a["minute"] <= params["last_entry_minute"]
    ph, pl = numpy.empty(size), numpy.empty(size)
    po, pc = numpy.empty(size), numpy.empty(size)
    ph[0] = pl[0] = po[0] = pc[0] = numpy.nan
    ph[1:], pl[1:], po[1:], pc[1:] = h[:-1], lo[:-1], o[:-1], c[:-1]
    if params["engulf"] == "range":
        top, bottom = ph, pl
    else:
        top, bottom = numpy.maximum(po, pc), numpy.minimum(po, pc)
    long = (lo < pl) & (c > top) & (c > o)
    short = ~long & (h > ph) & (c < bottom) & (c < o)
    side = numpy.where(long, 1.0, numpy.where(short, -1.0, 0.0))
    body = c - o
    for k in range(1, n + 1):
        shifted = numpy.zeros(size)
        shifted[k:] = body[:-k]
        ok &= side * shifted > 0
    if params["ema"]:
        ema = numpy.asarray(_bar_ema(bars, ctx, params["ema"]), dtype=float)
        ok &= side * (c - ema) > 0
    ok &= side != 0
    ok[:start] = False
    return numpy.flatnonzero(ok).tolist()


#: Families with a vectorised firing scan.
VECTOR_FIRES = {"luxalgo_manipulation": luxalgo_manipulation_fires}
_plain_fire_table = cf._fire_table


def _fire_table(family, bars, ctx, params, signal_fn, start=0):
    """`cfd_families._fire_table`, from `VECTOR_FIRES` where a family has one,
    and straight from the event table for the creator (event) families."""
    if family in EVENT_FAMILIES:
        cut = params.get("last_entry_minute", 10 ** 6)
        minute = ctx["minute"]
        return sorted(i for i in _events(family, bars, ctx, params)
                      if i >= start and minute[i] <= cut)
    fast = VECTOR_FIRES.get(family)
    if fast is None:
        return _plain_fire_table(family, bars, ctx, params, signal_fn, start)
    key = ("tt", family, start,
           tuple(sorted((k, repr(v)) for k, v in params.items()
                        if k not in cf._FIRE_EXIT_AXES)))
    cache = ctx.setdefault("_fire_cache", {})
    hit = cache.get(key)
    if hit is None:
        if len(cache) >= cf.FIRE_CACHE_SIZE:
            cache.pop(next(iter(cache)))
        hit = cache[key] = fast(bars, ctx, params, start)
    return hit


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

#: Exits the engine understands (`exit_plan`): `rr_N` is a target N times the
#: stop distance, `trail_X` trails X daily ranges behind the best close. All of
#: them are also flattened at the session close.
TT_EXIT_MODES = ("rr_1", "rr_2", "rr_3", "trail_0.25")

#: The engine's own filters are switched off: the strategy carries its EMA
#: filter as an axis. `stop_day` is inert (every cell sets its own stop) but
#: the engine reads it.
_ENGINE = {"trend": ("none",), "vol_mode": ("none",), "stop_day": (0.2,)}

#: THE WIDE-STOP TEST (2026-09-28). `EXNESS_TT_STOP_WIDEN=1,2,4` adds a
#: `stop_widen` axis to every family: the stop distance a signal returns is
#: multiplied by it, and since lots are sized on that distance the trade still
#: risks `RISK_FRACTION` -- a wider stop is a smaller position, not more risk.
#: `rr_N` targets scale with it (they are multiples of the risk unit); `level`
#: targets are prices and do not. The hypothesis: a tight structural stop on a
#: fast market pays the late-market-order exit (no broker-side stops) on most
#: trades. Results go to their own prefix so no sealed study is overwritten.
STOP_WIDEN = tuple(float(v) for v in os.environ.get(
    "EXNESS_TT_STOP_WIDEN", "").replace(" ", "").split(",") if v) or None
if STOP_WIDEN:
    _ENGINE["stop_widen"] = STOP_WIDEN


def _widen(side, params):
    """Apply `stop_widen` to a signal's `(side, distance, target)`."""
    k = params.get("stop_widen", 1.0)
    if k == 1.0 or not isinstance(side, tuple) or side[1] is None:
        return side
    return (side[0], side[1] * k) + tuple(side[2:])

FAMILIES = {
    "luxalgo_manipulation": cf.Family(
        (lambda i, b, c, p, _st: _widen(luxalgo_manipulation_signal(i, b, c, p, None), p)),
        lambda p, g: {
            "direction": ("follow", "fade"),
            "engulf": ("range", "body"),
            "prior_bars": (0, 1, 2),
            "ema": (0, 100, 200),
            "stop": ("sweep", "atr_2", "atr_5"),
            "exit_mode": TT_EXIT_MODES,
            "last_entry_minute": g["last"],
            **_ENGINE},
        scope="intraday"),
}

# --------------------------------------------------------------------------- #
# event families: one module per creator (`tt_luxalgo`, ...)
#
# A creator module's `SPECS` maps a family name to `(compute, axes, ny_clock,
# exit_signal)`. `compute(bars, ctx, params)` returns `{bar: (side, stop,
# target)}` for one setting of the SIGNAL axes; it is cached per worker and
# the engine's signal is a dict lookup. The entry cutoff is applied here, not
# in `compute`, so the two cutoff values share one computation. `ny_clock`
# families read New York wall-clock windows and are skipped on the markets
# `cfd_families` runs on a shifted clock (the Asian cash indices).
# --------------------------------------------------------------------------- #

from sandbox.research import tt_luxalgo                          # noqa: E402
from sandbox.research import tt_quantpad                         # noqa: E402
from sandbox.research import tt_thequantbuilder                  # noqa: E402
from sandbox.research import tt_tradingdata2                     # noqa: E402

#: Every creator module, in the order its group is listed.
CREATOR_MODULES = (tt_luxalgo, tt_quantpad, tt_thequantbuilder,
                   tt_tradingdata2)

#: Axes that change the exit or the engine's own filters, never the events.
EVENT_EXIT_KEYS = frozenset(("exit_mode", "stop_day", "trend", "vol_mode",
                             "last_entry_minute", "stop_widen"))
EVENT_FAMILIES = {}
EVENT_CACHE_SIZE = 64


def _events(family, bars, ctx, params):
    key = (family, tuple(sorted((k, repr(v)) for k, v in params.items()
                                if k not in EVENT_EXIT_KEYS)))
    cache = ctx.setdefault("tt_events", {})
    hit = cache.get(key)
    if hit is None:
        if len(cache) >= EVENT_CACHE_SIZE:
            cache.pop(next(iter(cache)))
        core = {k: v for k, v in params.items() if k != "last_entry_minute"}
        hit = cache[key] = EVENT_FAMILIES[family](bars, ctx, core)
    return hit


def _event_signal(family):
    def signal(index, bars, ctx, params, _state):
        if ctx["minute"][index] > params.get("last_entry_minute", 10 ** 6):
            return None
        return _widen(_events(family, bars, ctx, params).get(index), params)
    signal.__name__ = f"{family}_signal"
    return signal


#: Markets on New York wall clock: everything but the shifted Asian indices.
NY_CLOCK_SYMBOLS = tuple(s for s in cf.CLASS if s not in cf.SHIFT_HOURS)

for _name, (_compute, _axes, _ny, _exit) in (
        item for module in CREATOR_MODULES for item in module.SPECS.items()):
    EVENT_FAMILIES[_name] = _compute
    FAMILIES[_name] = cf.Family(
        _event_signal(_name),
        (lambda p, g, _a=_axes: {**_a(g), **_ENGINE}),
        scope="intraday", symbols=NY_CLOCK_SYMBOLS if _ny else None)
    if _exit is not None:
        cf.EXIT_SIGNALS[_name] = _exit

#: One group per creator.
GROUPS = {
    "luxalgo": ("luxalgo_manipulation", *tt_luxalgo.SPECS),
    "quantpad": tuple(tt_quantpad.SPECS),
    "thequantbuilder": tuple(tt_thequantbuilder.SPECS),
    "tradingdata2": tuple(tt_tradingdata2.SPECS),
}

for _group, _members in GROUPS.items():
    for _name in _members:
        FAMILIES[_name].group = _group
_covered = [name for members in GROUPS.values() for name in members]
assert sorted(_covered) == sorted(FAMILIES), "GROUPS must partition FAMILIES"


def lean_context(symbol, phase, bar=None, only=None, full_bars=None):
    """`cfd_families.context` cut to what the engine and these families read.

    The full context builds ~50 per-bar arrays for 130 families -- 1.8 GB a
    worker on FX at 5m, which held a 16-core machine to 2 workers. These
    families read bars, `day` and their own cached EMA/ATR; the engine reads
    the keys below (the same set `BAR_WAVE_KEEPS` names). Every value is built
    by the same calls `context` uses, so a backtest scores identically.
    """
    spec, p = cf.INSTRUMENTS[symbol], cf.periods(symbol, bar)
    daily = cf.is_daily(bar)
    full = cf.all_bars(symbol, phase, bar) if full_bars is None else full_bars
    bars = (full if daily or cf.FULL_DAY
            else [b for b in full if cf.in_session(symbol, b[TS])])
    outside, _prior = cf.anchors(full, symbol, daily)
    # The whole series, session or not, as five arrays: the creator families
    # read the overnight range and New York clock windows (the 08:00 candle,
    # the Asian and London ranges) from it. ~24 MB at 5m on a 24h market.
    fa = numpy.asarray(full, dtype=float) if full else numpy.zeros((0, 6))
    tt_full = {"ts": fa[:, TS].astype(numpy.int64), "o": fa[:, O].copy(),
               "h": fa[:, H].copy(), "l": fa[:, L].copy(), "c": fa[:, C].copy()}
    del full, _prior, fa
    annual = spec["calendar"] * p["session"]
    short = cf.trailing_volatility(bars, p["vol"], annual)
    long = cf.trailing_volatility(bars, p["long_vol"], annual)
    stamps = [b[TS] for b in bars]
    session_index, session_last = cf.session_ordinals(
        bars, spec["session"][1], daily)
    ctx = {
        "in_rth": None,
        "atr": es.average_true_range(bars, periods=p["atr"]),
        "risk": cf.daily_risk(bars),
        "ema": {},
        "overnight": outside, "volatility": short,
        "calm": [None if s is None or lv is None or lv <= 0 else s < lv
                 for s, lv in zip(short, long)],
        "day": [t // 86_400 for t in stamps],
        "minute": [t % 86_400 // 60 for t in stamps], "ts": stamps,
        "session_index": session_index, "session_last": session_last,
        "cfg": spec, "periods": p, "symbol": symbol, "daily": daily,
        "bar_minutes": bar or cf.BAR_MINUTES, "tt_full": tt_full,
    }
    lo = cf.is_start(symbol)
    sample = [v for v, b in zip(short, bars)
              if v is not None and lo <= b[TS] < cf.IS_END]
    ctx["vol_target"] = cf.statistics.median(sample) if sample else 0.3
    del long
    cf.gc.collect()
    return bars, cf.compact(ctx)


#: What a spawned worker costs before its context: interpreter, imports and
#: fill maps. Measured 2026-09-28 on FX 5m: a worker's working set was ~557 MB
#: against a 229 MB context, so ~330 MB, rounded up.
WORKER_OVERHEAD = 350e6


def fit_workers(symbol, phase, bar, only, workers):
    """`cfd_families.fit_workers`, costing a worker at what it really holds.

    The original charges every worker the PARENT's whole working set, which
    carries every earlier symbol's leftovers: it read 1.9 GB a worker on
    gbpusd 5m while the workers themselves sat at 560 MB, so the pool ran 3
    wide with 5.7 GB free. Here a worker costs its context (measured as the
    growth while one is alive in the parent) plus `WORKER_OVERHEAD`.
    """
    free, before = cf.available_memory(), cf.process_rss()
    if free is None or before is None:
        return workers, None
    held = cf.context(symbol, phase, bar, only)
    footprint = max(cf.process_rss() - before, 0) + WORKER_OVERHEAD
    del held
    cf.gc.collect()
    room = max(1, int((free - cf.MEMORY_RESERVE) / footprint))
    return min(workers, room), footprint


def install():
    """Point `cfd_families` at this registry. Runs at import, so a spawned
    worker -- which re-imports this module as `__mp_main__` -- gets it too."""
    cf.FAMILIES = FAMILIES
    cf.GROUPS = GROUPS
    cf.ALIASES = {}
    cf.SIGNALS = {name: entry.signal for name, entry in FAMILIES.items()}
    cf._PURE = None
    cf.CATEGORICAL = tuple(dict.fromkeys(
        tuple(cf.CATEGORICAL) + ("engulf", "stop")
        + tuple(c for module in CREATOR_MODULES for c in module.CATEGORICAL)))
    # `stop` moves the distance, never whether a bar fires, so cells that
    # differ only in it share one firing table.
    cf._FIRE_EXIT_AXES = cf._FIRE_EXIT_AXES | {"stop", "stop_widen"}
    # A protocol change gets its own files: the wide-stop axis and a moved
    # in-sample start (`EXNESS_IS_FIRST_YEAR`, see `cf.install_fills`).
    first = os.environ.get("EXNESS_IS_FIRST_YEAR", "").replace(":", "").replace(",", "_")
    cf.RESULT_PREFIX = ("exness_tt_families"
                        + ("_widestop" if STOP_WIDEN else "")
                        + (f"_is{first}" if first else ""))
    cf.context = lean_context
    cf.fit_workers = fit_workers
    cf._fire_table = _fire_table
    cf.UNIVERSE = DEFAULT_SYMBOLS
    # 1-minute bars: allowed here only (the creator families are intraday).
    cf.BAR_CHOICES = tuple(sorted(set(cf.BAR_CHOICES) | {1}))


install()


# --------------------------------------------------------------------------- #
# the job scheduler: many symbols at once
#
# `cfd_families` runs one (symbol, timeframe) at a time with a worker pool
# under it. Here a job is small -- 864 cells -- so most of its wall time is the
# single-threaded prep (bars, fill maps, context) and the pool sat idle through
# it: the CPU graph was spikes over a 22% floor. So the parallelism is moved
# up a level. Every (symbol, timeframe) is its own process, running
# `cfd_families.select`/`validate`/`why` unchanged but with the cells scored
# in-process, and as many run at once as there are cores and memory for.
# One job's prep overlaps another's search, and every core stays busy.
# --------------------------------------------------------------------------- #

#: Opening guesses for a job's peak working set, by bar size. Replaced by the
#: largest peak actually observed at that bar size (x1.15) once one finishes.
JOB_MEMORY_GUESS = {1: 4.2e9, 5: 1.2e9, 15: 0.7e9, 30: 0.5e9}
LOGS = os.path.join(cf.RESULTS, "tt_logs")
#: How long a new job counts at its full estimate before its own memory is
#: trusted to show up in the machine's free-memory reading.
WARMUP_SECONDS = 240


def _peak_rss():
    """This process's peak working set in bytes, or 0 off Windows."""
    try:
        import ctypes                                   # noqa: PLC0415
        import ctypes.wintypes as wt                    # noqa: PLC0415

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        *[(f"f{i}", ctypes.c_size_t) for i in range(6)]]

        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                   ctypes.byref(counters), counters.cb)
        return int(counters.PeakWorkingSetSize)
    except Exception:                                   # noqa: BLE001
        return 0


class _InlinePool:
    """Stands in for `multiprocessing.Pool` inside a job: the initializer runs
    here and the cells are scored here, one after another."""

    def __init__(self, processes=None, initializer=None, initargs=()):
        if initializer is not None:
            initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def imap_unordered(self, fn, jobs, chunksize=1):
        return map(fn, jobs)


def _job(command, symbol, bar, only, spread_bp, stale, log_path, queue):
    """One (symbol, timeframe) in its own process; output goes to its log."""
    import traceback                                    # noqa: PLC0415
    import types                                        # noqa: PLC0415
    status = "ok"
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            cf.multiprocessing = types.SimpleNamespace(Pool=_InlinePool)
            cf.fit_workers = lambda *a, **k: (1, None)
            cf.BAR_MINUTES = bar
            cf.resolve(symbol, allow_stale=stale)
            if command == "select":
                cf.select(symbol, 1, spread_bp, bar, only)
            elif command == "validate":
                cf.validate(symbol, spread_bp, bar, only)
            else:
                cf.why(symbol, 1, spread_bp, only, bar)
        except BaseException as error:                  # noqa: BLE001
            traceback.print_exc()
            status = f"{type(error).__name__}: {error}"
    queue.put((symbol, bar, status, _peak_rss()))


#: Lines of a job's log worth echoing when it finishes.
_ECHO = ("luxalgo", "OOS", "flip", "no requested family")


def _sealed_complete(symbol, bar, only):
    """True when this job's seal already covers every family that runs here."""
    import json                                         # noqa: PLC0415
    path = cf.output_path(symbol, bar, only)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            have = set(json.load(handle).get("families", {}))
    except (OSError, ValueError):
        return False
    return set(cf.axes(symbol, bar, only)) <= have


def _oos_winners(symbol, bar, only=None):
    """Families whose sealed winner made money out of sample here, read from
    the seal the same `--groups`/`--families` scope wrote (a group's seal is
    `..._<group>.json`; the untagged file is the full registry's)."""
    import json                                         # noqa: PLC0415
    path = cf.output_path(symbol, bar, only)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        d = json.load(handle)
    val = d.get("validation", {})
    return {f for f, w in d.get("families", {}).items()
            if w and val.get(f, {}).get("oos", {}).get("return_pct", 0) > 0}


def run_jobs(command, symbols, bars, only, spread_bp, stale, slots,
             missing=False, winners=False):
    """Every (symbol, timeframe) of `command`, as many at once as fit."""
    import multiprocessing                              # noqa: PLC0415
    import queue as queues                              # noqa: PLC0415
    import time                                         # noqa: PLC0415

    jobs = []
    per_job = {}
    for bar in bars:
        for symbol in symbols:
            spec = cf.INSTRUMENTS[symbol]
            if spec["source"] == "30m" and bar < 30:
                print(f"{symbol}: skipped at {bar}m -- {spec['table']} is "
                      "native 30m")
                continue
            if command == "validate" and not os.path.exists(
                    cf.output_path(symbol, bar, only)):
                print(f"{symbol} {bar}m: no seal to validate -- run select")
                continue
            if missing and command == "select" and _sealed_complete(symbol, bar, only):
                continue
            job_only = only
            if winners:
                job_only = _oos_winners(symbol, bar, only)
                if not job_only:
                    continue
                per_job[(symbol, bar)] = job_only
            opened, closed = spec["session"]
            jobs.append(((closed - opened) % 1440 / bar, symbol, bar))
    # Biggest first, so the longest job never starts last and becomes the tail.
    jobs.sort(reverse=True)
    pending = [(symbol, bar) for _work, symbol, bar in jobs]
    total = len(pending)
    os.makedirs(LOGS, exist_ok=True)
    budget = (cf.available_memory() or 8e9) - cf.MEMORY_RESERVE
    peaks = {}
    spawn = multiprocessing.get_context("spawn")
    results = spawn.Queue()
    running = {}
    failures = []
    done = 0
    began = time.time()
    print(f"{command}: {total} jobs, up to {slots} at once, "
          f"{budget / 1e9:.1f} GB to spend", flush=True)

    def need(bar):
        return peaks.get(bar, JOB_MEMORY_GUESS.get(bar, 1.0e9))

    while pending or running:
        # READ THE MACHINE, NOT A SNAPSHOT. Free memory is re-read before every
        # launch; jobs younger than `WARMUP_SECONDS` have not reached their peak
        # yet, so their full cost is still held back from what is free. (A fixed
        # startup budget ran ONE 1m job with 7 GB free -- it had been taken while
        # other processes were exiting.)
        now = time.time()
        young = sum(entry[1] for entry in running.values()
                    if now - entry[2] < WARMUP_SECONDS)
        free = cf.available_memory()
        spare = ((free - cf.MEMORY_RESERVE - young) if free is not None
                 else budget - sum(entry[1] for entry in running.values()))
        for key in list(pending):
            if len(running) >= slots:
                break
            cost = need(key[1])
            # A job always runs if nothing else is -- a machine too small for
            # one job should run slowly, not stall.
            if running and cost > spare:
                continue
            spare -= cost
            symbol, bar = key
            log_path = os.path.join(LOGS, f"{command}_{symbol}_{bar}m.log")
            process = spawn.Process(
                target=_job, args=(command, symbol, bar,
                                   per_job.get((symbol, bar), only), spread_bp,
                                   stale, log_path, results))
            process.start()
            running[key] = (process, cost, time.time(), log_path)
            pending.remove(key)
        try:
            symbol, bar, status, peak = results.get(timeout=1.0)
        except queues.Empty:
            # A job killed from outside never reports; reap it here.
            for key, (process, _cost, started, log_path) in list(running.items()):
                if process.exitcode is not None and not process.is_alive():
                    running.pop(key)
                    done += 1
                    failures.append(f"{key[0]} {key[1]}m: exited "
                                    f"{process.exitcode} (see {log_path})")
                    print(f"[{done}/{total}] {key[0]} {key[1]}m DIED "
                          f"exit {process.exitcode}", flush=True)
            continue
        process, _cost, started, log_path = running.pop((symbol, bar))
        process.join()
        done += 1
        if peak:
            peaks[bar] = max(peaks.get(bar, 0), 1.15 * peak)
        print(f"[{done}/{total}] {symbol} {bar}m  {time.time() - started:.0f}s"
              f"  peak {peak / 1e9:.2f} GB", flush=True)
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if any(tag in line for tag in _ECHO):
                    print("    " + line.rstrip(), flush=True)
        if status != "ok":
            failures.append(f"{symbol} {bar}m: {status} (see {log_path})")
            print(f"    FAILED {status}", flush=True)
    print(f"{command}: {total} jobs in {time.time() - began:.0f}s", flush=True)
    if failures:
        print(f"\n{len(failures)} job(s) failed:")
        for line in failures:
            print(f"  {line}")


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("select", "validate", "why"):
        return _scheduled(argv)
    _delegate(argv)


def _scheduled(argv):
    import argparse                                     # noqa: PLC0415
    parser = argparse.ArgumentParser(
        description="cfd_tt_families select/validate/why, many symbols at once")
    parser.add_argument("command", choices=("select", "validate", "why"))
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--bar-minutes", default=DEFAULT_BARS)
    parser.add_argument("--families", default=None)
    parser.add_argument("--groups", default=None)
    parser.add_argument("--spread-bp", type=float, default=None)
    parser.add_argument("--stale-spreads", action="store_true")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                        help="jobs at once (default: every core)")
    parser.add_argument("--window", default="rth")
    parser.add_argument("--winners", action="store_true",
                        help="why: per job, test only the families whose "
                             "sealed winner made money out of sample")
    parser.add_argument("--missing", action="store_true",
                        help="select: skip jobs whose seal already holds every "
                             "family that runs there")
    args = parser.parse_args(argv)
    _require_rth(args.window)
    only = cf.expand_families(args.families, args.groups)
    bars = [int(v) for v in args.bar_minutes.replace(" ", "").split(",") if v]
    for bar in bars:
        if bar not in cf.BAR_CHOICES or cf.is_daily(bar):
            raise SystemExit(f"--bar-minutes {bar}: intraday only, one of "
                             f"{[b for b in cf.BAR_CHOICES if b < 1440]}")
    symbols, refused = [], []
    for symbol in cf.expand(args.symbols):
        try:
            cf.resolve(symbol, allow_stale=args.stale_spreads)
        except SystemExit as error:
            refused.append(str(error))
            print(f"SKIPPED {error}", flush=True)
            continue
        symbols.append(symbol)
    if not symbols:
        raise SystemExit("none of the requested symbols can be studied")
    run_jobs(args.command, symbols, bars, only, args.spread_bp,
             args.stale_spreads, max(1, args.workers), missing=args.missing,
             winners=args.winners)
    if refused:
        print(f"\n{len(refused)} symbol(s) refused before the sweep:")
        for line in refused:
            print(f"  {line}")


def _require_rth(window=None):
    if (cf.expand_windows(window or cf._argv_option("--window")
                          or os.environ.get("EXNESS_WINDOW")) != ["rth"]
            or cf.FULL_DAY or not cf.SESSION_ONLY):
        raise SystemExit("cfd_tt_families runs RTH only: unset EXNESS_WINDOW "
                         "and EXNESS_FULL_DAY")


def _delegate(argv):
    """Every other command (`budget`, `families`, `spreads`, ...) is
    `cfd_families`' own, with this module's defaults filled in."""
    if not any(a == "--symbols" or a.startswith("--symbols=") for a in argv):
        argv += ["--symbols", ",".join(DEFAULT_SYMBOLS)]
    if not any(a == "--bar-minutes" or a.startswith("--bar-minutes=")
               for a in argv):
        argv += ["--bar-minutes", DEFAULT_BARS]
    if not any(a == "--workers" or a.startswith("--workers=") for a in argv):
        argv += ["--workers", str(os.cpu_count() or 1)]
    sys.argv = [sys.argv[0], *argv]
    _require_rth()
    cf.main()


if __name__ == "__main__":
    main()
