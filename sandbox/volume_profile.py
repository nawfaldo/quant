"""Price-binned volume profiles. Strategy-agnostic.

Minute bars record volume per *minute*, not volume at price, so a profile built
from them spreads each minute's volume evenly across the price bins its range
touches. That is the only honest reconstruction available at this resolution,
and it is enough for what the published setups ask of it: locating the shelf
where an auction did its business, and the edges of the area around it.
"""
import math


def profile(rows, bin_size):
    """`{bin: volume}` for `rows` of `(bar, volume)`, binned on price."""
    from sandbox.data import H, L

    bins = {}
    for bar, volume in rows:
        if volume <= 0:
            continue
        low = int(math.floor(bar[L] / bin_size))
        high = int(math.floor(bar[H] / bin_size))
        share = volume / (high - low + 1)
        for index in range(low, high + 1):
            bins[index] = bins.get(index, 0.0) + share
    return bins


def value_area(bins, fraction, bin_size):
    """`(low, high)` prices covering `fraction` of `bins`' volume around the POC.

    The usual construction: start at the point of control and repeatedly take
    whichever neighbouring bin holds more volume until the target share is
    covered. Ties resolve toward the lower bin so a given profile always yields
    the same area. Empty bins between occupied ones are skipped rather than
    walked, so a gappy profile does not stall the expansion.
    """
    if not bins:
        return None
    total = sum(bins.values())
    if total <= 0:
        return None

    ordered = sorted(bins)
    poc = max(ordered, key=lambda index: bins[index])
    low = high = ordered.index(poc)
    covered = bins[poc]
    target = total * fraction
    while covered < target and (low > 0 or high < len(ordered) - 1):
        below = bins[ordered[low - 1]] if low > 0 else -1.0
        above = bins[ordered[high + 1]] if high < len(ordered) - 1 else -1.0
        if above >= below:
            high += 1
            covered += bins[ordered[high]]
        else:
            low -= 1
            covered += bins[ordered[low]]
    return ordered[low] * bin_size, (ordered[high] + 1) * bin_size


def point_of_control(bins, bin_size):
    """Price of the busiest bin, or None for an empty profile."""
    if not bins:
        return None
    return max(sorted(bins), key=lambda index: bins[index]) * bin_size
