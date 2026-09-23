"""Trader Dale: the Volume Accumulation / heavy-volume-zone pullback.

Dale's published setups all rest on one observation: a rotation where heavy
volume built is a zone institutions accumulated in, and after price leaves that
zone with a trend, the *beginning* of the zone — the edge price left from — is
where he wants to trade the pullback, in the direction of the trend.

His three named setups (Accumulation, Rejection, Trend) differ mainly in how
the zone is found; the entry is the same shape in each, which is what this
implements:

  1. Build a profile over a completed lookback window that ended `gap` minutes
     ago. Waiting for the gap is what makes it an *established* zone rather
     than one still forming around the current price.
  2. Take the value area of that profile as the heavy-volume zone.
  3. Require price to have left the zone — above it for longs, below for
     shorts. That is the "strong trend activity" leg.
  4. Wait for a pullback that touches the near edge, and enter there.
  5. Invalidation is the far edge: if price is accepted back *through* the
     zone, the reason for the trade is gone. The stop is therefore the zone's
     own width plus a buffer, not a fixed number of points.

Because the stop is the zone width, a wide zone means a wide stop and a smaller
position — `max_stop` caps how wide a zone the strategy will still trade.
"""
from sandbox import data, metrics, volume_profile
from sandbox.data import C, D, H, L, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN = 9 * 60 + 30
LAST_ENTRY = 15 * 60
EXIT_MIN = 16 * 60


@register
class VolumeAccumulation(Strategy):
    """Pull back into an established heavy-volume zone, with the trend."""

    name = "Volume Accumulation Pullback"
    bars = "level_two"
    execution = Execution(initial=1_000.0, spread=0.2, session_end_min=EXIT_MIN)

    defaults = {
        "lookback": 60,        # minutes of profile that define the zone
        "gap": 15,             # minutes between the zone and the decision
        "zone_share": 0.60,    # share of the window's volume the zone holds
        "bin_size": 1.0,
        "min_departure": 5.0,  # how far beyond the zone the trend must have gone
        "touch_tolerance": 1.0,
        "stop_buffer": 2.0,
        "min_stop": 4.0,
        "max_stop": 40.0,
        "rr": 2.0,
        "trail_frac": 0.0,
        "time_stop": 120,
        "max_trades": 2,
        "entry_to": LAST_ENTRY,
        "skip_days": "",
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "lookback": [30, 60, 90],
        "zone_share": [0.5, 0.6, 0.7],
        "rr": [1.5, 2.0, 3.0],
    }

    def valid(self, params):
        return (params["lookback"] > 0
                and params["gap"] >= 0
                and 0 < params["zone_share"] <= 1
                and params["bin_size"] > 0
                and params["min_stop"] > 0
                and params["max_stop"] >= params["min_stop"]
                and params["time_stop"] > 0
                and params["max_trades"] >= 1
                and (params["rr"] > 0 or params["trail_frac"] > 0)
                and params["entry_to"] <= LAST_ENTRY)

    def context(self):
        return {"features": data.load_l2_features(self.symbol)}

    def signals(self, bars, context, group, params):
        if group != "all" or not self.valid(params):
            return []

        features = (context or {}).get("features", {})
        skip = self._skip_set(params["skip_days"])
        from_ts = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        to_ts = metrics.split_ts(params["to_date"]) if params["to_date"] else None
        lookback, gap = int(params["lookback"]), int(params["gap"])
        bin_size = float(params["bin_size"])

        signals = []
        day = None
        session = []          # (bar, volume) for the current session, in order
        taken = 0
        for index, bar in enumerate(bars):
            ts = int(bar[TS])
            current = ts // 86_400
            if current != day:
                day, session, taken = current, [], 0
            feature = features.get(ts)
            session.append((bar, self._volume(bar, feature)))

            if taken >= int(params["max_trades"]):
                continue
            # The window must be complete *and* separated from this bar by the
            # gap, so the zone is never built from the move being traded.
            end = len(session) - 1 - gap
            if end < lookback:
                continue
            if from_ts is not None and ts < from_ts:
                continue
            if to_ts is not None and ts >= to_ts:
                continue
            if (current + 3) % 7 in skip:
                continue
            if index + 1 >= len(bars) or bars[index + 1][TS] // 86_400 != current:
                continue
            if (bars[index + 1][TS] % 86_400) // 60 > int(params["entry_to"]):
                continue

            zone = volume_profile.value_area(
                volume_profile.profile(session[end - lookback:end], bin_size),
                float(params["zone_share"]), bin_size)
            if zone is None:
                continue
            signal = self._entry(bar, index, zone, params)
            if signal is not None:
                signals.append(signal)
                taken += 1
        return signals

    def _entry(self, bar, index, zone, params):
        """Enter on a pullback that touches the near edge from the trend side."""
        low, high = zone
        width = high - low
        # A zone narrower than the noise floor is widened to it; one too wide
        # to risk is refused outright, because widening is a judgement about
        # noise and narrowing would be a judgement about the setup.
        stop = max(float(params["min_stop"]), width + float(params["stop_buffer"]))
        if stop > float(params["max_stop"]):
            return None

        departure = float(params["min_departure"])
        tolerance = float(params["touch_tolerance"])
        trail = float(params["trail_frac"])
        close = bar[C]

        # Long: price is trending above the zone and this bar dipped back to
        # its upper edge. Short is the mirror. A bar that closes back *inside*
        # the zone is acceptance, not a pullback, so it is not an entry.
        if close > high + departure and bar[L] <= high + tolerance:
            side, risk = LONG, stop
        elif close < low - departure and bar[H] >= low - tolerance:
            side, risk = SHORT, stop
        else:
            return None

        return Signal(index + 1, side, risk, risk * float(params["rr"]),
                      int(params["time_stop"]),
                      risk * trail if trail > 0 else None)

    @staticmethod
    def _volume(bar, feature):
        if feature:
            volume = (feature.get("aggressive_buy_volume", 0.0)
                      + feature.get("aggressive_sell_volume", 0.0))
            if volume > 0:
                return volume
        return abs(bar[D])

    @staticmethod
    def _skip_set(value):
        if not value:
            return set()
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
        return {names[item.strip().lower()] for item in str(value).split(",")
                if item.strip().lower() in names}
