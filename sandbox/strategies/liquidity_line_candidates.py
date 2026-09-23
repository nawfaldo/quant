"""Candidates C and D on causal one-second top-10 liquidity-line rows."""
from __future__ import annotations

from dataclasses import dataclass, replace

from sandbox import execution
from sandbox.liquidity_lines import TICK_SIZE, level_size

OPEN_MIN, LAST_ENTRY, CLOSE_MIN = 570, 900, 960

# line tuple: price,size,share,distance_ticks,age,adds,cancels,executions
PRICE, SIZE, SHARE, DISTANCE, AGE, ADDS, CANCELS, EXECUTIONS = range(8)


@dataclass(frozen=True)
class LineSignal:
    decision_ts: int
    entry_index: int
    side: str
    stop: float
    target: float
    max_seconds: int
    line_price: float
    line_size: float
    exit_on_pull: bool = False


class LiquidityLineReversal:
    name = "Large Liquidity Line Reversal"
    defaults = {
        "percentile": 97.5,
        "min_age": 30,
        "rejection_ticks": 1,
        "replenishment_ratio": 0.25,
        "stop_buffer": 1.0,
        "max_stop": 12.0,
        "rr": 1.5,
        "time_stop": 45 * 60,
    }
    grid = {
        "percentile": [95.0, 97.5, 99.0],
        "min_age": [15, 30, 60],
        "rejection_ticks": [1, 2],
    }

    def all_params(self, overrides=None):
        out = dict(self.defaults)
        out.update(overrides or {})
        return out

    def signals(self, rows, params=None, mode="candidate"):
        p = self.all_params(params)
        out = []
        for index, row in enumerate(rows[:-1]):
            nxt = rows[index + 1]
            if not row.valid or not nxt.valid or nxt.ts // 86_400 != row.ts // 86_400:
                continue
            entry_minute = (nxt.ts % 86_400) // 60
            if not OPEN_MIN <= entry_minute <= LAST_ENTRY:
                continue

            candidates = []
            for side, line, rank in (
                (execution.LONG, row.bid, row.bid_pct),
                (execution.SHORT, row.ask, row.ask_pct),
            ):
                large = rank >= p["percentile"]
                if mode == "non_large":
                    large = 0 < rank < p["percentile"]
                if not large or line[AGE] < p["min_age"] or line[SIZE] <= 0:
                    continue
                if side == execution.LONG:
                    touched = line[EXECUTIONS] > 0 and row.delta < 0
                    rejected = row.trade_close >= line[PRICE] + p["rejection_ticks"] * TICK_SIZE
                    stop = nxt.mid - (line[PRICE] - p["stop_buffer"])
                else:
                    touched = line[EXECUTIONS] > 0 and row.delta > 0
                    rejected = row.trade_close <= line[PRICE] - p["rejection_ticks"] * TICK_SIZE
                    stop = (line[PRICE] + p["stop_buffer"]) - nxt.mid
                replenished = line[ADDS] >= p["replenishment_ratio"] * line[EXECUTIONS]
                if not touched or not rejected or row.trade_close <= 0:
                    continue
                if mode != "wall_touch" and not replenished:
                    continue
                if not 0 < stop <= p["max_stop"]:
                    continue
                candidates.append(LineSignal(
                    row.ts, index + 1, side, stop, stop * p["rr"],
                    p["time_stop"], line[PRICE], line[SIZE], False,
                ))
            if len(candidates) == 1:
                signal = candidates[0]
                if mode == "side_shuffled":
                    signal = replace(
                        signal,
                        side=(execution.SHORT if signal.side == execution.LONG
                              else execution.LONG),
                    )
                out.append(signal)
        return out


