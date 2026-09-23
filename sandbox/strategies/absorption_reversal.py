"""Replica of `live_trade/src/strategies/idk/absorption_reversal.rs`.

Fades exhausted aggression: when a window of heavy one-sided `trade_delta` fails
to move price and the opposite book keeps refilling, the passive side is
absorbing and price reverts once the aggressors are spent.

Two things here are worth knowing before touching the parameters.

`repl_score` is built from *window sums* of `bid_replenishment` and
`ask_replenishment`, per the strategy spec. Summing 10 book levels over W
minutes before forming the ratio concentrates it hard around zero — measured on
this data the 1st and 99th percentiles are about -0.23 and +0.23, so the spec's
suggested 0.15-0.40 band starts near the 95th percentile. The grid below is
calibrated on the observed distribution instead, and `repl_pct` offers a
distribution-relative alternative to the raw cut.

`aggression` is z-scored against the same minute-of-day over the previous 20
sessions, because a raw delta threshold does not survive the volatility regimes
in a seven-month sample. That warm-up costs the first 20 sessions of whatever
range is loaded, so signals are always generated over the full history and then
filtered to the evaluation window by `from_date`/`to_date`.
"""
from sandbox import data
from sandbox import metrics
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960          # 09:30 .. 16:00
FIRST_ENTRY, LAST_ENTRY = 585, 915      # 09:45 .. 15:15
EXIT_MIN = 945                          # 15:45 session flatten
NORM_SESSIONS = 20
#: weekday index 0 = Monday, matching (epoch day + 3) % 7
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ATR_MINUTES = 5
ATR_SAMPLES = 10
MAX_WINDOW = 3

#: Rust's default `OrderFlowFeatures` for a bar with no matching feature row
NO_FEATURES = {"trade_delta": 0.0, "price_change": 0.0, "bid_replenishment": 0.0,
               "ask_replenishment": 0.0, "spread": 0.0, "book_valid": False}


