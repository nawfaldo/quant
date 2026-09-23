"""Global selection across non-momentum ES intraday strategy families.

Families compete on one 2017-2024 protocol and only the global winner may read
2025-2026.  This prevents family shopping on the holdout.  Signals use five-
minute RTH bars from Chicago-clock ``es_1m`` and execute on the next bar open.

Families:

* opening-gap reversal after the documented initial continuation window;
* NR5/NR7/NR10 volatility-contraction opening-range breakout;
* failed initial-balance breakout fade;
* prior-session high/low rejection;
* daily-trend VWAP pullback and reclaim.
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
from sandbox.research import es_noise_momentum_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "es_diverse_families_selection.json")

SESSION_OPEN = 8 * 60 + 30
SESSION_CLOSE = 15 * 60
SLOTS = tuple(range(SESSION_OPEN, SESSION_CLOSE, 5))
IS_START, IS_END, OOS_END = base.IS_START, base.IS_END, base.OOS_END
IS_YEARS = base.IS_YEARS

TS, O, H, L, C, V = range(6)


@dataclass(frozen=True)
class Session:
    day: int
    bars: tuple
    vwap: tuple
    open: float
    high: float
    low: float
    close: float
    prior_close: float | None
    prior_high: float | None
    prior_low: float | None
    atr: float | None
    volatility: float | None
    sma20: float | None
    past_ranges: tuple


FAMILY_AXES = {
    "open_reversal": {
        "gap_threshold": (0.001, 0.002, 0.003),
        "wait_bars": (2, 3, 6),
        "stop_atr": (0.25, 0.50, 0.75),
        "exit_mode": ("prior_close", "time_12", "session"),
        "vol_target": (None, 0.15),
    },
    "nr_breakout": {
        "nr_lookback": (5, 7, 10),
        "range_bars": (3, 4, 6, 9, 12),
        "buffer_atr": (0.0, 0.025, 0.05),
        "stop_atr": (0.35, 0.50, 0.65),
        "exit_mode": ("rr_1_5", "rr_2", "session"),
        "side_mode": ("both", "long"),
    },
    "failed_ib": {
        "range_bars": (6, 12),
        "buffer_atr": (0.0, 0.05),
        "stop_atr": (0.25, 0.50),
        "exit_mode": ("vwap", "mid", "rr_1_5"),
        "side_mode": ("both", "long"),
    },
    "prior_rejection": {
        "buffer_atr": (0.0, 0.05),
        "stop_atr": (0.25, 0.50),
        "exit_mode": ("vwap", "prior_mid", "rr_1_5"),
        "side_mode": ("both", "long"),
    },
    "vwap_reclaim": {
        "deviation_atr": (0.05, 0.10, 0.20),
        "stop_atr": (0.25, 0.50),
        "exit_mode": ("rr_1_5", "rr_2", "session"),
        "side_mode": ("both", "long"),
    },
}

CATEGORICAL = {"exit_mode", "side_mode"}
MIN_TRADES = 150
MIN_TRADES_PER_YEAR = 12
MIN_PF = 1.08
MAX_DD = 18.0
MIN_ANNUAL_RETURN = 3.0
MIN_YEARS_ABOVE_FIVE = 5
MAX_RETURN_CV = 0.85
MAX_YEAR_SHARE = 0.35
MIN_POSITIVE_MONTHS = 58
RISK_FRACTION = 0.02


def bars_5m(phase):
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long),first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM es_1m WHERE timestamp >= '2016-01-01' "
        f"{upper} AND ((hour(timestamp)=8 AND minute(timestamp)>=30) OR "
        "(hour(timestamp)>=9 AND hour(timestamp)<15)) "
        "SAMPLE BY 5m FILL(NONE) ALIGN TO CALENDAR"
    )
    return [(int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
            for row in rows]


def build_sessions(bars):
    grouped = {}
    for bar in bars:
        grouped.setdefault(bar[TS] // 86_400, []).append(bar)
    ranges = deque(maxlen=20)
    true_ranges = deque(maxlen=20)
    returns = deque(maxlen=20)
    closes = deque(maxlen=20)
    previous = None
    sessions = []
    for day in sorted(grouped):
        by_slot = {bar[TS] % 86_400 // 60: bar for bar in grouped[day]}
        if any(slot not in by_slot for slot in SLOTS):
            continue
        day_bars = tuple(by_slot[slot] for slot in SLOTS)
        day_open = day_bars[0][O]
        day_high = max(bar[H] for bar in day_bars)
        day_low = min(bar[L] for bar in day_bars)
        day_close = day_bars[-1][C]
        notional = volume = 0.0
        vwaps = []
        for bar in day_bars:
            typical = (bar[H] + bar[L] + bar[C]) / 3.0
            notional += typical * bar[V]
            volume += bar[V]
            vwaps.append(notional / volume if volume else bar[C])
        atr = statistics.fmean(true_ranges) if len(true_ranges) == 20 else None
        volatility = (statistics.pstdev(returns) * math.sqrt(252.0)
                      if len(returns) == 20 else None)
        sma20 = statistics.fmean(closes) if len(closes) == 20 else None
        sessions.append(Session(
            day=day, bars=day_bars, vwap=tuple(vwaps), open=day_open,
            high=day_high, low=day_low, close=day_close,
            prior_close=None if previous is None else previous[2],
            prior_high=None if previous is None else previous[0],
            prior_low=None if previous is None else previous[1],
            atr=atr, volatility=volatility, sma20=sma20,
            past_ranges=tuple(ranges),
        ))
        true_range = day_high - day_low
        if previous is not None:
            true_range = max(true_range, abs(day_high - previous[2]),
                             abs(day_low - previous[2]))
            returns.append(math.log(day_close / previous[2]))
        ranges.append(day_high - day_low)
        true_ranges.append(true_range)
        closes.append(day_close)
        previous = (day_high, day_low, day_close)
    return sessions


def candidates():
    result = []
    for family, axes in FAMILY_AXES.items():
        for values in itertools.product(*axes.values()):
            result.append({"family": family, **dict(zip(axes, values))})
    return result


def frozen(params):
    return tuple(sorted((key, str(value)) for key, value in params.items()))


def _signal_open_reversal(session, params):
    if session.prior_close is None:
        return None
    gap = session.open / session.prior_close - 1.0
    if abs(gap) < params["gap_threshold"]:
        return None
    index = params["wait_bars"]
    side = -1 if gap > 0.0 else 1
    target = session.prior_close if params["exit_mode"] == "prior_close" else None
    if target is not None:
        entry = session.bars[index][O]
        if (side == 1 and target <= entry) or (side == -1 and target >= entry):
            return None
    exit_index = (min(len(session.bars) - 1, index + 12)
                  if params["exit_mode"] == "time_12" else len(session.bars) - 1)
    return index, side, target, exit_index


def _signal_nr_breakout(session, params):
    n = params["nr_lookback"]
    if len(session.past_ranges) < n:
        return None
    recent = session.past_ranges[-n:]
    if recent[-1] > min(recent) + 1e-12:
        return None
    count = params["range_bars"]
    high = max(bar[H] for bar in session.bars[:count])
    low = min(bar[L] for bar in session.bars[:count])
    buffer = params["buffer_atr"] * session.atr
    for index in range(count, len(session.bars) - 1):
        close = session.bars[index][C]
        side = 1 if close > high + buffer else -1 if close < low - buffer else None
        if side is not None and base.accepts_side(side, params["side_mode"]):
            return index + 1, side, None, len(session.bars) - 1
    return None


def _signal_failed_ib(session, params):
    count = params["range_bars"]
    high = max(bar[H] for bar in session.bars[:count])
    low = min(bar[L] for bar in session.bars[:count])
    middle = (high + low) / 2.0
    buffer = params["buffer_atr"] * session.atr
    for index in range(count, len(session.bars) - 1):
        bar = session.bars[index]
        side = None
        if bar[H] > high + buffer and bar[C] < high:
            side = -1
        elif bar[L] < low - buffer and bar[C] > low:
            side = 1
        if side is None or not base.accepts_side(side, params["side_mode"]):
            continue
        target = session.vwap[index] if params["exit_mode"] == "vwap" else (
            middle if params["exit_mode"] == "mid" else None)
        entry = session.bars[index + 1][O]
        if target is not None and ((side == 1 and target <= entry)
                                   or (side == -1 and target >= entry)):
            continue
        return index + 1, side, target, len(session.bars) - 1
    return None


def _signal_prior_rejection(session, params):
    if session.prior_high is None:
        return None
    buffer = params["buffer_atr"] * session.atr
    middle = (session.prior_high + session.prior_low) / 2.0
    for index in range(1, len(session.bars) - 1):
        bar = session.bars[index]
        side = None
        if bar[H] > session.prior_high + buffer and bar[C] < session.prior_high:
            side = -1
        elif bar[L] < session.prior_low - buffer and bar[C] > session.prior_low:
            side = 1
        if side is None or not base.accepts_side(side, params["side_mode"]):
            continue
        target = session.vwap[index] if params["exit_mode"] == "vwap" else (
            middle if params["exit_mode"] == "prior_mid" else None)
        entry = session.bars[index + 1][O]
        if target is not None and ((side == 1 and target <= entry)
                                   or (side == -1 and target >= entry)):
            continue
        return index + 1, side, target, len(session.bars) - 1
    return None


def _signal_vwap_reclaim(session, params):
    if session.sma20 is None or session.prior_close is None:
        return None
    threshold = params["deviation_atr"] * session.atr
    for index in range(2, len(session.bars) - 1):
        previous = session.bars[index - 1][C]
        current = session.bars[index][C]
        side = None
        if (session.prior_close > session.sma20
                and previous < session.vwap[index - 1] - threshold
                and current > session.vwap[index]):
            side = 1
        elif (session.prior_close < session.sma20
              and previous > session.vwap[index - 1] + threshold
              and current < session.vwap[index]):
            side = -1
        if side is not None and base.accepts_side(side, params["side_mode"]):
            return index + 1, side, None, len(session.bars) - 1
    return None


SIGNALS = {
    "open_reversal": _signal_open_reversal,
    "nr_breakout": _signal_nr_breakout,
    "failed_ib": _signal_failed_ib,
    "prior_rejection": _signal_prior_rejection,
    "vwap_reclaim": _signal_vwap_reclaim,
}


def _rr(params):
    mode = params["exit_mode"]
    if mode == "rr_1_5":
        return 1.5
    if mode == "rr_2":
        return 2.0
    return None


def backtest(sessions, params, lo=IS_START, hi=IS_END, spread=base.SPREAD):
    equity = peak = base.INITIAL
    maximum_drawdown = 0.0
    trades = []
    for session in sessions:
        day_ts = session.day * 86_400
        if day_ts < lo or day_ts >= hi or session.atr is None or session.volatility is None:
            continue
        setup = SIGNALS[params["family"]](session, params)
        if setup is None:
            continue
        entry_index, side, target, exit_index = setup
        stop_distance = params["stop_atr"] * session.atr
        risk = RISK_FRACTION
        vol_target = params.get("vol_target")
        if vol_target is not None and session.volatility > 0.0:
            risk *= min(1.0, vol_target / session.volatility)
        raw_entry = session.bars[entry_index][O]
        amount = base.quantity(equity, raw_entry, stop_distance, risk, spread)
        if amount < base.MINIMUM_QUANTITY:
            continue
        entry = raw_entry + side * spread
        stop = raw_entry - side * stop_distance
        rr = _rr(params)
        if rr is not None:
            target = raw_entry + side * rr * stop_distance
        exit_price = session.bars[exit_index][C]
        exit_ts = session.bars[exit_index][TS] + 5 * 60
        reason = "time" if exit_index < len(session.bars) - 1 else "session"
        actual_exit_index = exit_index
        for index in range(entry_index, exit_index + 1):
            bar = session.bars[index]
            stopped = ((side == 1 and bar[L] <= stop)
                       or (side == -1 and bar[H] >= stop))
            reached = (target is not None and
                       ((side == 1 and bar[H] >= target)
                        or (side == -1 and bar[L] <= target)))
            if stopped:
                exit_price = min(bar[O], stop) if side == 1 else max(bar[O], stop)
                exit_ts, reason = bar[TS], "stop"
                actual_exit_index = index
                break
            if reached:
                exit_price = max(bar[O], target) if side == 1 else min(bar[O], target)
                exit_ts, reason = bar[TS], "target"
                actual_exit_index = index
                break
        adverse = min(bar[L] for bar in session.bars[entry_index:actual_exit_index + 1]) if side == 1 else max(
            bar[H] for bar in session.bars[entry_index:actual_exit_index + 1])
        marked = equity + base._trade_pnl(side, entry, adverse, amount)
        maximum_drawdown = max(maximum_drawdown,
                               (peak - marked) / peak if peak > 0 else 1.0)
        pnl = base._trade_pnl(side, entry, exit_price, amount)
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown,
                               (peak - equity) / peak if peak > 0 else 1.0)
        trades.append({"entry_ts": session.bars[entry_index][TS],
                       "exit_ts": exit_ts, "side": side, "quantity": amount,
                       "pnl": pnl, "reason": reason})
    return base.summarize(trades, equity, maximum_drawdown)


def passes(stat, neighbour=False):
    annual = stat["annual"]
    consistency = stat["consistency"]
    return (stat["trades"] >= MIN_TRADES and stat["pf"] >= MIN_PF
            and stat["max_dd_pct"] <= MAX_DD + (2.0 if neighbour else 0.0)
            and stat["positive_months"] >= MIN_POSITIVE_MONTHS
            and all(str(year) in annual
                    and annual[str(year)]["return_pct"] >= MIN_ANNUAL_RETURN
                    and annual[str(year)]["trades"] >= MIN_TRADES_PER_YEAR
                    for year in IS_YEARS)
            and consistency["years_above_5pct"] >= MIN_YEARS_ABOVE_FIVE
            and consistency["annual_return_cv"] <= MAX_RETURN_CV
            and consistency["max_single_year_return_share"] <= MAX_YEAR_SHARE)


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in IS_YEARS]
    return (25.0 * math.log(stat["final"] / base.INITIAL) + 3.0 * min(returns)
            + statistics.median(returns) - 1.5 * statistics.pstdev(returns)
            - 0.5 * stat["max_dd_pct"] + 0.1 * stat["positive_months"])


def neighbours(params):
    axes = FAMILY_AXES[params["family"]]
    result = []
    for axis, values in axes.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                changed = dict(params)
                changed[axis] = values[other]
                result.append(changed)
    return result


def _write(payload, sealed=True):
    if sealed:
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
        if number % 100 == 0:
            print(f"evaluated {number}/{len(universe)}", flush=True)
    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        nearby = [results[frozen(item)] for item in neighbours(params)]
        robust = [item for item in nearby if passes(item, neighbour=True)]
        if not nearby or len(robust) < math.ceil(0.60 * len(nearby)):
            continue
        strict = [quality(item) for item in robust if passes(item)]
        if not strict:
            continue
        plateau = statistics.median(strict)
        ranked.append((min(own, plateau), own, plateau, params, stat,
                       len(robust), len(nearby)))
    ranked.sort(reverse=True, key=lambda row: (row[0], (row[1] + row[2]) / 2.0))
    print("Top diverse-family cells (2017-2024 only):")
    for robust_score, own, plateau, params, stat, robust, total in ranked[:10]:
        print(json.dumps({"robust_score": round(robust_score, 3),
                          "score": round(own, 3), "plateau": round(plateau, 3),
                          "robust_neighbours": f"{robust}/{total}",
                          "params": params, "stats": stat}, sort_keys=True))
    if not ranked:
        strict_cells = sorted(
            ((quality(results[frozen(params)]), params, results[frozen(params)])
             for params in universe if math.isfinite(quality(results[frozen(params)]))),
            reverse=True, key=lambda row: row[0])
        strict_diagnostics = []
        for score, params, stat in strict_cells[:5]:
            local = []
            for item in neighbours(params):
                neighbour_stat = results[frozen(item)]
                local.append({"params": item,
                              "passes": passes(neighbour_stat, neighbour=True),
                              "return_pct": neighbour_stat["return_pct"],
                              "pf": neighbour_stat["pf"],
                              "max_dd_pct": neighbour_stat["max_dd_pct"],
                              "positive_months": neighbour_stat["positive_months"],
                              "consistency": neighbour_stat["consistency"],
                              "annual_returns": {
                                  year: values["return_pct"]
                                  for year, values in neighbour_stat["annual"].items()
                              }})
            strict_diagnostics.append({"score": round(score, 6), "params": params,
                                       "stats": stat, "neighbours": local})
        family_best = {}
        for family in FAMILY_AXES:
            cells = [(sum(stat["annual"].get(str(year), {}).get("pnl", 0.0) > 0.0
                          for year in IS_YEARS), stat["pf"], params, stat)
                     for params in universe if params["family"] == family
                     for stat in (results[frozen(params)],)]
            family_best[family] = max(cells, key=lambda row: row[:2])
        payload = {"sealed": False, "verdict": "REJECTED_IN_SAMPLE",
                   "protocol": protocol(len(universe)),
                   "strict_cell_diagnostics": strict_diagnostics,
                   "family_best": {family: {"positive_years": row[0],
                                             "params": row[2], "stats": row[3]}
                                   for family, row in family_best.items()}}
        _write(payload, sealed=False)
        print(json.dumps(payload, indent=2, sort_keys=True))
        raise SystemExit("no diverse ES family cleared consistency and plateau gates")
    robust_score, own, plateau, params, stat, robust, total = ranked[0]
    _write({"sealed": True, "protocol": protocol(len(universe)),
            "winner": {"params": params, "in_sample": stat,
                       "robust_score": round(robust_score, 6),
                       "score": round(own, 6), "plateau_score": round(plateau, 6),
                       "robust_neighbours": f"{robust}/{total}"}})


def protocol(count):
    return {
        "families": list(FAMILY_AXES), "candidate_count": count,
        "prior_coarse_discovery_count": 486,
        "in_sample": "2017-2024", "out_of_sample": "2025-2026 global winner only",
        "bars": "ES five-minute RTH; Chicago wall clock",
        "execution": "next-bar entry, hard-stop-first, flat by 16:00 New York",
        "sizing": "2% compounded equity risk; regular Exness US500; 0.20 spread",
        "consistency_gate": (
            "each year >=3% and >=12 trades; 5/8 years >=5%; >=58/96 positive months; "
            "annual return CV <=0.85; max year share <=35%; PF >=1.08; DD <=18%"
        ),
    }


def validate(sessions):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not payload.get("sealed"):
        raise SystemExit("diverse-family search was rejected in sample; no OOS validation")
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("diverse-family seal mismatch")
    params = payload["winner"]["params"]
    stress = {f"spread_{spread:.2f}": backtest(
        sessions, params, IS_END, OOS_END, spread
    ) for spread in (0.20, 0.40, 0.80)}
    result = stress["spread_0.20"]
    payload["seal_sha256"] = expected
    payload["validation"] = {
        "verdict": ("PASS" if result["pnl"] > 0 and result["pf"] >= 1.05
                    else "REJECTED_OUT_OF_SAMPLE"),
        "stress": stress,
    }
    _write(payload, sealed=False)
    print(json.dumps(payload["validation"], indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    bars = bars_5m(args.phase)
    sessions = build_sessions(bars)
    print(f"loaded {len(bars)} five-minute bars and {len(sessions)} sessions")
    if args.phase == "select":
        select(sessions)
    else:
        validate(sessions)


if __name__ == "__main__":
    main()