class LiquidityLineMagnet:
    name = "Large Liquidity Line Magnet Chase"
    defaults = {
        "percentile": 97.5,
        "min_age": 30,
        "min_imbalance": 0.20,
        "time_stop": 10 * 60,
        "pull_fraction": 0.50,
        "spread": 0.20,
    }
    grid = {
        "percentile": [95.0, 97.5, 99.0],
        "min_age": [15, 30],
        "min_imbalance": [0.10, 0.20, 0.30],
    }

    def all_params(self, overrides=None):
        out = dict(self.defaults)
        out.update(overrides or {})
        return out

    @staticmethod
    def _random_level(levels, line_price, ts):
        alternatives = [(price, size) for price, size in levels if price != line_price]
        return alternatives[ts % len(alternatives)] if alternatives else (0.0, 0.0)

    def signals(self, rows, params=None, mode="candidate"):
        p = self.all_params(params)
        out = []
        for index, row in enumerate(rows[:-1]):
            nxt = rows[index + 1]
            if not row.valid or not nxt.valid or nxt.ts // 86_400 != row.ts // 86_400:
                continue
            entry_minute = (nxt.ts % 86_400) // 60
            if not OPEN_MIN <= entry_minute <= LAST_ENTRY:
                continue
            candidates = []
            for side, line, rank, levels, next_levels in (
                (execution.LONG, row.ask, row.ask_pct, row.asks, nxt.asks),
                (execution.SHORT, row.bid, row.bid_pct, row.bids, nxt.bids),
            ):
                if rank < p["percentile"] or line[AGE] < p["min_age"] or line[SIZE] <= 0:
                    continue
                if side == execution.LONG:
                    ahead = line[PRICE] > row.mid and (row.trade_high <= 0 or row.trade_high < line[PRICE])
                    confirmed = row.delta > 0 and row.top5 >= p["min_imbalance"] and row.micro > row.mid
                    target_price = line[PRICE] - TICK_SIZE
                    distance = target_price - nxt.mid
                else:
                    ahead = line[PRICE] < row.mid and (row.trade_low <= 0 or row.trade_low > line[PRICE])
                    confirmed = row.delta < 0 and row.top5 <= -p["min_imbalance"] and row.micro < row.mid
                    target_price = line[PRICE] + TICK_SIZE
                    distance = nxt.mid - target_price
                if not ahead or (mode != "no_confirmation" and not confirmed):
                    continue

                present = level_size(next_levels, line[PRICE]) > 0
                if mode == "pulled_before_entry" and present:
                    continue
                if mode != "pulled_before_entry" and not present:
                    continue
                line_price, line_size = line[PRICE], line[SIZE]
                if mode == "random_line":
                    line_price, line_size = self._random_level(levels, line[PRICE], row.ts)
                    target_price = line_price + (-TICK_SIZE if side == execution.LONG else TICK_SIZE)
                    distance = ((target_price - nxt.mid) if side == execution.LONG
                                else (nxt.mid - target_price))
                    if level_size(next_levels, line_price) <= 0:
                        continue
                if distance <= p["spread"] + TICK_SIZE:
                    continue
                candidates.append(LineSignal(
                    row.ts, index + 1, side, distance, distance,
                    p["time_stop"], line_price, line_size, True,
                ))
            if len(candidates) == 1:
                out.append(candidates[0])
        return out


def resolve_day(rows, signals, spread=0.20, pull_fraction=0.50):
    """Conservative second-path resolution with one concurrent position."""
    fills = []
    free_at = -1
    for signal in signals:
        entry_row = rows[signal.entry_index]
        if entry_row.ts < free_at:
            continue
        entry = entry_row.mid
        exit_ts = entry_row.ts
        exit_price = entry
        for row in rows[signal.entry_index + 1:]:
            if row.ts // 86_400 != entry_row.ts // 86_400:
                break
            # Fallback is the latest executable observation, so an early-close
            # session or a sparse tail still flattens rather than inventing a
            # zero-move exit at the entry timestamp.
            exit_ts, exit_price = row.ts, row.mid
            high, low = row.trade_high, row.trade_low
            if signal.side == execution.LONG:
                stop_price, target_price = entry - signal.stop, entry + signal.target
                stop_hit = low > 0 and low <= stop_price
                target_hit = high > 0 and high >= target_price
                if stop_hit:
                    exit_price = min(row.trade_open or stop_price, stop_price)
                elif target_hit:
                    exit_price = max(row.trade_open or target_price, target_price)
                levels = row.asks
            else:
                stop_price, target_price = entry + signal.stop, entry - signal.target
                stop_hit = high > 0 and high >= stop_price
                target_hit = low > 0 and low <= target_price
                if stop_hit:
                    exit_price = max(row.trade_open or stop_price, stop_price)
                elif target_hit:
                    exit_price = min(row.trade_open or target_price, target_price)
                levels = row.bids
            if stop_hit or target_hit:
                exit_ts = row.ts
                break
            if (signal.exit_on_pull
                    and level_size(levels, signal.line_price) < signal.line_size * pull_fraction):
                exit_ts, exit_price = row.ts, row.mid
                break
            if row.ts >= entry_row.ts + signal.max_seconds:
                exit_ts, exit_price = row.ts, row.mid
                break
            if (row.ts % 86_400) // 60 >= CLOSE_MIN - 1:
                exit_ts, exit_price = row.ts, row.mid
                break
        sign = 1.0 if signal.side == execution.LONG else -1.0
        fills.append(execution.Fill(
            entry_row.ts, exit_ts, signal.side,
            sign * (exit_price - entry) - spread,
            entry, signal.stop,
        ))
        free_at = exit_ts
    return fills
