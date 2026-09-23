"""Fabervaale's orderflow models, as a replica strategy.

Fabio Valentini ("Fabervaale") teaches an *IVB* family: an opening-range
breakout whose entry and invalidation are decided by orderflow rather than by
the breakout candle alone. The published material describes three distinct
pieces, and this file implements all three behind one `model` parameter:

  * ``ivb1`` — breakout, with the invalidation read off the opening range's
    volume profile. The stop sits beyond the value-area edge on the far side
    of the trade ("the sensitive order-block area"), so risk is set by where
    the auction actually built inventory instead of by a round number.
  * ``ivb2`` — breakout, then *orderflow confirmation* before entering.
    Confirmation is aggression that follows through: cumulative delta must
    print a new session extreme in the breakout's direction while price holds
    the broken level, and the confirming minute must not be absorbed.
  * ``exhaustion`` — the delta-divergence reversal. Price makes a new session
    extreme while cumulative delta fails to, i.e. the aggressors pushing the
    extreme are thinning out, so the extreme is faded.

Two ideas from the source material are load-bearing and worth naming, because
they are what separates this from a plain ORB:

*Absorption* is heavy aggression that does not move price — passive limit
orders are eating it. On a breakout that is a reason to stand aside, not to
join, so `absorption_delta`/`absorption_move` reject those minutes.

*Exhaustion* is the opposite failure: price extends but the delta behind it
shrinks. That is a reversal signal, and it is the whole of the ``exhaustion``
model.

Every model is causal. The breakout or divergence is judged on a *completed*
minute and `Signal.index` points at the following minute, which is the one the
engine can actually fill at the open.
"""
import bisect

from sandbox import data, metrics, volume_profile
from sandbox.data import C, D, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN = 9 * 60 + 30
LAST_ENTRY = 15 * 60
EXIT_MIN = 16 * 60

MODELS = ("ivb1", "ivb2", "exhaustion")


def bar_volume(bar, feature):
    """Traded volume for a minute, in contracts.

    The bar tuple carries *signed* delta, not volume, so the two aggressive
    volume columns are the real source. A minute with no feature row falls back
    to `abs(delta)`, which is a lower bound on its volume — the profile is a
    shape, and a bound keeps an absent minute from vanishing out of it.
    """
    if feature:
        volume = feature.get("aggressive_buy_volume", 0.0) + feature.get(
            "aggressive_sell_volume", 0.0)
        if volume > 0:
            return volume
    return abs(bar[D])


# Re-exported so this module's own tests and callers keep their import site;
# the implementations are shared with the other volume-profile strategies.
profile = volume_profile.profile
value_area = volume_profile.value_area


