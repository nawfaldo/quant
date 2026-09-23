"""Causal ES noise-area momentum research with a sealed holdout.

This is a futures/CFD adaptation of Zarattini, Aziz, and Barbon's published
intraday-momentum rule.  At each completed half hour it compares price with the
average absolute open-to-that-time move over prior sessions.  Price outside the
gap-adjusted "noise area" is evidence of an intraday demand/supply imbalance.

The paper's structural rules are kept intact:

* gap-adjusted, time-of-day volatility bands;
* decisions only on completed half-hour bars;
* trend-following entries outside the bands;
* a current-band plus RTH VWAP close-based trailing exit;
* no overnight positions.

Only 2017-2024 is visible to ``select``.  It hash-seals one parameter cell before
``validate`` can evaluate 2025-2026.  The executable model is the user's Forex
account convention applied to Exness's regular US500 contract: USD 1 per index
point per lot, 0.01 lot step, 0.03 lot minimum, and 0.25% fixed margin.  The
whole 0.20-point spread is charged at entry.  Quantity is floored from live
equity and the emergency stop loss, so profits and losses compound.

Research references:

* https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172
* https://www.sciencedirect.com/science/article/pii/S0304405X18301351
* https://get.exness.help/hc/en-us/articles/17854383867548-Indices
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import os
import statistics

from sandbox import data


OUTPUT = os.path.join(os.path.dirname(__file__), "es_noise_momentum_selection.json")

INITIAL = 1_000.0
SPREAD = 0.20
POINT_VALUE = 1.0
MARGIN_RATE = 0.0025
QUANTITY_STEP = 0.01
MINIMUM_QUANTITY = 0.03
BASE_RISK_FRACTION = 0.01

WARMUP_START = "2016-01-01"
IS_START = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
IS_YEARS = tuple(range(2017, 2025))

# ``es_1m`` is Chicago wall clock.  These are 09:30-16:00 New York.
SESSION_OPEN_MINUTE = 8 * 60 + 30
SESSION_CLOSE_MINUTE = 15 * 60
SESSION_SLOTS = tuple(range(SESSION_OPEN_MINUTE, SESSION_CLOSE_MINUTE, 30))

TS, O, H, L, C, V = range(6)


@dataclass(frozen=True)
class Session:
    day: int
    bars: tuple
    open: float
    high: float
    low: float
    close: float
    prior_close: float | None
    atr: float | None
    volatility: float | None
    vix: float | None
    sigma: dict


AXES = {
    # Refined after the broad 3,456-cell structural pass.  Removed values had
    # no consistent plateau; the retained values surround every viable region.
    "lookback": (10, 14, 20),
    "band_multiplier": (0.75, 1.0, 1.25),
    # Completed 09:30, 10:00, or 10:30 New York bar is the first decision.
    "first_decision_minute": (8 * 60 + 30,),
    "stop_atr": (0.25, 0.35, 0.50),
    "exit_mode": ("band_vwap", "band"),
    "side_mode": ("both", "long"),
    # Volatility targeting may reduce the fixed-fraction risk, never increase it.
    "vol_target": (None, 0.15, 0.20),
    # The authors' complementary open-to-10:00 ET overnight-gap fade.  None is
    # the matched do-nothing control; thresholds are fractions of prior close.
    "gap_threshold": (None, 0.010, 0.0125, 0.015, 0.0175, 0.020),
}

CATEGORICAL = {"exit_mode", "side_mode"}
MIN_TRADES = 500
MIN_PROFIT_FACTOR = 1.05
MAX_DRAWDOWN_PCT = 18.0
MAX_ANNUAL_DRAWDOWN_PCT = 20.0
MIN_ANNUAL_RETURN_PCT = 3.0
MIN_YEARS_ABOVE_FIVE_PCT = 6
MAX_ANNUAL_RETURN_CV = 0.75
MAX_SINGLE_YEAR_RETURN_SHARE = 0.30


def bars_30m(phase):
    """Load RTH half-hours; selection SQL cannot return a holdout row."""
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM es_1m WHERE timestamp >= '{WARMUP_START}' "
        f"{upper} AND ((hour(timestamp)=8 AND minute(timestamp)>=30) OR "
        "(hour(timestamp)>=9 AND hour(timestamp)<15)) "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    out = []
    for row in rows:
        ts = int(row[0]) // 1_000_000
        minute = ts % 86_400 // 60
        if SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE:
            out.append((ts, *(float(value) for value in row[1:])))
    return out


def vix_by_day(phase):
    """Previous calendar day's VIX close, never the current unfinished day."""
    upper = "WHERE timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        f"SELECT cast(timestamp as long),close FROM vix_1d {upper} ORDER BY timestamp"
    )
    daily = [(int(row[0]) // 1_000_000 // 86_400, float(row[1])) for row in rows]
    return daily


def _sample_std(values):
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def build_sessions(bars, phase, max_lookback=max(AXES["lookback"])):
    """Create strictly causal daily context and time-of-day move histories."""
    grouped = {}
    for bar in bars:
        grouped.setdefault(bar[TS] // 86_400, []).append(bar)

    vix_rows = vix_by_day(phase)
    vix_cursor = 0
    last_vix = None
    move_history = {slot: deque(maxlen=max_lookback) for slot in SESSION_SLOTS}
    ranges = deque(maxlen=20)
    close_returns = deque(maxlen=20)
    previous_close = None
    sessions = []

    for day in sorted(grouped):
        day_bars = sorted(grouped[day])
        by_slot = {bar[TS] % 86_400 // 60: bar for bar in day_bars}
        if any(slot not in by_slot for slot in SESSION_SLOTS):
            continue
        day_bars = tuple(by_slot[slot] for slot in SESSION_SLOTS)
        day_open = day_bars[0][O]
        day_high = max(bar[H] for bar in day_bars)
        day_low = min(bar[L] for bar in day_bars)
        day_close = day_bars[-1][C]

        while vix_cursor < len(vix_rows) and vix_rows[vix_cursor][0] < day:
            last_vix = vix_rows[vix_cursor][1]
            vix_cursor += 1

        atr = statistics.fmean(ranges) if len(ranges) == ranges.maxlen else None
        if len(close_returns) == close_returns.maxlen:
            volatility = _sample_std(close_returns) * math.sqrt(252.0)
        else:
            volatility = None
        sigma = {
            lookback: {
                slot: (statistics.fmean(list(move_history[slot])[-lookback:])
                       if len(move_history[slot]) >= lookback else None)
                for slot in SESSION_SLOTS
            }
            for lookback in AXES["lookback"]
        }
        sessions.append(Session(
            day=day, bars=day_bars, open=day_open, high=day_high, low=day_low,
            close=day_close, prior_close=previous_close, atr=atr,
            volatility=volatility, vix=last_vix, sigma=sigma,
        ))

        for slot, bar in zip(SESSION_SLOTS, day_bars):
            move_history[slot].append(abs(bar[C] / day_open - 1.0))
        true_range = day_high - day_low
        if previous_close is not None:
            true_range = max(true_range, abs(day_high - previous_close),
                             abs(day_low - previous_close))
            close_returns.append(math.log(day_close / previous_close))
        ranges.append(true_range)
        previous_close = day_close
    return sessions


def band(session, slot, params):
    sigma = session.sigma[params["lookback"]][slot]
    if sigma is None or session.prior_close is None:
        return None
    width = params["band_multiplier"] * sigma
    upper_basis = max(session.open, session.prior_close)
    lower_basis = min(session.open, session.prior_close)
    return upper_basis * (1.0 + width), lower_basis * (1.0 - width)


def quantity(equity, price, stop_distance, risk_fraction, spread=SPREAD):
    """Equity-risk sizing under regular Exness US500 contract constraints."""
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    loss_per_lot = (stop_distance + spread) * POINT_VALUE
    risk_sized = equity * risk_fraction / loss_per_lot
    margin_sized = equity / (price * POINT_VALUE * MARGIN_RATE)
    raw = min(risk_sized, margin_sized)
    sized = math.floor(raw / QUANTITY_STEP) * QUANTITY_STEP
    return sized if sized + 1e-12 >= MINIMUM_QUANTITY else 0.0


def _trade_pnl(side, entry, exit_price, amount):
    return side * (exit_price - entry) * amount * POINT_VALUE


def _annual_detail(trades):
    by_year = {}
    for trade in trades:
        year = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc).year
        by_year.setdefault(year, []).append(trade["pnl"])
    equity = INITIAL
    result = {}
    for year in range(min(by_year, default=2017), max(by_year, default=2016) + 1):
        start = peak = equity
        drawdown = pnl = 0.0
        for value in by_year.get(year, ()):
            pnl += value
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 1.0)
        result[str(year)] = {
            "pnl": round(pnl, 2),
            "return_pct": round(100.0 * pnl / start, 2) if start > 0 else -100.0,
            "max_dd_pct": round(100.0 * drawdown, 2),
            "trades": len(by_year.get(year, ())),
        }
    return result


def summarize(trades, equity, maximum_drawdown):
    wins = sum(trade["pnl"] for trade in trades if trade["pnl"] > 0.0)
    losses = -sum(trade["pnl"] for trade in trades if trade["pnl"] < 0.0)
    months = {}
    for trade in trades:
        dt = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc)
        months.setdefault(f"{dt.year}-{dt.month:02d}", 0.0)
        months[f"{dt.year}-{dt.month:02d}"] += trade["pnl"]
    monthly = list(months.values())
    mean = statistics.fmean(monthly) if monthly else 0.0
    std = _sample_std(monthly)
    annual = _annual_detail(trades)
    annual_returns = [annual[key]["return_pct"] for key in sorted(annual)]
    mean_annual = statistics.fmean(annual_returns) if annual_returns else 0.0
    total_positive_return = sum(max(0.0, value) for value in annual_returns)
    consistency = {
        "worst_year_return_pct": round(min(annual_returns), 2) if annual_returns else 0.0,
        "median_year_return_pct": round(statistics.median(annual_returns), 2)
                                  if annual_returns else 0.0,
        "annual_return_std_pct": round(_sample_std(annual_returns), 2),
        "annual_return_cv": round(_sample_std(annual_returns) / mean_annual, 3)
                            if mean_annual > 0.0 else 999.0,
        "years_above_5pct": sum(value >= 5.0 for value in annual_returns),
        "max_single_year_return_share": round(
            max(annual_returns, default=0.0) / total_positive_return, 3
        ) if total_positive_return > 0.0 else 1.0,
    }
    return {
        "pnl": round(equity - INITIAL, 2),
        "final": round(equity, 2),
        "return_pct": round(100.0 * (equity / INITIAL - 1.0), 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "win_rate": round(sum(t["pnl"] > 0.0 for t in trades) / len(trades), 3)
                    if trades else 0.0,
        "max_dd_pct": round(100.0 * maximum_drawdown, 2),
        "monthly_sharpe": round(mean / std, 3) if std else 0.0,
        "positive_months": sum(value > 0.0 for value in monthly),
        "n_months": len(monthly),
        "annual": annual,
        "consistency": consistency,
    }


def accepts_side(side, mode):
    return mode == "both" or (mode == "long" and side == 1) or (mode == "short" and side == -1)


def backtest(sessions, params, lo=IS_START, hi=IS_END, spread=SPREAD,
             risk_fraction=BASE_RISK_FRACTION):
    return _backtest(sessions, params, lo, hi, risk_fraction, spread)


def _backtest(sessions, params, lo, hi, risk_fraction, spread):
    equity = peak = INITIAL
    maximum_drawdown = 0.0
    trades = []
    position = None

    for session in sessions:
        session_ts = session.day * 86_400
        if session_ts < lo or session_ts >= hi:
            continue
        if session.atr is None or session.volatility is None:
            continue

        gap_threshold = params["gap_threshold"]
        if gap_threshold is not None and session.prior_close is not None:
            overnight_move = session.open / session.prior_close - 1.0
            if abs(overnight_move) > gap_threshold:
                side = -1 if overnight_move > 0.0 else 1
                adjusted_risk = risk_fraction
                if params["vol_target"] is not None and session.volatility > 0.0:
                    adjusted_risk *= min(1.0, params["vol_target"] / session.volatility)
                stop_distance = params["stop_atr"] * session.atr
                amount = quantity(equity, session.open, stop_distance, adjusted_risk,
                                  spread)
                if amount >= MINIMUM_QUANTITY:
                    first_bar = session.bars[0]
                    entry = session.open + side * spread
                    hard_stop = session.open - side * stop_distance
                    stopped = ((side == 1 and first_bar[L] <= hard_stop)
                               or (side == -1 and first_bar[H] >= hard_stop))
                    if stopped:
                        raw_exit = (min(first_bar[O], hard_stop) if side == 1
                                    else max(first_bar[O], hard_stop))
                        reason = "gap_stop"
                    else:
                        raw_exit = first_bar[C]
                        reason = "gap_30m"
                    adverse = first_bar[L] if side == 1 else first_bar[H]
                    marked = equity + _trade_pnl(side, entry, adverse, amount)
                    maximum_drawdown = max(
                        maximum_drawdown, (peak - marked) / peak if peak > 0 else 1.0
                    )
                    pnl = _trade_pnl(side, entry, raw_exit, amount)
                    equity += pnl
                    peak = max(peak, equity)
                    maximum_drawdown = max(
                        maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
                    )
                    trades.append({
                        "entry_ts": first_bar[TS],
                        "exit_ts": first_bar[TS] + 30 * 60,
                        "side": side, "quantity": amount, "pnl": pnl,
                        "reason": reason,
                    })

        cumulative_notional = cumulative_volume = 0.0
        for bar, slot in zip(session.bars, SESSION_SLOTS):
            exited_this_bar = False
            typical = (bar[H] + bar[L] + bar[C]) / 3.0
            cumulative_notional += typical * bar[V]
            cumulative_volume += bar[V]
            vwap = cumulative_notional / cumulative_volume if cumulative_volume else bar[C]
            levels = band(session, slot, params)
            if levels is None:
                continue
            upper, lower = levels

            if position is not None:
                side = position["side"]
                hard_stop = position["stop"]
                stopped = ((side == 1 and bar[L] <= hard_stop)
                           or (side == -1 and bar[H] >= hard_stop))
                if stopped:
                    raw_exit = min(bar[O], hard_stop) if side == 1 else max(bar[O], hard_stop)
                    reason = "hard_stop"
                else:
                    trailing = (upper if params["exit_mode"] == "band" else max(upper, vwap))
                    if side == -1:
                        trailing = (lower if params["exit_mode"] == "band" else min(lower, vwap))
                    crossed = bar[C] < trailing if side == 1 else bar[C] > trailing
                    raw_exit = bar[C] if crossed else None
                    reason = "trail"
                if raw_exit is not None:
                    pnl = _trade_pnl(side, position["entry"], raw_exit,
                                     position["quantity"])
                    equity += pnl
                    peak = max(peak, equity)
                    maximum_drawdown = max(
                        maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
                    )
                    trades.append({
                        "entry_ts": position["entry_ts"], "exit_ts": bar[TS],
                        "side": side, "quantity": position["quantity"],
                        "pnl": pnl, "reason": reason,
                    })
                    position = None
                    exited_this_bar = True

            if exited_this_bar or slot < params["first_decision_minute"]:
                continue
            signal = 1 if bar[C] > upper else -1 if bar[C] < lower else None
            if position is None and signal is not None and accepts_side(signal, params["side_mode"]):
                adjusted_risk = risk_fraction
                if params["vol_target"] is not None and session.volatility > 0.0:
                    adjusted_risk *= min(1.0, params["vol_target"] / session.volatility)
                stop_distance = params["stop_atr"] * session.atr
                amount = quantity(equity, bar[C], stop_distance, adjusted_risk,
                                  spread)
                if amount >= MINIMUM_QUANTITY:
                    entry = bar[C] + signal * spread
                    position = {
                        "side": signal, "entry": entry, "entry_ts": bar[TS],
                        "quantity": amount, "stop": bar[C] - signal * stop_distance,
                    }

            if position is not None:
                side = position["side"]
                adverse = bar[L] if side == 1 else bar[H]
                marked = equity + _trade_pnl(side, position["entry"], adverse,
                                             position["quantity"])
                maximum_drawdown = max(
                    maximum_drawdown, (peak - marked) / peak if peak > 0 else 1.0
                )

        if position is not None:
            bar = session.bars[-1]
            pnl = _trade_pnl(position["side"], position["entry"], session.close,
                             position["quantity"])
            equity += pnl
            peak = max(peak, equity)
            maximum_drawdown = max(
                maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
            )
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": bar[TS] + 30 * 60,
                "side": position["side"], "quantity": position["quantity"],
                "pnl": pnl, "reason": "session",
            })
            position = None

    return summarize(trades, equity, maximum_drawdown)


