"""Replica of `live_trade/src/strategies/idk/ofi_momentum.rs`.

Deep Order Flow Imbalance (Cont, Kukanov & Stoikov 2014) as a minute-scale
momentum signal: net book growth on the bid side minus the ask side, smoothed by
an EWMA and z-scored against the last 20 sessions of readings.

Three things differ from the other replicas here.

The add/cancel columns cover all ten book levels, so this is *deep* OFI rather
than the canonical touch-only statistic. It is noisier and decays more slowly;
whether that helps at minute holds is exactly what the run is for.

`scale` and `norm` exist because the literal spec statistic — raw OFI over a
single trailing stdev — measured no stable forward-return relationship on this
data (t under 2 in every month, sign flipping 4/3). Both alternatives address a
documented weakness of that construction: `scale="ratio"` divides OFI by the
minute's total book activity, the depth normalization MLOFI work relies on, and
`norm="slot"` z-scores against the same minute-of-day, which is how OFI's
U-shaped intraday profile stops the open and close from owning the signal count.
`absorption_reversal` already normalizes per slot for the same reason.

The Rust strategy holds at most one position at a time, so its signals are
entangled with its own exits — a signal is only taken while flat. `signals()`
therefore resolves each entry's exit bar with `_exit_index`, which repeats
`execution.resolve`'s rules for a single position. That duplication is the price
of the occupancy rule; `validation.py` is what keeps it honest.
"""
from sandbox import data
from sandbox import metrics
from sandbox import ofi_ml_gate
from sandbox.data import H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960          # 09:30 .. 16:00
EXIT_MIN = 945                          # 15:45 session flatten
NORM_SESSIONS = 20
NORM_SAMPLES = NORM_SESSIONS * (CLOSE_MIN - OPEN_MIN)

#: Rust's default `OrderFlowFeatures` for a bar with no matching feature row
NO_FEATURES = {"bid_add_volume": 0.0, "bid_cancel_volume": 0.0, "ask_add_volume": 0.0,
               "ask_cancel_volume": 0.0, "top5_imbalance": 0.0, "spread": 0.0,
               "trade_delta": 0.0, "book_valid": False}


