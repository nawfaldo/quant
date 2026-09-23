"""Does `btc_hourly_momentum` survive being confined to the 09:30-16:00 session?

The live strategy decides on the completed 16:00 hourly candle, fills at the
17:00 open, and holds up to 24 hours -- so it is an overnight strategy by
construction. Confining it to New York regular trading hours means two changes
that cannot be separated:

  1. entries only inside 09:30-16:00, and
  2. a forced flatten at 16:00, capping the hold at a few hours instead of 24.

With the locked 16:00 signal hour, rule 1 alone makes the strategy untradeable:
its only fill lands at 17:00, outside the window, so it never enters. The sweep
below therefore also walks the signal hour across every value whose fill lands
inside the session (9 through 14 -> fills at 10:00 through 15:00), which is the
only way the question has a non-empty answer.

Execution mirrors `live_trade/src/strategies/idk/btc_hourly_momentum.rs` bar for
bar: hourly candles assembled causally from the minute feed, stop checked before
target within a minute, the same leverage-capped sizing, and the research
convention of charging the whole 0.20 spread once at exit accounting.

Minute bars are pulled a year at a time and the strategy state carries across
the boundary, so the 336-hour EMA warm-up stays causal without ever holding the
whole 4.7M-row history in memory at once.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import deque
from datetime import datetime, timezone

from sandbox import data


INITIAL = 1_000.0
SPREAD = 0.2
STEP = 0.01

# Locked strategy constants, mirrored from the Rust module.
MOMENTUM_HOURS = 24
EMA_HOURS = 336.0
ATR_HOURS = 24
MOMENTUM_ATR = 1.0
STOP_ATR = 2.5
REWARD_RISK = 1.5
MAX_HOLD_SECONDS = 24 * 60 * 60
RISK_FRACTION = 0.01
POSITION_LEVERAGE = 2.0
RESEARCH_MAX_LEVERAGE = 4.0
MAX_LEVERAGE = 2.0

OPEN_MINUTE = 9 * 60 + 30
CLOSE_MINUTE = 16 * 60

OUTPUT = os.path.join(os.path.dirname(__file__), "btc_hourly_momentum_rth.json")


def minute_bars(year):
    """Minute bars for one calendar year, as (ts, open, high, low, close)."""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,open,high,low,close FROM btc_1m "
        f"WHERE timestamp >= '{year}-01-01' AND timestamp < '{year + 1}-01-01' "
        "ORDER BY timestamp"
    )
    return [
        (int(row[0]) // 1_000_000, float(row[1]), float(row[2]), float(row[3]),
         float(row[4]))
        for row in rows
    ]


def is_weekday(ts):
    """Epoch day 0 is a Thursday, so `+ 3` rotates the week to start on Monday."""
    return (ts // 86_400 + 3) % 7 < 5


def quantity(equity, price, stop_distance):
    """The Rust `quantity`: researched size floored to the step, then levered."""
    if equity <= 0 or price <= 0 or stop_distance <= 0:
        return None
    risk_sized = equity * RISK_FRACTION / stop_distance
    research_margin_sized = equity * RESEARCH_MAX_LEVERAGE / price
    baseline = math.floor(min(risk_sized, research_margin_sized) / STEP) * STEP
    leverage_cap = math.floor(equity * MAX_LEVERAGE / price / STEP) * STEP
    amount = min(baseline * POSITION_LEVERAGE, leverage_cap)
    return amount if math.isfinite(amount) and amount >= STEP else None


class Momentum:
    """Faithful replica of `BtcHourlyMomentum`, plus an optional RTH confinement."""

    def __init__(self, signal_hour, rth):
        self.signal_hour = signal_hour
        self.rth = rth
        self.hour = None                 # (bucket, high, low, close)
        self.closes = deque()
        self.true_ranges = deque()
        self.true_range_sum = 0.0
        self.previous_close = None
        self.ema = None
        self.pending = None              # (side, stop_distance)
        self.position = None

    def in_session(self, ts):
        """`rth=True` is weekday hours; `"hours_only"` keeps Saturday and Sunday."""
        minute = ts % 86_400 // 60
        if not OPEN_MINUTE <= minute < CLOSE_MINUTE:
            return False
        return self.rth == "hours_only" or is_weekday(ts)

    def finish_hour(self, hour):
        bucket, high, low, close = hour
        previous_momentum_close = (
            self.closes[len(self.closes) - MOMENTUM_HOURS]
            if len(self.closes) >= MOMENTUM_HOURS else None
        )
        if self.previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - self.previous_close),
                             abs(low - self.previous_close))
        self.true_ranges.append(true_range)
        self.true_range_sum += true_range
        if len(self.true_ranges) > ATR_HOURS:
            self.true_range_sum -= self.true_ranges.popleft()

        alpha = 2.0 / (EMA_HOURS + 1.0)
        self.ema = close if self.ema is None else self.ema + alpha * (close - self.ema)

        if (self.position is None and self.pending is None
                and bucket % 24 == self.signal_hour
                and len(self.true_ranges) == ATR_HOURS
                and previous_momentum_close is not None):
            atr = self.true_range_sum / ATR_HOURS
            movement = close - previous_momentum_close
            side = None
            if movement > MOMENTUM_ATR * atr and close > self.ema:
                side = 1
            elif movement < -MOMENTUM_ATR * atr and close < self.ema:
                side = -1
            if side is not None:
                self.pending = (side, STOP_ATR * atr)

        self.closes.append(close)
        if len(self.closes) > MOMENTUM_HOURS:
            self.closes.popleft()
        self.previous_close = close

    def update(self, bar, equity):
        """`(exit_price, reason)` and/or an entry, applied to this minute bar."""
        ts, open_, high, low, close = bar
        bucket = ts // 3_600
        if self.hour is None:
            self.hour = (bucket, high, low, close)
        elif self.hour[0] != bucket:
            self.finish_hour(self.hour)
            self.hour = (bucket, high, low, close)
        else:
            _, prev_high, prev_low, _ = self.hour
            self.hour = (bucket, max(prev_high, high), min(prev_low, low), close)

        minute = ts % 86_400 // 60
        exit_fill = None

        if self.position is not None:
            side = self.position["side"]
            stop = self.position["entry"] - side * self.position["stop_distance"]
            stop_hit = low <= stop if side == 1 else high >= stop
            if stop_hit:
                price = min(open_, stop) if side == 1 else max(open_, stop)
                exit_fill = (price, "stop")
            else:
                target = self.position["entry"] + side * self.position["target_distance"]
                target_hit = high >= target if side == 1 else low <= target
                if target_hit:
                    price = max(open_, target) if side == 1 else min(open_, target)
                    exit_fill = (price, "target")
                elif ts - self.position["entry_ts"] >= MAX_HOLD_SECONDS:
                    exit_fill = (open_, "time")
                elif self.rth and not self.in_session(ts):
                    # The confinement itself: flat at the 16:00 bar regardless
                    # of where the bracket stands.
                    exit_fill = (open_, "session_end")
            if exit_fill is not None:
                self.position = None

        entry = None
        if self.position is None and self.pending is not None:
            side, stop_distance = self.pending
            self.pending = None
            allowed = not self.rth or self.in_session(ts)
            amount = quantity(equity, open_, stop_distance) if allowed else None
            if amount is not None:
                self.position = {
                    "side": side, "entry": open_, "stop_distance": stop_distance,
                    "target_distance": REWARD_RISK * stop_distance, "entry_ts": ts,
                }
                entry = (side, open_, amount)
        return exit_fill, entry


def run(signal_hour, rth, years):
    strategy = Momentum(signal_hour, rth)
    equity = peak = INITIAL
    max_drawdown = 0.0
    annual = {}
    open_trade = None
    # Realised pnl attributed to the entry's day type. Compounding makes this
    # path-dependent, so it splits the realised dollars rather than proving what
    # either group would have earned on its own.
    split = {
        "weekday": {"pnl": 0.0, "trades": 0, "wins": 0},
        "weekend": {"pnl": 0.0, "trades": 0, "wins": 0},
    }

    for year in years:
        bars = minute_bars(year)
        if not bars:
            continue
        stats = annual.setdefault(
            year, {"start_equity": equity, "pnl": 0.0, "trades": 0, "wins": 0,
                   "gross_win": 0.0, "gross_loss": 0.0, "peak": equity,
                   "max_dd_pct": 0.0}
        )
        for bar in bars:
            exit_fill, entry = strategy.update(bar, equity)
            if exit_fill is not None and open_trade is not None:
                price, _reason = exit_fill
                points = open_trade["side"] * (price - open_trade["entry"]) - SPREAD
                pnl = points * open_trade["quantity"]
                equity += pnl
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - equity) / peak)
                bucket = split["weekend" if open_trade["weekend"] else "weekday"]
                bucket["pnl"] += pnl
                bucket["trades"] += 1
                if pnl > 0:
                    bucket["wins"] += 1
                stats["pnl"] += pnl
                stats["trades"] += 1
                if pnl > 0:
                    stats["wins"] += 1
                    stats["gross_win"] += pnl
                else:
                    stats["gross_loss"] -= pnl
                stats["peak"] = max(stats["peak"], equity)
                if stats["peak"] > 0:
                    stats["max_dd_pct"] = max(
                        stats["max_dd_pct"],
                        (stats["peak"] - equity) / stats["peak"] * 100.0,
                    )
                open_trade = None
            if entry is not None:
                side, price, amount = entry
                open_trade = {"side": side, "entry": price, "quantity": amount,
                              "weekend": not is_weekday(bar[0])}
        del bars

    out = {}
    for year, stats in annual.items():
        start = stats["start_equity"]
        out[str(year)] = {
            "return_pct": round(stats["pnl"] / start * 100.0, 2) if start > 0 else 0.0,
            "pnl": round(stats["pnl"], 2),
            "trades": stats["trades"],
            "win_pct": round(stats["wins"] / stats["trades"] * 100.0, 1)
            if stats["trades"] else 0.0,
            "pf": round(stats["gross_win"] / stats["gross_loss"], 3)
            if stats["gross_loss"] > 0 else None,
            "max_dd_pct": round(stats["max_dd_pct"], 2),
        }
    return {
        "signal_hour": signal_hour,
        "rth": rth,
        "final_equity": round(equity, 2),
        "total_return_pct": round((equity - INITIAL) / INITIAL * 100.0, 2),
        "max_dd_pct": round(max_drawdown * 100.0, 2),
        "trades": sum(stats["trades"] for stats in annual.values()),
        "day_type": {
            name: {
                "pnl": round(bucket["pnl"], 2),
                "trades": bucket["trades"],
                "win_pct": round(bucket["wins"] / bucket["trades"] * 100.0, 1)
                if bucket["trades"] else 0.0,
                "pnl_per_trade": round(bucket["pnl"] / bucket["trades"], 2)
                if bucket["trades"] else 0.0,
            }
            for name, bucket in split.items()
        },
        "annual": out,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=2017)
    parser.add_argument("--to-year", type=int,
                        default=datetime.now(timezone.utc).year)
    args = parser.parse_args()
    years = list(range(args.from_year, args.to_year + 1))

    results = {
        "baseline_h16_overnight": run(16, False, years),
        "live_h9_rth_all_days": run(9, "hours_only", years),
        "h9_rth_weekday_only": run(9, True, years),
    }

    with open(OUTPUT, "w") as handle:
        json.dump(results, handle, indent=2)

    header = f"{'variant':<26}{'total%':>9}{'maxDD%':>8}{'trades':>8}"
    print(header)
    print("-" * len(header))
    for name, result in results.items():
        print(f"{name:<26}{result['total_return_pct']:>9}"
              f"{result['max_dd_pct']:>8}{result['trades']:>8}")

    print()
    years_seen = sorted({year for result in results.values() for year in result["annual"]})
    print(f"{'year':<6}" + "".join(f"{name.replace('_confined_to_rth', '').replace('baseline_h16_overnight', 'base'):>10}"
                                   for name in results))
    for year in years_seen:
        row = f"{year:<6}"
        for result in results.values():
            entry = result["annual"].get(year)
            row += f"{entry['return_pct']:>10}" if entry else f"{'-':>10}"
        print(row)
    print(f"\nwritten to {OUTPUT}")


if __name__ == "__main__":
    main()
