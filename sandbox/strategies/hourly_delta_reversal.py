"""Replica of `live_trade/src/strategies/idk/hourly_delta_reversal.rs`.

Fades a completed hourly candle whose aggressor delta disagrees with its body:
buy the next hour's open after a red candle with positive delta, sell after a
green candle with negative delta. Shorts are additionally gated by an N-session
SMA (don't short into an uptrend), and both sides by the book-state gate below.

The book-state gate is strategy 5 from `L2_STRATEGY_SPECS.md`. It reads the
minute *before* the entry bar — every snapshot column is an end-of-minute value
and entries fill at a bar's open, so reading the entry bar would be lookahead —
and refuses the entry when that minute's spread was wider than `max_spread`.

Two behaviours worth keeping in step with the Rust file. The gate **fails open**:
a minute with no feature row, or one whose book is invalid, is not evidence of a
bad book, and treating it as one turns the gate into a kill switch (the feature
table ends 2026-07-16 while traded bars run to 07-23). And it is an *absolute*
cut rather than the trailing percentile the spec suggests, because minute spread
is a coarse discrete variable — 0.50/0.75/1.00 cover 91% of minutes — so a fixed
percentile lands on a different tick from one regime to the next. `spread_pct`
keeps the percentile form available for re-checking that finding.

The spec's other three gates (imbalance agreement, absorption veto, activity
floor) were each measured here one at a time and are all harmful; see the Rust
file's header for the table.
"""
import bisect
from dataclasses import replace

from sandbox import data
from sandbox.data import C, D, DE, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960   # 09:30 .. 16:00
THURSDAY = 3

#: sessions of trailing history behind the optional percentile form of the gate
PERCENTILE_SESSIONS = 20
RTH_MINUTES = 390
PERCENTILE_WINDOW = PERCENTILE_SESSIONS * RTH_MINUTES
#: percentile cuts precomputed per bar, so `spread_pct` can be swept without
#: rebuilding the trailing window
SPREAD_PERCENTILES = (0.50, 0.60, 0.70, 0.80, 0.90)


def _percentile(sorted_values, fraction):
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return None
    rank = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[rank]


def book_gate(bars, features):
    """`gate[i]` = the reading the entry at bar `i` is judged on, or None.

    A reading is `(spread, {percentile: cut})` taken from bar `i - 1`; None means
    no usable reading, which the gate treats as permission.
    """
    gate = [None] * len(bars)
    window = []       # trailing spreads, insertion-sorted
    history = []      # the same spreads in bar order
    for i, bar in enumerate(bars):
        if i:
            previous = features.get(bars[i - 1][TS])
            if previous is not None and previous["book_valid"]:
                gate[i] = (previous["spread"],
                           {p: _percentile(window, p) for p in SPREAD_PERCENTILES})
        row = features.get(bar[TS])
        if row is None or not row["book_valid"]:
            continue
        bisect.insort(window, row["spread"])
        history.append(row["spread"])
        if len(history) > PERCENTILE_WINDOW:
            window.pop(bisect.bisect_left(window, history.pop(0)))
    return gate