@register
class AbsorptionReversal(Strategy):
    name = "Absorption Reversal"
    bars = "level_two"
    #: `risk` and `leverage` mirror ENTRY_RISK_FRACTION and ENTRY_LEVERAGE in
    #: the Rust file -- neither is the `Execution` default. Both must move with
    #: the Rust constants or `validation.py` compares differently-sized positions
    #: and reads the gap as PnL drift. See `hourly_delta_reversal.py`.
    execution = Execution(session_end_min=EXIT_MIN, risk=0.01, leverage=2.0)

    defaults = {
        "window": 3,
        "aggression_z": 2.0,
        "move_fraction": 0.55,
        "repl_threshold": 0.15,
        "repl_pct": 0,          # 0 = use the raw threshold above
        "target": 40,
        "stop": 8,
        "max_stop": 12,
        "stop_buffer": 5.0,
        "time_stop": 25,
        "max_spread": 1.5,
        "max_entries": 4,
        "require_repl": True,
        # Entry window, minutes from midnight. The default is the spec's
        # 09:45-15:15; narrowing it is the time-of-day gate.
        "entry_from": FIRST_ENTRY,
        "entry_to": LAST_ENTRY,
        # 0 = fixed point brackets above. Non-zero expresses the bracket as a
        # multiple of the 5-minute ATR, so it scales with the regime instead of
        # meaning something different in a quiet month than a violent one.
        "atr_stop_mult": 0,
        "atr_target_mult": 0,
        #: weekday abbreviations to skip, e.g. "Fri" or "Mon,Fri"
        "skip_days": "",
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "window": [1, 2, 3],
        "aggression_z": [1.0, 1.5, 2.0],
        "move_fraction": [0.25, 0.40, 0.55],
        "repl_threshold": [0.05, 0.10, 0.15],
        "target": [15, 25, 40],
        "stop": [8, 12, 20],
        "time_stop": [10, 15, 25],
    }

    def valid(self, params):
        # A target inside the stop is a losing bracket by construction, and a
        # stop wider than the cap can never bind.
        return params["target"] >= params["stop"] and params["stop"] <= params["max_stop"]

    def context(self):
        """Per-bar statistics for every window length, computed once.

        Returns `{window: [row or None per bar]}` where a row is
        `(z, net_move, atr, repl, tradable, window_low, window_high)`. Building
        this once keeps `signals()` a cheap filter, which is what makes a
        2000-cell sweep finish.
        """
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        return {w: self._statistics(bars, features, w) for w in range(1, MAX_WINDOW + 1)}

    @staticmethod
    def _statistics(bars, features, window):
        rows = [None] * len(bars)
        rings = {}          # minute-of-day slot -> prior sessions' aggression
        recent = []         # [(high, low, delta, move, bid_repl, ask_repl)]
        atr_samples = []
        previous_minute = None
        day = None

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if ts // 86_400 != day:
                day, recent, atr_samples, previous_minute = ts // 86_400, [], [], None
            if not (OPEN_MIN <= minute < CLOSE_MIN):
                continue
            # A minute with no feature row still enters the window, zero-filled:
            # the Rust bar carries a default `OrderFlowFeatures`, which is all
            # zeros with `book_valid` false. It cannot signal, but it does shift
            # the window and the ATR.
            feature = features.get(ts, NO_FEATURES)

            if previous_minute is not None and previous_minute + 1 != minute:
                recent, atr_samples = [], []
            previous_minute = minute
            recent.append((bar[H], bar[L], feature["trade_delta"], feature["price_change"],
                           feature["bid_replenishment"], feature["ask_replenishment"]))
            del recent[:-max(window, ATR_MINUTES)]
            if len(recent) >= ATR_MINUTES:
                block = recent[-ATR_MINUTES:]
                atr_samples.append(max(m[0] for m in block) - min(m[1] for m in block))
                del atr_samples[:-ATR_SAMPLES]

            if len(recent) < window or len(atr_samples) < ATR_SAMPLES:
                continue
            span = recent[-window:]
            aggression = sum(m[2] for m in span)
            bid = sum(m[4] for m in span)
            ask = sum(m[5] for m in span)
            total = bid + ask
            slot = minute - OPEN_MIN
            ring = rings.setdefault(slot, [])

            # Rows cover the whole session; `signals()` applies the entry window,
            # so the window itself can be swept.
            if len(ring) >= NORM_SESSIONS:
                mean = sum(ring) / len(ring)
                sd = (sum((x - mean) ** 2 for x in ring) / len(ring)) ** 0.5
                if sd > 0:
                    rows[i] = (
                        (aggression - mean) / sd,
                        sum(m[3] for m in span),
                        sum(atr_samples) / len(atr_samples),
                        (bid - ask) / total if total > 0 else 0.0,
                        feature["spread"] if feature["book_valid"] else None,
                        min(m[1] for m in span),
                        max(m[0] for m in span),
                    )
            # recorded last, so a bar never enters its own z-score
            ring.append(aggression)
            del ring[:-NORM_SESSIONS]
        return rows

    def signals(self, bars, context, group, params):
        rows = context[params["window"]]
        z_cut = params["aggression_z"]
        move_fraction = params["move_fraction"]
        stop, max_stop, buffer = params["stop"], params["max_stop"], params["stop_buffer"]
        target, time_stop = params["target"], params["time_stop"]
        max_spread, max_entries = params["max_spread"], params["max_entries"]
        require_repl = params["require_repl"]
        entry_from, entry_to = params["entry_from"], params["entry_to"]
        atr_stop, atr_target = params["atr_stop_mult"], params["atr_target_mult"]
        skip_days = {DAYS.index(d.strip()) for d in params["skip_days"].split(",")
                     if d.strip() in DAYS}
        repl_cut = self._repl_cut(rows, params)
        # Signals are always generated over the whole history so the 20-session
        # warm-up comes from bars before the window being evaluated.
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = metrics.split_ts(params["to_date"]) + 86_400 if params["to_date"] else None

        out = []
        entries_today = 0
        day = None
        for i, row in enumerate(rows):
            if row is None:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue        # fills happen on the contiguous next minute only
            ts = bars[i][TS]
            if not entry_from <= (ts % 86_400) // 60 <= entry_to:
                continue
            if skip_days and (ts // 86_400 + 3) % 7 in skip_days:
                continue
            if ts // 86_400 != day:
                day, entries_today = ts // 86_400, 0
            if entries_today >= max_entries:
                continue
            z, net_move, atr, repl, spread, low, high = row
            if spread is None or spread > max_spread:
                continue
            if abs(net_move) >= move_fraction * atr:
                continue
            if z <= -z_cut:
                side = LONG
            elif z >= z_cut:
                side = SHORT
            else:
                continue
            if require_repl and (repl < repl_cut if side == LONG else repl > -repl_cut):
                continue
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue

            entry = nxt[O]
            # ATR-scaled brackets when enabled, otherwise the fixed points.
            near = atr * atr_stop if atr_stop else stop
            far = atr * atr_target if atr_target else target
            if side == LONG:
                stop_price = max(min(low - buffer, entry - near), entry - max_stop)
            else:
                stop_price = min(max(high + buffer, entry + near), entry + max_stop)
            entries_today += 1
            out.append(Signal(i + 1, side, abs(entry - stop_price), far, time_stop))
        return out

    @staticmethod
    def _repl_cut(rows, params):
        """The `|repl_score|` cut, either raw or as a percentile of the data."""
        percentile = params.get("repl_pct") or 0
        if not percentile:
            return params["repl_threshold"]
        values = sorted(abs(row[3]) for row in rows if row is not None)
        if not values:
            return params["repl_threshold"]
        return values[min(len(values) - 1, int(len(values) * percentile / 100))]
