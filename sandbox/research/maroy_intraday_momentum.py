"""Maroy (2025) intraday momentum exit strategies, ported to BTCUSD.

Implements `ssrn-5095349` -- "Improvements to Intraday Momentum Strategies Using
Parameter Optimization and Different Exit Strategies" -- which extends Zarattini,
Aziz & Barbon (2024) by holding the entry rule fixed (price leaves a per-minute
'noise area') and varying only how the position is closed. Eight exit families
result, and this module runs each one on BTC with the paper's own published
parameter sets. Nothing is re-optimised here: the point is to measure what the
paper's QQQ-fitted answers are worth on a different asset, not to fit new ones.

ENTRY (identical for every exit type, per the paper's Section 4). The noise area
at offset m of a session is

    UB(m) = max(open_today, prev_close) * (1 + k_enter * sigma(m))
    LB(m) = min(open_today, prev_close) * (1 - k_enter * sigma(m))

with sigma(m) the mean over the last `lookback_days` sessions of the absolute
move from that session's open to its close at the *same* offset m. The strategy
looks at the market only every `trade_frequency_minutes`, starting
`start_trade_after_open_minutes` in, and goes long above UB / short below LB.

CAUSALITY. Both boundaries for offset m are fixed before bar m opens, so filling
a resting stop at the boundary is a real order, not hindsight. Everything else
follows the same rule: the VWAP level used during bar m is the VWAP through bar
m-1, and sigma(m) reads strictly prior sessions. No exit level is ever computed
from the bar it is evaluated against.

SESSION. BTC has no market open, so the paper's intraday frame has to be
imposed. Both readings are run: `rth` uses 09:30-16:00 New York, matching every
earlier BTC family in this directory, and `full` uses the 00:00-24:00 calendar
day, which is the honest 24/7 reading. They are different strategies and are
reported side by side rather than averaged.

CLOCK. `btc_1m` stores New York wall-clock despite the `Z` suffix, verified
against `nq_1m` in `btc_families_research`. RTH is therefore minutes 570-960.

COSTS AND SIZING. `idk` forex conventions: the 0.2 spread is charged wholly at
entry, point value 1.0, quantity step 0.01, 25% margin (4x). Position size is
the paper's equation (3): available margin scaled by
min(1, sigma_target / sigma_symbol) and divided by the live price. On a $1,000
account the margin leg binds almost everywhere, so the volatility target mostly
expresses itself by *reducing* size below 4x, never by raising it.

READ THE RESULT AS A TRANSFER TEST. The parameters were tuned on QQQ over
2014-2024 by maximising Sharpe and alpha with Optuna over eleven axes. Their
Sharpe of 3.0+ is an in-sample number for an asset this run does not touch. A
number here is evidence about BTC only to the extent it survives without any
refitting -- which is exactly why no refitting is offered.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data


OUTPUT = os.path.join(os.path.dirname(__file__), "maroy_intraday_momentum_result.json")

INITIAL = 1_000.0
SPREAD = 0.2          # charged wholly at entry, as in execution.py
POINT_VALUE = 1.0     # Instrument::Forex
QUANTITY_STEP = 0.01  # QUANTITY_STEP (Forex)
MARGIN = 0.25         # LONG_MARGIN_REQUIREMENT -> 4x available margin
LADDER_RISK_FRACTION = 0.02   # the paper's r: 2% of AUM per ladder step unit

#: When non-zero, overrides the fixed spread with a proportional round-trip cost
#: in basis points of notional. Set by `--cost-bps`; 0 keeps the literal spread.
COST_BPS = 0.0

#: New York minutes. RTH matches btc_orb / btc_rth_momentum / btc_families.
SESSIONS = {"rth": (570, 960), "full": (0, 1440)}

#: BTC trades every calendar day, so a year is 365 sessions, not 252.
SESSIONS_PER_YEAR = 365

#: The paper skips the first 60 sessions so every lookback is valid and every
#: strategy is scored over an identical window (Section 5).
WARMUP_SESSIONS = 60


# --------------------------------------------------------------------------- #
# parameter sets
# --------------------------------------------------------------------------- #

#: Tables 9, 10 and 11 of the paper, plus the Section 4.1 time-only reference
#: and the Zarattini baselines of Table 1. `exit_kind` selects the exit machinery:
#: which of the boundary / VWAP / ladder legs are armed alongside the mandatory
#: time exit.
CONFIGS = [
    # -- Section 4.1: time exit alone, on the Zarattini baseline entry ------- #
    dict(name="Time only", exit_kind="time", family="Time only",
         lookback_days=14, k_enter=1.0, k_exit=None, target_vol=0.005,
         start_after=1, frequency=30, exit_before=12),

    # -- Table 1: the Zarattini 'Curr.Band + VWAP' reference ---------------- #
    dict(name="Reference lb14 x1.0", exit_kind="boundary+vwap", family="Boundary & VWAP",
         lookback_days=14, k_enter=1.0, k_exit=1.0, target_vol=0.005,
         start_after=1, frequency=30, exit_before=12),
    dict(name="Reference lb90 x1.3", exit_kind="boundary+vwap", family="Boundary & VWAP",
         lookback_days=90, k_enter=1.3, k_exit=1.3, target_vol=0.005,
         start_after=1, frequency=30, exit_before=12),

    # -- Table 9 ------------------------------------------------------------ #
    dict(name="Boundary & VWAP #1", exit_kind="boundary+vwap", family="Boundary & VWAP",
         lookback_days=5, k_enter=1.05, k_exit=1.05, target_vol=0.030,
         start_after=20, frequency=42, exit_before=12),
    dict(name="Boundary & VWAP #2", exit_kind="boundary+vwap", family="Boundary & VWAP",
         lookback_days=8, k_enter=1.05, k_exit=1.05, target_vol=0.018,
         start_after=58, frequency=45, exit_before=12),
    dict(name="Boundary with different exit #1", exit_kind="boundary",
         family="Boundary with different exit",
         lookback_days=5, k_enter=1.29, k_exit=0.78, target_vol=0.016,
         start_after=7, frequency=35, exit_before=16),
    dict(name="Boundary with different exit #2", exit_kind="boundary",
         family="Boundary with different exit",
         lookback_days=5, k_enter=1.29, k_exit=0.78, target_vol=0.029,
         start_after=7, frequency=35, exit_before=22),

    # -- Table 10 ----------------------------------------------------------- #
    dict(name="Boundary with different exit & VWAP", exit_kind="boundary+vwap",
         family="Boundary with different exit & VWAP",
         lookback_days=4, k_enter=1.14, k_exit=0.35, target_vol=0.030,
         start_after=10, frequency=50, exit_before=31),
    dict(name="VWAP #1", exit_kind="vwap", family="VWAP",
         lookback_days=2, k_enter=1.03, k_exit=None, target_vol=0.013,
         start_after=12, frequency=45, exit_before=13),
    dict(name="VWAP #2", exit_kind="vwap", family="VWAP",
         lookback_days=2, k_enter=0.85, k_exit=None, target_vol=0.014,
         start_after=12, frequency=45, exit_before=13),

    # -- Table 11 ----------------------------------------------------------- #
    dict(name="Boundary & Ladder #1", exit_kind="boundary+ladder",
         family="Boundary & Ladder",
         lookback_days=4, k_enter=0.85, k_exit=0.85, target_vol=0.024,
         start_after=23, frequency=18, exit_before=30,
         ladder=((-0.54, 3.22), (1.73, 4.35))),
    dict(name="Boundary & Ladder #2", exit_kind="boundary+ladder",
         family="Boundary & Ladder",
         lookback_days=4, k_enter=1.08, k_exit=1.08, target_vol=0.020,
         start_after=4, frequency=18, exit_before=30,
         ladder=((-0.54, 3.96), (2.47, 5.09))),
    dict(name="VWAP & Ladder #1", exit_kind="vwap+ladder", family="VWAP & Ladder",
         lookback_days=4, k_enter=1.33, k_exit=None, target_vol=0.025,
         start_after=42, frequency=46, exit_before=30,
         ladder=((-0.41, 2.20), (-0.28, 37.08))),
    dict(name="VWAP & Ladder #2", exit_kind="vwap+ladder", family="VWAP & Ladder",
         lookback_days=4, k_enter=1.33, k_exit=None, target_vol=0.020,
         start_after=13, frequency=15, exit_before=30,
         ladder=((-0.65, 11.72), (2.87, 21.64))),
    dict(name="Ladder #1", exit_kind="ladder", family="Ladder",
         lookback_days=4, k_enter=1.28, k_exit=None, target_vol=0.064,
         start_after=42, frequency=23, exit_before=30,
         ladder=((-0.40, 2.55), (0.16, 3.68))),
    dict(name="Ladder #2", exit_kind="ladder", family="Ladder",
         lookback_days=4, k_enter=1.28, k_exit=None, target_vol=0.052,
         start_after=42, frequency=23, exit_before=30,
         ladder=((-0.40, 2.12), (-0.27, 20.32))),
]


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


def load_sessions(session="rth", symbol="btc"):
    """Minute bars folded into per-day sessions, indexed by minute offset.

    Each session carries arrays of length `span` where index m is the bar that
    opened m minutes after the session start, or None when the feed has a gap.
    Offset indexing (rather than array position) is what makes the noise profile
    comparable across days with different gaps.
    """
    start_min, end_min = SESSIONS[session]
    span = end_min - start_min
    rows = data.query(
        f"SELECT cast(timestamp as long) ts,open,high,low,close,volume "
        f"FROM {symbol}_1m ORDER BY timestamp"
    )

    by_day = {}
    for row in rows:
        ts = int(row[0]) // 1_000_000
        minute = (ts % 86_400) // 60
        if not start_min <= minute < end_min:
            continue
        day = ts // 86_400
        bucket = by_day.get(day)
        if bucket is None:
            bucket = by_day[day] = [None] * span
        bucket[minute - start_min] = (
            ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        )

    sessions = []
    previous_close = None
    for day in sorted(by_day):
        bars = by_day[day]
        present = [bar for bar in bars if bar is not None]
        # A session with a handful of minutes cannot support an intraday frame;
        # it would also poison the noise profile with a near-empty day.
        if len(present) < span // 2:
            continue
        sessions.append({
            "day": day,
            "ts": present[0][0],
            "bars": bars,
            "span": span,
            "open": present[0][1],
            "close": present[-1][4],
            "prev_close": previous_close,
        })
        previous_close = present[-1][4]
    return sessions


def derive(session):
    """Cache the two config-independent per-session series on the session.

    Both the move profile and the VWAP path depend only on the bars, so a search
    that re-runs the same sessions hundreds of times should build them once.
    """
    if "_moves" not in session:
        session["_moves"] = _moves(session)
        session["_vwap"] = _vwap_series(session)
    return session["_moves"], session["_vwap"]


def _moves(session):
    """|close(m)/open - 1| by offset, forward-filled across gaps."""
    span, open_price = session["span"], session["open"]
    out = [0.0] * span
    last = 0.0
    for offset in range(span):
        bar = session["bars"][offset]
        if bar is not None:
            last = abs(bar[4] / open_price - 1.0)
        out[offset] = last
    return out


def _vwap_series(session):
    """Session VWAP *through the previous bar*, by offset.

    Index m holds the VWAP of bars 0..m-1, so a level consulted during bar m
    never contains bar m's own trades.
    """
    span = session["span"]
    out = [None] * span
    volume_sum = price_sum = 0.0
    running = None
    for offset in range(span):
        out[offset] = running
        bar = session["bars"][offset]
        if bar is None:
            continue
        typical = (bar[2] + bar[3] + bar[4]) / 3.0
        if bar[5] > 0:
            price_sum += typical * bar[5]
            volume_sum += bar[5]
        if volume_sum > 0:
            running = price_sum / volume_sum
        else:
            running = typical
    return out


# --------------------------------------------------------------------------- #
# backtest
# --------------------------------------------------------------------------- #


#: The paper's own sizing, and the null policy every search is measured against.
PAPER_POLICY = {"sizing": "paper"}


def _quantity(equity, price, target_vol, symbol_vol, policy, stop_distance, throttle):
    """Position size in units, under `policy`.

    `paper` is equation (3): the whole available margin, scaled down only when
    realised volatility exceeds the target. Every other mode exists because that
    one runs 4x notional on almost every trade, which is where the 70-95%
    drawdowns come from -- the exits were never the binding constraint.
    """
    if price <= 0 or equity <= 0:
        return 0.0
    mode = policy.get("sizing", "paper")
    vol_scale = 1.0 if not symbol_vol else min(1.0, target_vol / symbol_vol)
    cap = policy.get("leverage", 1.0 / MARGIN)

    if mode == "paper":
        notional = equity / MARGIN * vol_scale
    elif mode == "leverage_cap":
        # The paper's rule, but forbidden from exceeding `leverage` x equity.
        notional = min(equity / MARGIN * vol_scale, equity * cap)
    elif mode == "vol_target":
        # Leverage set so the *position's* annualised volatility hits a target,
        # rather than scaling a fixed 4x down. This is the sizing that reacts to
        # BTC's regime instead of to a ceiling.
        annual = symbol_vol * math.sqrt(SESSIONS_PER_YEAR)
        leverage = (policy["vol_target_annual"] / annual) if annual > 0 else cap
        notional = equity * min(leverage, cap)
    elif mode == "risk_per_trade":
        # Classic fixed-fractional: risk `risk_fraction` of equity over the
        # distance to the exit level that is already known at entry.
        if not stop_distance or stop_distance <= 0:
            return 0.0
        units = equity * policy["risk_fraction"] / stop_distance
        notional = min(units * price, equity * cap)
    else:
        raise ValueError(f"unknown sizing mode {mode!r}")

    notional *= throttle
    return math.floor(notional / price / QUANTITY_STEP) * QUANTITY_STEP


def _entry_stop_distance(side, entry, vwap, anchor_hi, anchor_lo, sigma, k_exit,
                         uses_vwap, uses_boundary):
    """Distance from entry to the tightest price-based exit already armed.

    Ladder-only and time-only variants have no such level, so risk-per-trade
    sizing does not apply to them and returns None.
    """
    level = None

    def tighten(candidate):
        nonlocal level
        if candidate is None:
            return
        if level is None or (candidate > level if side == "long" else candidate < level):
            level = candidate

    if uses_boundary and k_exit is not None:
        tighten(anchor_hi * (1.0 + k_exit * sigma) if side == "long"
                else anchor_lo * (1.0 - k_exit * sigma))
    if uses_vwap:
        tighten(vwap)
    if level is None:
        return None
    distance = (entry - level) if side == "long" else (level - entry)
    return distance if distance > 0 else None


def null_side(ts, seed):
    """A deterministic coin flip for `ts`, for the null control.

    Deterministic rather than drawn from an RNG so it is identical across worker
    processes and reproducible between runs, and derived from the bar timestamp
    so two different cells firing on the same bar agree -- which is what keeps
    the null's correlation structure across a grid comparable to the real one.
    """
    return "long" if hashlib.md5(f"{seed}:{ts}".encode()).digest()[0] & 1 else "short"


def _allowed(policy, session, offset, session_name, regime):
    """Entry filters: hour of day, weekday, and daily regime."""
    hours = policy.get("hours")
    if hours is not None:
        if (SESSIONS[session_name][0] + offset) // 60 not in hours:
            return False
    weekdays = policy.get("weekdays")
    if weekdays is not None:
        if datetime.fromtimestamp(session["ts"], timezone.utc).weekday() not in weekdays:
            return False
    for key, (low, high) in policy.get("regime", {}).items():
        value = regime.get(key) if regime else None
        # An absent feature blocks the trade rather than waving it through; a
        # gate that silently stops gating is how a filter looks free.
        if value is None or not low <= value <= high:
            return False
    return True


def run_config(sessions, config, session_name, policy=None, window=None):
    """One parameter set over the whole sample. Returns trades and daily equity.

    `policy` swaps the sizing rule and adds entry filters; None reproduces the
    paper exactly. `policy["null_seed"]` turns the run into a coin-flip control:
    entries fire on the same bars at the same prices, but each direction is
    replaced by a deterministic flip. `window` is a (lo_ts, hi_ts) pair bounding the sessions that
    may be *traded* -- earlier sessions still feed the noise profile and the
    volatility lookback, so an in-sample run and an out-of-sample run see
    identically warmed state and equity starts at INITIAL on the first traded
    session.
    """
    policy = policy or PAPER_POLICY
    regimes = policy.get("regime_features") or {}
    lo_ts, hi_ts = window if window else (None, None)
    dd_threshold = policy.get("dd_threshold")
    dd_throttle = policy.get("dd_throttle", 1.0)
    null_seed = policy.get("null_seed")
    lookback = config["lookback_days"]
    k_enter, k_exit = config["k_enter"], config["k_exit"]
    kind = config["exit_kind"]
    uses_vwap = "vwap" in kind
    uses_boundary = "boundary" in kind
    uses_ladder = "ladder" in kind
    ladder = config.get("ladder")
    span = SESSIONS[session_name][1] - SESSIONS[session_name][0]
    cutoff = span - config["exit_before"]

    equity = INITIAL
    peak = INITIAL
    curve = []          # (day, ts, equity at session close)
    trades = []
    exit_reasons = {}

    profile = []        # last `lookback` move profiles
    sums = [0.0] * span
    closes = []         # session closes, for the symbol volatility
    history = 0

    for session in sessions:
        moves, vwaps = derive(session)
        session_returns = None
        if len(closes) > lookback:
            recent = closes[-(lookback + 1):]
            session_returns = [b / a - 1.0 for a, b in zip(recent, recent[1:])]

        in_window = ((lo_ts is None or session["ts"] >= lo_ts)
                     and (hi_ts is None or session["ts"] < hi_ts))
        ready = (
            len(profile) == lookback
            and session["prev_close"] is not None
            and session_returns is not None
            and history >= WARMUP_SESSIONS
            and in_window
        )

        if ready:
            symbol_vol = (statistics.pstdev(session_returns)
                          if len(session_returns) > 1 else 0.0)
            anchor_hi = max(session["open"], session["prev_close"])
            anchor_lo = min(session["open"], session["prev_close"])
            regime = regimes.get(session["day"])
            bars = session["bars"]

            position = None
            for offset in range(span):
                bar = bars[offset]
                if bar is None:
                    continue
                sigma = sums[offset] / lookback
                _ts, bar_open, high, low, close, _volume = bar

                closed_here = False
                if position is not None:
                    forced = offset >= cutoff
                    result = _resolve_exit(
                        position, bar, offset, forced, vwaps[offset],
                        anchor_hi, anchor_lo, sigma, k_exit,
                        uses_vwap, uses_boundary, uses_ladder,
                    )
                    for price, quantity, reason in result["closed"]:
                        pnl = _pnl(position["side"], position["entry"], price, quantity)
                        equity += pnl
                        trades.append({
                            "ts": position["entry_ts"], "exit_ts": bar[0],
                            "side": position["side"], "pnl": pnl,
                            "quantity": quantity, "reason": reason,
                            "entry": position["entry"],
                        })
                        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
                    if position["quantity"] <= 1e-9:
                        position = None
                        # Re-entry waits for the next bar: the order in which
                        # this minute hit the stop and then the entry boundary
                        # is unknowable from OHLC.
                        closed_here = True

                if position is None and not closed_here and offset < cutoff:
                    is_check = (offset >= config["start_after"]
                                and (offset - config["start_after"]) % config["frequency"] == 0)
                    if is_check:
                        upper = anchor_hi * (1.0 + k_enter * sigma)
                        lower = anchor_lo * (1.0 - k_enter * sigma)
                        side = entry = None
                        # A resting stop at the boundary: the level was known
                        # before this bar opened, so a gap through it fills at
                        # the open, never at the (better) boundary price.
                        if high >= upper:
                            side, entry = "long", max(bar_open, upper)
                        elif low <= lower:
                            side, entry = "short", min(bar_open, lower)
                        if side is not None and not _allowed(policy, session, offset,
                                                            session_name, regime):
                            side = None
                        if side is not None and null_seed is not None:
                            # Keep the entry's timing and fill price, replace only
                            # its direction. The control then differs from the real
                            # cell in exactly the thing under test.
                            side = null_side(bar[0], null_seed)
                        if side is not None:
                            # Cut size while the equity curve is underwater. The
                            # throttle reads only closed PnL, so it is a rule the
                            # account could actually follow in real time.
                            throttle = 1.0
                            if dd_threshold is not None and equity < peak * (1 - dd_threshold):
                                throttle = dd_throttle
                            distance = _entry_stop_distance(
                                side, entry, vwaps[offset], anchor_hi, anchor_lo,
                                sigma, k_exit, uses_vwap, uses_boundary)
                            quantity = _quantity(equity, entry, config["target_vol"],
                                                 symbol_vol, policy, distance, throttle)
                            if quantity >= QUANTITY_STEP:
                                position = _open(side, entry, quantity, bar[0], equity,
                                                 ladder if uses_ladder else None)

            if position is not None:
                # The cutoff always lands inside the session, so an open
                # position here means the feed ended early. Flatten on the last
                # bar rather than carrying risk across the gap.
                last = next(b for b in reversed(bars) if b is not None)
                pnl = _pnl(position["side"], position["entry"], last[4],
                           position["quantity"])
                equity += pnl
                trades.append({
                    "ts": position["entry_ts"], "exit_ts": last[0],
                    "side": position["side"], "pnl": pnl,
                    "quantity": position["quantity"], "reason": "session_end",
                    "entry": position["entry"],
                })
                exit_reasons["session_end"] = exit_reasons.get("session_end", 0) + 1

        if in_window:
            curve.append((session["day"], session["ts"], equity))
            peak = max(peak, equity)
        closes.append(session["close"])
        history += 1
        profile.append(moves)
        for offset in range(span):
            sums[offset] += moves[offset]
        if len(profile) > lookback:
            dropped = profile.pop(0)
            for offset in range(span):
                sums[offset] -= dropped[offset]

    return {"trades": trades, "curve": curve, "exit_reasons": exit_reasons}


def _open(side, entry, quantity, ts, equity, ladder):
    position = {
        "side": side, "entry": entry, "quantity": quantity,
        "entry_ts": ts, "ladder": ladder, "step": 0,
    }
    if ladder is not None:
        # r is a price distance: 2% of AUM spread over the units actually held,
        # so a ladder step means the same fraction of the account regardless of
        # how large the position is.
        position["r"] = (LADDER_RISK_FRACTION * equity) / quantity if quantity else 0.0
    return position


def _cost(entry):
    """The entry's cost in price units.

    The requested 0.2 spread is an absolute number, which on a $50,000 asset is
    0.0004% -- three orders of magnitude below what a crypto venue charges. When
    `COST_BPS` is set it replaces the fixed spread with a proportional one, which
    is the only way to see whether a result survives its own turnover.
    """
    return entry * COST_BPS / 10_000.0 if COST_BPS else SPREAD


def _pnl(side, entry, exit_price, quantity):
    """Cost charged wholly at entry, matching execution.py's spread convention."""
    cost = _cost(entry)
    if side == "long":
        return (exit_price - (entry + cost)) * quantity * POINT_VALUE
    return ((entry - cost) - exit_price) * quantity * POINT_VALUE


