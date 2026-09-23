"""Volume-profile key levels, point in time. OPTIMIZATION_PLAN.md Stage 5.

The hypothesis, written down before it was tested, as Stage 5 requires:

    Absorption is passive size consuming aggression. Where no resting size has
    accumulated, there is nothing behind the passive side and price drifts
    through; where a level has already attracted volume, the passive side has a
    reason to defend it and the reversal has room to develop. So a fade should
    only be taken near a level the market has already agreed matters.

This is the standard order-flow construction -- levels first from the volume
profile, timing second from the book -- and it is a claim about *entries*, not
about exits. That distinction is why it survives the stop-width experiment:
widening the bracket rescued trades from the stop and they lost anyway, which
says the entries were wrong rather than early. This module is about not taking
them.

Levels are built from the *previous* session's profile, so every reading is
known at the open of the day it is used on. `dbento_nq_ticks` ends 2026-07-16
while traded bars run to 07-23; the last few sessions therefore carry the last
available profile rather than a fresh one, and the filter fails **open** when no
profile exists -- an absent level is not evidence of a bad location.

The proximity threshold is expressed in ATR units and swept over a range rather
than fitted, following the `sma_gate` precedent: the finding is only adopted if
it holds across the range, not at one value.
"""
from sandbox import data

#: Fraction of session volume inside the value area. 70% is the Market Profile
#: convention (one standard deviation); it is not a tuned number and is not
#: swept -- moving it would be fitting the level definition to the result.
VALUE_AREA_FRACTION = 0.70


def session_profiles(symbol="nq", use_cache=True):
    """`{day: (poc, vah, val, high, low)}` per session, from traded volume.

    Prices are bucketed to one point, which is four NQ ticks -- fine enough to
    locate a level and coarse enough that a single large print cannot become
    one. Volume is `size` from the tick table, matching what a footprint chart
    would show rather than depth-book quantity.
    """
    sql = ("SELECT cast(timestamp_floor('d',timestamp) as long) day,"
           "cast(price as long) px, sum(size) vol "
           f"FROM dbento_{symbol}_ticks WHERE size>0 GROUP BY day,px ORDER BY day,px")

    def build():
        return [[int(r[0]) // 1_000_000_000 // 86_400, int(r[1]), float(r[2])]
                for r in data.query(sql)]

    rows = (data._cached(f"{symbol}_profile", f"vp:{sql}:"
                         + data._table_fingerprint([f"dbento_{symbol}_ticks"]), build)
            if use_cache else build())

    by_day = {}
    for day, px, vol in rows:
        by_day.setdefault(day, []).append((px, vol))

    out = {}
    for day, buckets in by_day.items():
        buckets.sort()
        total = sum(v for _, v in buckets)
        if total <= 0:
            continue
        poc_index = max(range(len(buckets)), key=lambda i: buckets[i][1])
        # Value area: walk outward from the POC, always taking the richer side,
        # until 70% of volume is enclosed. This is the standard construction.
        lo = hi = poc_index
        covered = buckets[poc_index][1]
        while covered < VALUE_AREA_FRACTION * total and (lo > 0 or hi < len(buckets) - 1):
            below = buckets[lo - 1][1] if lo > 0 else -1.0
            above = buckets[hi + 1][1] if hi < len(buckets) - 1 else -1.0
            if above >= below:
                hi += 1
                covered += buckets[hi][1]
            else:
                lo -= 1
                covered += buckets[lo][1]
        out[day] = (float(buckets[poc_index][0]), float(buckets[hi][0]),
                    float(buckets[lo][0]), float(buckets[-1][0]), float(buckets[0][0]))
    return out


def prior_levels(profiles):
    """`{day: [levels]}` using the most recent session that closed *before* `day`.

    Carrying the last available profile forward is deliberate: the alternative
    is dropping the entry, which changes the trade population instead of
    measuring the location of the trades that exist.
    """
    days = sorted(profiles)
    out, previous = {}, None
    for day in days:
        if previous is not None:
            out[day] = list(profiles[previous])
        previous = day
    return out, days


def distance(levels, price):
    """Points from `price` to the nearest level, or None when there are none."""
    return min((abs(price - level) for level in levels), default=None)