@register
class HourlyDeltaReversal(Strategy):
    #: The historical fixed-point form. It is no longer what the Rust file
    #: compiles -- `HourlyDeltaReversalVol` below carries that name, and so is
    #: what `validation.py` checks -- but it is kept registered under its own name
    #: as the do-nothing control every later result is measured against.
    name = "Hourly Delta Reversal (fixed)"
    bars = "level_two"
    #: `risk` mirrors ENTRY_RISK_FRACTION in the Rust file. It is not the
    #: `Execution` default (0.005) -- the three shared-account strategies were
    #: raised to 0.01 on the operator's call, and a stale default here makes
    #: `validation.py` compare half-size replica positions against full-size
    #: engine ones and read the gap as PnL drift.
    execution = Execution(session_end_min=CLOSE_MIN, risk=0.01)

    defaults = {
        "buy_delta": 50, "buy_stop": 50, "buy_target": 50,
        "sell_delta": 150, "sell_stop": 60, "sell_target": 75,
        "short_trend_days": 35,
        "skip_thursday": True,
        #: skip entries whose preceding minute's spread exceeded this, in points
        "max_spread": 1.5,
        #: 0 = use the absolute cut above; non-zero swaps in a trailing
        #: percentile of the spread distribution instead
        "spread_pct": 0,
    }

    grid = {
        "buy_delta": [0, 25, 50, 75, 100, 150],
        "buy_stop": [30, 40, 50, 60],
        "buy_target": [40, 50, 60, 75, 100],
        "sell_delta": [75, 100, 150, 200, 300],
        "sell_stop": [30, 40, 50, 60],
        "sell_target": [40, 50, 60, 75, 100],
        "short_trend_days": [0, 30, 35, 45, 60],
    }

    def groups(self):
        # The two sides share no parameters, so each is generated once per
        # combination of its own axes and then joined.
        return {
            "long": ["buy_delta", "buy_stop", "buy_target"],
            "short": ["sell_delta", "sell_stop", "sell_target", "short_trend_days"],
        }

    def valid(self, params):
        # A target tighter than its stop is a losing bracket by construction.
        return (params["buy_target"] >= params["buy_stop"]
                and params["sell_target"] >= params["sell_stop"])

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        return data.load_session_closes(self.symbol), book_gate(bars, features)

    @staticmethod
    def expand(params):
        """Resolve the tied `stop`/`rr` axes into the four bracket parameters.

        `HourlyDeltaReversalTied` searches one stop and one reward:risk ratio
        shared by both sides instead of four independent bracket axes; when
        neither is present this is the identity, so the six-axis form is
        unaffected.
        """
        if params.get("stop") is None:
            return params
        stop, rr = params["stop"], params["rr"]
        return {**params, "buy_stop": stop, "sell_stop": stop,
                "buy_target": round(stop * rr), "sell_target": round(stop * rr)}

    @staticmethod
    def book_allows(reading, params):
        """Whether the book state permits an entry. No reading means yes."""
        if reading is None:
            return True
        spread, cuts = reading
        percentile = params["spread_pct"]
        cut = cuts[percentile] if percentile else params["max_spread"]
        return cut is None or spread <= cut

    def signals(self, bars, context, group, params):
        session_closes, gate = context
        params = self.expand(params)
        long_side = group == "long"
        if long_side:
            delta, stop, target = (params["buy_delta"], params["buy_stop"],
                                   params["buy_target"])
            allowed = None
        else:
            delta, stop, target = (params["sell_delta"], params["sell_stop"],
                                   params["sell_target"])
            allowed = data.sma_gate(session_closes, params["short_trend_days"])
        skip_thursday = params["skip_thursday"]

        out = []
        hour = None
        hour_open = hour_close = hour_delta = 0.0
        depth_events = 0

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            current_hour = ts // 3_600
            day = ts // 86_400

            if hour == current_hour:
                hour_close = bar[C]
                hour_delta += bar[D]
                depth_events += bar[DE]
                continue

            # A new hour: the previous one is complete and may fire.
            fires = (hour is not None and hour + 1 == current_hour
                     and OPEN_MIN <= minute < CLOSE_MIN
                     and depth_events != 0
                     and not (skip_thursday and (day + 3) % 7 == THURSDAY))
            if fires and self.book_allows(gate[i], params):
                if long_side:
                    if hour_close < hour_open and hour_delta >= delta:
                        out.append(Signal(i, LONG, stop, target))
                elif (hour_close > hour_open and hour_delta <= -delta
                        and allowed(day, bar[O])):
                    out.append(Signal(i, SHORT, stop, target))

            hour = current_hour
            hour_open = bar[O]
            hour_close = bar[C]
            hour_delta = bar[D]
            depth_events = bar[DE]

        return out


