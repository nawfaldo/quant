"""Matteo Conti's "Drift VWAP Pullback" from the IQCapital interview.

The video specifies a 15-minute trend model and a 5-minute execution chart:

* anchor a 15-minute VWAP at the 09:30 New York cash open;
* long drift: 15-minute close above VWAP, VWAP higher than 15 minutes ago,
  and NQ up at least 0.1% over the past hour;
* short drift: the symmetric three conditions;
* after 10:30, buy the first red 5-minute pullback candle in a long drift or
  sell the first green candle in a short drift, at the next 5-minute open;
* 80-point stop; 40-point long target; 50-point short target;
* one position at a time, at most four trades and two losses per day, no new
  entries after 15:30, and flatten at 15:55.

The transcript calls a counter-colour candle the pullback and explicitly says
it need not touch VWAP.  "First" is therefore implemented as the first candle
of each contiguous counter-colour run.  Higher-timeframe values are only made
available when their 15-minute bar has closed, and an entry always occurs at
the following 5-minute open, so the backtest has no look-ahead.

Run from the repository root:

    py -B -m sandbox.research.drift_vwap_pullback
    py -B -m sandbox.research.drift_vwap_pullback --from 2025-01-01
    py -B -m sandbox.research.drift_vwap_pullback --sizing-study
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from sandbox import data, metrics


OPEN_MIN = 9 * 60 + 30
FIRST_ENTRY_MIN = 10 * 60 + 30
LAST_ENTRY_MIN = 15 * 60 + 30
FLATTEN_MIN = 15 * 60 + 55
RTH_END_MIN = 16 * 60

INITIAL_BALANCE = 1_000.0
#: Quoted spread. ZERO on this Exness account -- 218 live fills read 0.00.
ENTRY_SPREAD = 0.0
#: Execution slippage in NQ price points, what `ENTRY_SPREAD` used to hold.
#: Measured median slippage on USTEC is 0 (1 fill in 30 slipped at all), so this
#: is a conservative allowance.
ENTRY_SLIPPAGE = 0.2
#: Broker commission, USD per lot per round trip, billed wholly at entry.
#: USTEC measured $1.24-$1.40/lot on 2026-08-12; the conservative end is used.
#: Divided by `point_value` to reach price points, which is 1.0 here.
COMMISSION_PER_LOT = 1.40
FOREX_POINT_VALUE = 1.0
FOREX_QUANTITY_STEP = 0.01
FOREX_MARGIN = 0.25
RISK_FRACTION = 0.005

STOP_POINTS = 80.0
LONG_TARGET_POINTS = 40.0
SHORT_TARGET_POINTS = 50.0
MOMENTUM_THRESHOLD = 0.001
MAX_TRADES_PER_DAY = 4
MAX_LOSSES_PER_DAY = 2

OUTPUT = os.path.join(os.path.dirname(__file__), "drift_vwap_pullback_result.json")
SIZING_OUTPUT = os.path.join(
    os.path.dirname(__file__), "drift_vwap_pullback_sizing_result.json"
)

IS_FROM, IS_TO = "2020-01-01", "2025-01-01"
OOS_FROM, OOS_TO = "2025-01-01", "2027-01-01"
VOL_LOOKBACK_SESSIONS = 20
SESSIONS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Account:
    initial: float = INITIAL_BALANCE
    spread: float = ENTRY_SPREAD
    slippage: float = ENTRY_SLIPPAGE
    commission_per_lot: float = COMMISSION_PER_LOT
    point_value: float = FOREX_POINT_VALUE
    quantity_step: float = FOREX_QUANTITY_STEP
    margin: float = FOREX_MARGIN
    risk: float = RISK_FRACTION
    leverage: float = 1.0

    @property
    def entry_cost(self):
        """Spread + slippage + commission, in price points, charged at entry.

        Mirrors `execution.Execution.entry_cost` so the two NQ paths in this
        repo cannot drift apart on cost.
        """
        commission = (self.commission_per_lot / self.point_value
                      if self.point_value else 0.0)
        return self.spread + self.slippage + commission


@dataclass(frozen=True, slots=True)
class Rules:
    stop: float = STOP_POINTS
    long_target: float = LONG_TARGET_POINTS
    short_target: float = SHORT_TARGET_POINTS
    momentum: float = MOMENTUM_THRESHOLD
    max_trades: int = MAX_TRADES_PER_DAY
    max_losses: int = MAX_LOSSES_PER_DAY


@dataclass(frozen=True, slots=True)
class Sizing:
    """One live-equity sizing policy; every mode compounds after each exit."""

    mode: str = "equity_risk"
    risk: float = RISK_FRACTION
    vol_target_annual: float | None = None
    leverage_cap: float = 4.0


EQUITY_SIZING_GRID = tuple(
    Sizing(mode="equity_risk", risk=risk)
    for risk in (0.0025, 0.005, 0.01, 0.02)
)
VOLATILITY_SIZING_GRID = tuple(
    Sizing(mode="vol_target", vol_target_annual=target)
    for target in (0.10, 0.20, 0.30, 0.40)
)


def _date(value: str | None) -> str | None:
    """Validate a CLI date before interpolating it into a QuestDB query."""
    if value is None:
        return None
    return date.fromisoformat(value).isoformat()


def _load_ticks_sample_1m(table, start_iso=None, end_iso=None):
    from sandbox import parquet_store as store
    import numpy as np
    out = {}
    for stamps, chunk in store.iter_batches(table, columns=["price", "size"], start=start_iso, end=end_iso):
        sizes = store._floats(chunk["size"])
        traded = sizes > 0
        if not traded.any():
            continue
        prices = store._floats(chunk["price"])[traded]
        sizes = sizes[traded]
        minutes = stamps[traded] // 60_000_000_000 * 60
        order = np.argsort(minutes, kind="stable")
        minutes, prices, sizes = minutes[order], prices[order], sizes[order]
        starts = np.flatnonzero(np.concatenate(([True], minutes[1:] != minutes[:-1])))
        ends = np.append(starts[1:], len(minutes))
        for m, o, h, l, c, v in zip(
            minutes[starts].tolist(),
            prices[starts].tolist(),
            np.maximum.reduceat(prices, starts).tolist(),
            np.minimum.reduceat(prices, starts).tolist(),
            prices[ends - 1].tolist(),
            np.add.reduceat(sizes, starts).tolist(),
        ):
            if m not in out:
                out[m] = [m, o, h, l, c, v]
            else:
                row = out[m]
                row[2] = max(row[2], h)
                row[3] = min(row[3], l)
                row[4] = c
                row[5] += v
    return out


def _load_cached_l2_minute_bars():
    def build():
        db = _load_ticks_sample_1m("dbento_nq_ticks", end_iso="2026-07-17")
        bm = _load_ticks_sample_1m("bm_nq_ticks", start_iso="2026-07-17")
        merged = {**db, **bm}
        return [merged[k] for k in sorted(merged)]

    tables = ["dbento_nq_ticks", "bm_nq_ticks"]
    key = f"nq_l2_1m_volume:{data._table_fingerprint(tables)}"
    return data._cached("nq_l2_1m_volume", key, build)


def load_minutes(symbol="nq", from_date=None, to_date=None, source=None):
    """Load one-minute OHLCV, from level-two ticks if requested or symbol_1m.

    ``from_date`` receives warm-up calendar days. Trades are still filtered
    to the requested range later; the extra rows only make the first session's
    hourly comparison well-defined.
    """
    from sandbox import parquet_store as store
    start, end = _date(from_date), _date(to_date)
    if source == "level_two" and symbol == "nq":
        warm_ts = None
        if start:
            warm = date.fromisoformat(start) - timedelta(days=45)
            warm_ts = int(datetime.fromisoformat(warm.isoformat()).replace(tzinfo=timezone.utc).timestamp())
        exclusive_ts = None
        if end:
            exclusive = date.fromisoformat(end) + timedelta(days=1)
            exclusive_ts = int(datetime.fromisoformat(exclusive.isoformat()).replace(tzinfo=timezone.utc).timestamp())
        raw_bars = _load_cached_l2_minute_bars()
        out = []
        for b in raw_bars:
            ts = b[0]
            if warm_ts is not None and ts < warm_ts:
                continue
            if exclusive_ts is not None and ts >= exclusive_ts:
                continue
            out.append(Bar(int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])))
        return out

    warm_iso = None
    if start:
        warm = date.fromisoformat(start) - timedelta(days=45)
        warm_iso = warm.isoformat()
    exclusive_iso = None
    if end:
        exclusive = date.fromisoformat(end) + timedelta(days=1)
        exclusive_iso = exclusive.isoformat()

    table = f"{symbol}_1m"
    rows = store.read_bars(table, bar_minutes=1, start=warm_iso, end=exclusive_iso)
    return [
        Bar(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in rows
    ]


def aggregate(bars, minutes, rth_only=False):
    """Aggregate chronological bars into clock-aligned OHLCV bars."""
    seconds = int(minutes) * 60
    out = []
    key = None
    current = None
    for bar in bars:
        minute = (bar.ts % 86_400) // 60
        if rth_only and not OPEN_MIN <= minute < RTH_END_MIN:
            continue
        bucket = bar.ts // seconds
        if bucket != key:
            if current is not None:
                out.append(Bar(*current))
            key = bucket
            current = [bucket * seconds, bar.open, bar.high, bar.low,
                       bar.close, bar.volume]
        else:
            current[2] = max(current[2], bar.high)
            current[3] = min(current[3], bar.low)
            current[4] = bar.close
            current[5] += bar.volume
    if current is not None:
        out.append(Bar(*current))
    return out


def trend_states(bars_15m, momentum=MOMENTUM_THRESHOLD):
    """Return ``{decision_ts: long|short|None}`` from completed 15-minute bars.

    VWAP uses the 15-minute HLC3 and volume exactly as described in the video.
    It resets at 09:30.  The one-hour return uses the full 15-minute NQ chart,
    including premarket, by comparing with the bar exactly four intervals ago.
    A state stamped 10:30 can only be read by a 5-minute bar closing at 10:30.
    """
    closes = {bar.ts: bar.close for bar in bars_15m}
    states = {}
    day = None
    price_volume = volume = 0.0
    previous_vwap = None

    for bar in bars_15m:
        minute = (bar.ts % 86_400) // 60
        if not OPEN_MIN <= minute < RTH_END_MIN:
            continue
        current_day = bar.ts // 86_400
        if current_day != day:
            day = current_day
            price_volume = volume = 0.0
            previous_vwap = None

        typical = (bar.high + bar.low + bar.close) / 3.0
        if bar.volume > 0:
            price_volume += typical * bar.volume
            volume += bar.volume
        vwap = price_volume / volume if volume > 0 else typical
        hour_ago = closes.get(bar.ts - 60 * 60)
        side = None
        if previous_vwap is not None and hour_ago and hour_ago > 0:
            move = bar.close / hour_ago - 1.0
            if bar.close > vwap and vwap > previous_vwap and move >= momentum:
                side = "long"
            elif bar.close < vwap and vwap < previous_vwap and move <= -momentum:
                side = "short"
        states[bar.ts + 15 * 60] = side
        previous_vwap = vwap
    return states


def session_volatility(bars_5m, lookback=VOL_LOOKBACK_SESSIONS):
    """Daily close volatility known at each session open.

    The feature for day D uses the last ``lookback`` close-to-close returns
    ending on D-1.  It never reads D's close, so volatility sizing cannot see
    the session it is sizing.
    """
    closes = {}
    for bar in bars_5m:
        closes[bar.ts // 86_400] = bar.close
    days = sorted(closes)
    returns = [None]
    for previous, current in zip(days, days[1:]):
        returns.append(
            closes[current] / closes[previous] - 1.0 if closes[previous] else 0.0
        )
    out = {}
    for index, day in enumerate(days):
        prior = [value for value in returns[max(1, index - lookback):index]
                 if value is not None]
        out[day] = statistics.pstdev(prior) if len(prior) >= lookback else None
    return out


def _quantity(equity, entry, account, stop, sizing=None, daily_vol=None):
    if equity <= 0 or entry <= 0 or stop <= 0:
        return 0.0
    sizing = sizing or Sizing(risk=account.risk)
    buying_power = equity / account.margin / entry
    policy_cap = equity * sizing.leverage_cap / entry
    if sizing.mode == "equity_risk":
        raw = equity * sizing.risk / stop
    elif sizing.mode == "vol_target":
        if daily_vol is None or daily_vol <= 0 or not sizing.vol_target_annual:
            return 0.0
        annual = daily_vol * math.sqrt(SESSIONS_PER_YEAR)
        leverage = min(sizing.vol_target_annual / annual, sizing.leverage_cap)
        raw = equity * leverage / entry
    else:
        raise ValueError(f"unknown sizing mode {sizing.mode!r}")
    raw = min(raw, buying_power, policy_cap) * account.leverage
    return math.floor((raw + 1e-12) / account.quantity_step) * account.quantity_step


def _exit(position, exit_price, exit_ts, reason, account):
    sign = 1.0 if position["side"] == "long" else -1.0
    gross_points = sign * (exit_price - position["entry_price"])
    net_points = gross_points - account.entry_cost
    pnl = net_points * position["quantity"] * account.point_value
    return {
        **position,
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "reason": reason,
        "gross_points": gross_points,
        "net_points": net_points,
        "pnl": pnl,
    }


def _bracket_exit(position, bar):
    """Conservative stop-before-target resolution, matching execution.py."""
    entry = position["entry_price"]
    stop = position["stop"]
    target = position["target"]
    if position["side"] == "long":
        stop_price, target_price = entry - stop, entry + target
        if bar.low <= stop_price:
            return min(bar.open, stop_price), "stop"
        if bar.high >= target_price:
            return max(bar.open, target_price), "target"
    else:
        stop_price, target_price = entry + stop, entry - target
        if bar.high >= stop_price:
            return max(bar.open, stop_price), "stop"
        if bar.low <= target_price:
            return min(bar.open, target_price), "target"
    return None


def backtest(bars_5m, states, account=Account(), rules=Rules(),
             from_ts=None, to_ts=None, sizing=None, volatility=None,
             fills=None):
    """Run the complete strategy, including outcome-dependent daily guardrails.

    `fills` optionally maps a bar timestamp to the bar to EXECUTE against, so
    the strategy can decide on one price series and be filled on another --
    used to price this sleeve at the broker's own bars instead of the vendor's.
    Signals, VWAP, the trend states and the counter-candle test all stay on
    `bars_5m`; only the entry open, the bracket and the session flatten move.
    A timestamp with no entry in `fills` executes against its own bar.
    """
    sizing = sizing or Sizing(risk=account.risk)
    volatility = volatility if volatility is not None else session_volatility(bars_5m)
    equity = account.initial
    trades = []
    daily_equity = []
    by_day = {}
    for bar in bars_5m:
        by_day.setdefault(bar.ts // 86_400, []).append(bar)

    for day in sorted(by_day):
        day_bars = by_day[day]
        if from_ts is not None and day * 86_400 < from_ts:
            continue
        if to_ts is not None and day * 86_400 >= to_ts:
            continue

        position = None
        pending = None
        current_state = previous_state = None
        pullback_active = False
        trade_count = loss_count = 0
        day_vol = volatility.get(day)

        for bar in day_bars:
            # `bar` decides, `fbar` pays -- the same object unless an alternate
            # execution feed was handed in.
            fbar = fills.get(bar.ts, bar) if fills else bar
            minute = (bar.ts % 86_400) // 60

            # 15:55 is an exact market-time flatten at this bar's open.
            if minute >= FLATTEN_MIN:
                pending = None
                if position is not None:
                    trade = _exit(position, fbar.open, bar.ts,
                                  "session_close", account)
                    trades.append(trade)
                    equity += trade["pnl"]
                    loss_count += trade["pnl"] < 0
                    position = None
                continue

            opened_now = False
            if (pending is not None and position is None
                    and minute <= LAST_ENTRY_MIN
                    and trade_count < rules.max_trades
                    and loss_count < rules.max_losses):
                side = pending
                quantity = _quantity(
                    equity, fbar.open, account, rules.stop, sizing, day_vol
                )
                if quantity >= account.quantity_step:
                    position = {
                        "entry_ts": bar.ts,
                        "side": side,
                        "entry_price": fbar.open,
                        "quantity": quantity,
                        "stop": rules.stop,
                        "target": (rules.long_target if side == "long"
                                   else rules.short_target),
                        "sizing_mode": sizing.mode,
                        "daily_volatility": day_vol,
                    }
                    trade_count += 1
                    opened_now = True
                pending = None

            # As in execution.py, an entry cannot hit a bracket in its own bar.
            if position is not None and not opened_now:
                resolved = _bracket_exit(position, fbar)
                if resolved is not None:
                    exit_price, reason = resolved
                    trade = _exit(position, exit_price, bar.ts, reason, account)
                    trades.append(trade)
                    equity += trade["pnl"]
                    loss_count += trade["pnl"] < 0
                    position = None

            decision_ts = bar.ts + 5 * 60
            if decision_ts in states:
                current_state = states[decision_ts]
            if current_state != previous_state:
                pullback_active = False
                previous_state = current_state

            counter = (
                (current_state == "long" and bar.close < bar.open)
                or (current_state == "short" and bar.close > bar.open)
            )
            first_pullback = counter and not pullback_active
            pullback_active = counter if current_state is not None else False

            next_minute = minute + 5
            eligible = (
                FIRST_ENTRY_MIN <= next_minute <= LAST_ENTRY_MIN
                and position is None
                and pending is None
                and trade_count < rules.max_trades
                and loss_count < rules.max_losses
            )
            if first_pullback and eligible:
                pending = current_state

        # Incomplete source days still cannot leak a position overnight.
        if position is not None:
            last = day_bars[-1]
            trade = _exit(position, last.close, last.ts + 5 * 60,
                          "data_end", account)
            trades.append(trade)
            equity += trade["pnl"]
        daily_equity.append((day, equity))

    return {"trades": trades, "daily_equity": daily_equity,
            "initial": account.initial, "final": equity}


def _summary(result, span):
    sized = [(trade["entry_ts"], trade["pnl"]) for trade in result["trades"]]
    stat = metrics.stats(sized, initial=result["initial"], span=span)
    gross = [trade["gross_points"] for trade in result["trades"]]
    exits = {}
    sides = {}
    for trade in result["trades"]:
        exits[trade["reason"]] = exits.get(trade["reason"], 0) + 1
        sides[trade["side"]] = sides.get(trade["side"], 0) + 1
    stat.update({
        "initial_balance": result["initial"],
        "final_equity": round(result["final"], 2),
        "return_pct": round(100 * (result["final"] / result["initial"] - 1), 2),
        "gross_points_per_trade": round(statistics.fmean(gross), 3) if gross else 0.0,
        "net_points_per_trade": round(statistics.fmean(
            trade["net_points"] for trade in result["trades"]), 3)
            if result["trades"] else 0.0,
        "exit_reasons": exits,
        "sides": sides,
    })
    return stat


def run(symbol="nq", from_date=None, to_date=None, account=Account(),
        rules=Rules(), sizing=None):
    minutes = load_minutes(symbol, from_date, to_date)
    if not minutes:
        raise SystemExit(f"no rows in {symbol}_1m for the requested range")
    bars_5m = aggregate(minutes, 5, rth_only=True)
    bars_15m = aggregate(minutes, 15, rth_only=False)
    states = trend_states(bars_15m, rules.momentum)
    volatility = session_volatility(bars_5m)

    requested_from = _date(from_date)
    requested_to = _date(to_date)
    from_ts = (metrics.split_ts(requested_from) if requested_from
               else (bars_5m[0].ts // 86_400) * 86_400)
    to_ts = (metrics.split_ts((date.fromisoformat(requested_to) + timedelta(days=1)).isoformat())
             if requested_to else ((bars_5m[-1].ts // 86_400) + 1) * 86_400)
    sizing = sizing or Sizing(risk=account.risk)
    result = backtest(
        bars_5m, states, account, rules, from_ts, to_ts, sizing, volatility
    )
    summary = _summary(result, (from_ts, to_ts))
    return {
        "strategy": "Drift VWAP Pullback",
        "source": "https://www.youtube.com/watch?v=wm4A6qo0g3I",
        "symbol": symbol.upper(),
        "coverage": {
            "from": datetime.fromtimestamp(from_ts, timezone.utc).date().isoformat(),
            "to": datetime.fromtimestamp(to_ts - 1, timezone.utc).date().isoformat(),
            "minute_rows": len(minutes),
            "bars_5m_rth": len(bars_5m),
            "bars_15m_all_hours": len(bars_15m),
        },
        "account": asdict(account),
        "sizing": asdict(sizing),
        "rules": asdict(rules),
        "assumptions": {
            "clock": "New York wall clock encoded as UTC",
            "vwap": "cumulative 15m HLC3 x volume, anchored 09:30",
            "hour_return": "15m close versus the close exactly four 15m bars earlier",
            "pullback": "first counter-colour 5m candle in each contiguous run",
            "fill": "next 5m open",
            "ambiguous_bar": "stop before target",
            "cost": "spread 0.0 + slippage 0.2 + commission $1.40|lot, NQ price points, charged once at entry",
        },
        "summary": summary,
        "trades": result["trades"],
    }


def _sizing_label(sizing):
    if sizing.mode == "equity_risk":
        return f"equity_risk_{100 * sizing.risk:g}pct"
    return f"vol_target_{100 * float(sizing.vol_target_annual or 0):g}pct"


def _rank_key(summary):
    """IS-only ranking: consistency first, return second, drawdown third."""
    return summary["msharpe"], summary["return_pct"], -summary["max_dd"]


def sizing_study(symbol="nq", account=Account(), rules=Rules()):
    """Select sizing on 2020-2024, then reset equity and evaluate 2025-2026."""
    # OOS_TO is exclusive. ``load_minutes`` accepts an inclusive end date.
    requested_last = (
        date.fromisoformat(OOS_TO) - timedelta(days=1)
    ).isoformat()
    minutes = load_minutes(symbol, IS_FROM, requested_last)
    if not minutes:
        raise SystemExit(f"no rows in {symbol}_1m for the sizing study")
    bars_5m = aggregate(minutes, 5, rth_only=True)
    bars_15m = aggregate(minutes, 15, rth_only=False)
    states = trend_states(bars_15m, rules.momentum)
    volatility = session_volatility(bars_5m)

    is_lo, is_hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    oos_lo, requested_oos_hi = metrics.split_ts(OOS_FROM), metrics.split_ts(OOS_TO)
    data_lo = (bars_5m[0].ts // 86_400) * 86_400
    data_hi = ((bars_5m[-1].ts // 86_400) + 1) * 86_400
    if data_lo > is_lo or data_hi < is_hi:
        raise SystemExit(
            "nq_1m does not fully cover the requested 2020-2024 in-sample window"
        )
    oos_hi = min(requested_oos_hi, data_hi)
    if oos_hi <= oos_lo:
        raise SystemExit("nq_1m has no rows in the requested 2025-2026 OOS window")

    families = {
        "equity_risk": EQUITY_SIZING_GRID,
        "vol_target": VOLATILITY_SIZING_GRID,
    }
    report_families = {}
    for family, grid in families.items():
        candidates = []
        for sizing in grid:
            inside = backtest(
                bars_5m, states, account, rules, is_lo, is_hi, sizing, volatility
            )
            summary = _summary(inside, (is_lo, is_hi))
            candidates.append({
                "label": _sizing_label(sizing),
                "sizing": asdict(sizing),
                "summary": summary,
            })
        winner = max(candidates, key=lambda row: _rank_key(row["summary"]))
        selected = Sizing(**winner["sizing"])
        outside = backtest(
            bars_5m, states, account, rules, oos_lo, oos_hi, selected, volatility
        )
        report_families[family] = {
            "selection_metric": "highest IS monthly Sharpe; return then drawdown tie-break",
            "is_candidates": candidates,
            "selected": winner,
            "oos": {
                "summary": _summary(outside, (oos_lo, oos_hi)),
                "trades": outside["trades"],
            },
        }

    return {
        "strategy": "Drift VWAP Pullback sizing study",
        "symbol": symbol.upper(),
        "source": "https://www.youtube.com/watch?v=wm4A6qo0g3I",
        "protocol": {
            "in_sample": [IS_FROM, IS_TO],
            "out_of_sample_requested": [OOS_FROM, OOS_TO],
            "out_of_sample_evaluated": [
                OOS_FROM,
                datetime.fromtimestamp(oos_hi, timezone.utc).date().isoformat(),
            ],
            "windows": "half-open; sizing selected only on IS; OOS starts from a fresh $1,000",
            "volatility": (
                "20 prior RTH session close returns, population stdev, annualized by sqrt(252)"
            ),
            "volatility_leverage": (
                "target annual vol / prior realized annual vol, capped at 4x and broker buying power"
            ),
        },
        "data": {
            "minute_rows_with_warmup": len(minutes),
            "bars_5m_rth": len(bars_5m),
            "from": datetime.fromtimestamp(data_lo, timezone.utc).date().isoformat(),
            "to": datetime.fromtimestamp(data_hi - 1, timezone.utc).date().isoformat(),
        },
        "account": asdict(account),
        "rules": asdict(rules),
        "families": report_families,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="nq")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE)
    parser.add_argument("--spread", type=float, default=ENTRY_SPREAD,
                        help="entry spread in NQ price points")
    parser.add_argument("--risk", type=float, default=RISK_FRACTION,
                        help="fraction of live equity risked per 80-point stop")
    parser.add_argument("--mode", choices=("equity_risk", "vol_target"),
                        default="equity_risk")
    parser.add_argument("--vol-target", type=float, default=0.20,
                        help="annual volatility target, e.g. 0.20")
    parser.add_argument("--leverage-cap", type=float, default=4.0)
    parser.add_argument("--sizing-study", action="store_true",
                        help="select on 2020-2024 and evaluate on 2025-2026")
    parser.add_argument("--out")
    args = parser.parse_args()

    account = Account(initial=args.balance, slippage=args.spread, risk=args.risk)
    if args.sizing_study:
        report = sizing_study(args.symbol, account)
        output = args.out or SIZING_OUTPUT
        console = {
            family: {
                "selected": row["selected"],
                "oos_summary": row["oos"]["summary"],
            }
            for family, row in report["families"].items()
        }
    else:
        sizing = Sizing(
            mode=args.mode,
            risk=args.risk,
            vol_target_annual=(args.vol_target if args.mode == "vol_target" else None),
            leverage_cap=args.leverage_cap,
        )
        report = run(
            args.symbol, args.from_date, args.to_date, account, sizing=sizing
        )
        output = args.out or OUTPUT
        console = {
            "coverage": report["coverage"],
            "account": report["account"],
            "sizing": report["sizing"],
            "summary": report["summary"],
        }
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({
        "strategy": report["strategy"],
        **console,
        "output": output,
    }, indent=2))


if __name__ == "__main__":
    main()
