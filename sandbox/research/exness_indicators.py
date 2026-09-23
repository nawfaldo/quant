"""Indicator math for `exness_families`. Pure functions over plain lists.

WHY THIS IS A SEPARATE MODULE.

`exness_families` is the protocol -- resolution, cost, sizing, the engine, the
selection gates. What an indicator IS has nothing to do with any of that, and
mixing the two would push the one file that has to be read carefully in order to
trust a result past four thousand lines. Everything here is arithmetic on a list
of floats: no symbol, no session, no cost, no state that outlives a call.

THREE RULES, AND THEY ARE NOT NEGOTIABLE.

1.  CAUSAL. Index `i` of every return value may read bars `0..i` and nothing
    later. The one place this is easy to get wrong is a confirmed swing pivot --
    a fractal at bar `i` is not *known* until `k` bars later -- so
    `swing_pivots` returns the pivot against the bar that could first have seen
    it, not against the bar it happened on.

2.  ALIGNED. Every function returns a list exactly as long as its input, with
    `None` through the warm-up. The engine indexes context arrays by bar index
    with no offset anywhere, so a short list is a silent off-by-N on every
    reading after it.

3.  BUILT ONCE. These are computed per worker and then read by hundreds of
    thousands of backtest cells, so an O(n*p) implementation of something with
    no running form -- `cci`, `swing_pivots` -- is paid for once at start-up and
    is tolerable. The same cost *inside* a signal would not be, and nothing here
    is called from a signal.

THE MOVING-AVERAGE ZOO IS NOT A COLLECTION OF SYNONYMS.

`MA_KINDS` carries six averages, and they are here because they differ in LAG
and in what they do to a turn, not because more is better:

    sma    equal weight; the reference, and the most lag
    wma    linear weight; roughly half the lag of `sma` at the same period
    lsma   the endpoint of a least-squares line -- it EXTRAPOLATES, so it leads
           price in a steady trend and overshoots hard at a turn
    hma    Hull; near-zero lag by differencing two `wma`s, and it rings
    tema   triple EMA; low lag by adding the smoothing error back twice
    kama   Kaufman adaptive; the only one whose period is not fixed -- it
           freezes in chop and accelerates in a clean move

Ordered by lag they run roughly sma > wma > kama > tema > hma > lsma, and by
overshoot roughly the reverse. A cross of two `sma`s and a cross of two `lsma`s
at the same periods are not one hypothesis at two settings: the first asks
whether the trend has already turned, the second asks whether it is about to.

`ema` is deliberately absent. `ma_cross` is already an EMA cross, and repeating
it inside `xma_cross` would count one thesis twice and spend the budget twice.

ALMA, ZLEMA and T3 were considered and left out. ALMA is a shifted Gaussian
window, which lands between `wma` and `sma`; ZLEMA's lag profile sits on top of
`tema`'s; T3 is a smoother `tema`. None adds a shape the six above do not
already span, and each would have multiplied the `xma` grids by 7/6 for nothing.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

TS, O, H, L, C, V = range(6)


# --------------------------------------------------------------------------- #
# windows
# --------------------------------------------------------------------------- #

def rolling_extreme_inclusive(values, period, maximum):
    """Extreme of the `period` values ENDING AT each index, the bar included.

    The counterpart to `es.rolling_extreme`, which excludes the current bar
    because it exists for breakout tests. Anything that asks "where does this
    close sit inside its own recent range" -- a stochastic, an Aroon, a channel
    position -- has to include the bar, or the answer can exceed 100%.
    """
    out = [None] * len(values)
    queue = deque()
    for index, value in enumerate(values):
        while queue and queue[0] <= index - period:
            queue.popleft()
        while queue and ((values[queue[-1]] <= value) if maximum
                         else (values[queue[-1]] >= value)):
            queue.pop()
        queue.append(index)
        if index >= period - 1:
            out[index] = values[queue[0]]
    return out


def rolling_argextreme(values, period, maximum):
    """How many bars back the `period`-window extreme sits; 0 is this bar.

    Aroon is the only thing that reads this, and it is why Aroon is worth
    carrying: it measures the AGE of an extreme rather than its level, and
    nothing else in the study reads recency at all.
    """
    out = [None] * len(values)
    queue = deque()
    for index, value in enumerate(values):
        while queue and queue[0] <= index - period:
            queue.popleft()
        while queue and ((values[queue[-1]] <= value) if maximum
                         else (values[queue[-1]] >= value)):
            queue.pop()
        queue.append(index)
        if index >= period - 1:
            out[index] = index - queue[0]
    return out


# --------------------------------------------------------------------------- #
# the moving-average zoo
# --------------------------------------------------------------------------- #

def sma(values, period):
    out = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = running / period
    return out


def wma(values, period):
    """Linear-weighted mean in O(n).

    `weight` holds `p*v[t] + (p-1)*v[t-1] + ... + 1*v[t-p+1]`, maintained by
    `W[t] = W[t-1] + p*v[t] - S[t-1]` where `S` is the plain trailing sum. The
    naive form rescans the window every bar, which at a 280-bar slow period and
    40k bars is 11M multiplications per call and was measurable at start-up.
    """
    out = [None] * len(values)
    denominator = period * (period + 1) / 2.0
    running = weight = 0.0
    for index, value in enumerate(values):
        weight += period * value - running
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = weight / denominator
    return out


def hma(values, period):
    """Hull: `wma(2*wma(p/2) - wma(p), sqrt(p))`.

    The inner difference cancels most of the lag and leaves an average that
    turns almost with price -- and rings afterwards, which is the trade. Both
    halves must be defined before the outer average means anything, so the
    warm-up is `period + sqrt(period)` rather than `period`.
    """
    half = max(1, period // 2)
    root = max(1, int(round(math.sqrt(period))))
    fast, slow = wma(values, half), wma(values, period)
    blend = [None if a is None or b is None else 2.0 * a - b
             for a, b in zip(fast, slow)]
    first = next((i for i, v in enumerate(blend) if v is not None), len(blend))
    inner = wma(blend[first:], root) if first < len(blend) else []
    return ([None] * first + list(inner))[:len(values)]


def _ema_raw(values, period):
    alpha = 2.0 / (period + 1.0)
    out = []
    level = None
    for value in values:
        level = value if level is None else level + alpha * (value - level)
        out.append(level)
    return out


def tema(values, period):
    """`3*e1 - 3*e2 + e3`: an EMA with two rounds of its own error added back.

    Low lag bought with overshoot, and the overshoot is the point -- it is the
    member of the zoo that reliably crosses BEFORE `sma` does, so a cross that
    fires on `tema` and not on `sma` is early rather than different.
    """
    e1 = _ema_raw(values, period)
    e2 = _ema_raw(e1, period)
    e3 = _ema_raw(e2, period)
    out = [3.0 * a - 3.0 * b + c for a, b, c in zip(e1, e2, e3)]
    for index in range(min(len(values), 3 * period)):
        out[index] = None
    return out


def lsma(values, period):
    """Endpoint of the least-squares line through the last `period` values.

    The only average here that EXTRAPOLATES: it reports where the fitted line
    is now, not where the data was on average, so in a steady trend it sits
    ahead of price instead of behind it. Recovered from the same two running
    sums `wma` keeps, because `sum(i * v_i)` over the window is exactly
    `weight - running`.
    """
    out = [None] * len(values)
    x_sum = period * (period - 1) / 2.0
    xx_sum = (period - 1) * period * (2 * period - 1) / 6.0
    denominator = period * xx_sum - x_sum * x_sum
    if denominator == 0:
        return out
    running = weight = 0.0
    for index, value in enumerate(values):
        weight += period * value - running
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            slope = (period * (weight - running) - x_sum * running) / denominator
            out[index] = running / period + slope * (period - 1) / 2.0
    return out


def efficiency_ratio(values, period):
    """Kaufman's ER: net travel over gross travel, in [0, 1].

    A pure PATH-SHAPE statistic -- it says nothing about direction and nothing
    about size. 1.0 is a straight line; 0.0 is a walk that ends where it began.
    Everything else in the study reads a level or a displacement; this reads how
    the displacement was achieved.
    """
    out = [None] * len(values)
    steps = [0.0] + [abs(b - a) for a, b in zip(values, values[1:])]
    running = 0.0
    for index in range(len(values)):
        running += steps[index]
        if index > period:
            running -= steps[index - period]
        if index >= period:
            net = abs(values[index] - values[index - period])
            out[index] = net / running if running > 0 else 0.0
    return out


def kama(values, period, fast=2, slow=30):
    """Kaufman adaptive: the smoothing constant is driven by `efficiency_ratio`.

    The only average whose effective period changes bar to bar. In chop the ER
    collapses and it goes nearly flat -- so a cross against it needs a real move
    rather than a wide one -- and in a clean trend it converges on a `fast`-bar
    EMA. That makes it the natural partner for `sma` in a cross: the two
    disagree exactly when the market is noisy, which is the case worth
    separating.
    """
    ratio = efficiency_ratio(values, period)
    quick, slack = 2.0 / (fast + 1.0), 2.0 / (slow + 1.0)
    out = [None] * len(values)
    level = None
    for index, value in enumerate(values):
        er = ratio[index]
        if er is None:
            continue
        if level is None:
            level = values[index - 1] if index else value
        level += (er * (quick - slack) + slack) ** 2 * (value - level)
        out[index] = level
    return out


#: Name to builder. `xma_cross`, `xma_slope` and `xma_ribbon` take their `kind`
#: axis straight from here, so adding an average is the whole change.
MA_KINDS = {"sma": sma, "wma": wma, "hma": hma, "tema": tema, "lsma": lsma,
            "kama": kama}


def moving_average(kind, values, period):
    return MA_KINDS[kind](values, period)


# --------------------------------------------------------------------------- #
# trend quality and regime
# --------------------------------------------------------------------------- #

def linreg(values, period):
    """`(slope_per_bar, r_squared)` of the least-squares line, both causal.

    Slope and fit are a PAIR, and that is the whole idea. A slope alone cannot
    separate a clean drift from a violent chop that happens to end higher, and
    R^2 alone has no direction. Nothing else here splits "which way" from "how
    convincingly", so this is the only family that can demand both at once.
    """
    n = len(values)
    slopes, fits = [None] * n, [None] * n
    x_sum = period * (period - 1) / 2.0
    xx_sum = (period - 1) * period * (2 * period - 1) / 6.0
    denominator = period * xx_sum - x_sum * x_sum
    if denominator == 0:
        return slopes, fits
    running = weight = squares = 0.0
    for index, value in enumerate(values):
        weight += period * value - running
        running += value
        squares += value * value
        if index >= period:
            old = values[index - period]
            running -= old
            squares -= old * old
        if index >= period - 1:
            slope = (period * (weight - running) - x_sum * running) / denominator
            variance = squares - running * running / period
            explained = slope * slope * (xx_sum - x_sum * x_sum / period)
            slopes[index] = slope
            fits[index] = (max(0.0, min(1.0, explained / variance))
                           if variance > 1e-15 else None)
    return slopes, fits


def variance_ratio(values, step, window):
    """Lo-MacKinlay variance ratio of `step`-bar returns against 1-bar ones.

    Above 1 the series trends at that horizon, below 1 it reverts, and 1.0 is a
    random walk. It is the only reading in the study that names a REGIME rather
    than a position, so the families built on it CHOOSE between momentum and
    reversion instead of assuming one -- which is exactly what makes them
    uncorrelated with both.

    Overlapping `step`-returns over a trailing `window`, so this is the
    biased-but-stable form rather than the unbiased one; the bias is a constant
    at fixed `step` and `window` and cannot move a threshold comparison.
    """
    n = len(values)
    out = [None] * n
    if n < step + window + 2:
        return out
    short = [0.0] * n
    long = [0.0] * n
    for index in range(1, n):
        a, b = values[index - 1], values[index]
        short[index] = math.log(b / a) if a > 0 and b > 0 else 0.0
    for index in range(step, n):
        a, b = values[index - step], values[index]
        long[index] = math.log(b / a) if a > 0 and b > 0 else 0.0
    s1 = s1q = s2 = s2q = 0.0
    for index in range(n):
        s1 += short[index]
        s1q += short[index] * short[index]
        s2 += long[index]
        s2q += long[index] * long[index]
        if index >= window:
            old_s, old_l = short[index - window], long[index - window]
            s1 -= old_s
            s1q -= old_s * old_s
            s2 -= old_l
            s2q -= old_l * old_l
        if index >= step + window:
            v1 = s1q / window - (s1 / window) ** 2
            v2 = s2q / window - (s2 / window) ** 2
            out[index] = v2 / (step * v1) if v1 > 1e-18 else None
    return out


def rolling_skew(values, period):
    """Skewness of the last `period` one-bar log returns.

    A distribution SHAPE, and the third of those in the study after volatility
    and the variance ratio. Persistent negative skew is the signature of a
    market that grinds up and breaks down; it is not a directional forecast by
    itself, which is why the family reading it has to pick a side explicitly and
    be judged on that.
    """
    n = len(values)
    out = [None] * n
    returns = [0.0] * n
    for index in range(1, n):
        a, b = values[index - 1], values[index]
        returns[index] = math.log(b / a) if a > 0 and b > 0 else 0.0
    s1 = s2 = s3 = 0.0
    for index in range(n):
        r = returns[index]
        s1 += r
        s2 += r * r
        s3 += r * r * r
        if index >= period:
            old = returns[index - period]
            s1 -= old
            s2 -= old * old
            s3 -= old * old * old
        if index >= period:
            mean = s1 / period
            variance = s2 / period - mean * mean
            if variance <= 1e-18:
                continue
            third = s3 / period - 3 * mean * (s2 / period) + 2 * mean ** 3
            out[index] = third / variance ** 1.5
    return out


# --------------------------------------------------------------------------- #
# oscillators
# --------------------------------------------------------------------------- #

def stochastic(bars, period, smooth):
    """`(%K, %D)` -- where the close sits inside the `period`-bar RANGE.

    Not a slower RSI and not a rescaled z-score. A z-score divides by
    dispersion, so one violent bar widens the denominator and every later
    reading shrinks; %K divides by the range, so the same bar MOVES THE
    BOUNDARY and every later close is measured against the new extreme. They
    disagree hardest on exactly the days that matter.
    """
    highs = rolling_extreme_inclusive([b[H] for b in bars], period, True)
    lows = rolling_extreme_inclusive([b[L] for b in bars], period, False)
    fast = []
    for bar, top, bottom in zip(bars, highs, lows):
        fast.append(None if top is None or bottom is None or top - bottom <= 0
                    else 100.0 * (bar[C] - bottom) / (top - bottom))
    # A window whose whole range is one price leaves a hole in the middle of an
    # otherwise defined series -- rare, but it happens on a halted or pegged
    # bar, and `sma` would raise on it. The smoothing input carries the last
    # reading across the hole; the raw %K keeps its `None`, so the signal still
    # refuses to trade a bar it could not measure.
    first = next((i for i, v in enumerate(fast) if v is not None), len(fast))
    filled, carried = [], 50.0
    for value in fast[first:]:
        carried = value if value is not None else carried
        filled.append(carried)
    slow = [None] * first + list(sma(filled, smooth))
    return fast, slow[:len(bars)]


def cci(bars, period):
    """Typical-price deviation over MEAN ABSOLUTE deviation, not standard.

    The whole difference from `zscore` is the denominator, and it is not
    cosmetic: a squared denominator is dominated by the largest move in the
    window, so after one shock a z-score reads "nothing is extreme any more"
    for the rest of the window while a CCI keeps its scale. On a series with fat
    tails -- every series here -- that is a different signal, not a rescaling of
    the same one.
    """
    typical = [(b[H] + b[L] + b[C]) / 3.0 for b in bars]
    means = sma(typical, period)
    out = [None] * len(bars)
    for index in range(period - 1, len(bars)):
        mean = means[index]
        deviation = sum(abs(value - mean)
                        for value in typical[index - period + 1:index + 1]) / period
        out[index] = ((typical[index] - mean) / (0.015 * deviation)
                      if deviation > 1e-15 else None)
    return out


def macd(closes, fast, slow, signal):
    """`(line, trigger, histogram)`.

    The histogram is the part worth having. A cross of two averages says the
    faster one has overtaken the slower; the histogram's own turn says the GAP
    between them has stopped widening, which happens first and is a claim about
    acceleration rather than position. `ma_cross` cannot express it.
    """
    quick, slack = _ema_raw(closes, fast), _ema_raw(closes, slow)
    line = [a - b for a, b in zip(quick, slack)]
    trigger = _ema_raw(line, signal)
    histogram = [a - b for a, b in zip(line, trigger)]
    for index in range(min(len(closes), slow + signal)):
        line[index] = trigger[index] = histogram[index] = None
    return line, trigger, histogram


def adx_dmi(bars, period):
    """Wilder's `(+DI, -DI, ADX)`.

    Built from DIRECTIONAL MOVEMENT -- how far each bar extended past the
    previous bar's high or low -- and so from the one part of a bar nothing else
    here reads. A close-based momentum measure cannot separate a bar that ran up
    and closed flat from a bar that never moved; +DM can. ADX is strength with
    no sign, which makes it a gate rather than a signal, and the DI cross is the
    signal it gates.
    """
    n = len(bars)
    plus, minus, adx = [None] * n, [None] * n, [None] * n
    if n < 2 * period + 1:
        return plus, minus, adx
    tr_sum = up_sum = down_sum = 0.0
    dx = []
    smoothed = None
    for index in range(1, n):
        bar, previous = bars[index], bars[index - 1]
        up, down = bar[H] - previous[H], previous[L] - bar[L]
        up_move = up if up > down and up > 0 else 0.0
        down_move = down if down > up and down > 0 else 0.0
        true = max(bar[H] - bar[L], abs(bar[H] - previous[C]),
                   abs(bar[L] - previous[C]))
        if index <= period:
            tr_sum += true
            up_sum += up_move
            down_sum += down_move
            if index < period:
                continue
        else:
            tr_sum += true - tr_sum / period
            up_sum += up_move - up_sum / period
            down_sum += down_move - down_sum / period
        if tr_sum <= 0:
            continue
        pdi, mdi = 100.0 * up_sum / tr_sum, 100.0 * down_sum / tr_sum
        plus[index], minus[index] = pdi, mdi
        total = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / total if total > 0 else 0.0)
        if len(dx) == period:
            smoothed = sum(dx) / period
            adx[index] = smoothed
        elif len(dx) > period:
            smoothed = (smoothed * (period - 1) + dx[-1]) / period
            adx[index] = smoothed
    return plus, minus, adx


def aroon(bars, period):
    """`(up, down)` in [0, 100]: how RECENTLY the window's extreme was set.

    100 means the high is this bar; 0 means it is `period` bars old. It is the
    only reading in the study denominated in TIME rather than price -- a market
    that keeps making new highs scores 100 whether it has doubled or crept, and
    one that has stalled decays towards 0 without falling at all. Nothing built
    from levels can say that.
    """
    ups = rolling_argextreme([b[H] for b in bars], period, True)
    downs = rolling_argextreme([b[L] for b in bars], period, False)
    return ([None if a is None else 100.0 * (period - a) / period for a in ups],
            [None if a is None else 100.0 * (period - a) / period for a in downs])


def money_flow_index(bars, period):
    """Volume-weighted RSI on typical price.

    `rsi` counts how far price moved up against down; this counts how much MONEY
    moved. The two come apart when a move happens on nothing -- a drift on thin
    volume maxes the RSI and leaves the MFI mid-range -- and that disagreement
    is the family's whole thesis.
    """
    n = len(bars)
    out = [None] * n
    typical = [(b[H] + b[L] + b[C]) / 3.0 for b in bars]
    positive, negative = [0.0] * n, [0.0] * n
    for index in range(1, n):
        flow = typical[index] * bars[index][V]
        if typical[index] > typical[index - 1]:
            positive[index] = flow
        elif typical[index] < typical[index - 1]:
            negative[index] = flow
    up = down = 0.0
    for index in range(n):
        up += positive[index]
        down += negative[index]
        if index > period:
            up -= positive[index - period]
            down -= negative[index - period]
        if index >= period:
            out[index] = 100.0 if down <= 0 else 100.0 - 100.0 / (1.0 + up / down)
    return out


def chaikin_money_flow(bars, period):
    """Volume weighted by where each bar CLOSED inside its own range.

    A bar that ranges wide and closes on its high contributes its whole volume
    as buying; one that closes mid-range contributes nothing either way. That
    makes it a measure of who won each bar, summed -- distinct from `obv`, which
    credits a whole bar's volume to the sign of one close-to-close change and so
    cannot tell a decisive bar from a marginal one.
    """
    n = len(bars)
    out = [None] * n
    flows, volumes = [0.0] * n, [0.0] * n
    for index, bar in enumerate(bars):
        span = bar[H] - bar[L]
        location = (((bar[C] - bar[L]) - (bar[H] - bar[C])) / span
                    if span > 0 else 0.0)
        flows[index], volumes[index] = location * bar[V], bar[V]
    flow_sum = volume_sum = 0.0
    for index in range(n):
        flow_sum += flows[index]
        volume_sum += volumes[index]
        if index >= period:
            flow_sum -= flows[index - period]
            volume_sum -= volumes[index - period]
        if index >= period - 1 and volume_sum > 0:
            out[index] = flow_sum / volume_sum
    return out


def on_balance_volume(bars):
    """Cumulative signed volume. The level is meaningless; the SHAPE is the point.

    `climax` and `volume_thrust` read one bar's volume, so they see events. This
    accumulates, so it sees whether the events have been one-sided over a
    stretch -- and, crucially, it can make a lower high while price makes a
    higher one, which is the only way an OHLCV series can say "this move is not
    being paid for".
    """
    out, total = [], 0.0
    for index, bar in enumerate(bars):
        if index:
            previous = bars[index - 1][C]
            total += (bar[V] if bar[C] > previous
                      else -bar[V] if bar[C] < previous else 0.0)
        out.append(total)
    return out


def relative_volume(bars, days=20):
    """This bar's volume against the average for its OWN clock minute.

    Intraday volume is U-shaped: the first and last bars of a session carry
    several times the middle of the day. A rolling 20-bar mean therefore calls
    every open and every close unusual, which is why `volume_thrust` and
    `climax` cluster their entries at the session edges. Comparing a bar only
    with the same minute on earlier days removes the shape and leaves the
    surprise.

    Causal by construction -- a minute's average is built from strictly earlier
    days, and the current bar is appended only after it has been scored.
    """
    out = [None] * len(bars)
    history = {}
    for index, bar in enumerate(bars):
        window = history.setdefault(bar[TS] % 86_400 // 60, deque(maxlen=days))
        if len(window) >= max(5, days // 4):
            average = sum(window) / len(window)
            out[index] = bar[V] / average if average > 0 else None
        window.append(bar[V])
    return out


# --------------------------------------------------------------------------- #
# price geometry
# --------------------------------------------------------------------------- #

def supertrend(bars, atr, multiple):
    """`(direction, band)`: an ATR envelope that RATCHETS and then flips.

    `keltner` re-reads its band every bar, so the same price can be inside it
    and outside it on consecutive bars as the ATR breathes. This one only ever
    moves its band towards price while the trend is intact, so the level that
    finally breaks is the tightest one the move ever produced. That memory is
    the difference, and it is why the two disagree in a widening range.
    """
    n = len(bars)
    direction, band = [None] * n, [None] * n
    state, upper, lower = 1, None, None
    for index, bar in enumerate(bars):
        width = atr[index]
        if not width:
            continue
        middle = (bar[H] + bar[L]) / 2.0
        top, bottom = middle + multiple * width, middle - multiple * width
        if upper is None:
            upper, lower = top, bottom
            direction[index], band[index] = state, lower
            continue
        previous = bars[index - 1][C]
        upper = top if top < upper or previous > upper else upper
        lower = bottom if bottom > lower or previous < lower else lower
        if state == 1 and bar[C] < lower:
            state = -1
        elif state == -1 and bar[C] > upper:
            state = 1
        direction[index] = state
        band[index] = lower if state == 1 else upper
    return direction, band


def parabolic_sar(bars, step, cap):
    """Wilder's SAR: a stop that accelerates towards price and flips on touch.

    The only trailing construct here whose speed depends on how many NEW
    EXTREMES the move has made rather than on elapsed time or on volatility. A
    move that keeps extending gets chased hard; one that stalls at the same high
    is given room. Neither `supertrend` nor a bar-count exit can express that.

    `flip[i]` is the side the trend turned to on bar `i`, and 0 otherwise.
    """
    n = len(bars)
    out, flip = [None] * n, [0] * n
    if n < 2:
        return out, flip
    state = 1 if bars[1][C] >= bars[0][C] else -1
    sar = bars[0][L] if state == 1 else bars[0][H]
    extreme = bars[0][H] if state == 1 else bars[0][L]
    speed = step
    for index in range(1, n):
        bar = bars[index]
        sar += speed * (extreme - sar)
        # Wilder clamps the SAR to the PREVIOUS TWO bars' extremes. Including
        # the current bar's low here instead makes `sar <= bar[L]` true by
        # construction, so the flip test can never fire and the whole family
        # emits zero signals -- which is what it did, and which reads as "no
        # edge" rather than as the arithmetic error it is.
        floor_low = min(bars[index - 1][L], bars[index - 2][L]) if index > 1 \
            else bars[index - 1][L]
        ceiling_high = max(bars[index - 1][H], bars[index - 2][H]) if index > 1 \
            else bars[index - 1][H]
        if state == 1:
            sar = min(sar, floor_low)
            if bar[L] < sar:
                state, sar, extreme, speed = -1, extreme, bar[L], step
                flip[index] = -1
            elif bar[H] > extreme:
                extreme, speed = bar[H], min(cap, speed + step)
        else:
            sar = max(sar, ceiling_high)
            if bar[H] > sar:
                state, sar, extreme, speed = 1, extreme, bar[H], step
                flip[index] = 1
            elif bar[L] < extreme:
                extreme, speed = bar[L], min(cap, speed + step)
        out[index] = sar
    return out, flip


def swing_pivots(bars, wing):
    """`(high, previous_high, low, previous_low)` -- the last two CONFIRMED
    fractal pivots of each kind available at every bar.

    A fractal pivot needs `wing` bars on both sides, so a pivot at bar `i` is
    not knowable until bar `i + wing`. That lag is the entire reason this is not
    a `donchian` channel: a rolling extreme updates the moment a new high
    prints, while a swing high is a level the market has already turned away
    from and then failed to reclaim for `wing` bars.

    Recording a pivot against bar `i` instead of `i + wing` would be lookahead,
    and a cheap one -- it would put the level in place exactly in time to trade
    the break of it.

    TWO of each are returned because one pivot is a level and two are a
    STRUCTURE. Whether the last swing high is above the one before it is the
    only way this series can state "higher high", and that is a claim no single
    extreme, channel or average can make.
    """
    n = len(bars)
    highs, previous_highs = [None] * n, [None] * n
    lows, previous_lows = [None] * n, [None] * n
    last_high = prior_high = last_low = prior_low = None
    for index in range(n):
        centre = index - wing
        if centre - wing >= 0:
            window = range(centre - wing, centre + wing + 1)
            top, bottom = bars[centre][H], bars[centre][L]
            if all(bars[j][H] <= top for j in window):
                prior_high, last_high = last_high, top
            if all(bars[j][L] >= bottom for j in window):
                prior_low, last_low = last_low, bottom
        highs[index], previous_highs[index] = last_high, prior_high
        lows[index], previous_lows[index] = last_low, prior_low
    return highs, previous_highs, lows, previous_lows


def fair_value_gaps(bars, limit=6):
    """Unfilled three-bar imbalances visible at each bar, as `(low, high, side)`.

    A gap between bar `i-2`'s high and bar `i`'s low is a band of price the
    middle bar travelled straight through and never traded back into -- the
    OHLCV signature of an order that had to be filled at any price. Retail calls
    it a fair value gap; what earns it a place here is that it is the only LEVEL
    in the study defined by an ABSENCE of trade rather than by an extreme, a
    mean or a session boundary.

    A zone is created when its third bar closes and destroyed once price has
    traded through it, so a wick that merely clips the edge does not erase a
    level the market then respects. `limit` caps how many are carried, newest
    last, so a quiet stretch cannot accumulate hundreds of stale bands.
    """
    n = len(bars)
    out = [()] * n
    live = []
    for index, bar in enumerate(bars):
        live = [zone for zone in live
                if not (zone[2] == 1 and bar[L] < zone[0])
                and not (zone[2] == -1 and bar[H] > zone[1])]
        if index >= 2:
            first, third = bars[index - 2], bar
            if first[H] < third[L]:
                live.append((first[H], third[L], 1))
            elif third[H] < first[L]:
                live.append((third[H], first[L], -1))
        live = live[-limit:]
        out[index] = tuple(live)
    return out


def floor_pivots(bars):
    """Prior-day `(pivot, r1, s1, r2, s2)` from the classic floor formula.

    A different ANCHOR from `pdr`, not a different rule on the same one. `pdr`
    reads yesterday's high and low, two prices that actually traded; the pivot
    is `(H+L+C)/3` and the R/S levels are reflections of the range around it --
    prices that mostly did NOT trade yesterday. Whether such a level works is a
    question about self-fulfilment, and it can only be asked with a level nobody
    transacted at.
    """
    got, order = {}, []
    for bar in bars:
        day = bar[TS] // 86_400
        if day not in got:
            order.append(day)
            got[day] = [bar[H], bar[L], bar[C]]
        else:
            got[day][0] = max(got[day][0], bar[H])
            got[day][1] = min(got[day][1], bar[L])
            got[day][2] = bar[C]
    out = {}
    for index, day in enumerate(order):
        if not index:
            continue
        high, low, close = got[order[index - 1]]
        pivot, span = (high + low + close) / 3.0, high - low
        out[day] = (pivot, 2 * pivot - low, 2 * pivot - high,
                    pivot + span, pivot - span)
    return out


def anchored_vwap(bars, anchor):
    """Volume-weighted average price since the start of the week or the month.

    `session_vwap` resets every day, so it can only say where price sits against
    today's participants. Reset on a calendar boundary instead and the same
    arithmetic answers a different question -- whether everyone who bought this
    month is up -- which is a position measure with a horizon no daily reset can
    reach.
    """
    out, key, notional, volume = [], None, 0.0, 0.0
    for bar in bars:
        moment = datetime.fromtimestamp(bar[TS], tz=timezone.utc)
        current = ((moment.year, moment.month) if anchor == "month"
                   else moment.isocalendar()[:2])
        if current != key:
            key, notional, volume = current, 0.0, 0.0
        notional += (bar[H] + bar[L] + bar[C]) / 3.0 * bar[V]
        volume += bar[V]
        out.append(notional / volume if volume > 0 else None)
    return out


def wick_ratios(bars):
    """`(upper, lower, body)` as fractions of each bar's own range.

    Bar ANATOMY, which nothing else reads: `climax` will only look at the
    close's location once volume and range have already agreed, and every other
    family throws the wicks away entirely. A long lower wick on an otherwise
    ordinary bar is a price the market visited and rejected inside one bar, and
    whether that rejection means anything is a question in its own right.
    """
    uppers, lowers, bodies = [], [], []
    for bar in bars:
        span = bar[H] - bar[L]
        if span <= 0:
            uppers.append(None)
            lowers.append(None)
            bodies.append(None)
            continue
        top, bottom = max(bar[O], bar[C]), min(bar[O], bar[C])
        uppers.append((bar[H] - top) / span)
        lowers.append((bottom - bar[L]) / span)
        bodies.append(abs(bar[C] - bar[O]) / span)
    return uppers, lowers, bodies


# --------------------------------------------------------------------------- #
# relative to a benchmark
# --------------------------------------------------------------------------- #

def align(bars, other):
    """`other`'s close carried onto `bars`' timestamps, causal, `None` before.

    A benchmark series is a different table with its own holidays, halts and
    row count, so it cannot be indexed alongside the symbol. Each bar takes the
    LAST benchmark close at or before its own timestamp: never a future one, and
    never interpolated. A benchmark that has not printed for a while therefore
    goes stale rather than wrong, which the families guard by requiring a
    reading at both ends of their lookback.
    """
    out = [None] * len(bars)
    stamps = [b[TS] for b in other]
    closes = [b[C] for b in other]
    cursor = 0
    for index, bar in enumerate(bars):
        while cursor < len(stamps) and stamps[cursor] <= bar[TS]:
            cursor += 1
        if cursor:
            out[index] = closes[cursor - 1]
    return out


def relative_line(values, reference):
    """`values / reference`, the ratio series, `None` wherever either is missing.

    Every relative family reads this one line, and reading it rather than a
    difference of returns is deliberate: a ratio is itself a price series, so a
    channel, a z-score or a moving average computed on it means exactly what it
    means on a price, and the same well-understood machinery applies with no new
    statistics to justify.
    """
    return [None if v is None or r is None or r <= 0 else v / r
            for v, r in zip(values, reference)]


# --------------------------------------------------------------------------- #
# the sixth wave: path statistics, microstructure, filters, adaptive horizons
#
# WHY THESE AND NOT MORE OF THE SAME. Everything above this line is one of three
# things: a moving average (a LOWPASS filter), an oscillator built from a range
# or a difference of averages, or a level. The readings below were chosen
# because each names a property of the series that no combination of those three
# can state:
#
#   the SCALING LAW of dispersion across horizons      `hurst_exponent`
#   the ORDER of returns, independent of their size    `permutation_entropy`
#   the FOURTH moment                                  `rolling_kurtosis`
#   dependence at ONE named lag                        `rolling_autocorr`
#   whether a sign SEQUENCE is random                  `runs_z`
#   a monotone trend test that outliers cannot move    `mann_kendall_z`
#   tail asymmetry that a single outlier cannot move   `tail_ratio`
#   the JUMP part of variance, separated from the rest `bipower_jump`
#   which SIDE the variance came from                  `signed_jump`
#   price impact per unit of volume                    `amihud`
#   path volatility against close-to-close volatility  `variance_estimators`
#   flow imbalance from bar shape alone                `bulk_imbalance`
#   a BANDPASS rather than a lowpass                   `roofing_filter`
#   a Gaussian-ised position within a range            `fisher_transform`
#   a filter whose GAIN adapts to observed noise       `kalman_trend`
#   a stationary series that KEEPS its memory          `frac_diff`
#   the dominant CYCLE PERIOD, measured                `dominant_cycle`
#   the mean-reversion half-life, estimated            `ou_half_life`
#   a threshold on ACCUMULATED deviation               `cusum_events`
#   an order statistic instead of an extreme           `rolling_quantile`
#
# THE THREE RULES AT THE TOP OF THIS FILE STILL HOLD: causal, aligned, built
# once. Two of these are O(n*p) with a real constant -- `permutation_entropy`
# and `mann_kendall_z` -- and both are capped so the constant cannot grow with
# the timeframe; see their docstrings.
#
# WHAT WAS CONSIDERED AND LEFT OUT. A Katz or Higuchi fractal dimension was the
# obvious twenty-first entry and was dropped on inspection: Katz's D is
# log(n) / (log(n) + log(d/L)), where d/L is the net displacement over the path
# length -- which IS `efficiency_ratio`. At a fixed window the two are a
# monotone transform of each other, so the family would have been a rename of
# one that already runs. `hurst_exponent` survives the same test because a
# scaling slope across five horizons is not a function of the displacement at
# any one of them.
# --------------------------------------------------------------------------- #

def _log_returns(values):
    """One-bar log returns, `0.0` where the ratio is not defined.

    ZERO RATHER THAN `None`, and only here. Every statistic below consumes the
    whole return window as a block, so a `None` in the middle would have to be
    handled identically by nine functions or produce nine different answers to
    the same missing bar. A zero return is the honest reading for a bar the
    series did not move through, and the warm-up `None`s that matter -- the ones
    before a window is full -- are emitted by the callers, not by this.
    """
    out = [0.0] * len(values)
    for index in range(1, len(values)):
        a, b = values[index - 1], values[index]
        if a is not None and b is not None and a > 0 and b > 0:
            out[index] = math.log(b / a)
    return out


def hurst_exponent(values, period, lags=(1, 2, 4, 8, 16)):
    """The scaling exponent of dispersion against horizon, over a trailing window.

    NOT A LONGER VARIANCE RATIO, and the distinction is the reason this exists
    next to one. `variance_ratio` compares dispersion at exactly two horizons --
    one bar and `step` bars -- so it is a single point on a curve. This fits the
    line log(sd of k-bar moves) = H*log(k) + c across five horizons and returns
    its SLOPE, which is a statement about the whole curve. A series that trends
    at 16 bars and reverts at 2 has a variance ratio above one at whichever step
    it is asked about and an H near 0.5; the two readings disagree by
    construction rather than by noise.

    H > 0.5 is persistent, H < 0.5 antipersistent, H = 0.5 a random walk. The
    aggregated-dispersion estimator rather than R/S: R/S is badly biased at the
    window lengths a trading rule can afford, and its bias is a function of the
    window, which would make the reading incomparable across timeframes.

    `lags` are BAR counts and are fixed, not swept. The construct is the slope
    across a fixed ladder of horizons; sweeping which horizons would turn one
    hypothesis into a search over which scaling to believe.
    """
    n = len(values)
    out = [None] * n
    if period < 4 * max(lags):
        return out
    returns = _log_returns(values)
    # Prefix sums of one-bar returns let a k-bar move be read in O(1), so the
    # whole thing is O(n * len(lags)) rather than O(n * period).
    prefix = [0.0] * (n + 1)
    for index, value in enumerate(returns):
        prefix[index + 1] = prefix[index] + value
    sums = {k: [0.0, 0.0] for k in lags}
    xs = [math.log(k) for k in lags]
    mean_x = sum(xs) / len(xs)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return out

    def move(k, index):
        """The k-bar log move ENDING at `index`."""
        return prefix[index + 1] - prefix[index + 1 - k]

    for index in range(n):
        for k in lags:
            if index + 1 - k < 0:
                continue
            value = move(k, index)
            state = sums[k]
            state[0] += value
            state[1] += value * value
            gone = index - period
            if gone >= 0 and gone + 1 - k >= 0:
                old = move(k, gone)
                state[0] -= old
                state[1] -= old * old
        if index < period + max(lags):
            continue
        ys, ok = [], True
        for k in lags:
            total, square = sums[k]
            variance = square / period - (total / period) ** 2
            if variance <= 1e-24:
                ok = False
                break
            ys.append(0.5 * math.log(variance))
        if not ok:
            continue
        mean_y = sum(ys) / len(ys)
        covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        out[index] = covariance / denominator
    return out


def permutation_entropy(values, period, order=3):
    """Bandt-Pompe entropy of the ORDINAL patterns in the return sequence.

    THE ONLY READING IN THE STUDY THAT IS BLIND TO MAGNITUDE. Every other
    statistic here changes if you double one return; this one does not, because
    it reads only which of three consecutive returns was largest. That makes it
    the complement of `efficiency_ratio`, which is pure magnitude and blind to
    order: a clean straight-line move and a sawtooth of identical net
    displacement have very different efficiency ratios, and a stretch of tiny
    alternating moves has a LOW entropy -- up-down-up-down is one pattern
    repeating -- while its efficiency ratio says only that it went nowhere.

    Normalised by log(order!) so the reading is in [0, 1] whatever the
    embedding: 1 is "every ordering equally likely", which is what a random walk
    produces, and low is a repeating structure.

    `order=3` fixes six patterns, and it is fixed rather than swept for a
    concrete reason: at order 4 there are 24 patterns and a usable histogram
    needs several hundred bars, which at 30m is a fortnight -- the reading would
    then be about a fortnight ago and not about now. Six patterns are estimable
    from one session's worth of bars.
    """
    n = len(values)
    out = [None] * n
    if period < 20 or order < 2:
        return out
    returns = _log_returns(values)
    # The ordinal pattern of the `order` returns ENDING at each index, encoded
    # as a rank permutation. Ties break by index, the standard convention, which
    # matters only on a flat series.
    patterns = [None] * n
    for index in range(order - 1, n):
        window = returns[index - order + 1:index + 1]
        ranking = sorted(range(order), key=lambda i: (window[i], i))
        code = 0
        for item in ranking:
            code = code * order + item
        patterns[index] = code
    counts = {}
    live = 0
    scale = math.log(math.factorial(order))
    for index in range(n):
        code = patterns[index]
        if code is not None:
            counts[code] = counts.get(code, 0) + 1
            live += 1
        gone = index - period
        if gone >= 0 and patterns[gone] is not None:
            counts[patterns[gone]] -= 1
            live -= 1
        if live < period:
            continue
        total = 0.0
        for count in counts.values():
            if count > 0:
                share = count / live
                total -= share * math.log(share)
        out[index] = total / scale if scale > 0 else None
    return out


def rolling_kurtosis(values, period):
    """Excess kurtosis of the last `period` one-bar log returns.

    THE FOURTH MOMENT, AND THE STUDY HAD THE FIRST THREE. Level is the price,
    dispersion is `trailing_volatility`, asymmetry is `rolling_skew`; nothing
    read the weight of the tails. It is not a slower volatility reading: a
    window of many tiny moves and two violent ones has ordinary volatility and
    enormous kurtosis, and a window of uniformly large moves has high volatility
    and kurtosis near zero. Those are different markets and no variance
    statistic can separate them.

    Excess -- three subtracted -- so zero is the Gaussian reference and the sign
    is readable without knowing the convention.
    """
    n = len(values)
    out = [None] * n
    if period < 8:
        return out
    returns = _log_returns(values)
    s1 = s2 = s3 = s4 = 0.0
    for index in range(n):
        r = returns[index]
        s1 += r
        s2 += r * r
        s3 += r ** 3
        s4 += r ** 4
        if index >= period:
            old = returns[index - period]
            s1 -= old
            s2 -= old * old
            s3 -= old ** 3
            s4 -= old ** 4
        if index < period:
            continue
        mean = s1 / period
        variance = s2 / period - mean * mean
        if variance <= 1e-24:
            continue
        fourth = (s4 / period - 4 * mean * (s3 / period)
                  + 6 * mean * mean * (s2 / period) - 3 * mean ** 4)
        out[index] = fourth / (variance * variance) - 3.0
    return out


def rolling_autocorr(values, period, lag):
    """Autocorrelation of one-bar log returns at exactly `lag`, over `period`.

    ONE LAG, NAMED, and that is what distinguishes it from `variance_ratio`. A
    variance ratio at step k is a triangular-weighted SUM of the autocorrelations
    at lags 1 through k-1, so it cannot tell a series with rho(1) = +0.2 from one
    with rho(1) = -0.2 and rho(2) = +0.4 -- both aggregate to the same number,
    and the two demand opposite trades on the very next bar.

    The sign is the trade: positive autocorrelation says the last move continues,
    negative says it reverses. That makes this the only reading in the study
    whose value maps onto a DIRECTION without a threshold having to be chosen,
    which is why the family built on it carries a magnitude threshold instead.
    """
    n = len(values)
    out = [None] * n
    if period < 3 * max(2, lag):
        return out
    returns = _log_returns(values)
    sx = sy = sxy = sxx = syy = 0.0

    def pair(index):
        return returns[index], returns[index - lag]

    for index in range(n):
        if index >= lag:
            x, y = pair(index)
            sx += x
            sy += y
            sxy += x * y
            sxx += x * x
            syy += y * y
        gone = index - period
        if gone >= lag:
            x, y = pair(gone)
            sx -= x
            sy -= y
            sxy -= x * y
            sxx -= x * x
            syy -= y * y
        if index < period + lag:
            continue
        count = period
        cov = sxy / count - (sx / count) * (sy / count)
        vx = sxx / count - (sx / count) ** 2
        vy = syy / count - (sy / count) ** 2
        if vx <= 1e-24 or vy <= 1e-24:
            continue
        out[index] = cov / math.sqrt(vx * vy)
    return out


def runs_z(values, period):
    """Wald-Wolfowitz runs statistic on the SIGN sequence of the last `period`.

    `consecutive` reads the CURRENT streak and fires when it reaches a count.
    This reads the whole window and asks whether the number of sign changes in
    it is what chance would produce -- so it fires on a bar whose current streak
    is one, provided the window as a whole has too few runs, and it stays silent
    through a five-bar streak sitting inside an otherwise choppy window. The two
    rules therefore trade almost disjoint bars while both claiming to be about
    persistence, which is exactly the disagreement worth measuring.

    Negative z is too FEW runs -- trending, sticky signs. Positive z is too many
    -- alternating, the signature of a market being made rather than moved.

    A FLAT BAR CONTINUES THE RUN rather than ending it or being deleted, and the
    choice is forced by wanting this in O(n). The textbook test drops ties, but
    "adjacent after the ties are removed" is not a fixed-offset relation, so a
    sliding window cannot maintain the change count incrementally -- and the
    O(n*p) form costs a minute of worker start-up at 30m. Carrying the previous
    sign through a zero return is the reading that says a bar which did not move
    did not end anything, and on a liquid 30m series exact zeros are a fraction
    of a percent of bars.
    """
    n = len(values)
    out = [None] * n
    if period < 10:
        return out
    returns = _log_returns(values)
    # Signs, with a flat bar carrying the previous one forward.
    signs = [0] * n
    carry = 1
    for index in range(n):
        r = returns[index]
        if r > 0:
            carry = 1
        elif r < 0:
            carry = -1
        signs[index] = carry
    # `changed[i]` is 1 when bar i starts a new run. Runs in a window are then
    # one plus the changes strictly inside it, and both are rolling sums.
    changed = [0] * n
    for index in range(1, n):
        changed[index] = 1 if signs[index] != signs[index - 1] else 0
    ups = changes = 0
    for index in range(n):
        ups += 1 if signs[index] > 0 else 0
        # The change at the window's FIRST bar refers to the bar before it and
        # is therefore not inside the window; the sum runs over the tail.
        changes += changed[index]
        if index >= period:
            gone = index - period
            ups -= 1 if signs[gone] > 0 else 0
            changes -= changed[gone]
        if index < period:
            continue
        # Exclude the leading edge's change, which compares across the boundary.
        inside = changes - changed[index - period + 1]
        count = period
        downs = count - ups
        if ups == 0 or downs == 0:
            continue
        runs = 1 + inside
        mean = 2.0 * ups * downs / count + 1.0
        variance = (2.0 * ups * downs * (2.0 * ups * downs - count)
                    / (count * count * (count - 1.0)))
        if variance <= 1e-12:
            continue
        out[index] = (runs - mean) / math.sqrt(variance)
    return out


def mann_kendall_z(values, period):
    """The nonparametric monotone-trend test, standardised, over `period` closes.

    ROBUST WHERE `linreg` IS NOT, and that is the whole reason to carry both. A
    least-squares slope is a weighted mean and one violent bar moves it; the
    Mann-Kendall statistic counts only the SIGN of every pairwise comparison, so
    that bar contributes at most its share of the count. On a series that ground
    steadily upward and then gapped down once, the regression says "flat" and
    this still says "up" -- and which of the two a trader should believe is a
    question the study could not previously ask.

    COMPUTED BY SLIDING, NOT BY RECOMPUTING. The naive form is O(period^2) per
    bar, which at 30m is a minute of worker start-up for one lookback. S is a
    sum over pairs, so when the window advances by one bar the only pairs that
    change are those involving the departing and the arriving element: subtract
    the departing bar's comparisons against the window, add the arriving bar's.
    That is O(period) a bar and gives the EXACT statistic -- no subsampling, and
    no reading that jitters because the thinning grid moved under it.
    """
    n = len(values)
    out = [None] * n
    if period < 10:
        return out
    # A window of values with no gaps in it. A `None` resets the accumulator
    # rather than being skipped: S is defined over a contiguous ordering, and
    # bridging a hole would compare across it as though nothing were missing.
    window = []
    score = 0
    variance = period * (period - 1) * (2 * period + 5) / 18.0
    if variance <= 0:
        return out
    root = math.sqrt(variance)
    for index in range(n):
        value = values[index]
        if value is None:
            window = []
            score = 0
            continue
        for other in window:
            score += 1 if value > other else -1 if value < other else 0
        window.append(value)
        if len(window) > period:
            gone = window.pop(0)
            for other in window:
                score -= 1 if other > gone else -1 if other < gone else 0
        if len(window) < period:
            continue
        if score > 0:
            out[index] = (score - 1) / root
        elif score < 0:
            out[index] = (score + 1) / root
        else:
            out[index] = 0.0
    return out


def tail_ratio(values, period, quantile=0.1):
    """The upper tail against the lower tail, as an ORDER STATISTIC.

    `rolling_skew` answers the same question with the third moment, and a single
    outlier dominates a third moment completely: one -8% bar in a window of calm
    makes skew strongly negative whether or not the window's tails are otherwise
    symmetric. This compares the `quantile` point of the positive returns with
    the same point of the negative ones, so no single bar can move it by more
    than one rank.

    Above 1 the up-tail is fatter. The two readings usually agree, and the
    family exists for the cases where they do not -- which are precisely the
    windows that contain one event.
    """
    n = len(values)
    out = [None] * n
    if period < 20:
        return out
    returns = _log_returns(values)
    for index in range(period, n):
        window = returns[index - period + 1:index + 1]
        ups = sorted((r for r in window if r > 0), reverse=True)
        downs = sorted((-r for r in window if r < 0), reverse=True)
        if len(ups) < 5 or len(downs) < 5:
            continue
        top = ups[max(0, min(len(ups) - 1, int(quantile * len(ups))))]
        bottom = downs[max(0, min(len(downs) - 1, int(quantile * len(downs))))]
        if bottom <= 1e-12:
            continue
        out[index] = top / bottom
    return out


# --------------------------------------------------------------------------- #
# microstructure, read off an OHLCV bar
# --------------------------------------------------------------------------- #

def bipower_jump(values, period):
    """The share of realised variance that was a JUMP, after Barndorff-Nielsen.

    Realised variance sums squared returns and cannot tell a violent diffusion
    from a single gap. Bipower variation sums the product of ADJACENT absolute
    returns, and because a jump lands in only one of the two factors it is
    robust to jumps while realised variance is not. Their difference is
    therefore the jump part, and this returns it as a SHARE of total variance so
    the reading is comparable across symbols and volatility regimes.

    Nothing else in the study can separate the two. `trailing_volatility` adds
    them together by construction, ATR adds them together, and `climax` looks
    for one big bar -- which is a jump detector at bar resolution and says
    nothing about whether the REGIME is jumpy. A window of eight ordinary bars
    and one gap has the same volatility as nine energetic bars and a completely
    different jump share.

    The scaling constant is pi/2, which is 1/E|Z|^2 for a standard normal; it
    makes bipower variation an unbiased estimator of integrated variance in the
    absence of jumps, so a jumpless window reads near zero rather than near some
    symbol-specific offset.
    """
    n = len(values)
    out = [None] * n
    if period < 10:
        return out
    returns = _log_returns(values)
    absolute = [abs(r) for r in returns]
    squares = [r * r for r in returns]
    products = [0.0] * n
    for index in range(1, n):
        products[index] = absolute[index] * absolute[index - 1]
    mu = math.pi / 2.0
    rv = bv = 0.0
    for index in range(n):
        rv += squares[index]
        bv += products[index]
        if index >= period:
            rv -= squares[index - period]
            bv -= products[index - period]
        if index < period or rv <= 1e-24:
            continue
        out[index] = max(0.0, rv - mu * bv) / rv
    return out


def signed_jump(values, period):
    """Realised SEMIvariance: up-variance minus down-variance, scaled by total.

    After Barndorff-Nielsen, Kinnebrock and Shephard, and it is not skew. Skew
    is the third standardised moment and is dimensionless in a way that hides
    magnitude; this is a difference of two variances, so a window with a few
    large up-moves and many small down-moves reads strongly positive here and
    can read either sign as skew depending on how the small moves are spread.

    The published result the family is built on is that the two halves forecast
    differently -- downside realised variance carries the risk premium and
    upside does not -- so separating them IS the hypothesis, and adding them
    back together is what every volatility reading in the study already does.

    Divided by total realised variance, so the range is [-1, 1] and a level is
    comparable between symbols.
    """
    n = len(values)
    out = [None] * n
    if period < 10:
        return out
    returns = _log_returns(values)
    ups = [r * r if r > 0 else 0.0 for r in returns]
    downs = [r * r if r < 0 else 0.0 for r in returns]
    up = down = 0.0
    for index in range(n):
        up += ups[index]
        down += downs[index]
        if index >= period:
            up -= ups[index - period]
            down -= downs[index - period]
        if index < period:
            continue
        total = up + down
        if total <= 1e-24:
            continue
        out[index] = (up - down) / total
    return out


def amihud(bars, period):
    """Amihud illiquidity: absolute return per unit of volume, averaged.

    PRICE IMPACT, WHICH IS A RATIO AND NOT EITHER OF ITS PARTS. `rvol` reads
    volume against its own history and is silent about what the volume achieved;
    every return-based reading in the study is silent about what it cost. The
    ratio is the only one of the three that can say "today's move was bought
    cheaply", and that statement is the whole illiquidity-premium literature.

    Scaled by 1e6 so the numbers are readable rather than 1e-9, and meant to be
    read as a RATIO against its own trailing mean rather than as a level -- the
    raw magnitude depends on the volume units the table happens to carry, which
    differ between a tick-count feed and a contract-count one.
    """
    n = len(bars)
    out = [None] * n
    if period < 5:
        return out
    ratios = [None] * n
    for index in range(1, n):
        previous, close = bars[index - 1][C], bars[index][C]
        volume = bars[index][V]
        if previous > 0 and close > 0 and volume > 0:
            ratios[index] = abs(math.log(close / previous)) / volume * 1e6
    total, live = 0.0, 0
    for index in range(n):
        if ratios[index] is not None:
            total += ratios[index]
            live += 1
        gone = index - period
        if gone >= 0 and ratios[gone] is not None:
            total -= ratios[gone]
            live -= 1
        if live >= max(5, period // 2):
            out[index] = total / live
    return out


def variance_estimators(bars, period):
    """`(parkinson, garman_klass, close_to_close)` variances over `period` bars.

    THREE ESTIMATORS OF ONE QUANTITY, AND THEIR DISAGREEMENT IS THE SIGNAL.
    Close-to-close uses only closes and therefore counts a bar that travelled
    two percent and came back as zero. Parkinson uses the high-low range and
    counts it in full. Garman-Klass uses the range AND the open-close body, so
    it separates the two.

    The ratio Parkinson/close-to-close is consequently a measure of how much
    movement was retraced WITHIN bars rather than carried between them: high
    when the market is trading a range violently, low when it is gapping and
    trending. Neither ATR nor `trailing_volatility` can express that, because
    each is only one of the two numbers.

    Returned as variances, not standard deviations, and unannualised: every
    consumer here takes ratios, and a shared scaling factor cancels in a ratio
    while costing a square root per bar.
    """
    n = len(bars)
    park = [None] * n
    gk = [None] * n
    cc = [None] * n
    if period < 5:
        return park, gk, cc
    log4 = 4.0 * math.log(2.0)
    body_scale = 2.0 * math.log(2.0) - 1.0
    range_terms = [0.0] * n
    body_terms = [0.0] * n
    return_terms = [0.0] * n
    for index, bar in enumerate(bars):
        high, low, open_, close = bar[H], bar[L], bar[O], bar[C]
        if high > 0 and low > 0 and high >= low:
            span = math.log(high / low)
            range_terms[index] = span * span
        if open_ > 0 and close > 0:
            body = math.log(close / open_)
            body_terms[index] = body * body
        if index and bars[index - 1][C] > 0 and close > 0:
            move = math.log(close / bars[index - 1][C])
            return_terms[index] = move * move
    sr = sb = sc = 0.0
    for index in range(n):
        sr += range_terms[index]
        sb += body_terms[index]
        sc += return_terms[index]
        if index >= period:
            sr -= range_terms[index - period]
            sb -= body_terms[index - period]
            sc -= return_terms[index - period]
        if index < period:
            continue
        park[index] = sr / period / log4
        gk[index] = max(0.0, 0.5 * sr / period - body_scale * sb / period)
        cc[index] = sc / period
    return park, gk, cc


def bulk_imbalance(bars, period):
    """Signed volume share, classified by where the bar CLOSED in its own range.

    A bar-resolution stand-in for order-flow imbalance, and the honest name for
    it is bulk volume classification: the fraction of a bar's volume treated as
    buying is the close's location within the bar's range, so a bar that closed
    on its high is all buying and one that closed mid-range is neither.

    NOT `on_balance_volume`. OBV assigns a bar's ENTIRE volume to whichever way
    the close moved, so a bar that rose one tick and a bar that rose two percent
    contribute identically and a doji contributes nothing at all. This is
    continuous in the close's location, which means it can report "heavy volume,
    and the buyers barely won" -- the state that precedes a failed breakout, and
    the one OBV records as an unambiguous accumulation bar.

    Returned as a share in [-1, 1] of the window's total volume, so it is a
    proportion rather than a level and needs no normalisation by the family.
    """
    n = len(bars)
    out = [None] * n
    if period < 5:
        return out
    signed = [0.0] * n
    total = [0.0] * n
    for index, bar in enumerate(bars):
        volume = bar[V]
        if volume <= 0:
            continue
        span = bar[H] - bar[L]
        share = 0.5 if span <= 0 else (bar[C] - bar[L]) / span
        signed[index] = volume * (2.0 * share - 1.0)
        total[index] = volume
    ss = st = 0.0
    for index in range(n):
        ss += signed[index]
        st += total[index]
        if index >= period:
            ss -= signed[index - period]
            st -= total[index - period]
        if index < period:
            continue
        if st > 0:
            out[index] = ss / st
    return out


# --------------------------------------------------------------------------- #
# filters: the things a moving average is not
# --------------------------------------------------------------------------- #

def roofing_filter(closes, high_period=48, low_period=10):
    """Ehlers' roofing filter: a two-pole highpass, then the SuperSmoother.

    A BANDPASS, AND EVERY OTHER FILTER IN THIS MODULE IS A LOWPASS. Six moving
    averages span a lag spectrum and all six answer the same question -- what is
    left when the fast wiggles are removed. None can remove the SLOW component,
    so none can say "price is high relative to the last two days, and that has
    nothing to do with the six-month trend". Detrending by subtracting a moving
    average is the naive version and it leaks: an SMA has a ragged frequency
    response with sidelobes, so the difference still carries trend energy at
    some periods.

    The highpass is Ehlers' two-pole Butterworth at `high_period`, which removes
    everything slower than that; the SuperSmoother is his two-pole lowpass at
    `low_period`, which removes everything faster. What is left is genuinely a
    band, its mean is zero by construction, and a zero crossing of it is a
    statement about ONE time scale rather than about the sum of all of them.

    The defaults are Ehlers' own -- 48 bars and 10 bars -- and they are BAR
    counts; the caller converts if it wants them in sessions.
    """
    n = len(closes)
    out = [None] * n
    if n < 8 or high_period < 4 or low_period < 3:
        return out
    radians = 0.707 * 2.0 * math.pi / high_period
    alpha = (math.cos(radians) + math.sin(radians) - 1.0) / math.cos(radians)
    gain = (1.0 - alpha / 2.0) ** 2
    highpass = [0.0] * n
    for index in range(2, n):
        a, b, c = closes[index], closes[index - 1], closes[index - 2]
        if a is None or b is None or c is None:
            continue
        highpass[index] = (gain * (a - 2.0 * b + c)
                           + 2.0 * (1.0 - alpha) * highpass[index - 1]
                           - (1.0 - alpha) ** 2 * highpass[index - 2])
    a1 = math.exp(-1.414 * math.pi / low_period)
    b1 = 2.0 * a1 * math.cos(1.414 * math.pi / low_period)
    c2, c3 = b1, -a1 * a1
    c1 = 1.0 - c2 - c3
    smooth = [0.0] * n
    for index in range(2, n):
        smooth[index] = (c1 * (highpass[index] + highpass[index - 1]) / 2.0
                         + c2 * smooth[index - 1] + c3 * smooth[index - 2])
    # The recursion's transient has to die before the reading means anything;
    # three periods of the slower pole is the usual allowance and is cheap at
    # any window this module uses.
    warm = min(n, 3 * high_period)
    for index in range(warm, n):
        out[index] = smooth[index]
    return out


def fisher_transform(bars, period):
    """Ehlers' Fisher transform of the close's position in its recent range.

    A DISTRIBUTIONAL TRANSFORM, WHICH THE STUDY HAD NONE OF. Where a price sits
    in its range is roughly uniformly distributed -- which is why `stochastic`
    spends so much of its life in the middle and why a threshold on it is
    arbitrary. The Fisher transform maps that uniform variable to something
    close to Gaussian, so its tails become sharp and rare: a reading of 2 is
    genuinely unusual in a way that a stochastic of 90 is not, and the same
    threshold means the same rarity on every symbol.

    That is a different family from `stochastic` and not a rescaling of it,
    because the transform is nonlinear: two bars four stochastic points apart
    near the middle are almost the same Fisher reading, and two bars four points
    apart near the edge are far apart. The turns the transform makes visible are
    exactly the ones a linear oscillator smears.

    The 0.33/0.67 input smoothing and the 0.5 output smoothing are Ehlers'
    constants and are not swept: they are the construct.
    """
    n = len(bars)
    out = [None] * n
    if period < 3:
        return out
    highs = [b[H] for b in bars]
    lows = [b[L] for b in bars]
    top = rolling_extreme_inclusive(highs, period, True)
    bottom = rolling_extreme_inclusive(lows, period, False)
    value = 0.0
    fish = 0.0
    for index in range(n):
        high, low = top[index], bottom[index]
        if high is None or low is None or high <= low:
            continue
        median = (bars[index][H] + bars[index][L]) / 2.0
        raw = 2.0 * ((median - low) / (high - low)) - 1.0
        value = 0.33 * raw + 0.67 * value
        value = max(-0.999, min(0.999, value))
        fish = 0.5 * math.log((1.0 + value) / (1.0 - value)) + 0.5 * fish
        if index >= period:
            out[index] = fish
    return out


def kalman_trend(closes, process=1e-4, measurement=1e-2):
    """`(level, slope)` from a local-linear-trend Kalman filter on log price.

    THE ONLY FILTER HERE WHOSE WEIGHTING IS NOT FIXED IN ADVANCE. Every moving
    average applies the same kernel to every bar forever; `kama` adapts its
    period from the efficiency ratio, which is a heuristic bolted onto a fixed
    filter. This solves the estimation problem directly: it carries a covariance,
    and the gain it applies to each new bar is the optimal one GIVEN how noisy
    the series has actually been. In a quiet stretch it trusts its own state and
    barely moves; after a violent bar the covariance opens and it re-anchors
    fast. No fixed kernel does both.

    It also returns a SLOPE as a state rather than as a difference of two
    smoothed points, which is what `linreg` and `xma_slope` both do. A
    differenced slope is the slope of the past window; this is the filter's
    current estimate of the rate, and after a turn the two have opposite signs
    for as long as the window is wide.

    The two variances matter only through their RATIO, and they are fixed rather
    than swept: sweeping them would be sweeping how much to smooth, which the
    `trend` axis and the moving-average zoo already ask, and would turn one
    estimator into a grid.
    """
    n = len(closes)
    level_out = [None] * n
    slope_out = [None] * n
    level = slope = None
    # State covariance, symmetric 2x2 as (p00, p01, p11).
    p00 = p01 = p11 = 1.0
    for index in range(n):
        close = closes[index]
        if close is None or close <= 0:
            continue
        observed = math.log(close)
        if level is None:
            level, slope = observed, 0.0
            continue
        # Predict: level advances by the slope, the slope persists, covariance
        # grows by the process noise.
        level += slope
        p00 = p00 + 2.0 * p01 + p11 + process
        p01 = p01 + p11
        p11 = p11 + process
        innovation = observed - level
        s = p00 + measurement
        k0, k1 = p00 / s, p01 / s
        level += k0 * innovation
        slope += k1 * innovation
        # `p01` is read twice on the right, so the update order matters: p11
        # must consume the PRE-update p01.
        new_p00 = p00 - k0 * p00
        new_p01 = p01 - k0 * p01
        p11 = p11 - k1 * p01
        p00, p01 = new_p00, new_p01
        level_out[index] = math.exp(level)
        slope_out[index] = slope
    return level_out, slope_out


def frac_diff(closes, order, width=64, threshold=1e-4):
    """Fractionally differenced log price -- Lopez de Prado's memory trick.

    THE POINT IS WHAT IT KEEPS. A price is non-stationary and a return is
    stationary but has thrown away every level; a z-score splits the difference
    by subtracting a rolling mean, which is a full difference of a smoothed
    series and destroys memory beyond the window. A fractional difference of
    order d in (0, 1) is the continuum between the two: it is stationary at a
    high enough d while its weights decay as a POWER LAW rather than being
    truncated, so the reading at bar i still carries information from hundreds
    of bars ago in a way `zscore` structurally cannot.

    So this and `zscore` disagree exactly where it matters -- after a long slow
    drift. The z-score's mean has followed the drift and reports no deviation;
    the fractional difference has not forgotten where the series started and
    reports a large one.

    Fixed-width window, weights truncated once one falls below `threshold`,
    which is the standard construction: it keeps the reading's memory the same
    length at every bar rather than growing it, so early and late readings are
    comparable.
    """
    n = len(closes)
    out = [None] * n
    if n < 8 or not 0.0 < order < 1.0:
        return out
    weights = [1.0]
    for k in range(1, width):
        weight = -weights[-1] * (order - k + 1.0) / k
        if abs(weight) < threshold:
            break
        weights.append(weight)
    span = len(weights)
    logs = [None if c is None or c <= 0 else math.log(c) for c in closes]
    for index in range(span - 1, n):
        window = logs[index - span + 1:index + 1]
        if any(v is None for v in window):
            continue
        # `weights[0]` multiplies the MOST RECENT observation, so the window is
        # consumed backwards. Getting this the wrong way round produces a
        # perfectly plausible series that is a smoothing rather than a
        # differencing.
        out[index] = sum(w * window[-1 - k] for k, w in enumerate(weights))
    return out


def dominant_cycle(closes, minimum=8, maximum=50):
    """The dominant cycle PERIOD in bars, by Ehlers' Hilbert-transform quadrature.

    A MEASURED TIME SCALE, WHICH NOTHING ELSE IN THE STUDY PRODUCES. Every
    lookback here is chosen -- 14 because Wilder chose it, 20 because it is a
    month -- and the search then picks between two of them. This asks the series
    what its own time scale currently is, and answers with a number of bars.

    The construction is the standard one: detrend with a four-tap
    Hilbert-transform kernel, form the in-phase and quadrature components, and
    read the period from the rate of phase rotation. Clamped to
    [minimum, maximum] and rate-limited to a 50% change per bar, both of which
    are Ehlers' own guards against the estimator jumping to a harmonic on one
    noisy bar.

    The family reading it does not trade the period; it trades whether the
    period is SHORT or LONG -- the market saying whether it is currently
    oscillating quickly, a condition under which a fade has a defined horizon,
    or slowly.
    """
    n = len(closes)
    out = [None] * n
    if n < 4 * maximum + 8:
        return out
    smooth = [0.0] * n
    detrend = [0.0] * n
    q1 = [0.0] * n
    i1 = [0.0] * n
    period = [0.0] * n
    for index in range(n):
        if index < 7 or any(closes[index - k] is None for k in range(4)):
            continue
        smooth[index] = (4.0 * closes[index] + 3.0 * closes[index - 1]
                         + 2.0 * closes[index - 2] + closes[index - 3]) / 10.0
        previous = period[index - 1] or minimum
        factor = 0.075 * previous + 0.54
        detrend[index] = (0.0962 * smooth[index] + 0.5769 * smooth[index - 2]
                          - 0.5769 * smooth[index - 4]
                          - 0.0962 * smooth[index - 6]) * factor
        q1[index] = (0.0962 * detrend[index] + 0.5769 * detrend[index - 2]
                     - 0.5769 * detrend[index - 4]
                     - 0.0962 * detrend[index - 6]) * factor
        i1[index] = detrend[index - 3]
        # Phase advance between consecutive quadrature pairs; a full turn
        # divided by it is the period.
        dq = q1[index] - q1[index - 1]
        di = i1[index] - i1[index - 1]
        numerator = i1[index] * dq - q1[index] * di
        denominator = i1[index] * i1[index] + q1[index] * q1[index]
        if denominator <= 1e-18 or numerator == 0.0:
            value = previous
        else:
            value = 2.0 * math.pi * denominator / abs(numerator)
            value = max(0.67 * previous, min(1.5 * previous, value))
            value = max(minimum, min(maximum, value))
        # Ehlers smooths the period itself; without it the reading is unusable.
        period[index] = 0.2 * value + 0.8 * previous
        if index >= 4 * maximum:
            out[index] = period[index]
    return out


# --------------------------------------------------------------------------- #
# adaptive horizons and event thresholds
# --------------------------------------------------------------------------- #

def ou_half_life(closes, period, floor=2, ceiling=None):
    """The Ornstein-Uhlenbeck half-life of mean reversion, in BARS.

    THE HORIZON, ESTIMATED RATHER THAN CHOSEN. `zscore` takes a period from
    `p["zscore"]` and the search picks whichever of three worked; this regresses
    the one-bar change on the level -- dp = lambda*p + c -- and converts the
    fitted lambda into the time a deviation takes to decay by half. When the
    fitted lambda is not negative the series is not reverting at all and the
    reading is `None`, which is a refusal rather than a large number.

    So the family built on it re-measures its own lookback every bar, and it is
    genuinely a different rule from any fixed-period z-score: on the same series
    it uses a 12-bar window in a fast-reverting stretch and a 200-bar window in
    a slow one, and no cell of `zscore` does both.

    `ceiling` defaults to `period`, because a half-life longer than the sample
    it was estimated from is an extrapolation and not a measurement.
    """
    n = len(closes)
    out = [None] * n
    if period < 20:
        return out
    limit = ceiling or period
    logs = [None if c is None or c <= 0 else math.log(c) for c in closes]
    diffs = [0.0] * n
    for index in range(1, n):
        if logs[index] is not None and logs[index - 1] is not None:
            diffs[index] = logs[index] - logs[index - 1]
    sx = sy = sxy = sxx = 0.0
    for index in range(n):
        if index and logs[index - 1] is not None:
            x, y = logs[index - 1], diffs[index]
            sx += x
            sy += y
            sxy += x * y
            sxx += x * x
        gone = index - period
        if gone >= 1 and logs[gone - 1] is not None:
            x, y = logs[gone - 1], diffs[gone]
            sx -= x
            sy -= y
            sxy -= x * y
            sxx -= x * x
        if index < period + 1:
            continue
        count = period
        variance = sxx / count - (sx / count) ** 2
        if variance <= 1e-24:
            continue
        slope = (sxy / count - (sx / count) * (sy / count)) / variance
        if slope >= -1e-9:
            continue
        life = math.log(2.0) / -slope
        # REFUSED RATHER THAN CLAMPED. Clamping to `limit` would report the
        # ceiling as a measurement, and the ceiling is the one value that means
        # "this series is not reverting on any timescale I can see" -- on a
        # trending series the fitted lambda is a hair below zero by chance, the
        # implied half-life is thousands of bars, and a clamp turns that into a
        # confident reading indistinguishable from a genuine slow reverter.
        if life > limit:
            continue
        out[index] = max(floor, life)
    return out


def cusum_events(closes, threshold_series):
    """Lopez de Prado's symmetric CUSUM filter: `1`, `-1` or `0` at each bar.

    A THRESHOLD ON ACCUMULATED DEVIATION, WHICH IS NOT A THRESHOLD ON A BAR.
    Every breakout family here fires when one reading crosses one level, so a
    move made of twenty small steps in the same direction is invisible to all of
    them until it happens to clear a channel. This accumulates the returns since
    the last event and fires when the running sum -- floored at zero on the long
    side and capped at zero on the short -- exceeds the threshold, then RESETS.
    So it fires once per move, on a bar which by construction is not an extreme
    of anything, and it fires on the twenty-small-steps move at the point where
    it has become a move.

    The reset is what makes it an event sampler rather than another oscillator:
    the same drift cannot fire it twice without an intervening retracement,
    which is precisely the property a fixed threshold lacks.

    `threshold_series` is per bar -- the family passes a volatility so the
    filter is scale-free -- and a bar with no threshold reading passes through
    without accumulating, rather than accumulating against a stale level.
    """
    n = len(closes)
    out = [0] * n
    positive = negative = 0.0
    for index in range(1, n):
        a, b = closes[index - 1], closes[index]
        threshold = (threshold_series[index]
                     if index < len(threshold_series) else None)
        if (a is None or b is None or a <= 0 or b <= 0 or threshold is None
                or threshold != threshold or threshold <= 0):
            continue
        move = math.log(b / a)
        positive = max(0.0, positive + move)
        negative = min(0.0, negative + move)
        if positive > threshold:
            out[index] = 1
            positive = negative = 0.0
        elif negative < -threshold:
            out[index] = -1
            positive = negative = 0.0
    return out


def rolling_quantile(values, period, quantile):
    """The `quantile` order statistic of the last `period` values.

    A ROBUST CHANNEL. `rolling_extreme` takes the maximum, which is one bar --
    so a Donchian break is a comparison against the single most extreme print in
    the window, and one bad tick or one news spike sets the level for the whole
    lookback. The 90th percentile of the same window is set by a tenth of the
    bars, so it moves when the DISTRIBUTION moves and not when one bar does.

    The two therefore disagree in a specific and common situation: after a spike
    that is not revisited, the Donchian channel stays wide and refuses every
    entry until it rolls off, while the quantile channel narrows back within a
    few bars and takes the continuation. Which of the two is right is a real
    question and it has never been asked here.

    A sort per bar over the window, O(n*p*log p), paid once at worker start-up
    like every other block. Use `quantile_channel` when both edges are wanted:
    it takes the two off ONE sort instead of two.
    """
    n = len(values)
    out = [None] * n
    if period < 5:
        return out
    for index in range(period - 1, n):
        window = [v for v in values[index - period + 1:index + 1]
                  if v is not None and v == v]
        if len(window) < period:
            continue
        window.sort()
        position = quantile * (len(window) - 1)
        low = int(math.floor(position))
        high = min(len(window) - 1, low + 1)
        weight = position - low
        out[index] = window[low] * (1.0 - weight) + window[high] * weight
    return out


def quantile_channel(values, period, quantile):
    """`(upper, lower)` at `quantile` and `1 - quantile`, from ONE sort per bar.

    A channel needs both edges, and calling `rolling_quantile` twice sorts the
    same window twice. That is the whole reason this exists next to it: the
    sort is the entire cost of the reading, and a channel family sweeping two
    lookbacks would otherwise pay for four passes over the series where two
    will do.
    """
    n = len(values)
    upper = [None] * n
    lower = [None] * n
    if period < 5 or not 0.5 < quantile < 1.0:
        return upper, lower

    def pick(window, share):
        position = share * (len(window) - 1)
        low = int(math.floor(position))
        high = min(len(window) - 1, low + 1)
        weight = position - low
        return window[low] * (1.0 - weight) + window[high] * weight

    for index in range(period - 1, n):
        window = [v for v in values[index - period + 1:index + 1]
                  if v is not None and v == v]
        if len(window) < period:
            continue
        window.sort()
        upper[index] = pick(window, quantile)
        lower[index] = pick(window, 1.0 - quantile)
    return upper, lower