def _exit_index(bars, entry_index, side, stop, target, time_stop, session_end):
    """Index of the bar a position opened at `entry_index` leaves on.

    Mirrors `execution.resolve` for one position: stop before target, the time
    stop only once the bracket has not fired, and the session flatten last (so a
    position opened on the final bar of the session leaves on that same bar).
    """
    entry = bars[entry_index][O]
    entry_minute = (bars[entry_index][TS] % 86_400) // 60
    total = len(bars)
    for j in range(entry_index, total):
        bar = bars[j]
        minute = (bar[TS] % 86_400) // 60
        if j > entry_index:
            if side == LONG:
                if bar[L] <= entry - stop or bar[H] >= entry + target:
                    return j
            elif bar[H] >= entry + stop or bar[L] <= entry - target:
                return j
            if minute >= entry_minute + time_stop:
                return j
        if minute < session_end:
            nxt = bars[j + 1] if j + 1 < total else None
            if (nxt is None or nxt[TS] // 86_400 != bar[TS] // 86_400
                    or (nxt[TS] % 86_400) // 60 >= session_end):
                return j
    return total - 1


@register
class OfiMomentum(Strategy):
    name = "Deep OFI Momentum"
    server_name = "NQ Deep OFI Momentum"
    bars = "level_two"
    #: `risk` and `leverage` mirror ENTRY_RISK_FRACTION and ENTRY_LEVERAGE in
    #: the Rust file, not the `Execution` defaults. `risk` really is 0.005 here,
    #: unlike the other three shared-account strategies, and is stated rather
    #: than inherited so the default drifting cannot silently change it.
    #: `leverage` was missing entirely while the engine compiled 2.0, so every
    #: replica figure -- drawdown above all -- was understated by half.
    execution = Execution(session_end_min=EXIT_MIN, risk=0.005, leverage=2.0)

    defaults = {
        "halflife": 2,
        "ofi_z": 2.0,
        "opposing_imbalance": 0.30,
        "target": 120,
        "stop": 60,
        "time_stop": 20,
        "max_spread": 1.0,
        #: "raw" = OFI in contracts; "ratio" = OFI / the minute's total book
        #: activity, i.e. depth-normalized.
        "scale": "raw",
        #: "global" = one trailing stdev, the spec's formula; "slot" = mean and
        #: stdev of the same minute-of-day over the last 20 sessions.
        "norm": "slot",
        #: require the minute's aggressive `trade_delta` to agree with the book
        #: pressure — traded flow confirming quoted flow. REQUIRE_DELTA_AGREEMENT
        #: in the Rust file; keep the two in step.
        "require_delta_agreement": True,
        #: apply the frozen ML meta-labeler.
        #:
        #: **False since 2026-08-05**: the gate was deleted from the Rust file,
        #: so True no longer replicates anything the engine does and is kept only
        #: so `research/ofi_ml.py` and `research/defaults_test.py` can still
        #: measure what it was worth. It was worth $3.35 of out-of-sample PnL
        #: inside the entry window it was trained on, and it was harmful outside
        #: it -- `ofi_ml.py` trains on the candidate pool built from the compiled
        #: defaults, so a model fit under `entry_from` 660 is out of distribution
        #: at 570. See `results/DEFAULTS_TEST.md`.
        "ml_gate": False,
        #: "momentum" trades with the sign of the deep OFI, which is what the
        #: Rust file compiles and what Cont/Kukanov/Stoikov document at the
        #: touch. "reversion" trades against it: adds at levels 2-10 are largely
        #: non-executable liquidity that is pulled as price approaches, so the
        #: sign of a *deep* statistic is not obviously the sign of the move.
        "direction": "momentum",
        #: "both", "long" or "short" — a structural question (is the edge
        #: one-sided?), not a threshold to tune.
        "side": "both",
        # Entry window, minutes from midnight. 570 = 09:30, the session open.
        # **660 -> 570 on 2026-08-05**, matching the Rust revert: the 11:00 cut
        # was chosen after reading the per-hour table, which Stage 5 forbids, so
        # the only defensible value is the one needing no justification from this
        # sample.
        "entry_from": 570,
        # 925 = EXIT_MIN - time_stop, mirroring the Rust file's derived
        # LAST_ENTRY_MINUTE: the last minute a position can still reach its time
        # stop before the forced flatten. Was a hardcoded 900 here while the
        # engine computed 925, which silently dropped every 15:00-15:25 entry
        # from the replica.
        "entry_to": EXIT_MIN - 20,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "halflife": [2, 3, 5],
        "ofi_z": [1.5, 2.0, 2.5],
        "target": [30, 45, 60],
        "stop": [20, 30, 40],
        "time_stop": [10, 20, 30],
        "require_delta_agreement": [False, True],
    }

    def valid(self, params):
        # A target inside the stop is a losing bracket by construction.
        return params["target"] >= params["stop"]

    def context(self):
        """`{(halflife, scale, norm): rows}`, row = (z, spread, top5, delta).

        The z-score is the only expensive part and depends on nothing but those
        three axes, so every other axis sweeps for free.
        """
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        keys = {(h, s, n)
                for h in set(self.grid.get("halflife", [])) | {self.defaults["halflife"]}
                for s in set(self.grid.get("scale", [])) | {self.defaults["scale"]}
                for n in set(self.grid.get("norm", [])) | {self.defaults["norm"]}}
        out = {key: self._statistics(bars, features, *key) for key in sorted(keys)}
        # The ML gate needs the whole feature row, not just the four fields the
        # z-score rows carry. Keyed by a string so the `(halflife, scale, norm)`
        # lookups every other caller does are unaffected.
        out["features"] = features
        return out

    @staticmethod
    def _statistics(bars, features, halflife, scale, norm):
        rows = [None] * len(bars)
        alpha = 1.0 - 0.5 ** (1.0 / halflife)
        warmup = 4 * halflife
        by_slot = norm == "slot"
        # One ring per minute-of-day when normalizing per slot, otherwise a
        # single ring of the last NORM_SAMPLES readings.
        rings = {}
        depth = NORM_SESSIONS if by_slot else NORM_SAMPLES
        ewma = None
        ewma_minutes = 0
        previous_minute = None
        day = None

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if ts // 86_400 != day:
                # RTH-only features, so sessions are separated by an overnight
                # gap the EWMA must not smooth across.
                day, ewma, ewma_minutes, previous_minute = ts // 86_400, None, 0, None
            if not (OPEN_MIN <= minute < CLOSE_MIN) or minute >= EXIT_MIN:
                continue
            feature = features.get(ts, NO_FEATURES)

            if previous_minute is not None and previous_minute + 1 != minute:
                ewma, ewma_minutes = None, 0
            previous_minute = minute
            # A minute with no feature row still folds in, as zero: the Rust bar
            # carries a default `OrderFlowFeatures`.
            ofi = ((feature["bid_add_volume"] - feature["bid_cancel_volume"])
                   - (feature["ask_add_volume"] - feature["ask_cancel_volume"]))
            if scale == "ratio":
                activity = (feature["bid_add_volume"] + feature["bid_cancel_volume"]
                            + feature["ask_add_volume"] + feature["ask_cancel_volume"])
                ofi = ofi / activity if activity > 0 else 0.0
            ewma = ofi if ewma is None else ewma + alpha * (ofi - ewma)
            ewma_minutes += 1
            if ewma_minutes < warmup:
                continue

            ring = rings.setdefault(minute if by_slot else 0, [])
            if len(ring) >= depth:
                mean = sum(ring) / depth
                variance = sum(x * x for x in ring) / depth - mean * mean
                if variance > 0:
                    # Per slot this is a true z-score; globally the numerator is
                    # left undemeaned, which is the spec's formula.
                    centre = mean if by_slot else 0.0
                    rows[i] = ((ewma - centre) / variance ** 0.5,
                               feature["spread"] if feature["book_valid"] else None,
                               feature["top5_imbalance"], feature["trade_delta"])
            # recorded last, so a bar never enters its own z-score
            ring.append(ewma)
            del ring[:-depth]
        return rows

    def bracket(self, bars, index, params, context):
        """`(stop, target)` in points for an entry filling at `index`.

        A hook rather than a lookup so the volatility-scaled subclasses below can
        vary the bracket per entry without restating the signal loop. The base
        form is the compiled one: constant points.
        """
        return params["stop"], params["target"]

    def signals(self, bars, context, group, params):
        rows = context[(params["halflife"], params["scale"], params["norm"])]
        z_cut, opposing = params["ofi_z"], params["opposing_imbalance"]
        agreement = params["require_delta_agreement"]
        fade = params["direction"] == "reversion"
        only = params["side"]
        time_stop = params["time_stop"]
        max_spread = params["max_spread"]
        entry_from, entry_to = params["entry_from"], params["entry_to"]
        session_end = self.execution.session_end_min
        # On by default because the registered Rust strategy has it on. Sweeps
        # and `research/ofi_ml.py`'s own candidate generation pass False to see the raw
        # rule underneath.
        ml_gate = params["ml_gate"]
        ml_features = context["features"] if ml_gate else {}
        # Signals are always generated over the whole history so the 20-session
        # warm-up comes from bars before the window being evaluated.
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = metrics.split_ts(params["to_date"]) + 86_400 if params["to_date"] else None

        out = []
        free_from = -1          # first bar index at which the strategy is flat again
        for i, row in enumerate(rows):
            if row is None or i < free_from:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != bars[i][TS] + 60:
                continue        # fills happen on the contiguous next minute only
            ts = bars[i][TS]
            if not entry_from <= (ts % 86_400) // 60 <= entry_to:
                continue
            z, spread, imbalance, delta = row
            if spread is None or spread > max_spread:
                continue
            if z >= z_cut:
                side = LONG
            elif z <= -z_cut:
                side = SHORT
            else:
                continue
            if fade:
                side = SHORT if side == LONG else LONG
            if only != "both" and side != only:
                continue
            # Both gates are keyed to the side actually traded, not to the sign
            # of the book pressure: "do not buy into a wall of offers" is a
            # statement about the entry, and stays true when the entry fades.
            if (imbalance < -opposing) if side == LONG else (imbalance > opposing):
                continue
            if agreement and ((delta <= 0) if side == LONG else (delta >= 0)):
                continue
            # Last gate, and it must sit before `free_from` is set: the Rust
            # strategy returns no signal when the model rejects, so the account
            # stays flat and a later candidate can still fill. Gating after the
            # occupancy claim would silently drop those replacements too.
            if ml_gate:
                feature = ml_features.get(ts)
                if feature is None or not ofi_ml_gate.accepts(
                        ts, feature, z, spread, delta, side == LONG,
                        entry_from, entry_to):
                    continue

            stop, target = self.bracket(bars, i + 1, params, context)
            if not stop or stop <= 0:
                continue
            # Occupied until the position leaves; the Rust strategy refuses a
            # signal while one is open, and its exits run before the signal
            # check, so the exit bar itself is free again.
            free_from = _exit_index(bars, i + 1, side, stop, target, time_stop,
                                    session_end)
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue
            out.append(Signal(i + 1, side, stop, target, time_stop))
        return out


@register
class OfiMomentumTied(OfiMomentum):
    """The same strategy over three tied axes instead of six.

    OPTIMIZATION_PLAN.md Stage 3 asks for <=3 free axes spaced wide, and the
    six-axis form asks 486 questions of 17 monthly samples. What stays free and
    what is fixed, with the reason from the full-sample `ofi_sensitivity.py`
    scan. Every number below is in-sample and is used only to decide *which*
    questions to ask; the answers come from the walk-forward.

      * `ofi_z` stays free. It is the only axis that is a claim about the
        signal rather than about the geometry of the bracket, so if the search
        is going to spend an axis anywhere it belongs here.
      * `stop` stays free, and is the one geometry axis. The scan is jagged
        across it (10 -> -244, 15 -> -37, 20 -> +59, 30 -> -75, 40 -> -54,
        50 -> +4, 60 -> -18) with no plateau, which is a reason to let a
        walk-forward pick it per fold rather than to trust any single value.
      * `rr` replaces the free `target` axis: the target as a multiple of that
        stop. The compiled 45/30 is rr 1.5, and the scan's target response is
        monotone *down* above it (60/80/100 all near -220), so one ratio states
        the finding that a target far beyond the stop is not paid for.

    Fixed, and why:

      * `halflife` = 2. The scan is worst at 1 (-266) and drifts down from 3 to
        8 (-173 .. -337); 2 is the interior best and the compiled value. The
        axis carries no plateau to search.
      * `time_stop` = 20. 10/15/20/30 span -75 to -120, which is one flat
        region, and the compiled value sits inside it. 45 and 60 score better
        (+15, +26) but sit at the grid edge, and at a 45-minute hold the entry
        is a 20-session z-score of book pressure held for most of an afternoon
        -- no longer the effect the literature documents, and not something a
        two-cell edge result should buy.
      * `opposing_imbalance` = 0.30. Inert: 0.1 to 1.0 moves the result by 17
        points on 3 843 trades and 1.0 is the same as 0.5, i.e. the gate almost
        never binds.
      * `max_spread` = 1.0. Tightening it to 0.75/0.5 is sharply worse
        (-200/-361) because it removes exactly the busy minutes the signal
        needs; loosening past 1.0 is flat. The compiled cut is the plateau's
        left edge and is kept for the cost reason it exists.
      * `scale` = "raw", `norm` = "slot", entry window 09:45-15:30 -- all
        compiled, none showing a plateau worth an axis. `entry_from` = 660
        scores +152 but the axis is non-monotone (570 -> -0, 585 -> -75,
        600 -> -123, 630 -> +104, 660 -> +152) and a tuned time-of-day cut is
        exactly what Stage 5 forbids. It is re-tested as a *filter*, per fold.
      * `direction` = "momentum", `side` = "both", `require_delta_agreement` =
        False. All three are structural hypotheses rather than parameters and
        are judged fold by fold in `ofi_filters.py`; this class holds them at
        the pre-Stage-5 setting so that `OfiMomentumConfirmed` isolates the one
        that was adopted.
      * risk fraction = 0.005. At a $1000 account the margin cap binds from
        0.0075 up, so the axis saturates immediately; it is leverage, not edge.
    """
    name = "Deep OFI Momentum (tied)"

    #: `require_delta_agreement` is pinned False here even though the Rust file
    #: now compiles it True: this class is the *unconfirmed* arm of the search,
    #: and `OfiMomentumConfirmed` below is the arm that turns it on. Inheriting
    #: the compiled value would collapse the two into the same candidate.
    defaults = {**OfiMomentum.defaults, "stop": 30, "rr": 1.5,
                "require_delta_agreement": False}

    grid = {
        "ofi_z": [1.5, 2.0, 2.5, 3.0],
        "stop": [20, 30, 45, 60],
        "rr": [0.75, 1.0, 1.5, 2.0],
    }

    def valid(self, params):
        # The base class refuses a target inside its stop. Here that constraint
        # is dropped deliberately: with a 20-minute time stop the position is
        # closed by the clock most of the time, so a target tighter than the
        # stop is a coherent high-win-rate shape rather than a losing bracket,
        # and rr=0.75 is on the grid to let the walk-forward say so.
        return True

    def bracket(self, bars, index, params, context):
        stop = params["stop"]
        return stop, stop * params["rr"]


@register
class OfiMomentumConfirmed(OfiMomentumTied):
    """The tied form with quoted book pressure required to be traded on.

    The one structural candidate out of `ofi_filters.py` that has both a
    rationale written before the number and a per-fold record worth a search:
    require the minute's aggressive `trade_delta` to agree with the side taken.
    Quoted book pressure that nobody lifts is an intention; pressure that trades
    is a commitment, and deep OFI -- which aggregates ten levels, most of them
    non-executable -- is exactly the statistic that should need confirming.

    It is not adopted on the strength of the filter study, which it does not
    pass: +137 overall but 4 of 5 folds, losing 50 in fold 3. It gets a
    walk-forward because it is already a compiled *parameter* of the Rust
    strategy rather than a new tuned cut, so fixing it True is a structural
    choice the plan permits testing, and because the honest way to find out
    whether a 4/5 filter is real is to select over it out of sample.

    Every other filter tried is rejected and stays rejected: `skip Tue` (+158,
    4/5) and `skip 10:00` (+264, 4/5) have no a-priori rationale and sit on
    jagged axes; every VIX and ATR cut is tuned and flips sign across nearby
    values; and the replenishment filter that scores 4/5 is the *opposite* of
    the sentence its rationale writes -- the correctly signed version is 1/5 and
    -82. `top1_imbalance` and `microprice - mid` are the same variable twice
    (microprice is the mid tilted by top-of-book imbalance) and both are 2/5.
    """
    name = "Deep OFI Momentum (confirmed)"

    defaults = {**OfiMomentumTied.defaults, "require_delta_agreement": True}


#: trading days a year, for turning VIX's annualised percentage into a move
TRADING_DAYS = 252


@register
class OfiMomentumAtr(OfiMomentumTied):
    """Brackets scaled by realised volatility instead of fixed in points.

    Every configuration above risks a constant number of points, so what it
    risks relative to the day's range drifts with the regime -- and this
    strategy spans a sample whose 20-session ATR moves by a factor of two.
    `stop = k * ATR`, `target = rr * stop`, entry signal untouched.

    Position sizing follows for free, and this is the whole volatility-sizing
    question: `execution.size` sets quantity to `equity * risk / stop`, so a
    bracket that widens in a violent regime *is* a position that shrinks in one,
    at a constant 0.5% of equity risked per trade. With a fixed stop that
    mechanism is inert.

    `k` is grided to cover the same 20-60 point range of actual stops as the
    fixed form, so neither is given the wider search. During the 20-session ATR
    warm-up there is no reading and the bracket falls back to `fallback_stop`;
    absent is not zero volatility, and dropping those entries would change the
    trade population the comparison rests on.
    """
    name = "Deep OFI Momentum (atr)"

    defaults = {**OfiMomentumTied.defaults, "k": 0.075, "atr_days": 20,
                "fallback_stop": 30, "vol_source": "atr"}

    grid = {
        "ofi_z": [1.5, 2.0, 2.5, 3.0],
        "k": [0.05, 0.075, 0.10, 0.15],
        "rr": [0.75, 1.0, 1.5, 2.0],
    }

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        out = dict(super().context())
        out["atr"] = data.atr_by_day(bars, self.defaults["atr_days"])
        out["vix"] = data.vix_series(bars)
        return out

    def unit(self, bars, index, context):
        return context["atr"].get(bars[index][TS] // 86_400)

    def bracket(self, bars, index, params, context):
        unit = self.unit(bars, index, context)
        stop = params["k"] * unit if unit else params["fallback_stop"]
        return stop, stop * params["rr"]


@register
class OfiMomentumVix(OfiMomentumAtr):
    """The same scaling driven by implied rather than realised volatility.

    Registered separately instead of putting `vol_source` on the grid, because
    the two units are competing hypotheses and searching across them would let
    the sweep keep whichever won on this sample. Each gets its own walk-forward
    and its own gate result, and both are counted against the search total.

    The unit is `price * vix / (100 * sqrt(252))` -- the daily move implied by
    the last completed hourly `vix_1h` close, so it is forward-looking and
    priced by someone else, where the ATR unit is trailing and realised. `k` is
    shifted up because the VIX-implied daily move is the smaller of the two
    units; both grids cover the same 20-60 point range of actual stops.
    """
    name = "Deep OFI Momentum (vix)"

    defaults = {**OfiMomentumAtr.defaults, "vol_source": "vix", "k": 0.10}

    grid = {
        "ofi_z": [1.5, 2.0, 2.5, 3.0],
        "k": [0.06, 0.10, 0.15, 0.20],
        "rr": [0.75, 1.0, 1.5, 2.0],
    }

    def unit(self, bars, index, context):
        level = context["vix"][index]
        if level <= 0:
            return None
        return bars[index][O] * level / (100.0 * TRADING_DAYS ** 0.5)