@register
class HourlyDeltaReversalTied(HourlyDeltaReversal):
    """The same strategy, searched over three tied axes instead of seven.

    Seven bracket/threshold axes over 43 350 cells is more questions than 18
    months of monthly samples can answer, and OPTIMIZATION_PLAN.md Stage 3 asks
    for <=3, spaced wide. What is tied and what is fixed, with the reason from
    the `sensitivity.py` scan over the full sample:

      * `stop` is **shared** by both sides. The compiled 50/60 split has no
        stated rationale, and the per-side curves do not supply one: `buy_stop`
        prefers 20-40 mildly while `sell_stop` is jagged (30 -> 131, 40 -> 88,
        50 -> 111, 60 -> 115) with no plateau anywhere. A jagged axis is noise,
        and noise is not a reason for two parameters.
      * `rr` is the target expressed as a multiple of that stop, also shared.
        Both sides want a target above their stop -- longs peak at 60-75 against
        a 50 stop, shorts at 75-125 against 60 -- i.e. the same ~1.5 ratio
        twice. One ratio states that once.
      * `sell_delta` stays free. It is the only entry threshold with a real
        monotone response (150 -> 115, 200 -> 122, 300 -> 134, with worst
        quarter improving -68 -> -47 the whole way), which is a claim about the
        signal rather than about the geometry, so it deserves its own axis.

    Fixed, and why:

      * `buy_delta` = 50 -- flat from 0 to 75 (109/111/115/111), so the axis
        carries no information and the compiled value is kept.
      * `short_trend_days` = 35 -- structural, not tuned. Removing it entirely
        costs two thirds of the PnL (115 -> 33, pf 1.29 -> 1.05) and any N in
        30-45 is within noise of any other. Re-validated over the full 18
        months here, as Stage 5 requires.
      * `skip_thursday` = True, `max_spread` = 1.5, `spread_pct` = 0 (absolute
        cut) -- all three re-confirmed on the full sample by the same scan.
      * risk fraction = 0.005. Leverage, not edge: it scales PnL and drawdown
        together (0.0075 gives +170/-109 against +115/-73) and saturates once
        the margin cap binds. Not something a consistency search should pick.
    """
    name = "Hourly Delta Reversal (tied)"

    defaults = {**HourlyDeltaReversal.defaults, "stop": 50, "rr": 1.5,
                "sell_delta": 300}

    grid = {
        "stop": [30, 40, 50, 60, 75],
        "rr": [1.0, 1.25, 1.5, 2.0],
        "sell_delta": [75, 150, 225, 300],
    }

    def groups(self):
        # Both sides read `stop` and `rr`, so the two-group decomposition would
        # resolve 100 bar walks where one joint group resolves 80 -- and a joint
        # group cannot express "these two groups must agree on `stop`" anyway,
        # since merging their combos would silently accept mismatched pairs.
        return {"all": sorted(self.grid)}

    def signals(self, bars, context, group, params):
        # The base class picks a side from the group name, so a single group has
        # to ask for both explicitly.
        return (super().signals(bars, context, "long", params)
                + super().signals(bars, context, "short", params))

    def valid(self, params):
        return True


#: trading days a year, for turning VIX's annualised percentage into a move
TRADING_DAYS = 252


