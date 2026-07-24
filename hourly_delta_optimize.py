import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

BASE = "http://127.0.0.1:9000/exec?query="
INITIAL = 10_000.0
SPREAD = 0.2


def query(sql):
    with urllib.request.urlopen(BASE + urllib.parse.quote(sql), timeout=120) as response:
        return json.load(response)["dataset"]


def epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    delta: float
    depth: int
    vix: float = 0.0


def bars_for(symbol):
    sources = []
    if symbol == "nq":
        sources.append(("dbento", True))
    sources.append(("bm", False))
    merged = {}
    for prefix, databento in sources:
        timestamp = (
            "date_trunc('minute',to_timezone(timestamp,'America/New_York'))"
            if databento
            else "timestamp"
        )
        rows = query(
            f"SELECT {timestamp} ts,first(price),max(price),min(price),last(price),"
            f"sum(CASE WHEN side='BUY' THEN size WHEN side='SELL' THEN -size ELSE 0 END) "
            f"FROM {prefix}_{symbol}_ticks WHERE size>0 "
            + ("GROUP BY ts ORDER BY ts" if databento else
               "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR")
        )
        depth_rows = query(
            f"SELECT {timestamp} ts,count() FROM {prefix}_{symbol}_depth "
            + ("GROUP BY ts ORDER BY ts" if databento else
               "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR")
        )
        depth = {epoch(row[0]): int(row[1]) for row in depth_rows}
        for row in rows:
            ts = epoch(row[0])
            merged[ts] = Bar(ts, *map(float, row[1:6]), depth.get(ts, 0))

    vix = [(epoch(row[0]), float(row[1])) for row in query(
        "SELECT timestamp,close FROM vix_1h ORDER BY timestamp"
    )]
    vi = 0
    result = []
    for bar in sorted(merged.values(), key=lambda item: item.ts):
        while vi + 1 < len(vix) and vix[vi + 1][0] <= bar.ts:
            vi += 1
        if vix and vix[vi][0] <= bar.ts:
            bar.vix = vix[vi][1]
        result.append(bar)
    return result


def run(bars, min_delta, stop, target):
    equity = INITIAL
    trades = []
    hour = None
    hour_open = hour_close = hour_delta = hour_vix = 0.0
    hour_depth = 0
    entry = None
    quantity = 0.0
    entry_ts = 0
    peak = equity
    max_dd = 0.0

    for bar in bars:
        minute = (bar.ts % 86_400) // 60
        current_hour = bar.ts // 3_600
        signal = False
        if hour == current_hour:
            hour_close = bar.close
            hour_delta += bar.delta
            hour_vix = bar.vix
            hour_depth += bar.depth
        else:
            # Only complete, contiguous hourly signals may enter during RTH.
            signal = (
                hour is not None
                and hour + 1 == current_hour
                and hour_close < hour_open
                and hour_delta >= min_delta
                and hour_depth > 0
                and 570 <= minute < 960
            )
            hour = current_hour
            hour_open = bar.open
            hour_close = bar.close
            hour_delta = bar.delta
            hour_vix = bar.vix
            hour_depth = bar.depth

        if entry is not None:
            exit_price = None
            if bar.low <= entry - stop:
                exit_price = min(bar.open, entry - stop) - SPREAD / 2
            elif bar.high >= entry + target:
                exit_price = max(bar.open, entry + target) - SPREAD / 2
            elif minute >= 960:
                exit_price = bar.close - SPREAD / 2
            if exit_price is not None:
                pnl = (exit_price - entry) * quantity
                equity += pnl
                trades.append((entry_ts, bar.ts, pnl))
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                entry = None

        if entry is None and signal:
            raw_qty = min(equity * 0.02 / stop, equity / 0.25 / bar.open)
            quantity = math.floor(raw_qty * 100) / 100
            if quantity >= 0.01:
                entry = bar.open + SPREAD / 2
                entry_ts = bar.ts

    if entry is not None:
        exit_price = bars[-1].close - SPREAD / 2
        pnl = (exit_price - entry) * quantity
        equity += pnl
        trades.append((entry_ts, bars[-1].ts, pnl))
        max_dd = max(max_dd, peak - equity)
    wins = sum(pnl > 0 for _, _, pnl in trades)
    gross_win = sum(max(0, pnl) for _, _, pnl in trades)
    gross_loss = -sum(min(0, pnl) for _, _, pnl in trades)
    return {
        "delta": min_delta,
        "stop": stop,
        "target": target,
        "trades": len(trades),
        "wins": wins,
        "pnl": equity - INITIAL,
        "max_dd": max_dd,
        "pf": gross_win / gross_loss if gross_loss else (999.0 if gross_win else 0.0),
    }