def _resolve_exit(position, bar, offset, forced, vwap, anchor_hi, anchor_lo,
                  sigma, k_exit, uses_vwap, uses_boundary, uses_ladder):
    """Close whatever this bar closes. Stops resolve before take-profits.

    Every non-ladder exit is a one-sided level -- 'leave if price trades back
    through it' -- so several armed legs collapse to the single tightest level,
    and only the take-profit needs separate handling.
    """
    _ts, bar_open, high, low, _close, _volume = bar
    side, entry = position["side"], position["entry"]
    closed = []

    stop_level, stop_reason = None, None

    def tighten(level, reason):
        nonlocal stop_level, stop_reason
        if level is None:
            return
        if stop_level is None or (level > stop_level if side == "long" else level < stop_level):
            stop_level, stop_reason = level, reason

    if uses_boundary and k_exit is not None:
        if side == "long":
            tighten(anchor_hi * (1.0 + k_exit * sigma), "boundary")
        else:
            tighten(anchor_lo * (1.0 - k_exit * sigma), "boundary")
    if uses_vwap:
        tighten(vwap, "vwap")
    if uses_ladder:
        step = position["ladder"][position["step"]]
        offset_r = step[0] * position["r"]
        tighten(entry + offset_r if side == "long" else entry - offset_r, "ladder_stop")

    if forced:
        closed.append((bar_open, position["quantity"], "time"))
        position["quantity"] = 0.0
        return {"closed": closed}

    hit = (stop_level is not None
           and (low <= stop_level if side == "long" else high >= stop_level))
    if hit:
        price = (min(bar_open, stop_level) if side == "long"
                 else max(bar_open, stop_level))
        closed.append((price, position["quantity"], stop_reason))
        position["quantity"] = 0.0
        return {"closed": closed}

    if uses_ladder:
        step = position["ladder"][position["step"]]
        target = (entry + step[1] * position["r"] if side == "long"
                  else entry - step[1] * position["r"])
        reached = high >= target if side == "long" else low <= target
        if reached:
            price = max(bar_open, target) if side == "long" else min(bar_open, target)
            final = position["step"] + 1 >= len(position["ladder"])
            if final:
                closed.append((price, position["quantity"], "ladder_target"))
                position["quantity"] = 0.0
            else:
                part = math.floor((position["quantity"] * 0.5) / QUANTITY_STEP) * QUANTITY_STEP
                if part >= QUANTITY_STEP:
                    closed.append((price, part, "ladder_partial"))
                    position["quantity"] = round(position["quantity"] - part, 8)
                position["step"] += 1

    return {"closed": closed}


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def summarise(result, label, warmup=WARMUP_SESSIONS):
    """`warmup` sessions are dropped from the return series. A windowed run has
    already excluded them, so it passes 0."""
    curve = [row for row in result["curve"]]
    equities = [row[2] for row in curve]
    trades = result["trades"]
    if len(equities) < 2:
        return {"strategy": label, "trades": 0}

    returns = []
    for previous, current in zip(equities, equities[1:]):
        returns.append(current / previous - 1.0 if previous > 0 else 0.0)
    # Only score sessions the strategy could actually trade.
    returns = returns[warmup:]

    final = equities[-1]
    years = len(returns) / SESSIONS_PER_YEAR
    total_return = final / INITIAL - 1.0
    cagr = ((final / INITIAL) ** (1 / years) - 1.0) if years > 0 and final > 0 else -1.0
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns) if returns else 0.0
    sharpe = (mean / volatility * math.sqrt(SESSIONS_PER_YEAR)) if volatility else 0.0

    peak, drawdown = equities[0], 0.0
    for value in equities:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak > 0 else 0.0)

    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)

    return {
        "strategy": label,
        "final_equity": round(final, 2),
        "total_return_pct": round(100 * total_return, 1),
        "cagr_pct": round(100 * cagr, 1),
        "sharpe": round(sharpe, 2),
        "ann_vol_pct": round(100 * volatility * math.sqrt(SESSIONS_PER_YEAR), 1),
        "max_drawdown_pct": round(100 * drawdown, 1),
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0.0,
        "exit_reasons": result["exit_reasons"],
    }