def candidates():
    return [dict(zip(AXES, values)) for values in itertools.product(*AXES.values())]


def frozen(params):
    return tuple(sorted((key, str(value)) for key, value in params.items()))


def passes(stat, neighbour=False):
    dd_limit = MAX_DRAWDOWN_PCT + (2.0 if neighbour else 0.0)
    annual = stat["annual"]
    consistency = stat["consistency"]
    return (
        stat["trades"] >= MIN_TRADES
        and stat["pf"] >= MIN_PROFIT_FACTOR
        and stat["max_dd_pct"] <= dd_limit
        and all(str(year) in annual and annual[str(year)]["pnl"] > 0.0
                and annual[str(year)]["max_dd_pct"] <= MAX_ANNUAL_DRAWDOWN_PCT
                for year in IS_YEARS)
        and consistency["worst_year_return_pct"] >= MIN_ANNUAL_RETURN_PCT
        and consistency["years_above_5pct"] >= MIN_YEARS_ABOVE_FIVE_PCT
        and consistency["annual_return_cv"] <= MAX_ANNUAL_RETURN_CV
        and consistency["max_single_year_return_share"] <= MAX_SINGLE_YEAR_RETURN_SHARE
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in IS_YEARS]
    # Growth has deliberately low weight.  The worst year and dispersion drive
    # selection so a spectacular crisis/rally year cannot hide a brittle edge.
    return (25.0 * math.log(stat["final"] / INITIAL)
            + 3.0 * min(returns) + statistics.median(returns)
            - 1.5 * statistics.pstdev(returns) - 0.5 * stat["max_dd_pct"])


