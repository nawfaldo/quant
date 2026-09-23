"""Bar-driven replica of `live_trade/src/strategies/idk/noise_momentum_2.rs`.

This strategy does not fit `execution.py`'s `Signal`/`Fill` pipeline, for two
reasons that are properties of the strategy rather than of the harness:

  * **Sizing is notional, not risk-fraction.** Quantity is
    `(equity / margin) * min(target_vol / vol, 1) * SIZING_SCALE / price` --
    it has no stop distance in it, so `execution.size` cannot express it.
  * **Exits ladder.** The first target closes half the position and widens the
    bracket for the remainder, so one entry produces up to two closes.

So it is simulated directly over the bars here. Quantity is emitted as a
*per-dollar-of-equity* factor rather than an absolute size, which lets the
shared-account driver in `portfolio_ml.py` multiply it by the live balance at
entry and keep joint compounding intact. Rounding to `quantity_step` is done by
the driver for the same reason.

Every constant and every ordering decision below is transcribed from the Rust
file; the docstrings note where a difference remains.
"""
from dataclasses import dataclass

from sandbox.data import TS, O, H, L, C

OPEN = 9 * 60 + 30
CLOSE = 16 * 60
EXIT = CLOSE - 30
SLOTS = CLOSE - OPEN
LOOKBACK = 14

BOUNDARY_MULTIPLIER = 1.35
TARGET_DAILY_VOLATILITY = 0.024
START_AFTER_OPEN = 30
FREQUENCY = 30
RISK_UNIT_SIGMA = 0.36
LADDER_STOP = (-0.425, -0.25)
LADDER_TARGET = (2.55, 8.0)
LONG_MARGIN = 0.25
SHORT_MARGIN = 0.30
SIZING_SCALE = 1.15

LONG, SHORT = "long", "short"


@dataclass(frozen=True)
class Leg:
    """One closed portion of a position, sized as a fraction of live equity.

    `qty_per_equity` is the *whole* position's quantity per dollar of account
    equity at entry; `fraction` is the share of it this leg closes (0.5 for the
    first rung of the ladder). Keeping them separate lets the driver round the
    full position to `quantity_step` once, as the Rust strategy does, rather
    than rounding each rung. `points` is per-unit PnL net of spread on both legs.
    """
    entry_ts: int
    exit_ts: int
    side: str
    points: float
    price: float
    qty_per_equity: float
    fraction: float


def run(bars, spread=0.2):
    """Every leg the strategy would produce over `bars`, in entry order."""
    half = spread / 2.0
    moves = [[0.0] * LOOKBACK for _ in range(SLOTS)]
    move_counts = [0] * SLOTS
    move_heads = [0] * SLOTS
    daily_returns = [0.0] * LOOKBACK
    return_count = return_head = 0

    current_day = None
    day_open = 0.0
    previous_close = None
    day_last_close = 0.0

    position = None      # (side, entry_ts, entry_price, risk_unit, qty_per_equity)
    ladder_step = 0
    open_fraction = 0.0  # share of the original quantity still held
    legs = []

    def points(side, entry, exit_price):
        if side == LONG:
            return (exit_price - half) - (entry + half)
        return (entry - half) - (exit_price + half)

    def close(exit_ts, exit_price, fraction):
        side, entry_ts, entry_price, _risk, qty = position
        legs.append(Leg(entry_ts, exit_ts, side, points(side, entry_price, exit_price),
                        entry_price, qty, fraction))

    for bar in bars:
        ts = bar[TS]
        day = ts // 86_400
        minute = (ts % 86_400) // 60

        if current_day != day:
            # `start_day`: roll the daily return off the previous session's last
            # close before anything else touches the new day.
            if day_last_close > 0.0 and previous_close and previous_close > 0.0:
                daily_returns[return_head] = day_last_close / previous_close - 1.0
                return_head = (return_head + 1) % LOOKBACK
                return_count = min(return_count + 1, LOOKBACK)
            if day_last_close > 0.0:
                previous_close = day_last_close
            current_day = day
            day_open = 0.0
            day_last_close = 0.0
            position = None
            ladder_step = 0
            open_fraction = 0.0

        if OPEN <= minute < CLOSE:
            if minute == OPEN:
                day_open = bar[O]
            day_last_close = bar[C]

        if minute == EXIT:
            if position is not None:
                close(ts, bar[O], open_fraction)
                position = None
                ladder_step = 0
                open_fraction = 0.0
            continue
        if not (OPEN <= minute < EXIT) or day_open <= 0.0:
            continue

        slot = minute - OPEN
        if move_counts[slot] >= LOOKBACK:
            sigma = sum(moves[slot]) / LOOKBACK
            reference = previous_close if previous_close is not None else day_open
            upper = max(day_open, reference) * (1.0 + BOUNDARY_MULTIPLIER * sigma)
            lower = min(day_open, reference) * (1.0 - BOUNDARY_MULTIPLIER * sigma)
        else:
            upper = lower = 0.0

        if position is not None:
            side, _entry_ts, entry_price, risk_unit, _qty = position
            direction = 1.0 if side == LONG else -1.0
            stop = entry_price + direction * LADDER_STOP[ladder_step] * risk_unit
            stop_hit = bar[L] <= stop if side == LONG else bar[H] >= stop
            if stop_hit:
                price = min(bar[O], stop) if side == LONG else max(bar[O], stop)
                close(ts, price, open_fraction)
                position = None
                ladder_step = 0
                open_fraction = 0.0
            else:
                target = entry_price + direction * LADDER_TARGET[ladder_step] * risk_unit
                target_hit = bar[H] >= target if side == LONG else bar[L] <= target
                if target_hit:
                    price = max(bar[O], target) if side == LONG else min(bar[O], target)
                    if ladder_step == 0:
                        # Half off, the rest rides a wider stop and target.
                        close(ts, price, open_fraction * 0.5)
                        open_fraction *= 0.5
                        ladder_step = 1
                    else:
                        close(ts, price, open_fraction)
                        position = None
                        ladder_step = 0
                        open_fraction = 0.0
        else:
            elapsed = minute - OPEN
            weekday = (day + 3) % 7
            scheduled = (elapsed >= START_AFTER_OPEN
                         and (elapsed - START_AFTER_OPEN) % FREQUENCY == 0
                         and weekday < 5)
            if scheduled and upper > 0.0 and return_count == LOOKBACK:
                long_break = bar[H] >= upper
                short_break = bar[L] <= lower
                if long_break != short_break:
                    side = LONG if long_break else SHORT
                    price = (max(bar[O], upper) if long_break else min(bar[O], lower))
                    mean = sum(daily_returns) / LOOKBACK
                    variance = sum((v - mean) ** 2 for v in daily_returns) / LOOKBACK
                    volatility = variance ** 0.5
                    if volatility > 0.0 and price > 0.0:
                        margin = LONG_MARGIN if side == LONG else SHORT_MARGIN
                        scale = min(TARGET_DAILY_VOLATILITY / volatility, 1.0)
                        qty = (1.0 / margin) * scale * SIZING_SCALE / price
                        position = (side, ts, price, price * RISK_UNIT_SIGMA * volatility,
                                    qty)
                        ladder_step = 0
                        open_fraction = 1.0

        head = move_heads[slot]
        moves[slot][head] = abs(bar[C] / day_open - 1.0)
        move_heads[slot] = (head + 1) % LOOKBACK
        move_counts[slot] = min(move_counts[slot] + 1, LOOKBACK)

    legs.sort(key=lambda leg: (leg.entry_ts, leg.exit_ts))
    return legs
