"""Position lifecycle, costs and equity sizing. Strategy-agnostic.

Replica of `live_trade/src/backtest/engine.rs` plus the parts every `strategies/idk`
strategy repeats: bracketed entries at a bar open, stop-before-target exits, a
session-close flatten, spread charged wholly at entry, and risk/margin position
sizing off live equity.

A strategy contributes only `Signal`s. Splitting it here matters for more than
tidiness: signals never depend on account equity, so a signal's outcome *per
unit* can be resolved once and re-sized for free. Sweeps exploit that, and
because sizing has a single implementation the fast path cannot drift from the
slow one.
"""
import math
from dataclasses import dataclass, replace

from sandbox.data import C, D, DE, H, L, O, TS  # noqa: F401  (re-exported for strategies)

LONG, SHORT = "long", "short"


@dataclass(frozen=True)
class Signal:
    """A strategy's decision to open a bracketed position at `index`'s open.

    `max_minutes` adds an optional time stop: the position leaves at the open of
    the first bar at least that many minutes after entry. Strategies that only
    exit on their bracket leave it None.

    `trail` adds an optional trailing stop `trail` points behind the best price
    the position has seen. It only ever tightens: the exit is the *closer* of
    the fixed stop and the trail, so a trailing position can never risk more
    than `stop`, and `stop` stays the number sizing is computed from.

    The Rust engine has no trailing leg, so a strategy that sets `trail` is
    replica-only and `validation.py` cannot check it against `/api/run`.
    """
    index: int
    side: str
    stop: float
    target: float
    max_minutes: int | None = None
    trail: float | None = None


@dataclass(frozen=True)
class Fill:
    """A resolved, size-independent trade outcome. `points` is per unit."""
    entry_ts: int
    exit_ts: int
    side: str
    points: float
    price: float
    stop: float


@dataclass(frozen=True)
class Execution:
    """Account and cost model. Defaults are the `idk` environment (id 2).

    COST HAS THREE PARTS and they are separate fields because they are three
    different things, measured three different ways. `entry_cost` sums them; all
    three are charged once, at entry.
    """
    initial: float = 10_000.0
    #: Quoted bid-ask spread. ZERO, and measured rather than assumed: 218 live
    #: fills across nine Exness symbols on 2026-08-12 quoted 0.00 spread on every
    #: one, because this is a zero-spread account that bills commission instead.
    #: Kept as its own field, not folded away, so moving to a spread account is a
    #: one-line change rather than a re-derivation.
    spread: float = 0.0
    #: Execution slippage, in price points. This is what the 0.2 that used to
    #: live in `spread` always was. Measured over the same 218 fills: the median
    #: is 0 on every symbol and the mean is symmetric noise, not a systematic
    #: cost -- BTCUSD was worst at +0.007 pts (p90 0.34, max 1.39) and USTEC
    #: slipped on 1 fill in 30. So 0.2 is conservative by roughly 28x at minimum
    #: lot. It scales with order size; re-measure before trading larger.
    slippage: float = 0.2
    #: Broker commission, USD per lot, per ROUND TRIP. Exness bills the whole
    #: round trip at entry -- every closing deal books commission 0.00 -- which
    #: is why it belongs with the other charge-once-at-entry costs rather than
    #: being split across the two legs.
    #:
    #: FIXED PER LOT, not proportional to notional: ETHBTC billed exactly
    #: 0.57 x $120.00 on a real trade and 0.01 x $120.00 on a test, XNGUSD
    #: exactly $70/lot at two sizes. USTEC measured $1.24-$1.40/lot; the
    #: conservative end is used. This is the cost the book omitted entirely.
    commission_per_lot: float = 1.40
    margin: float = 0.25         # LONG_MARGIN_REQUIREMENT
    step: float = 0.01           # QUANTITY_STEP (Forex)
    point_value: float = 1.0     # Instrument::Forex
    risk: float = 0.005          # ENTRY_RISK_FRACTION
    leverage: float = 1.0        # ENTRY_LEVERAGE; scales raw size before rounding
    session_end_min: int | None = 960   # flatten before 16:00; None keeps positions

    @property
    def entry_cost(self):
        """Every cost, in PRICE POINTS, charged once at entry.

        `commission_per_lot` is dollars per lot, so it divides by `point_value`
        to reach the same units as the other two. On USTEC (`point_value` 1.0)
        the three sum to 1.60 points against the 0.2 this model used to charge.
        """
        commission = (self.commission_per_lot / self.point_value
                      if self.point_value else 0.0)
        return self.spread + self.slippage + commission

    def with_risk(self, risk):
        return replace(self, risk=risk)


