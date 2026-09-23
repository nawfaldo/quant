"""Chris Creamer's Robbins Cup order-flow setup, ported to NQ level-two minutes.

Source: "Trading WORLD CHAMPION Reveals the Orderflow Strategy That Won the
Robbins Cup (Step-by-Step)" (youtube PL7LKUsCgIQ). The talk describes a
four-step discretionary process -- Environment, Location, Confirmation, Risk --
and this is the literal transcription of it that a minute-bar engine can run.

    Environment    value orientation -- today's developing point of control
                   against yesterday's value area -- plus a volatility regime
    Location       a Fibonacci pullback into 0.705-0.886 of the session's live
                   swing, sitting outside a value area (see `_location` -- the
                   talk's wording admits two opposite readings and both are here)
    Confirmation   absorption at the pullback extreme -- heavy one-sided
                   aggression that fails to move price -- then a delta flip
    Risk           stop beyond the failed aggressor's extreme, hard-invalidated
                   at 0.886; target 1.5R, the developing POC, or the swing

TWO VALUE AREAS, doing two jobs. The prior session's sets the day's orientation
(`structure`); either one can gate the pullback's location (`location`). Reading
"discount below the value area" against the prior session while also requiring a
value-up day is self-contradictory -- a long would need the day trading above
yesterday's value and a retracement below it at once -- and over 376 sessions
that conjunction produced exactly zero setups. That is why `_location` carries
two readings instead of one.

WHAT IS NOT A FAITHFUL PORT, and why. Three things.

GEX IS NOT IMPLEMENTED. The talk's Environment step reads dealer gamma off
Tanuki Trade and uses positive gamma to mean dampened volatility (fade
breakouts) and negative gamma to mean amplified volatility. This repository's
The GEX vendor cache holds four dates (2026-07-20..2026-07-24) against 376 sessions
of level-two bars, so gamma cannot be backtested here at all. `regime` stands in
for it with the observable it was being used to predict: the prior session's
5-minute ATR against its trailing 20-session median. That is a proxy for the
*consequence* of gamma, not for gamma, and it is a weaker filter than the real
thing -- it is measured after the fact rather than published in advance.

THE INSTRUMENT IS NQ, NOT MNQ. The talk's participation floor is 20,000
contracts per 5-minute MNQ candle. Micro and mini contract counts are not one
scale, so `participation_min` (a raw contract floor) defaults to 0 and the gate
runs on `participation_mult` instead -- 5-minute volume against the trailing
20-session median for the same minute of day. Set `participation_min` if you
want the literal rule.

THE SWING IS MECHANICAL. "Internal swing structure" is a discretionary read.
Here the leg is the session's running low and running high, directed by whichever
came *last*: high after low is an up-leg and only longs are considered, low after
high is a down-leg and only shorts are. A pullback deep enough to set a new
session extreme re-anchors the leg, which is the mechanical form of the talk's
0.886 invalidation.

TWO NORMALISATION WARNINGS travel with this file. `aggression` is z-scored
against the same minute of day over the previous 20 sessions, and the
participation reference is built the same way -- both are trailing-normalised,
so both change meaning at 2026-07-17 where the bar source switches from
Databento to Bookmap and the two feeds do not net the same delta
([[bookmap-and-databento-are-not-one-scale]]). Session-local normalisation is
NOT the safe alternative: it inverted the sign of the absorption edge in
`absorption_reversal` ([[absorption-edge-is-regime-not-book]]).
"""
from sandbox import data
from sandbox import metrics
from sandbox import volume_profile
from sandbox.data import C, H, L, O, TS
from sandbox.execution import Execution, LONG, SHORT, Signal
from sandbox.strategies.base import Strategy, register

OPEN_MIN, CLOSE_MIN = 570, 960          # 09:30 .. 16:00
#: "the first hour and a half of the New York open" -- the talk's own window.
FIRST_ENTRY, LAST_ENTRY = 570, 660      # 09:30 .. 11:00
EXIT_MIN = 945                          # 15:45 session flatten
NORM_SESSIONS = 20
ATR_MINUTES = 5
ATR_SAMPLES = 10
PARTICIPATION_MINUTES = 5
MAX_WINDOW = 5
#: the talk names 0.705, 0.788 and 0.886; the zone is the outer pair and 0.886
#: is the invalidation, exactly as stated ("0.886 is critical").
FIB_ENTRY, FIB_INVALIDATE = 0.705, 0.886
VALUE_AREA = 0.70