def signal_funnel(bars, min_delta):
    counts = {
        "rth_hour_boundaries": 0,
        "contiguous": 0,
        "red_hour": 0,
        "delta_threshold": 0,
        "depth_coverage": 0,
    }
    hour = None
    hour_open = hour_close = hour_delta = hour_vix = 0.0
    hour_depth = 0
    for bar in bars:
        minute = (bar.ts % 86_400) // 60
        current_hour = bar.ts // 3_600
        if hour == current_hour:
            hour_close = bar.close
            hour_delta += bar.delta
            hour_vix = bar.vix
            hour_depth += bar.depth
            continue
        if 570 <= minute < 960:
            counts["rth_hour_boundaries"] += 1
            if hour is not None and hour + 1 == current_hour:
                counts["contiguous"] += 1
                if hour_close < hour_open:
                    counts["red_hour"] += 1
                    if hour_delta >= min_delta:
                        counts["delta_threshold"] += 1
                        if hour_depth > 0:
                            counts["depth_coverage"] += 1
        hour = current_hour
        hour_open = bar.open
        hour_close = bar.close
        hour_delta = bar.delta
        hour_vix = bar.vix
        hour_depth = bar.depth
    return counts


def hourly_signals(bars):
    overnight_by_day = {}
    for bar in bars:
        day = bar.ts // 86_400
        minute = (bar.ts % 86_400) // 60
        if minute >= 18 * 60:
            session_day = day + 1
        elif minute < 570:
            session_day = day
        else:
            continue
        overnight_by_day[session_day] = overnight_by_day.get(session_day, 0.0) + bar.delta

    signals = []
    hour = None
    hour_open = hour_close = hour_delta = 0.0
    hour_depth = 0
    rth_day = None
    rth_delta = 0.0
    for index, bar in enumerate(bars):
        day = bar.ts // 86_400
        minute = (bar.ts % 86_400) // 60
        if rth_day != day:
            rth_day = day
            rth_delta = 0.0
        current_hour = bar.ts // 3_600
        if hour != current_hour:
            if (
                hour is not None
                and hour + 1 == current_hour
                and 570 <= minute < 960
                and hour_close < hour_open
                and hour_delta >= 0
                and hour_depth > 0
            ):
                signals.append({
                    "index": index,
                    "ts": bar.ts,
                    "minute": minute,
                    "weekday": datetime.utcfromtimestamp(bar.ts).weekday(),
                    "price": bar.open,
                    "hour_delta": hour_delta,
                    "overnight_delta": overnight_by_day.get(day, 0.0),
                    "rth_delta": rth_delta,
                })
            hour = current_hour
            hour_open = bar.open
            hour_close = bar.close
            hour_delta = bar.delta
            hour_depth = bar.depth
        else:
            hour_close = bar.close
            hour_delta += bar.delta
            hour_depth += bar.depth
        if 570 <= minute < 960:
            rth_delta += bar.delta
    return signals


OUTCOME_CACHE = {}


def trade_outcome(bars, signal, stop, target):
    cache_key = (id(bars), signal["index"], stop, target)
    if cache_key in OUTCOME_CACHE:
        return OUTCOME_CACHE[cache_key]

    def finish(points, exit_index):
        result = (points, exit_index)
        OUTCOME_CACHE[cache_key] = result
        return result

    entry = signal["price"] + SPREAD / 2
    previous = bars[signal["index"]]
    for index in range(signal["index"] + 1, len(bars)):
        bar = bars[index]
        minute = (bar.ts % 86_400) // 60
        if bar.ts // 86_400 != signal["ts"] // 86_400 or minute >= 960:
            return finish(previous.close - SPREAD / 2 - entry, index - 1)
        if bar.low <= entry - stop:
            exit_price = min(bar.open, entry - stop) - SPREAD / 2
            return finish(exit_price - entry, index)
        if bar.high >= entry + target:
            exit_price = max(bar.open, entry + target) - SPREAD / 2
            return finish(exit_price - entry, index)
        previous = bar
    return finish(bars[-1].close - SPREAD / 2 - entry, len(bars) - 1)


