"""S1 prior-session value-area reaction event construction.

Pre-declared before inspecting strategy PnL:

* profile: completed 09:30--15:59 New York traded volume, one-point buckets,
  with the conventional 70% value area;
* signal: first VAH touch from below is a short fade and first VAL touch from
  above is a long fade;
* timing: the touch minute must close before entry, so entry is the next
  contiguous minute's open;
* primary effect: signed 30-minute forward move, net of one 0.20-point spread;
* evidence: globally non-overlapping forward windows, not one sample per bar.

This module intentionally does not register a sweepable Strategy. S1's
pre-declared kill criterion is evaluated before a parameter grid exists. A
failed effect must not become a search for a profitable definition.
"""

from dataclasses import dataclass

from sandbox.data import C, H, L, O, TS
from sandbox.execution import LONG, SHORT, Fill

RTH_FROM = 570
RTH_TO = 959
VALUE_AREA_FRACTION = 0.70
PRIMARY_HORIZON = 30
REFERENCE_SPREAD = 0.20


@dataclass(frozen=True)
class Touch:
    """One causal first touch with the completed prior profile attached."""

    touch_index: int
    entry_index: int
    entry_ts: int
    side: str
    level_name: str
    level: float
    poc: float
    vah: float
    val: float


@dataclass(frozen=True)
class Observation:
    """A touch paired with its fixed-horizon, per-unit outcome."""

    touch: Touch
    fill: Fill


def profiles_from_rows(rows, fraction=VALUE_AREA_FRACTION):
    """Build ``{day: (poc, vah, val, high, low)}`` from cached profile rows."""
    by_day = {}
    for day, price, volume in rows:
        by_day.setdefault(int(day), []).append((float(price), float(volume)))

    profiles = {}
    for day, buckets in by_day.items():
        buckets.sort()
        total = sum(volume for _, volume in buckets)
        if total <= 0.0:
            continue
        poc_index = max(range(len(buckets)), key=lambda i: buckets[i][1])
        lo = hi = poc_index
        covered = buckets[poc_index][1]
        while covered < fraction * total and (
            lo > 0 or hi < len(buckets) - 1
        ):
            below = buckets[lo - 1][1] if lo > 0 else -1.0
            above = (
                buckets[hi + 1][1] if hi < len(buckets) - 1 else -1.0
            )
            if above >= below:
                hi += 1
                covered += buckets[hi][1]
            else:
                lo -= 1
                covered += buckets[lo][1]
        profiles[day] = (
            buckets[poc_index][0],
            buckets[hi][0],
            buckets[lo][0],
            buckets[-1][0],
            buckets[0][0],
        )
    return profiles


def prior_profiles_for_bars(bars, profiles):
    """Map a bar session to its immediately preceding observed session.

    The profile feed ends before the live bar feed. Carrying its last value
    over several later sessions would call a stale profile "prior-session".
    This mapping therefore opens only when the immediately preceding bar
    session has a completed profile. It permits the first live day after the
    historical feed and fails closed thereafter.
    """
    sessions = sorted({int(bar[TS]) // 86_400 for bar in bars})
    return {
        sessions[index]: profiles[sessions[index - 1]]
        for index in range(1, len(sessions))
        if sessions[index - 1] in profiles
    }


def first_touches(bars, prior):
    """Return first VAH/VAL touches, before any horizon-based de-overlap."""
    out = []
    state_day = None
    previous_close = None
    used = {"vah": False, "val": False}

    for index, bar in enumerate(bars):
        day = int(bar[TS]) // 86_400
        minute = (int(bar[TS]) % 86_400) // 60
        if day != state_day:
            state_day = day
            previous_close = float(bar[O])
            used = {"vah": False, "val": False}

        if day not in prior or not RTH_FROM <= minute <= RTH_TO:
            previous_close = float(bar[C])
            continue

        poc, vah, val, _high, _low = prior[day]
        crossed_vah = (
            not used["vah"]
            and previous_close < vah
            and float(bar[H]) >= vah
        )
        crossed_val = (
            not used["val"]
            and previous_close > val
            and float(bar[L]) <= val
        )

        # A one-minute bar spanning both edges has no observable touch order.
        # Mark both used and discard it instead of choosing the favourable leg.
        if crossed_vah and crossed_val:
            used["vah"] = used["val"] = True
            previous_close = float(bar[C])
            continue

        for crossed, name, level, side in (
            (crossed_vah, "vah", vah, SHORT),
            (crossed_val, "val", val, LONG),
        ):
            if not crossed:
                continue
            used[name] = True
            entry_index = index + 1
            if entry_index >= len(bars):
                continue
            entry = bars[entry_index]
            if (
                int(entry[TS]) != int(bar[TS]) + 60
                or int(entry[TS]) // 86_400 != day
            ):
                continue
            out.append(
                Touch(
                    touch_index=index,
                    entry_index=entry_index,
                    entry_ts=int(entry[TS]),
                    side=side,
                    level_name=name,
                    level=float(level),
                    poc=float(poc),
                    vah=float(vah),
                    val=float(val),
                )
            )
        previous_close = float(bar[C])
    return out


def fixed_horizon_observations(
    bars,
    touches,
    horizon=PRIMARY_HORIZON,
    spread=REFERENCE_SPREAD,
):
    """Resolve touches and keep globally non-overlapping observation windows."""
    candidates = []
    for touch in touches:
        exit_index = touch.entry_index + horizon
        if exit_index >= len(bars):
            continue
        entry = bars[touch.entry_index]
        exit_bar = bars[exit_index]
        if int(exit_bar[TS]) // 86_400 != int(entry[TS]) // 86_400:
            continue
        entry_price = float(entry[O])
        exit_price = float(exit_bar[O])
        points = (
            exit_price - entry_price - spread
            if touch.side == LONG
            else entry_price - exit_price - spread
        )
        candidates.append(
            Observation(
                touch,
                Fill(
                    entry_ts=int(entry[TS]),
                    exit_ts=int(exit_bar[TS]),
                    side=touch.side,
                    points=points,
                    price=entry_price,
                    # Not used for exits. A fixed-horizon effect has no
                    # defensible risk stop until it clears the kill test.
                    stop=1.0,
                ),
            )
        )

    kept = []
    free_at = -1
    for row in sorted(candidates, key=lambda item: item.fill.entry_ts):
        if row.fill.entry_ts < free_at:
            continue
        kept.append(row)
        free_at = row.fill.exit_ts
    return kept