@register
class HourlyDeltaReversalVol(HourlyDeltaReversalTied):
    """Brackets scaled by volatility instead of fixed in points.

    Every configuration searched so far risks a *constant* number of points, so
    what it actually risks relative to the day's range drifts with the regime.
    `regime.py` measures that drift and it is not harmless -- bucketing the
    compiled parameters' 197 trades by their 50-point stop as a fraction of a
    trailing 20-session ATR gives profit factors of 3.33 / 1.59 / 1.47 / 1.03 as
    that fraction falls from 0.2-0.3 to under 0.1. The edge does not survive a
    stop that the day's range swallows.

    That reading is observational -- a low stop/ATR is also just a violent
    regime -- so this class is the intervention that tests it: hold the fraction
    fixed and let the point distance move. `stop = k * unit`, `target = rr *
    stop`, entry signal untouched.

    Sizing follows for free and this is the whole volatility-sizing question:
    `execution.size` sets quantity to `equity * risk / stop`, so a bracket that
    widens in a violent regime *is* a position that shrinks in one, at a
    constant 0.5% of equity risked per trade. With a fixed stop that mechanism
    is inert.

    Two `vol_source` units, tested separately rather than blended, because they
    are different claims:

      * `atr` -- mean true range of the 20 sessions that closed before the
        entry's day. Realised, trailing, from the traded bars themselves.
      * `vix` -- `price * vix / (100 * sqrt(252))`, the implied daily move from
        the last completed hourly `vix_1h` close. Forward-looking, and priced by
        someone else.

    During the ATR warm-up (the first 20 sessions, 17 of 197 trades) there is no
    reading, and the bracket falls back to `fallback_stop`. Absent is not zero
    volatility, and skipping those entries would silently change the trade
    population the comparison rests on.
    """
    #: What `hourly_delta_reversal.rs` compiles, so this is the name
    #: `validation.py` sends to `/api/run`.
    name = "Hourly Delta Reversal"
    server_name = "NQ Hourly Delta Reversal"

    #: ATR_STOP_FRACTION / TARGET_RR / SELL_MIN_DELTA / ATR_SESSIONS /
    #: WARMUP_STOP in the Rust file. Keep the two in step.
    #: Synced to the Rust file on 2026-08-05, when every compiled constant was
    #: replaced by an a-priori one: symmetric round delta thresholds instead of
    #: the searched 50/300 split, `rr` 2.0 instead of the fold median 1.25,
    #: `short_trend_days` 45 (centre of the stated 30..60 plateau) instead of 35,
    #: and no weekday exclusion. `k` 0.2 was already a plateau centre and stands.
    defaults = {**HourlyDeltaReversalTied.defaults,
                "k": 0.2, "rr": 2.0,
                "buy_delta": 100, "sell_delta": 100,
                "short_trend_days": 45, "skip_thursday": False,
                "vol_source": "atr", "atr_days": 20, "fallback_stop": 50}

    grid = {
        "k": [0.10, 0.125, 0.15, 0.20],
        "rr": [1.0, 1.25, 1.5, 2.0],
        "sell_delta": [75, 150, 225, 300],
    }

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        return (data.load_session_closes(self.symbol), book_gate(bars, features),
                data.atr_by_day(bars, self.defaults["atr_days"]),
                data.vix_series(bars))

    def signals(self, bars, context, group, params):
        session_closes, gate, atr, vix = context
        params = self.expand(params)
        # The bracket only ever reaches `execution` through a Signal, so the
        # base loop can run once with a placeholder and have its brackets
        # rewritten per entry. Nothing else in that loop reads the stop.
        base = super().signals(bars, context[:2], group, params)
        k, source = params["k"], params["vol_source"]
        fallback = params["fallback_stop"]

        out = []
        for signal in base:
            bar = bars[signal.index]
            if source == "vix":
                level = vix[signal.index]
                unit = (bar[O] * level / (100.0 * TRADING_DAYS ** 0.5)
                        if level > 0 else None)
            else:
                unit = atr.get(bar[TS] // 86_400)
            stop = k * unit if unit else fallback
            if stop > 0:
                out.append(replace(signal, stop=stop,
                                   target=stop * params["rr"]))
        return out


@register
class HourlyDeltaReversalVix(HourlyDeltaReversalVol):
    """The same scaling driven by implied rather than realised volatility.

    Registered separately instead of putting `vol_source` on the grid, because
    the two units are competing hypotheses and searching across them would let
    the sweep pick whichever won on this sample. Each gets its own walk-forward
    and its own gate result, and both are logged against the search count.

    `k` is shifted up relative to the ATR class only because a VIX-implied daily
    move is the smaller unit -- the two grids cover the same 30-95 point range
    of actual stops, so neither is given a wider search than the other.
    """
    name = "Hourly Delta Reversal (vix)"

    defaults = {**HourlyDeltaReversalVol.defaults, "vol_source": "vix", "k": 0.175}

    grid = {
        "k": [0.15, 0.175, 0.20, 0.25],
        "rr": [1.0, 1.25, 1.5, 2.0],
        "sell_delta": [75, 150, 225, 300],
    }