def metrics_for(bars, signals, stop, target, sizing="fixed", risk_fraction=0.01):
    equity = INITIAL
    peak = equity
    max_dd = 0.0
    pnls = []
    busy_until = -1
    for signal in signals:
        if signal["index"] <= busy_until:
            continue
        points, busy_until = trade_outcome(bars, signal, stop, target)
        if sizing == "fixed":
            quantity = 1.0
        else:
            raw = min(
                equity * risk_fraction / stop,
                equity / 0.25 / signal["price"],
            )
            quantity = math.floor(raw * 100) / 100
        if quantity < 0.01:
            continue
        pnl = points * quantity
        equity += pnl
        pnls.append(pnl)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    wins = sum(pnl > 0 for pnl in pnls)
    gross_win = sum(max(0.0, pnl) for pnl in pnls)
    gross_loss = -sum(min(0.0, pnl) for pnl in pnls)
    return {
        "trades": len(pnls),
        "wins": wins,
        "pnl": equity - INITIAL,
        "max_dd": max_dd,
        "pf": gross_win / gross_loss if gross_loss else (999.0 if gross_win else 0.0),
    }


def daily_volatility_by_day(bars, lookback):
    closes = {}
    for bar in bars:
        closes[bar.ts // 86_400] = bar.close
    result = {}
    returns = []
    previous_close = None
    for day, close in sorted(closes.items()):
        if len(returns) >= lookback:
            window = returns[-lookback:]
            mean = sum(window) / lookback
            variance = sum((value - mean) ** 2 for value in window) / lookback
            result[day] = math.sqrt(variance)
        if previous_close is not None and previous_close > 0:
            returns.append(close / previous_close - 1.0)
        previous_close = close
    return result


def volatility_metrics(
    bars,
    signals,
    lookback,
    reference_volatility,
    minimum_scale,
    maximum_scale,
    risk_fraction=0.005,
):
    volatility = daily_volatility_by_day(bars, lookback)
    equity = INITIAL
    peak = equity
    max_dd = 0.0
    pnls = []
    scales = []
    busy_until = -1
    for signal in signals:
        if signal["index"] <= busy_until:
            continue
        daily_volatility = volatility.get(signal["ts"] // 86_400)
        scale = (
            max(minimum_scale, min(maximum_scale, daily_volatility / reference_volatility))
            if daily_volatility is not None
            else 1.0
        )
        stop = 40.0 * scale
        target = 50.0 * scale
        points, busy_until = trade_outcome(bars, signal, stop, target)
        raw = min(
            equity * risk_fraction / stop,
            equity / 0.25 / signal["price"],
        )
        quantity = math.floor(raw * 100) / 100
        if quantity < 0.01:
            continue
        pnl = points * quantity
        equity += pnl
        pnls.append(pnl)
        scales.append(scale)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    wins = sum(pnl > 0 for pnl in pnls)
    gross_win = sum(max(0.0, pnl) for pnl in pnls)
    gross_loss = -sum(min(0.0, pnl) for pnl in pnls)
    return {
        "trades": len(pnls),
        "wins": wins,
        "pnl": equity - INITIAL,
        "max_dd": max_dd,
        "pf": gross_win / gross_loss if gross_loss else (999.0 if gross_win else 0.0),
        "average_scale": sum(scales) / len(scales) if scales else 0.0,
    }


def volatility_optimize(bars):
    signals = [
        signal
        for signal in hourly_signals(bars)
        if signal["weekday"] != 3 and signal["hour_delta"] >= 50
    ]
    split_ts = epoch("2026-07-01T00:00:00Z")
    ranked = []
    for lookback in [4, 10, 14, 20]:
        for reference in [0.01, 0.015, 0.02, 0.024]:
            for minimum_scale in [0.5, 0.75]:
                for maximum_scale in [1.5, 2.0]:
                    total = volatility_metrics(
                        bars,
                        signals,
                        lookback,
                        reference,
                        minimum_scale,
                        maximum_scale,
                    )
                    before = volatility_metrics(
                        bars,
                        [signal for signal in signals if signal["ts"] < split_ts],
                        lookback,
                        reference,
                        minimum_scale,
                        maximum_scale,
                    )
                    july = volatility_metrics(
                        bars,
                        [signal for signal in signals if signal["ts"] >= split_ts],
                        lookback,
                        reference,
                        minimum_scale,
                        maximum_scale,
                    )
                    if before["pnl"] <= 0 or july["pnl"] <= 0:
                        continue
                    ranked.append({
                        "lookback": lookback,
                        "reference_volatility": reference,
                        "minimum_scale": minimum_scale,
                        "maximum_scale": maximum_scale,
                        "total": total,
                        "before_july": before,
                        "july": july,
                    })
    ranked.sort(
        key=lambda result: (
            min(result["before_july"]["pf"], result["july"]["pf"]),
            result["total"]["pnl"] / max(result["total"]["max_dd"], 1),
        ),
        reverse=True,
    )
    fixed = metrics_for(
        bars,
        signals,
        40,
        50,
        sizing="risk",
        risk_fraction=0.005,
    )
    return {"fixed_baseline": fixed, "top": ranked[:20]}


def hourly_short_signals(bars):
    signals = []
    hour = None
    hour_open = hour_close = hour_delta = 0.0
    hour_depth = 0
    for index, bar in enumerate(bars):
        minute = (bar.ts % 86_400) // 60
        current_hour = bar.ts // 3_600
        if hour != current_hour:
            if (
                hour is not None
                and hour + 1 == current_hour
                and 570 <= minute < 960
                and hour_close > hour_open
                and hour_delta < 0
                and hour_depth > 0
                and datetime.utcfromtimestamp(bar.ts).weekday() != 3
            ):
                signals.append({
                    "index": index,
                    "ts": bar.ts,
                    "price": bar.open,
                    "hour_delta": hour_delta,
                })
            hour = current_hour
            hour_open = bar.open
            hour_close = bar.close
            hour_delta = bar.delta
            hour_depth = bar.depth
        else:
            hour_close = bar.close
            hour_delta += bar.delta
            hour_depth += bar.depth
    return signals


def short_outcome(bars, signal, stop, target):
    entry = signal["price"] - SPREAD / 2
    previous = bars[signal["index"]]
    for index in range(signal["index"], len(bars)):
        bar = bars[index]
        minute = (bar.ts % 86_400) // 60
        if bar.ts // 86_400 != signal["ts"] // 86_400 or minute >= 960:
            return entry - (previous.close + SPREAD / 2)
        if bar.high >= signal["price"] + stop:
            return entry - (max(bar.open, signal["price"] + stop) + SPREAD / 2)
        if bar.low <= signal["price"] - target:
            return entry - (min(bar.open, signal["price"] - target) + SPREAD / 2)
        previous = bar
    return entry - (bars[-1].close + SPREAD / 2)


def short_metrics(bars, signals, min_delta, stop, target):
    selected = [signal for signal in signals if signal["hour_delta"] <= -min_delta]
    points = [short_outcome(bars, signal, stop, target) for signal in selected]
    wins = sum(value > 0 for value in points)
    gross_win = sum(max(0.0, value) for value in points)
    gross_loss = -sum(min(0.0, value) for value in points)
    equity = INITIAL
    peak = equity
    max_dd = 0.0
    pnl = 0.0
    for signal, result in zip(selected, points):
        raw = min(
            equity * 0.005 / stop,
            equity / 0.25 / signal["price"],
        )
        quantity = math.floor(raw * 100) / 100
        trade_pnl = result * quantity
        equity += trade_pnl
        pnl += trade_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(points),
        "wins": wins,
        "pnl": pnl,
        "max_dd": max_dd,
        "pf": gross_win / gross_loss if gross_loss else (999.0 if gross_win else 0.0),
    }


def optimize_short(bars):
    signals = hourly_short_signals(bars)
    split_ts = epoch("2026-07-01T00:00:00Z")
    deltas = [25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500]
    stops = [20, 25, 30, 40, 50, 75, 100, 125, 150, 200]
    targets = [20, 25, 30, 40, 50, 75, 100, 125, 150, 200, 250, 300, 400]
    ranked = []
    aggregate_ranked = []
    for delta in deltas:
        for stop in stops:
            for target in targets:
                if target <= stop:
                    continue
                total = short_metrics(bars, signals, delta, stop, target)
                before = short_metrics(
                    bars,
                    [signal for signal in signals if signal["ts"] < split_ts],
                    delta,
                    stop,
                    target,
                )
                july = short_metrics(
                    bars,
                    [signal for signal in signals if signal["ts"] >= split_ts],
                    delta,
                    stop,
                    target,
                )
                if total["trades"] >= 15:
                    aggregate_ranked.append({
                        "delta": delta,
                        "stop": stop,
                        "target": target,
                        "total": total,
                        "before_july": before,
                        "july": july,
                    })
                if (
                    total["trades"] < 15
                    or before["trades"] < 5
                    or july["trades"] < 5
                    or before["pnl"] <= 0
                    or july["pnl"] <= 0
                ):
                    continue
                ranked.append({
                    "delta": delta,
                    "stop": stop,
                    "target": target,
                    "total": total,
                    "before_july": before,
                    "july": july,
                })
    ranked.sort(
        key=lambda result: (
            min(result["before_july"]["pf"], result["july"]["pf"]),
            result["total"]["pnl"] / max(result["total"]["max_dd"], 1),
            result["total"]["trades"],
        ),
        reverse=True,
    )
    aggregate_ranked.sort(
        key=lambda result: (
            result["total"]["pnl"] / max(result["total"]["max_dd"], 1),
            result["total"]["pnl"],
        ),
        reverse=True,
    )
    return {
        "raw_signals": len(signals),
        "split_validated": ranked[:20],
        "aggregate": aggregate_ranked[:20],
    }


def advanced_optimize(bars):
    signals = hourly_signals(bars)
    split_ts = epoch("2026-07-01T00:00:00Z")
    time_filters = {
        "all_rth": set(range(570, 960)),
        "morning_1000_1259": set(range(600, 780)),
        "afternoon_1300_1559": set(range(780, 960)),
        "core_1100_1459": set(range(660, 900)),
    }
    day_filters = {"all_days": set(range(5))}
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    for weekday, name in enumerate(day_names):
        day_filters[f"exclude_{name}"] = set(range(5)) - {weekday}
    delta_filters = {
        "none": lambda value: True,
        "positive": lambda value: value >= 0,
        "negative": lambda value: value < 0,
    }
    stops = [20, 25, 30, 40, 50, 75, 100]
    targets = [25, 40, 50, 75, 100, 125, 150, 200]
    ranked = []
    for time_name, minutes in time_filters.items():
        for day_name, weekdays in day_filters.items():
            for overnight_name, overnight_ok in delta_filters.items():
                for rth_name, rth_ok in delta_filters.items():
                    for min_delta in [0, 25, 50, 75, 100, 150, 200, 300]:
                        selected = [
                            signal for signal in signals
                            if signal["minute"] in minutes
                            and signal["weekday"] in weekdays
                            and signal["hour_delta"] >= min_delta
                            and overnight_ok(signal["overnight_delta"])
                            and rth_ok(signal["rth_delta"])
                        ]
                        for stop in stops:
                            for target in targets:
                                if target <= stop:
                                    continue
                                total = metrics_for(bars, selected, stop, target)
                                if total["trades"] < 15:
                                    continue
                                before = metrics_for(
                                    bars,
                                    [signal for signal in selected if signal["ts"] < split_ts],
                                    stop,
                                    target,
                                )
                                july = metrics_for(
                                    bars,
                                    [signal for signal in selected if signal["ts"] >= split_ts],
                                    stop,
                                    target,
                                )
                                if (
                                    before["trades"] < 5
                                    or july["trades"] < 5
                                    or before["pnl"] <= 0
                                    or july["pnl"] <= 0
                                ):
                                    continue
                                ranked.append({
                                    "time": time_name,
                                    "days": day_name,
                                    "overnight_delta": overnight_name,
                                    "rth_delta": rth_name,
                                    "min_hour_delta": min_delta,
                                    "stop": stop,
                                    "target": target,
                                    "total": total,
                                    "before_july": before,
                                    "july": july,
                                })
    ranked.sort(
        key=lambda result: (
            min(result["before_july"]["pf"], result["july"]["pf"]),
            result["total"]["pnl"] / max(result["total"]["max_dd"], 1),
            result["total"]["trades"],
        ),
        reverse=True,
    )
    if not ranked:
        return {"raw_signals": len(signals), "top": [], "sizing": []}
    best = ranked[0]
    selected = [
        signal for signal in signals
        if signal["minute"] in time_filters[best["time"]]
        and signal["weekday"] in day_filters[best["days"]]
        and signal["hour_delta"] >= best["min_hour_delta"]
        and delta_filters[best["overnight_delta"]](signal["overnight_delta"])
        and delta_filters[best["rth_delta"]](signal["rth_delta"])
    ]
    sizing = [{
        "technique": "fixed_1",
        **metrics_for(bars, selected, best["stop"], best["target"]),
    }]
    for risk in [0.005, 0.01, 0.02]:
        sizing.append({
            "technique": f"fixed_fractional_{risk:.3f}",
            **metrics_for(
                bars,
                selected,
                best["stop"],
                best["target"],
                sizing="risk",
                risk_fraction=risk,
            ),
        })
    return {
        "raw_signals": len(signals),
        "top": ranked[:20],
        "sizing": sizing,
    }


def optimize(symbol):
    bars = bars_for(symbol)
    if symbol == "nq":
        deltas = [0, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500]
        stops = [25, 40, 50, 75, 100, 125, 150, 200]
        targets = [25, 40, 50, 75, 100, 150, 200, 250, 300, 400]
    else:
        deltas = [0, 50, 100, 200, 400, 600, 800, 1000, 1500, 2000]
        stops = [1, 2, 3, 4, 5, 7.5, 10, 15]
        targets = [2, 3, 5, 7.5, 10, 15, 20, 30, 40, 50]
    results = [
        run(bars, delta, stop, target)
        for delta in deltas
        for stop in stops
        for target in targets
        if target > stop
    ]
    eligible = [result for result in results if result["trades"] >= 15]
    ranked = sorted(
        eligible,
        key=lambda result: (result["pnl"] / max(result["max_dd"], 1), result["pnl"]),
        reverse=True,
    )
    baseline = run(bars, 100 if symbol == "nq" else 800,
                   125 if symbol == "nq" else 3,
                   200 if symbol == "nq" else 15)
    split_ts = epoch("2026-07-01T00:00:00Z")
    candidates = [(25, 40, 25), (150, 40, 25), (200, 40, 50)]
    validation = [{
        "parameters": {"delta": delta, "stop": stop, "target": target},
        "before_july": run([bar for bar in bars if bar.ts < split_ts], delta, stop, target),
        "july": run([bar for bar in bars if bar.ts >= split_ts], delta, stop, target),
    } for delta, stop, target in candidates]
    print(json.dumps({
        "symbol": symbol,
        "first": datetime.fromtimestamp(bars[0].ts).isoformat(),
        "last": datetime.fromtimestamp(bars[-1].ts).isoformat(),
        "minutes": len(bars),
        "signal_funnel": signal_funnel(bars, 150),
        "baseline": baseline,
        "validation": validation,
        "advanced": advanced_optimize(bars),
        "volatility": volatility_optimize(bars),
        "top": ranked[:20],
    }))


if __name__ == "__main__":
    bars = bars_for("nq")
    print(json.dumps({
        "first": datetime.fromtimestamp(bars[0].ts).isoformat(),
        "last": datetime.fromtimestamp(bars[-1].ts).isoformat(),
        "minutes": len(bars),
        "short": optimize_short(bars),
    }))