def resolve(bars, signals, ex, intrabar_prices=None, fill_bars=None):
    """Walk `bars` once and turn `signals` into per-unit `Fill`s.

    Ordering matches the Rust strategy exactly: exits are evaluated before the
    bar's own entry (so a position never opens and closes on one bar), the stop
    is checked before the target on ambiguous bars, a time stop is only consulted
    once the bracket has not fired, and the session flatten runs last (so a
    position opened on the final RTH bar is flattened at its close).

    ``intrabar_prices`` optionally maps a bar timestamp to its time-ordered raw
    trade prices.  When both bracket legs occur inside one minute, those prices
    resolve which leg actually traded first.  Missing paths retain the Rust
    engine's conservative stop-before-target convention.
    """
    intrabar_prices = intrabar_prices or {}
    if fill_bars is not None and len(fill_bars) != len(bars):
        raise ValueError("fill_bars must be aligned one-to-one with bars "
                         f"({len(fill_bars)} against {len(bars)})")
    pending = {}
    for signal in signals:
        pending.setdefault(signal.index, []).append(signal)

    cost = ex.entry_cost

    def points(side, entry, exit_price):
        # The whole cost is taken at entry and none of it at exit, matching
        # `fill_entry`/`fill_exit` in the Rust engine. A round trip still pays
        # each component exactly once; charging it up front just makes the entry
        # the price actually paid, which is what the brackets are measured from.
        # Exness bills commission this way too, so the convention is now the
        # broker's behaviour rather than only a modelling choice.
        if side == LONG:
            return (exit_price - (entry + cost)) * ex.point_value
        return ((entry - cost) - exit_price) * ex.point_value

    fills = []
    open_positions = []   # [side, entry_price, stop, target, entry_ts, max_minutes]
    total = len(bars)
    for i, bar in enumerate(bars):
        ts = bar[TS]
        day = ts // 86_400
        minute = (ts % 86_400) // 60
        # `bar` decides, `fbar` pays. Identical objects when no alternate
        # execution feed was supplied, so the single-feed path is unchanged.
        fbar = bar if fill_bars is None else fill_bars[i]
        bar_open, high, low = fbar[O], fbar[H], fbar[L]

        if open_positions:
            still = []
            for position in open_positions:
                side, entry, stop, target, entry_ts, max_minutes, trail, extreme = position
                exit_price = None
                # The trail ratchets off `extreme`, the best price seen on bars
                # that have already *closed*. Using this bar's own high would
                # let the stop tighten on a move whose ordering inside the
                # minute is unknown, and then exit at a price the trail only
                # reached later in the same minute.
                if side == LONG:
                    stop_price = entry - stop
                    if trail:
                        stop_price = max(stop_price, extreme - trail)
                    stop_hit = low <= stop_price
                    target_hit = target > 0 and high >= entry + target
                    if stop_hit and target_hit and ts in intrabar_prices:
                        for traded in intrabar_prices[ts]:
                            if traded <= stop_price:
                                exit_price = min(traded, stop_price)
                                break
                            if traded >= entry + target:
                                exit_price = max(traded, entry + target)
                                break
                    elif stop_hit:
                        exit_price = min(bar_open, stop_price)
                    elif target_hit:
                        exit_price = max(bar_open, entry + target)
                else:
                    stop_price = entry + stop
                    if trail:
                        stop_price = min(stop_price, extreme + trail)
                    stop_hit = high >= stop_price
                    target_hit = target > 0 and low <= entry - target
                    if stop_hit and target_hit and ts in intrabar_prices:
                        for traded in intrabar_prices[ts]:
                            if traded >= stop_price:
                                exit_price = max(traded, stop_price)
                                break
                            if traded <= entry - target:
                                exit_price = min(traded, entry - target)
                                break
                    elif stop_hit:
                        exit_price = max(bar_open, stop_price)
                    elif target_hit:
                        exit_price = min(bar_open, entry - target)
                if (exit_price is None and max_minutes is not None
                        and minute >= (entry_ts % 86_400) // 60 + max_minutes):
                    exit_price = bar_open
                if exit_price is None:
                    position[7] = max(extreme, high) if side == LONG else min(extreme, low)
                    still.append(position)
                else:
                    fills.append(Fill(entry_ts, ts, side, points(side, entry, exit_price),
                                      entry, stop))
            open_positions = still

        for signal in pending.get(i, ()):
            # A trail tighter than the fixed stop binds from the first bar, so
            # it *is* the initial risk. Recording that rather than the wider
            # fixed stop keeps `size` dividing the risk budget by the distance
            # the position can actually lose.
            risk = (min(signal.stop, signal.trail) if signal.trail
                    else signal.stop)
            open_positions.append([signal.side, bar_open, risk, signal.target, ts,
                                   signal.max_minutes, signal.trail, bar_open])

        if open_positions and ex.session_end_min is not None and minute < ex.session_end_min:
            nxt = bars[i + 1] if i + 1 < total else None
            if (nxt is None or nxt[TS] // 86_400 != day
                    or (nxt[TS] % 86_400) // 60 >= ex.session_end_min):
                close = fbar[C]
                for side, entry, stop, _t, entry_ts, _m, _tr, _e in open_positions:
                    fills.append(Fill(entry_ts, ts, side, points(side, entry, close),
                                      entry, stop))
                open_positions = []

    return fills


def size(fills, ex):
    """Apply live-equity position sizing to `fills`. Returns [(entry_ts, pnl)].

    Equity compounds, so a fill's size depends on every trade that closed before
    it opened; an event queue replays that in entry order. Trades too small to
    round up to one `step` are skipped, as the strategy does.
    """
    equity = ex.initial
    queue = []    # (exit_ts, pnl, entry_ts)
    sized = []
    for fill in sorted(fills, key=lambda f: f.entry_ts):
        queue.sort()
        while queue and queue[0][0] <= fill.entry_ts:
            _exit_ts, pnl, entry_ts = queue.pop(0)
            equity += pnl
            sized.append((entry_ts, pnl))
        raw = min(equity * ex.risk / fill.stop,
                  equity / ex.margin / fill.price) * ex.leverage
        quantity = math.floor(raw / ex.step) * ex.step
        if quantity < ex.step:
            continue
        queue.append((fill.exit_ts, fill.points * quantity, fill.entry_ts))
    for _exit_ts, pnl, entry_ts in sorted(queue):
        equity += pnl
        sized.append((entry_ts, pnl))
    return sized


def run(bars, signals, ex):
    """Signals -> sized trades. The one path every caller uses."""
    return size(resolve(bars, signals, ex), ex)
