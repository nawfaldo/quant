"""Does `hourly_delta_reversal` transfer to ES when driven by NQ order flow?

ES has no tick or depth data locally -- every order-flow table is NQ. But ES and
NQ are ~0.95 correlated intraday, so NQ aggressor delta may carry information
that is tradeable in ES. This script takes the signal from NQ and executes it in
ES, changing nothing else.

WHY THIS IS A FAIR TEST DESPITE THE SHORT WINDOW. NQ order flow begins
2025-02-12, so there is no 2018-2024 history and no room for the usual
in-sample/out-of-sample split. The test avoids that problem by having no free
parameters at all: every constant below is copied from
`live_trade/src/strategies/idk/hourly_delta_reversal.rs`, where it was selected
against **NQ**. Nothing is fitted to ES, so there is nothing to overfit -- the ES
result is a straight transfer measurement, not a search.

The `nq` control run exists to prove the replica before any ES number is
believed. It should reproduce the documented NQ behaviour; if it does not, the
ES figure means nothing and the discrepancy is the finding.

Split of responsibilities in the ES run:

  * NQ supplies the hourly candle direction, aggressor delta, depth activity and
    the book-spread veto -- the signal.
  * ES supplies entry price, the ATR bracket, the daily-close trend filter and
    the session flatten -- the trade.

CLOCKS. `nq_*` is New York; `es_*` is Chicago, one hour behind (see
[es-1m-is-chicago-time]). ES timestamps are shifted +3600s on load so both
series share the New York clock, and every minute constant here is New York.
Joining them raw would silently pair each NQ hour with the wrong ES hour.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import deque
from datetime import datetime, timedelta, timezone

from sandbox import data


# --- copied verbatim from hourly_delta_reversal.rs; none of these are tuned here
INITIAL = 1_000.0
SPREAD = 0.2
ENTRY_RISK_FRACTION = 0.01
LONG_MARGIN_REQUIREMENT = 0.25
QUANTITY_STEP = 0.01
MARKET_OPEN_MINUTE = 9 * 60 + 30
MARKET_CLOSE_MINUTE = 16 * 60
THURSDAY = 3
BUY_MIN_DELTA = 50.0
SELL_MIN_DELTA = 300.0
ATR_STOP_FRACTION = 0.2
TARGET_RR = 1.25
ATR_SESSIONS = 20
WARMUP_STOP = 50.0
SHORT_TREND_SMA_DAYS = 35
MAXIMUM_ENTRY_SPREAD = 1.5

CHICAGO_TO_NEW_YORK = 3_600

TS, O, H, L, C, D, DE = range(7)


def es_bars_by_ts(lo, hi):
    """ES RTH minute bars keyed by New York epoch second."""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,open,high,low,close FROM es_1m "
        f"WHERE timestamp >= '{lo}' AND timestamp < '{hi}' ORDER BY timestamp"
    )
    out = {}
    for row in rows:
        ts = int(row[0]) // 1_000_000 + CHICAGO_TO_NEW_YORK
        minute = ts % 86_400 // 60
        if MARKET_OPEN_MINUTE <= minute < MARKET_CLOSE_MINUTE:
            out[ts] = (ts, *(float(value) for value in row[1:]))
    return out


def session_closes(symbol):
    """`(New York epoch day, close)` per session, oldest first."""
    shift = 0 if symbol == "nq" else CHICAGO_TO_NEW_YORK
    rows = data.query(
        f"SELECT cast(timestamp as long) ts,close FROM {symbol}_1h ORDER BY timestamp"
    )
    last = {}
    for row in rows:
        ts = int(row[0]) // 1_000_000 + shift
        if ts % 86_400 // 3_600 < 17:
            last[ts // 86_400] = float(row[1])
    return sorted(last.items())


def entry_quantity(equity, price, stop):
    if equity <= 0.0 or price <= 0.0 or stop <= 0.0:
        return None
    raw = min(equity * ENTRY_RISK_FRACTION / stop, (equity / LONG_MARGIN_REQUIREMENT) / price)
    quantity = math.floor(raw / QUANTITY_STEP) * QUANTITY_STEP
    return quantity if quantity >= QUANTITY_STEP else None


def run(symbol):
    """Replay the strategy, taking signals from NQ and trades from `symbol`."""
    signal_bars = data.load_cached_level_two_bars("nq")
    features = data.load_cached_l2_features("nq")
    closes = session_closes(symbol)

    if symbol == "nq":
        execution = {bar[TS]: (bar[TS], bar[O], bar[H], bar[L], bar[C])
                     for bar in signal_bars}
    else:
        first = datetime.fromtimestamp(signal_bars[0][TS], tz=timezone.utc)
        last = datetime.fromtimestamp(signal_bars[-1][TS], tz=timezone.utc)
        execution = es_bars_by_ts(f"{first:%Y-%m-%d}",
                                  f"{last + timedelta(days=1):%Y-%m-%d}")

    equity = peak = INITIAL
    maximum_drawdown = 0.0
    trades = []
    positions = []

    hour = None
    hour_open = hour_close = hour_delta = 0.0
    hour_depth = 0
    previous_flow = None
    closed_sessions = 0
    session = None                      # (day, high, low, close)
    session_ranges = deque()
    previous_session_close = None
    current_atr = None
    missing = 0

    for bar in signal_bars:
        ts = bar[TS]
        traded = execution.get(ts)
        if traded is None:
            missing += 1
            continue
        _, t_open, t_high, t_low, t_close = traded

        flow = features.get(ts)
        prior_flow, previous_flow = previous_flow, flow

        day = ts // 86_400
        minute = ts % 86_400 // 60

        # --- session roll on the *traded* instrument, for its own ATR
        if session is not None and session[0] != day:
            _, high, low, close = session
            span = high - low
            if previous_session_close is not None:
                span = max(span, abs(high - previous_session_close),
                           abs(low - previous_session_close))
            if len(session_ranges) == ATR_SESSIONS:
                session_ranges.popleft()
            session_ranges.append(span)
            previous_session_close = close
            session = None
        if session is None:
            current_atr = (sum(session_ranges) / ATR_SESSIONS
                           if len(session_ranges) == ATR_SESSIONS else None)
            session = (day, t_high, t_low, t_close)
        else:
            _, high, low, _ = session
            session = (day, max(high, t_high), min(low, t_low), t_close)

        while closed_sessions < len(closes) and closes[closed_sessions][0] < day:
            closed_sessions += 1

        if minute >= MARKET_CLOSE_MINUTE or minute < MARKET_OPEN_MINUTE:
            positions.clear()

        # --- hourly NQ signal
        current_hour = ts // 3_600
        entry_side = None
        if hour == current_hour:
            hour_close = bar[C]
            hour_delta += bar[D]
            hour_depth += bar[DE]
        else:
            if (hour is not None and hour + 1 == current_hour
                    and MARKET_OPEN_MINUTE <= minute < MARKET_CLOSE_MINUTE
                    and (day + 3) % 7 != THURSDAY
                    and hour_depth != 0):
                if hour_close < hour_open and hour_delta >= BUY_MIN_DELTA:
                    entry_side = 1
                elif hour_close > hour_open and hour_delta <= -SELL_MIN_DELTA:
                    entry_side = -1
            hour = current_hour
            hour_open, hour_close = bar[O], bar[C]
            hour_delta = bar[D]
            hour_depth = bar[DE]

        # Shorts may not fight the traded instrument's daily uptrend.
        if entry_side == -1 and closed_sessions >= SHORT_TREND_SMA_DAYS:
            window = closes[closed_sessions - SHORT_TREND_SMA_DAYS:closed_sessions]
            if t_open >= sum(close for _, close in window) / SHORT_TREND_SMA_DAYS:
                entry_side = None
        # Either side is vetoed if NQ's book was dislocated a minute ago.
        if entry_side is not None and prior_flow is not None:
            if prior_flow.get("book_valid") and prior_flow.get("spread", 0.0) > MAXIMUM_ENTRY_SPREAD:
                entry_side = None

        # --- exits before the bar's own entry; stop before target
        still = []
        for position in positions:
            side, entry, stop, target, entry_ts, quantity = position
            exit_price = None
            if side == 1:
                if t_low <= entry - stop:
                    exit_price = min(t_open, entry - stop)
                elif t_high >= entry + target:
                    exit_price = max(t_open, entry + target)
            else:
                if t_high >= entry + stop:
                    exit_price = max(t_open, entry + stop)
                elif t_low <= entry - target:
                    exit_price = min(t_open, entry - target)
            if exit_price is None:
                still.append(position)
            else:
                points = side * (exit_price - entry) - SPREAD
                pnl = points * quantity
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": entry_ts, "exit_ts": ts, "side": side,
                               "points": points, "pnl": pnl})
        positions = still

        if entry_side is not None:
            stop = ATR_STOP_FRACTION * current_atr if current_atr else WARMUP_STOP
            target = stop * TARGET_RR
            quantity = entry_quantity(equity, t_open, stop)
            if quantity is not None:
                positions.append([entry_side, t_open, stop, target, ts, quantity])

    return summarize(trades, maximum_drawdown, equity, missing)


def summarize(trades, maximum_drawdown, final_equity, missing):
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    months = {}
    for trade in trades:
        dt = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        months[f"{dt.year}-{dt.month:02d}"] = (
            months.get(f"{dt.year}-{dt.month:02d}", 0.0) + trade["pnl"]
        )
    monthly = list(months.values())
    points = [t["points"] for t in trades]
    mean = statistics.fmean(points) if points else 0.0
    sd = statistics.stdev(points) if len(points) > 1 else 0.0
    return {
        "pnl": round(final_equity - INITIAL, 2),
        "final": round(final_equity, 2),
        "return_pct": round(100.0 * (final_equity - INITIAL) / INITIAL, 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "win_rate": round(sum(t["pnl"] > 0 for t in trades) / len(trades), 3)
                    if trades else 0.0,
        "max_dd_pct": round(100.0 * maximum_drawdown, 2),
        "mean_points": round(mean, 3),
        "t_stat": round(mean / (sd / math.sqrt(len(points))), 2)
                  if sd and len(points) > 1 else 0.0,
        "monthly_sharpe": round(statistics.fmean(monthly)
                                / statistics.pstdev(monthly), 3)
                          if len(monthly) > 1 and statistics.pstdev(monthly) else 0.0,
        "positive_months": sum(1 for value in monthly if value > 0),
        "n_months": len(monthly),
        "skipped_minutes": missing,
        "months": {k: round(v, 2) for k, v in sorted(months.items())},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", choices=("nq", "es"),
                        help="nq replicates the compiled strategy as a control")
    args = parser.parse_args()
    result = run(args.symbol)
    label = "CONTROL (replica of the compiled NQ strategy)" if args.symbol == "nq" \
        else "TRANSFER (NQ order-flow signal, ES execution)"
    print(f"{args.symbol.upper()} {label}")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