@register
class FabervaaleOrderflow(Strategy):
    """Fabervaale's IVB breakout models and the delta-divergence reversal."""

    name = "Fabervaale Orderflow"
    bars = "level_two"
    # The account the models are quoted on: $1,000 to start, the 0.2 spread of
    # the `idk` environment, and the Forex instrument's sizing (inherited from
    # `Execution`: 0.01 lot step, 1.0 point value, 0.5% risk per entry).
    execution = Execution(initial=1_000.0, slippage=0.2, session_end_min=EXIT_MIN)

    defaults = {
        "model": "ivb2",
        "opening_range": 30,
        "buffer": 0.0,
        "stop": 15.0,
        "rr": 2.0,
        "time_stop": 90,
        "trail_frac": 0.0,         # trailing stop, as a multiple of the stop; 0 off
        "stop_atr": 0.0,           # >0 sizes the stop off ATR instead of points
        "atr_days": 20,
        "entry_from": 0,           # 0 means "as soon as the range is complete"
        "entry_to": LAST_ENTRY,
        "skip_days": "",           # e.g. "mon,fri"
        "max_trades": 1,           # the source's own answer to overtrading a range
        # regime gates. Each is off at 0, and each reads only data that closed
        # before the session it judges.
        "vix_min": 0.0,
        "vix_max": 0.0,
        "atr_min": 0.0,
        "atr_max": 0.0,
        "trend_days": 0,           # require the side to agree with an N-day SMA
        # ivb1: volume-profile invalidation
        "value_area": 0.70,
        "bin_size": 1.0,
        "min_stop_frac": 0.34,     # floor on the VP stop, as a share of `stop`
        # ivb2: orderflow confirmation after the break
        "confirm_window": 5,       # minutes the break has to earn its confirmation
        "hold_tolerance": 0.0,     # how far back inside the level a hold may dip
        "require_book": False,     # also demand the resting book agree
        "min_imbalance": 0.20,
        # absorption veto: heavy delta that failed to move price
        "absorption_delta": 0.0,   # 0 disables the veto
        "absorption_move": 1.0,
        # exhaustion: the divergence reversal
        "min_break": 1.0,          # points beyond the old extreme that count as new
        "min_divergence": 0.0,     # contracts of cumulative delta the extreme gave up
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "opening_range": [15, 30],
        "buffer": [0.0, 1.0],
        "stop": [10.0, 15.0, 20.0],
        "rr": [1.5, 2.0, 3.0],
    }

    def valid(self, params):
        range_end = OPEN_MIN + params["opening_range"]
        entry_from = params["entry_from"] or range_end
        shape = (
            params["model"] in MODELS
            and params["opening_range"] > 0
            and params["stop"] > 0
            and params["time_stop"] > 0
            and params["bin_size"] > 0
            and 0 < params["value_area"] <= 1
            and 0 < params["min_stop_frac"] <= 1
            and params["confirm_window"] >= 0
            and params["max_trades"] >= 1
            and params["trail_frac"] >= 0
            and params["stop_atr"] >= 0
            and params["atr_days"] > 0
            and params["trend_days"] >= 0
        )
        # A position needs at least one profit exit: either a target or a trail.
        exits = params["rr"] > 0 or params["trail_frac"] > 0
        window = (range_end <= entry_from <= params["entry_to"] <= LAST_ENTRY)
        gates = ((params["vix_max"] <= 0 or params["vix_max"] >= params["vix_min"])
                 and (params["atr_max"] <= 0 or params["atr_max"] >= params["atr_min"]))
        return shape and exits and window and gates

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        return {
            "features": data.load_l2_features(self.symbol),
            "vix": data.vix_series(bars),
            "atr": data.atr_by_day(bars, 20),
            "closes": data.load_session_closes(self.symbol),
        }

    @staticmethod
    def _skip_set(value):
        """`skip_days` as weekday numbers. Monday is 0, matching `datetime`."""
        if not value:
            return set()
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        return {names[item.strip().lower()] for item in str(value).split(",")
                if item.strip().lower() in names}

    @staticmethod
    def _trend_average(ordered, days, day):
        """Average of the `days` closes that landed strictly before `day`.

        Point-in-time by construction, the same discipline `data.sma_gate`
        applies: a session's own close is withheld until the next day begins,
        so a trend gate can never read the close of the day it is judging.
        The lookup is by day rather than by position because the session being
        judged has not closed yet and so is not in the close series at all.
        """
        if days <= 0 or not ordered:
            return None
        cut = bisect.bisect_left(ordered, (day,))
        if cut < days:
            return None
        return sum(close for _, close in ordered[cut - days:cut]) / days

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []

        context = context or {}
        features = context.get("features", {})
        window = (
            metrics.split_ts(params["from_date"]) if params["from_date"] else None,
            metrics.split_ts(params["to_date"]) if params["to_date"] else None,
        )
        gates = {
            "vix": context.get("vix") or [0.0] * len(bars),
            "atr": context.get("atr") or {},
            "closes": sorted(context.get("closes") or []),
            "trend_days": int(params["trend_days"]),
            "skip": self._skip_set(params["skip_days"]),
        }
        emit = {
            "ivb1": self._ivb1,
            "ivb2": self._ivb2,
            "exhaustion": self._exhaustion,
        }[params["model"]]

        signals = []
        for session in self._sessions(bars, features, gates, params, window):
            if self._session_allowed(session, params):
                emit(session, params, signals)
        return signals

    # -- regime gates ------------------------------------------------------

    def _session_allowed(self, session, params):
        """Whole-session gates: weekday, VIX band, and ATR band.

        These decide before the first signal because they describe the day, not
        the trade. Each reads only values that were known when the opening range
        completed, so gating never consults the session it is judging.
        """
        # The epoch begins on a Thursday, so day 0 is weekday 3.
        if (session["day"] + 3) % 7 in session["skip"]:
            return False
        vix = session["vix"]
        if params["vix_min"] > 0 and (vix <= 0 or vix < params["vix_min"]):
            return False
        if params["vix_max"] > 0 and (vix <= 0 or vix >= params["vix_max"]):
            return False
        atr = session["atr"]
        # An absent ATR is "no reading", not "zero volatility": a gated run
        # skips the warm-up days rather than treating them as the calmest.
        if params["atr_min"] > 0 and (atr is None or atr < params["atr_min"]):
            return False
        if params["atr_max"] > 0 and (atr is None or atr >= params["atr_max"]):
            return False
        return True

    def _trend_allows(self, session, side, params):
        """Trade only with the N-session SMA, when `trend_days` asks for it."""
        if int(params["trend_days"]) <= 0:
            return True
        average = session["sma"]
        if average is None:
            return False
        reference = session["bars"][0][O]
        return reference > average if side == LONG else reference < average

    def _risk(self, session, params):
        """Base stop distance in points, or None when the day has no reading.

        With `stop_atr` set the bracket travels with volatility instead of
        being a constant number of points across regimes that are not
        comparable — a 15-point stop is a different trade in a 60-point ATR
        session than in a 200-point one.
        """
        if float(params["stop_atr"]) <= 0:
            return float(params["stop"])
        if not session["atr"]:
            return None
        return float(params["stop_atr"]) * session["atr"]

    # -- session framing ---------------------------------------------------

    def _sessions(self, bars, features, gates, params, window):
        """Yield one dict per session: its bars, the opening range, and delta.

        Everything downstream is per-session — the range, the profile, the
        cumulative delta, the regime readings and the trade cap all reset at the
        open — so the models receive a session rather than re-deriving one each.
        """
        from_ts, to_ts = window
        range_end = OPEN_MIN + int(params["opening_range"])
        current = None
        for index, bar in enumerate(bars):
            ts = int(bar[TS])
            day = ts // 86_400
            minute = (ts % 86_400) // 60
            if current is None or current["day"] != day:
                if current is not None:
                    yield current
                current = {"day": day, "index": [], "bars": [], "minute": [],
                           "cvd": [], "volume": [], "opening": [],
                           "high": None, "low": None, "features": features,
                           "skip": gates["skip"], "vix": 0.0,
                           "atr": gates["atr"].get(day),
                           "sma": self._trend_average(
                               gates["closes"], gates["trend_days"], day)}
            # The VIX print in force when the opening range completed. Reading
            # it later would let an afternoon print gate a morning decision.
            if minute < range_end:
                current["vix"] = gates["vix"][index] if index < len(gates["vix"]) else 0.0
            if from_ts is not None and ts < from_ts:
                continue
            if to_ts is not None and ts >= to_ts:
                continue
            feature = features.get(ts)
            volume = bar_volume(bar, feature)
            if OPEN_MIN <= minute < range_end:
                current["opening"].append((bar, volume))
                current["high"] = bar[H] if current["high"] is None else max(
                    current["high"], bar[H])
                current["low"] = bar[L] if current["low"] is None else min(
                    current["low"], bar[L])
            current["index"].append(index)
            current["bars"].append(bar)
            current["minute"].append(minute)
            current["volume"].append(volume)
            # Cumulative delta runs from the session open, opening range
            # included: the range is where the day's inventory is built, and a
            # CVD that ignored it could not tell a new extreme from a first one.
            current["cvd"].append((current["cvd"][-1] if current["cvd"] else 0.0) + bar[D])
        if current is not None:
            yield current

    def _tradable(self, session, position, params):
        """Is the completed bar at `position` one a signal may be judged on?

        Guards every model shares: the opening range must be closed, the next
        minute must exist inside the same session (it is the fill), and that
        minute must be early enough to still be entered.
        """
        range_end = OPEN_MIN + int(params["opening_range"])
        if session["high"] is None or session["minute"][position] < range_end:
            return False
        if position + 1 >= len(session["bars"]):
            return False
        entry_minute = session["minute"][position + 1]
        return (int(params["entry_from"]) or range_end) <= entry_minute <= int(
            params["entry_to"])

    def _absorbed(self, session, position, params):
        """True when this minute's aggression was eaten instead of paid for."""
        threshold = float(params["absorption_delta"])
        if threshold <= 0:
            return False
        bar = session["bars"][position]
        return (abs(bar[D]) >= threshold
                and abs(bar[C] - bar[O]) <= float(params["absorption_move"]))

    def _book_agrees(self, session, position, side, params):
        if not params["require_book"]:
            return True
        feature = session["features"].get(int(session["bars"][position][TS]))
        if not feature or not feature.get("book_valid", False):
            return False
        imbalance = feature.get("top5_imbalance", 0.0)
        return (imbalance >= params["min_imbalance"] if side == LONG
                else imbalance <= -params["min_imbalance"])

    def _beyond(self, session, position, params):
        """`LONG`/`SHORT` if the completed bar closed through the range, else None."""
        if session["high"] is None:
            return None
        close = session["bars"][position][C]
        buffer_ = float(params["buffer"])
        if close > session["high"] + buffer_:
            return LONG
        if close < session["low"] - buffer_:
            return SHORT
        return None

    def _breaks(self, session, params):
        """`{position: side}` for the minutes that *become* a breakout.

        Only the transition counts. Price that simply stays outside the range
        is one breakout, not one per minute: without this the confirmation
        window could never lapse, because every minute of a held breakout would
        re-arm it, and `max_trades` would be spent on consecutive minutes of a
        single move rather than on genuinely separate attempts.
        """
        range_end = OPEN_MIN + int(params["opening_range"])
        out = {}
        state = None
        for position, minute in enumerate(session["minute"]):
            if minute < range_end:
                continue
            side = self._beyond(session, position, params)
            if side is not None and side != state:
                out[position] = side
            state = side
        return out

    def _add(self, signals, session, position, side, stop, params):
        """Bracket and record one entry, if the trend gate lets it through."""
        if stop is None or stop <= 0 or not self._trend_allows(session, side, params):
            return False
        trail = float(params["trail_frac"])
        signals.append(Signal(session["index"][position + 1], side, stop,
                              stop * float(params["rr"]),
                              int(params["time_stop"]),
                              stop * trail if trail > 0 else None))
        return True

    # -- the three models --------------------------------------------------

    def _ivb1(self, session, params, signals):
        """Breakout, invalidated at the opening range's value-area edge."""
        area = value_area(profile(session["opening"], float(params["bin_size"])),
                          float(params["value_area"]), float(params["bin_size"]))
        if area is None:
            return
        val, vah = area
        cap = self._risk(session, params)
        if cap is None:
            return
        floor = cap * float(params["min_stop_frac"])

        taken = 0
        for position, side in sorted(self._breaks(session, params).items()):
            if taken >= int(params["max_trades"]):
                return
            if not self._tradable(session, position, params):
                continue
            if self._absorbed(session, position, params):
                continue
            if not self._book_agrees(session, position, side, params):
                continue
            # Measured from the decision close, the last price known when the
            # signal is formed. The engine fills at the next open, so the stop
            # travels with the gap rather than being re-derived from a price
            # the strategy could not have seen.
            reference = session["bars"][position][C]
            distance = reference - val if side == LONG else vah - reference
            if self._add(signals, session, position, side,
                         min(cap, max(floor, distance)), params):
                taken += 1

    def _ivb2(self, session, params, signals):
        """Breakout, then aggression that follows through, then entry.

        The break arms the session; confirmation is a later minute inside
        `confirm_window` whose cumulative delta prints a new session extreme
        while price is still holding the broken level. A break that cannot
        produce one within the window is exactly the failed breakout the model
        exists to skip, so the arming lapses.
        """
        breaks = self._breaks(session, params)
        risk = self._risk(session, params)
        taken = 0
        armed = None            # (side, level, expiry position)
        cvd_high = cvd_low = None
        for position in range(len(session["bars"])):
            cvd = session["cvd"][position]
            new_high = cvd_high is None or cvd > cvd_high
            new_low = cvd_low is None or cvd < cvd_low
            cvd_high = cvd if cvd_high is None else max(cvd_high, cvd)
            cvd_low = cvd if cvd_low is None else min(cvd_low, cvd)

            if armed is not None and position > armed[2]:
                armed = None
            side = breaks.get(position)
            if side is not None:
                level = session["high"] if side == LONG else session["low"]
                armed = (side, level, position + int(params["confirm_window"]))
            if armed is None or taken >= int(params["max_trades"]):
                continue
            if not self._tradable(session, position, params):
                continue

            side, level, _expiry = armed
            bar = session["bars"][position]
            tolerance = float(params["hold_tolerance"])
            holding = (bar[L] >= level - tolerance if side == LONG
                       else bar[H] <= level + tolerance)
            aggressive = new_high if side == LONG else new_low
            if not (holding and aggressive):
                continue
            if self._absorbed(session, position, params):
                continue
            if not self._book_agrees(session, position, side, params):
                continue
            if self._add(signals, session, position, side, risk, params):
                taken += 1
                armed = None

    def _exhaustion(self, session, params, signals):
        """Fade a new session extreme that cumulative delta refused to confirm.

        The comparison is against the last extreme *and the CVD that made it*,
        so the divergence is measured between two comparable moments rather
        than against a running maximum the price never revisited.
        """
        risk = self._risk(session, params)
        taken = 0
        low = high = None
        cvd_at_low = cvd_at_high = None
        min_break = float(params["min_break"])
        min_divergence = float(params["min_divergence"])
        cap = int(params["max_trades"])

        for position in range(len(session["bars"])):
            bar = session["bars"][position]
            cvd = session["cvd"][position]
            allowed = self._tradable(session, position, params)

            if low is not None and bar[L] < low - min_break:
                # Sellers took a new low but gave up ground on delta doing it.
                if (allowed and taken < cap and cvd - cvd_at_low > min_divergence
                        and self._book_agrees(session, position, LONG, params)
                        and self._add(signals, session, position, LONG, risk, params)):
                    taken += 1
                low, cvd_at_low = bar[L], cvd
            elif low is None or bar[L] < low:
                low, cvd_at_low = bar[L], cvd

            if high is not None and bar[H] > high + min_break:
                if (allowed and taken < cap and cvd_at_high - cvd > min_divergence
                        and self._book_agrees(session, position, SHORT, params)
                        and self._add(signals, session, position, SHORT, risk, params)):
                    taken += 1
                high, cvd_at_high = bar[H], cvd
            elif high is None or bar[H] > high:
                high, cvd_at_high = bar[H], cvd