def neighbours(params):
    out = []
    for axis, values in AXES.items():
        if axis in CATEGORICAL:
            continue
        neighbour_values = values
        if axis == "gap_threshold":
            if params[axis] is None:
                continue
            neighbour_values = tuple(value for value in values if value is not None)
        at = neighbour_values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(neighbour_values):
                changed = dict(params)
                changed[axis] = neighbour_values[other]
                out.append(changed)
    return out


def seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(sessions):
    universe = candidates()
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(sessions, params)
        if number % 500 == 0:
            print(f"evaluated {number}/{len(universe)}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        score = quality(stat)
        if not math.isfinite(score):
            continue
        if params["gap_threshold"] is not None:
            control_params = {**params, "gap_threshold": None}
            control = results[frozen(control_params)]
            if (stat["consistency"]["worst_year_return_pct"]
                    <= control["consistency"]["worst_year_return_pct"]
                    or stat["consistency"]["annual_return_cv"]
                    >= control["consistency"]["annual_return_cv"]):
                continue
        nearby = [results[frozen(item)] for item in neighbours(params)]
        robust = [item for item in nearby if passes(item, neighbour=True)]
        if not nearby or len(robust) < math.ceil(0.60 * len(nearby)):
            continue
        neighbour_scores = [quality(item) for item in robust if passes(item)]
        if not neighbour_scores:
            continue
        ranked.append((score, statistics.median(neighbour_scores), params, stat,
                       len(robust), len(nearby)))
    # A cell is only as strong as the weaker of itself and its neighbourhood.
    # This prevents either a lucky peak or a mediocre centre from winning.
    ranked.sort(key=lambda item: (min(item[0], item[1]),
                                  statistics.fmean(item[:2])), reverse=True)

    print("\nTop robust 2017-2024 cells:")
    for score, plateau, params, stat, robust, total in ranked[:10]:
        print(json.dumps({
            "score": round(score, 4), "plateau": round(plateau, 4),
            "robust_neighbours": f"{robust}/{total}", "params": params,
            "stats": stat,
        }, sort_keys=True))
    if not ranked:
        closest = sorted(
            ((sum(stat["annual"].get(str(year), {}).get("pnl", 0.0) > 0.0
                  for year in IS_YEARS), stat["pf"], params, stat)
             for params in universe for stat in (results[frozen(params)],)),
            key=lambda item: item[:2], reverse=True,
        )
        for positive_years, _pf, params, stat in closest[:10]:
            print(json.dumps({"positive_years": positive_years, "params": params,
                              "stats": stat}, sort_keys=True))
        raise SystemExit("no noise-momentum cell cleared the selection protocol")

    score, plateau, params, stat, robust, total = ranked[0]
    payload = {
        "sealed": True,
        "protocol": {
            "in_sample": "2017-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-07-29; unseen by select",
            "bars": "es_1m causally aggregated to RTH 30m bars",
            "clock": "QuestDB Chicago wall-clock; 08:30-15:00 CT = 09:30-16:00 ET",
            "execution": "close-confirmed half-hour decisions; conservative hard-stop first; flat by 16:00 ET",
            "sizing": "1% live-equity stop risk; vol target may only reduce; floored to Exness lot step",
            "contract": "regular Exness US500, point value/contract size 1, min 0.03 lot, 0.25% margin",
            "entry_spread_points": SPREAD,
            "candidate_count": len(universe),
            "prior_discovery_count": 5076,
            "selection_gate": (
                f"every {IS_YEARS[0]}-{IS_YEARS[-1]} year >= {MIN_ANNUAL_RETURN_PCT}%; "
                f">= {MIN_YEARS_ABOVE_FIVE_PCT}/{len(IS_YEARS)} years >=5%; annual return "
                f"CV <= {MAX_ANNUAL_RETURN_CV}; no year > {MAX_SINGLE_YEAR_RETURN_SHARE:.0%} "
                f"of positive annual returns; >= {MIN_TRADES} trades; PF >= {MIN_PROFIT_FACTOR}; "
                f"DD <= {MAX_DRAWDOWN_PCT}%; "
                ">=60% adjacent numeric cells robust; rank by neighbourhood median"
            ),
        },
        "winner": {
            "params": params, "in_sample": stat, "score": round(score, 6),
            "plateau_score": round(plateau, 6),
            "robust_neighbours": f"{robust}/{total}",
        },
    }
    seal(payload)
    print(f"\nSEALED selection to {OUTPUT}")


def validate(sessions):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("selection seal mismatch; rerun select before validate")
    params = payload["winner"]["params"]
    params["vol_target"] = (None if params["vol_target"] is None
                            else float(params["vol_target"]))
    stress = {
        "base_0.20_spread": backtest(sessions, params, IS_END, OOS_END),
        "spread_0.40_stress": backtest(sessions, params, IS_END, OOS_END, spread=0.40),
        "spread_0.80_stress": backtest(sessions, params, IS_END, OOS_END, spread=0.80),
    }
    base_result = stress["base_0.20_spread"]
    validation = {
        "verdict": ("PASS" if base_result["pnl"] > 0.0 and base_result["pf"] >= 1.05
                    else "REJECTED_OUT_OF_SAMPLE"),
        "stress": stress,
    }
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("\n2025-2026 OUT OF SAMPLE (single sealed strategy):")
    print(json.dumps(validation, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    bars = bars_30m(args.phase)
    sessions = build_sessions(bars, args.phase)
    print(f"loaded {len(bars)} half-hours and {len(sessions)} complete sessions")
    if args.phase == "select":
        select(sessions)
    else:
        validate(sessions)


if __name__ == "__main__":
    main()