#: Rust's default `OrderFlowFeatures` for a bar with no matching feature row.
#: A featureless minute still shifts every window; it just cannot signal.
NO_FEATURES = {"trade_delta": 0.0, "price_change": 0.0, "spread": 0.0,
               "aggressive_buy_volume": 0.0, "aggressive_sell_volume": 0.0,
               "book_valid": False}

#: Gate order, for `CreamerOrderflow.funnel`. `bar` counts every bar walked;
#: `candidate` is the ones inside the entry window with a usable context row.
STAGES = ("bar", "candidate", "environment", "leg", "zone", "location", "spread",
          "aggression", "absorption", "flip", "participation", "risk", "signal")

#: A developing value area built from three minutes of trade is not a value
#: area. Entries therefore cannot start before 09:30 + this.
MIN_DEVELOPING_MINUTES = 15

#: base-row fields, by position. TWO value areas, and they do different jobs:
#: the PRIOR session's sets the day's orientation (value up / down), the
#: DEVELOPING one says whether the pullback is at a discount right now.
(R_ATR, R_VOL5, R_VOL_REF, R_SPREAD, R_REGIME,
 R_PRIOR_VAL, R_PRIOR_VAH, R_PRIOR_POC,
 R_DEV_VAL, R_DEV_VAH, R_DEV_POC,
 R_LEG_SIDE, R_LEG_FROM, R_LEG_TO, R_DELTA) = range(15)


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def fib_levels(leg_from, leg_to):
    """`(entry_level, invalidation_level)` for a leg running `leg_from -> leg_to`.

    Retracements are measured back from the leg's end, so an up-leg's levels sit
    below its high and a down-leg's above its low. The sign of `span` carries the
    direction, which is why one expression covers both.
    """
    span = leg_to - leg_from
    return leg_to - FIB_ENTRY * span, leg_to - FIB_INVALIDATE * span