def yearly(result):
    """Per-calendar-year return, from session-close equity."""
    by_year = {}
    for day, ts, equity in result["curve"]:
        year = datetime.fromtimestamp(ts, timezone.utc).year
        by_year.setdefault(year, []).append(equity)
    out = {}
    previous = INITIAL
    for year in sorted(by_year):
        last = by_year[year][-1]
        out[year] = round(100 * (last / previous - 1.0), 1) if previous > 0 else None
        previous = last
    return out


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def spread_sweep(names, args, spreads):
    """Total return at each spread, plus the spread that takes each strategy to 0.

    The break-even spread is the only cost number worth quoting to a broker: it
    says how much execution a strategy can absorb before its edge is gone, which
    is comparable across venues in a way that any single assumed fee is not.
    Sessions are loaded once and re-run per spread, since the QuestDB read
    dominates.
    """
    global SPREAD
    report = {"spreads": spreads, "sessions": {}}
    for name in names:
        sessions = load_sessions(name, args.symbol)
        print(f"\n{'=' * 108}")
        print(f"TOTAL RETURN % BY SPREAD (points)  --  {args.symbol.upper()}USD "
              f"session={name}  {len(sessions)} sessions")
        print(f"{'=' * 108}")
        print(f"{'exit strategy':36}" + "".join(f"{s:>11}" for s in spreads)
              + f"{'break-even':>13}")
        print("-" * 108)

        rows = []
        for config in CONFIGS:
            returns = []
            for spread in spreads:
                SPREAD = spread
                summary = summarise(run_config(sessions, config, name), config["name"])
                returns.append(summary["total_return_pct"])
            # Linear interpolation between the last profitable spread and the
            # first losing one. Return is monotone in cost, so the crossing is
            # unique whenever one exists.
            breakeven = None
            for (low, low_ret), (high, high_ret) in zip(
                    zip(spreads, returns), zip(spreads[1:], returns[1:])):
                if low_ret > 0 >= high_ret:
                    breakeven = low + (high - low) * low_ret / (low_ret - high_ret)
                    break
            if breakeven is None and returns[0] <= 0:
                breakeven = 0.0
            label = f"{breakeven:.1f}" if breakeven is not None else f">{spreads[-1]:g}"
            print(f"{config['name']:36}"
                  + "".join(f"{value:>11.1f}" for value in returns)
                  + f"{label:>13}")
            rows.append({"strategy": config["name"], "family": config["family"],
                         "returns_pct": returns, "breakeven_spread": breakeven})
        report["sessions"][name] = rows

    SPREAD = 0.2
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", choices=[*SESSIONS, "both"], default="both")
    parser.add_argument("--symbol", default="btc")
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--cost-bps", type=float, default=0.0,
                        help="replace the fixed spread with this many bps of notional")
    parser.add_argument("--spread", type=float, default=None,
                        help="spread in points, charged wholly at entry")
    parser.add_argument("--spread-sweep", default=None,
                        help="comma-separated spreads in points; finds each "
                             "strategy's break-even and skips the per-spread tables")
    args = parser.parse_args()

    global COST_BPS, SPREAD
    COST_BPS = args.cost_bps
    if args.spread is not None:
        SPREAD = args.spread
    names = list(SESSIONS) if args.session == "both" else [args.session]

    if args.spread_sweep:
        spread_sweep(names, args, [float(v) for v in args.spread_sweep.split(",")])
        return
    report = {
        "paper": "Maroy (2025), Improvements to Intraday Momentum Strategies (ssrn-5095349)",
        "symbol": f"{args.symbol}usd",
        "initial_balance": INITIAL,
        "spread": SPREAD if not COST_BPS else f"{COST_BPS} bps of notional",
        "instrument": "forex (point value 1.0, step 0.01, margin 25%)",
        "parameters": "the paper's published QQQ optima, unchanged -- a transfer test",
        "sessions": {},
    }

    for name in names:
        sessions = load_sessions(name, args.symbol)
        window = (
            datetime.fromtimestamp(sessions[0]["ts"], timezone.utc).date().isoformat(),
            datetime.fromtimestamp(sessions[-1]["ts"], timezone.utc).date().isoformat(),
        )
        print(f"\n{'=' * 108}")
        print(f"{args.symbol.upper()}USD  session={name} "
              f"({SESSIONS[name][0]}-{SESSIONS[name][1]} NY minutes)  "
              f"{len(sessions)} sessions  {window[0]} .. {window[1]}")
        print(f"{'=' * 108}")
        header = (f"{'exit strategy':38}{'family':32}{'ret%':>9}{'CAGR%':>8}"
                  f"{'Sharpe':>8}{'MDD%':>7}{'trades':>8}{'win%':>7}")
        print(header)
        print("-" * 108)

        rows = []
        for config in CONFIGS:
            result = run_config(sessions, config, name)
            summary = summarise(result, config["name"])
            summary["family"] = config["family"]
            summary["yearly_pct"] = yearly(result)
            rows.append(summary)
            print(f"{config['name']:38}{config['family']:32}"
                  f"{summary['total_return_pct']:>9}{summary['cagr_pct']:>8}"
                  f"{summary['sharpe']:>8}{summary['max_drawdown_pct']:>7}"
                  f"{summary['trades']:>8}{summary['win_rate_pct']:>7}")

        report["sessions"][name] = {
            "window": window, "sessions": len(sessions), "results": rows,
        }

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