@register
class CreamerOrderflow(Strategy):
    name = "Creamer Orderflow Pullback"
    bars = "level_two"
    #: Forex/CFD account -- `point_value` 1.0, 0.01 lot step, 25% margin. Costs
    #: are left at the class defaults here and repriced by the caller;
    #: `research/creamer_orderflow_research.py` charges `combined_book`'s Exness
    #: Pro entry spread rather than these.
    execution = Execution(session_end_min=EXIT_MIN, risk=0.005, leverage=1.0)

    defaults = {
        # --- Environment -----------------------------------------------------
        #: "any" | "dampened" | "amplified" -- see the GEX note in the module
        #: docstring. This is a volatility proxy, not gamma.
        "regime": "any",
        #: prior-session value orientation required at the open.
        #: "none" | "va" (open outside the prior value area, in the trade's
        #: direction) | "poc" (open on the trade's side of the prior POC)
        "structure": "va",
        "bin_size": 5.0,
        # --- Location --------------------------------------------------------
        #: the impulse must be a real one, in multiples of the 5-minute ATR
        "min_leg_atr": 2.0,
        #: how long a zone touch stays live while waiting for confirmation
        "zone_window": 10,
        #: "holds_prior" | "below_developing" | "none" -- see `_location`
        "location": "holds_prior",
        # --- Confirmation ----------------------------------------------------
        "window": 3,
        "aggression_z": 1.5,
        #: absorption is aggression that does NOT move price: the window's net
        #: move must stay inside this fraction of the ATR
        "move_fraction": 0.55,
        #: which flip the entry bar must show: "delta" | "candle" | "both" | "none"
        "flip": "both",
        "max_spread": 1.5,
        # --- Participation ---------------------------------------------------
        "participation_mult": 1.0,
        "participation_min": 0,
        # --- Risk ------------------------------------------------------------
        #: "rr" | "poc" | "swing" -- 1.5R, the developing POC, or the leg's end
        "target_mode": "rr",
        "rr": 1.5,
        "stop_buffer": 2.0,
        "min_stop": 4.0,
        "max_stop": 40.0,
        #: trailing stop as a multiple of the initial risk; 0 disables it
        "trail_r": 0.0,
        "time_stop": 45,
        "max_entries": 2,
        "entry_from": FIRST_ENTRY,
        "entry_to": LAST_ENTRY,
        "from_date": None,
        "to_date": None,
    }

    grid = {
        "window": [2, 3, 5],
        "aggression_z": [1.0, 1.5, 2.0],
        "move_fraction": [0.40, 0.55, 0.75],
        "min_leg_atr": [1.0, 2.0, 3.0],
        "zone_window": [5, 10, 20],
        "rr": [1.5, 2.0],
        "target_mode": ["rr", "poc", "swing"],
        "structure": ["none", "va"],
        "regime": ["any", "dampened", "amplified"],
        "flip": ["delta", "candle", "both"],
        "location": ["holds_prior", "below_developing", "none"],
    }

    def valid(self, params):
        return params["min_stop"] <= params["max_stop"]

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        base = self._base_rows(bars, features)
        return {"base": base,
                "aggression": {w: self._aggression(bars, features, w)
                               for w in range(1, MAX_WINDOW + 1)}}

    # ------------------------------------------------------------------ #
    # context construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _base_rows(bars, features):
        """Window-independent per-bar state: value area, regime, leg, volume.

        One pass. A session's profile is finalised when the day rolls over and
        becomes the *next* session's reference, so nothing here reads a bar that
        had not printed by the time the decision is made.
        """
        rows = [None] * len(bars)
        session_atrs = []           # prior sessions' mean 5-minute range
        vol_rings = {}              # minute-of-day -> prior sessions' 5m volume
        bins = {}                   # developing profile of the session in progress
        prior = None                # (val, vah, poc) from the previous session
        regime = None               # prior ATR / trailing median
        day = None
        minutes = 0                 # RTH minutes elapsed in this session
        recent = []                 # [(high, low)] for the ATR
        volumes = []                # [minute volume] for participation
        atr_samples = []            # trailing 10, the live ATR
        session_ranges = []         # every 5-minute range of the session
        low = high = None           # (price, index) extremes since the open
        previous_minute = None

        def area_of(profile):
            """`(val, vah, poc)` or None for a profile with no usable area."""
            area = volume_profile.value_area(profile, VALUE_AREA, bin_size)
            poc = volume_profile.point_of_control(profile, bin_size)
            return (area[0], area[1], poc) if area and poc else None

        def close_session():
            nonlocal prior, regime
            prior = area_of(bins) if bins else None
            current = _mean(session_ranges)
            reference = _median(session_atrs[-NORM_SESSIONS:])
            regime = (current / reference) if current > 0 and reference > 0 else None
            if current > 0:
                session_atrs.append(current)

        #: The profile bin is fixed rather than swept: `bin_size` is a
        #: parameter of the *setup*, but the profile has to be built before any
        #: parameter is known, and re-binning per cell would rebuild 376
        #: sessions per sweep step. 5 points is one NQ handle-and-a-quarter,
        #: fine enough that the value area is not quantised into the tick grid.
        bin_size = CreamerOrderflow.defaults["bin_size"]

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if ts // 86_400 != day:
                if day is not None:
                    close_session()
                day = ts // 86_400
                bins, recent, volumes, minutes = {}, [], [], 0
                atr_samples, session_ranges = [], []
                low, high, previous_minute = None, None, None
            if not (OPEN_MIN <= minute < CLOSE_MIN):
                continue

            feature = features.get(ts, NO_FEATURES)
            volume = (feature["aggressive_buy_volume"]
                      + feature["aggressive_sell_volume"])
            # The bar has CLOSED by the time this row is read -- the fill is at
            # the next bar's open -- so it belongs in the developing profile the
            # decision sees. Leaving it out would also leave the last bar of the
            # session out of the profile `close_session` hands to the next day.
            for index, share in volume_profile.profile([(bar, volume)],
                                                       bin_size).items():
                bins[index] = bins.get(index, 0.0) + share
            minutes += 1

            # A minute gap breaks every rolling window: the bars either side of
            # it are not five minutes apart just because they are adjacent.
            if previous_minute is not None and previous_minute + 1 != minute:
                recent, volumes, atr_samples = [], [], []
            previous_minute = minute

            if low is None or bar[L] < low[0]:
                low = (bar[L], i)
            if high is None or bar[H] > high[0]:
                high = (bar[H], i)

            recent.append((bar[H], bar[L]))
            del recent[:-ATR_MINUTES]
            volumes.append(volume)
            del volumes[:-PARTICIPATION_MINUTES]
            if len(recent) >= ATR_MINUTES:
                span = max(m[0] for m in recent) - min(m[1] for m in recent)
                atr_samples.append(span)
                session_ranges.append(span)
                del atr_samples[:-ATR_SAMPLES]

            slot = minute - OPEN_MIN
            ring = vol_rings.setdefault(slot, [])
            vol5 = sum(volumes) if len(volumes) >= PARTICIPATION_MINUTES else None
            vol_ref = _median(ring) if len(ring) >= NORM_SESSIONS else None

            developing = (area_of(bins) if minutes > MIN_DEVELOPING_MINUTES
                          else None)
            if (prior is not None and developing is not None
                    and len(atr_samples) >= ATR_SAMPLES
                    and vol5 is not None and low is not None and high is not None):
                # The extreme that printed LAST directs the leg.
                if high[1] > low[1]:
                    leg = ("up", low[0], high[0])
                elif low[1] > high[1]:
                    leg = ("down", high[0], low[0])
                else:
                    leg = None      # both extremes on the opening bar
                if leg is not None:
                    rows[i] = (_mean(atr_samples), vol5, vol_ref,
                               feature["spread"] if feature["book_valid"] else None,
                               regime, prior[0], prior[1], prior[2],
                               developing[0], developing[1], developing[2],
                               leg[0], leg[1], leg[2], feature["trade_delta"])

            # recorded last, so a bar never enters its own reference
            if vol5 is not None:
                ring.append(vol5)
                del ring[:-NORM_SESSIONS]

        return rows

    @staticmethod
    def _aggression(bars, features, window):
        """`[(z, net_move) or None]`: window delta, z-scored, and its net move.

        Same construction as `absorption_reversal._statistics` -- a per
        minute-of-day ring over the previous 20 sessions -- because a raw delta
        threshold does not survive this sample's volatility regimes.
        """
        out = [None] * len(bars)
        rings = {}
        recent = []
        day = None
        previous_minute = None

        for i, bar in enumerate(bars):
            ts = bar[TS]
            minute = (ts % 86_400) // 60
            if ts // 86_400 != day:
                day, recent, previous_minute = ts // 86_400, [], None
            if not (OPEN_MIN <= minute < CLOSE_MIN):
                continue
            feature = features.get(ts, NO_FEATURES)
            if previous_minute is not None and previous_minute + 1 != minute:
                recent = []
            previous_minute = minute
            recent.append((feature["trade_delta"], feature["price_change"]))
            del recent[:-window]
            if len(recent) < window:
                continue
            aggression = sum(m[0] for m in recent)
            slot = minute - OPEN_MIN
            ring = rings.setdefault(slot, [])
            if len(ring) >= NORM_SESSIONS:
                mean = _mean(ring)
                sd = (sum((x - mean) ** 2 for x in ring) / len(ring)) ** 0.5
                if sd > 0:
                    out[i] = ((aggression - mean) / sd,
                              sum(m[1] for m in recent))
            ring.append(aggression)
            del ring[:-NORM_SESSIONS]
        return out

    # ------------------------------------------------------------------ #
    # signals
    # ------------------------------------------------------------------ #
    def signals(self, bars, context, group, params, trace=None):
        base = context["base"]
        aggression = context["aggression"][params["window"]]
        window = params["window"]
        # Signals are generated over the whole history so the 20-session warm-up
        # comes from bars before the evaluated window, then filtered by date.
        start = metrics.split_ts(params["from_date"]) if params["from_date"] else None
        end = metrics.split_ts(params["to_date"]) + 86_400 if params["to_date"] else None

        out = []
        entries_today = 0
        day = None
        for i, row in enumerate(base):
            if trace is not None:
                trace["bar"] = trace.get("bar", 0) + 1
            if row is None or aggression[i] is None:
                continue
            ts = bars[i][TS]
            if ts // 86_400 != day:
                day, entries_today = ts // 86_400, 0
            if not params["entry_from"] <= (ts % 86_400) // 60 <= params["entry_to"]:
                continue
            if entries_today >= params["max_entries"]:
                continue
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            if nxt is None or nxt[TS] != ts + 60:
                continue        # fills happen on the contiguous next minute only
            if start is not None and nxt[TS] < start:
                continue
            if end is not None and nxt[TS] >= end:
                continue

            side = LONG if row[R_LEG_SIDE] == "up" else SHORT
            signal = self._signal(bars, base, aggression, i, side, window, params,
                                  trace)
            if signal is not None:
                entries_today += 1
                out.append(signal)
        return out

    def funnel(self, bars, context, params):
        """`[(stage, bars still alive)]` for one parameter set, in gate order.

        Reports which of the talk's four steps is actually binding. It runs
        `signals` rather than re-stating the rules, so the funnel and the
        strategy cannot drift apart -- a re-implementation would only be
        measuring the copy.
        """
        trace = {}
        self.signals(bars, context, "all", params, trace)
        return [(stage, trace.get(stage, 0)) for stage in STAGES]

    @classmethod
    def _signal(cls, bars, base, aggression, i, side, window, params, trace=None):
        """The four steps, in the talk's order. `None` at the first failure.

        `trace` counts the bars reaching each stage; see `funnel`.
        """
        def reached(stage):
            if trace is not None:
                trace[stage] = trace.get(stage, 0) + 1

        row = base[i]
        atr = row[R_ATR]
        if atr <= 0:
            return None
        sign = 1 if side == LONG else -1
        reached("candidate")

        # --- Environment ------------------------------------------------ #
        if not cls._environment(row, sign, params):
            return None
        reached("environment")

        # --- Location --------------------------------------------------- #
        leg_from, leg_to = row[R_LEG_FROM], row[R_LEG_TO]
        if abs(leg_to - leg_from) < params["min_leg_atr"] * atr:
            return None
        reached("leg")
        entry_level, invalidation = fib_levels(leg_from, leg_to)
        bar = bars[i]
        # Price has to have REACHED the zone at some point in the last
        # `zone_window` minutes, not on the signal bar itself. The touch, the
        # absorption and the flip are three events in sequence in the talk, and
        # a trader who sees the zone tagged then waits several minutes for the
        # flip is following the rule, not breaking it. Collapsing all three onto
        # one minute is what makes them one event: measured on this data, tying
        # the touch to the confirmation window alone costs about two thirds of
        # the qualifying bars.
        pullback = [bars[j][L] if side == LONG else bars[j][H]
                    for j in cls._contiguous(bars, i, params["zone_window"])]
        touched = (min(pullback) <= entry_level if side == LONG
                   else max(pullback) >= entry_level)
        # ...and must not have closed through the 0.886 invalidation.
        if not touched or sign * (bar[C] - invalidation) <= 0:
            return None
        reached("zone")
        if not cls._location(row, entry_level, sign, params):
            return None
        reached("location")

        # --- Confirmation ----------------------------------------------- #
        spread = row[R_SPREAD]
        if spread is None or spread > params["max_spread"]:
            return None
        reached("spread")
        # ABSORPTION IS READ ON THE WINDOW ENDING AT THE PREVIOUS BAR, not on
        # the window ending here. The talk's sequence is two events -- sellers
        # get absorbed, THEN order flow flips -- and bar `i` is the flip. Summing
        # the window through the flip bar makes the flip's own positive delta
        # fight the negative window sum it is supposed to follow, and it does:
        # measured on this data, folding the flip bar in cuts the setup from 75
        # qualifying bars to 26.
        prior_bar = aggression[i - 1] if i > 0 else None
        if prior_bar is None or bars[i - 1][TS] != bar[TS] - 60:
            return None
        z, net_move = prior_bar
        # Absorption: the OPPOSITE side was the aggressor, and it failed.
        if sign * z > -params["aggression_z"]:
            return None
        reached("aggression")
        if abs(net_move) >= params["move_fraction"] * atr:
            return None
        reached("absorption")
        if not cls._flip(row, bar, sign, params):
            return None
        reached("flip")
        if not cls._participation(row, params):
            return None
        reached("participation")

        # --- Risk -------------------------------------------------------- #
        entry = bars[i + 1][O]
        # "Beyond the failed seller's extreme" -- the extreme of the pullback
        # that was just absorbed -- and never nearer than the 0.886, which the
        # talk calls the invalidation.
        extreme = (min(pullback + [invalidation]) if side == LONG
                   else max(pullback + [invalidation]))
        stop_price = extreme - sign * params["stop_buffer"]
        distance = sign * (entry - stop_price)
        if not params["min_stop"] <= distance <= params["max_stop"]:
            return None

        reached("risk")
        target = cls._target(row, entry, sign, distance, params)
        if target <= 0:
            return None
        reached("signal")
        trail = params["trail_r"] * distance if params["trail_r"] else None
        return Signal(i + 1, side, distance, target, params["time_stop"], trail)

    @staticmethod
    def _environment(row, sign, params):
        regime = params["regime"]
        if regime != "any":
            ratio = row[R_REGIME]
            if ratio is None:
                return False
            # Dampened volatility is the positive-gamma analogue (fade, small
            # moves); amplified is the negative-gamma one.
            if (ratio > 1.0) != (regime == "amplified"):
                return False
        structure = params["structure"]
        if structure == "none":
            return True
        # VALUE UP is today's business being done above yesterday's: the
        # developing point of control clear of the prior value area ("va") or of
        # the prior point of control ("poc"). That is the structure a long
        # pullback is bought inside; value down is the mirror for shorts.
        edge = (row[R_PRIOR_VAH] if sign > 0 else row[R_PRIOR_VAL]) \
            if structure == "va" else row[R_PRIOR_POC]
        return sign * (row[R_DEV_POC] - edge) > 0

    @staticmethod
    def _contiguous(bars, i, length):
        """Indices of the up-to-`length` bars ending at `i` with no minute gap.

        A gap is a data hole or a session boundary, and the bars either side of
        one are not a minute apart just because they are adjacent in the list.
        """
        first = i
        while first > 0 and i - first + 1 < length and \
                bars[first - 1][TS] == bars[first][TS] - 60:
            first -= 1
        return range(first, i + 1)

    @staticmethod
    def _location(row, entry_level, sign, params):
        """Where the fib zone must sit relative to a value area.

        THE TALK'S WORDING IS AMBIGUOUS and the two readings are opposites, so
        both are implemented rather than one being chosen silently:

        `holds_prior`   the zone sits on the far side of YESTERDAY'S value area
                        -- a long's pullback holds above the old value instead
                        of falling back into it. This is acceptance higher, and
                        it is the reading consistent with `structure`: a
                        value-up day whose retracement does not give the old
                        area back.
        `below_developing`  the literal "buy the discount below the value area",
                        against TODAY'S developing area. Inside the first ninety
                        minutes the developing area still spans most of the
                        session's range, so a 0.705 retracement is nearly always
                        inside it -- this reading yields a handful of setups
                        across 376 sessions and is kept to be measured, not
                        because it is expected to work.
        `none`          no location filter; the fib zone alone is the location.
        """
        mode = params["location"]
        if mode == "none":
            return True
        if mode == "holds_prior":
            edge = row[R_PRIOR_VAH] if sign > 0 else row[R_PRIOR_VAL]
            return sign * (entry_level - edge) > 0
        edge = row[R_DEV_VAL] if sign > 0 else row[R_DEV_VAH]
        return sign * (entry_level - edge) < 0

    @staticmethod
    def _flip(row, bar, sign, params):
        """The order-flow flip on the entry-signal bar.

        The talk calls it "the order-flow/candle flips bullish", which is two
        observables: signed trade delta and the candle body. `flip` selects
        which one is required -- "delta", "candle", "both" or "none".
        """
        mode = params["flip"]
        if mode == "none":
            return True
        delta_ok = sign * row[R_DELTA] > 0
        candle_ok = sign * (bar[C] - bar[O]) > 0
        if mode == "delta":
            return delta_ok
        if mode == "candle":
            return candle_ok
        return delta_ok and candle_ok

    @staticmethod
    def _participation(row, params):
        vol5 = row[R_VOL5]
        if vol5 < params["participation_min"]:
            return False
        multiple = params["participation_mult"]
        if not multiple:
            return True
        reference = row[R_VOL_REF]
        # No reference yet means the first 20 sessions cannot clear a gate that
        # is defined relative to them; refusing is the conservative reading.
        return reference is not None and vol5 >= multiple * reference

    @staticmethod
    def _target(row, entry, sign, distance, params):
        """Target DISTANCE in points, per `target_mode`.

        The talk names three: an R-multiple, a POC, and the swing the pullback
        came from. `poc` and `swing` fall back to the R-multiple when the level
        is already behind the entry -- a target the trade opens through is not a
        target, and skipping the setup entirely would silently make the mode a
        different filter rather than a different exit.
        """
        mode = params["target_mode"]
        fallback = params["rr"] * distance
        if mode == "rr":
            return fallback
        level = row[R_DEV_POC] if mode == "poc" else row[R_LEG_TO]
        reach = sign * (level - entry)
        return reach if reach > 0 else fallback
