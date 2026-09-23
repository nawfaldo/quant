"""Fixed 1-second diagnostics for the rejected A1/A2/A3/A5 hypotheses.

This is deliberately not a parameter optimizer.  It translates each strategy's
pre-declared minute configuration to the native one-second feature clock:

* z-score thresholds and point brackets are unchanged;
* minute-of-day causal normalization becomes second-of-day normalization;
* 5/10 minute time stops become 5/10 second horizons;
* entries use the next contiguous valid second's midprice;
* exits use the ordered one-second midprice path and retain the 0.2 round-trip
  spread from the common execution model.

The result is a diagnostic family trial, not a replacement for a tick-executable
backtest.  It answers whether the intended seconds-horizon feature has a visible
edge before investing in a full second-level execution engine.
"""
import csv
import math
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass

from sandbox import execution
from sandbox import metrics

QUERY = """
SELECT cast(timestamp as long) ts,source,midprice,spread,
       bid_add_volume,bid_cancel_volume,ask_add_volume,ask_cancel_volume,
       aggressive_buy_volume,aggressive_sell_volume,trade_delta,price_change,
       bid_depth_distance,ask_depth_distance,trade_count,
       executed_at_bid,executed_at_ask,book_valid
FROM nq_l2_features_1s
ORDER BY timestamp
"""

ENTRY_FROM = 585 * 60
ENTRY_TO = 930 * 60
SESSION_END = 945 * 60
OPEN = 570 * 60
CLOSE = 960 * 60
SPREAD = 0.2
NORM = 20


def rows():
    url = "http://127.0.0.1:9000/exp?query=" + urllib.parse.quote(QUERY)
    with urllib.request.urlopen(url, timeout=1800) as response:
        text = (line.decode("utf-8") for line in response)
        reader = csv.reader(text)
        next(reader)
        for row in reader:
            yield row


def number(text):
    return float(text) if text else 0.0


def zscore(value, history):
    if len(history) < NORM:
        return None
    mean = sum(history) / len(history)
    variance = sum((sample - mean) ** 2 for sample in history) / (len(history) - 1)
    return (value - mean) / math.sqrt(variance) if variance > 0.0 else None


@dataclass
class Position:
    side: str
    entry_ts: int
    entry: float
    stop: float
    target: float
    horizon: int


class Simulator:
    def __init__(self, name):
        self.name = name
        self.position = None
        self.pending = None
        self.fills = []

    def close(self, ts, price):
        position = self.position
        signed = (
            price - position.entry
            if position.side == execution.LONG
            else position.entry - price
        )
        self.fills.append(
            execution.Fill(
                position.entry_ts,
                ts,
                position.side,
                signed - SPREAD,
                position.entry,
                position.stop,
            )
        )
        self.position = None

    def advance(self, ts, mid, valid, previous):
        if previous is not None:
            previous_ts, previous_mid = previous
            gap = ts != previous_ts + 1
            crossed_close = previous_ts % 86_400 < SESSION_END <= ts % 86_400
            if self.position is not None and (gap or crossed_close):
                self.close(previous_ts, previous_mid)
            if gap:
                self.pending = None

        if self.position is not None and valid:
            position = self.position
            elapsed = ts - position.entry_ts
            if position.side == execution.LONG:
                if mid <= position.entry - position.stop:
                    self.close(ts, mid)
                elif mid >= position.entry + position.target:
                    self.close(ts, mid)
            else:
                if mid >= position.entry + position.stop:
                    self.close(ts, mid)
                elif mid <= position.entry - position.target:
                    self.close(ts, mid)
            if self.position is not None and elapsed >= position.horizon:
                self.close(ts, mid)

        if self.pending is not None:
            side, signal_ts, stop, target, horizon = self.pending
            if ts == signal_ts + 1 and valid:
                self.position = Position(side, ts, mid, stop, target, horizon)
            self.pending = None

    def signal(self, ts, side, stop, target, horizon):
        if self.position is None and self.pending is None:
            self.pending = (side, ts, stop, target, horizon)

    def finish(self, previous):
        if self.position is not None and previous is not None:
            self.close(*previous)


def report(simulators):
    for simulator in simulators:
        fills = simulator.fills
        ex = execution.Execution(spread=SPREAD, session_end_min=None)
        sized = execution.size(fills, ex)
        stat = metrics.stats(sized, initial=ex.initial)
        raw = sum(fill.points for fill in fills)
        raw_zero = raw + SPREAD * len(fills)
        print(
            f"{simulator.name}|trades={len(fills)}|raw={raw:.2f}|"
            f"raw_zero_spread={raw_zero:.2f}|edge={raw / len(fills) if fills else 0:.4f}|"
            f"pnl={stat['pnl']:.2f}|pf={stat['pf']:.3f}|"
            f"positive_months={stat['pos_months']}/{stat['n_months']}"
        )


def main():
    a1 = Simulator("A1 execution-adjusted LWI, 1s")
    a2 = Simulator("A2 book slope, 1s")
    a3 = Simulator("A3 Kyle lambda, 1s")
    a5 = Simulator("A5 air-pocket fade, 1s")
    simulators = (a1, a2, a3, a5)

    lwi_history = defaultdict(lambda: deque(maxlen=NORM))
    lambda_history = defaultdict(lambda: deque(maxlen=NORM))
    air_histories = tuple(
        defaultdict(lambda: deque(maxlen=NORM)) for _ in range(3)
    )
    flow = deque(maxlen=5)
    previous = None
    count = 0

    for row in rows():
        count += 1
        ts = int(row[0]) // 1_000_000
        source = row[1]
        mid = number(row[2])
        observed_spread = number(row[3])
        bid_add, bid_cancel = number(row[4]), number(row[5])
        ask_add, ask_cancel = number(row[6]), number(row[7])
        buy, sell, delta = number(row[8]), number(row[9]), number(row[10])
        move = number(row[11])
        bid_distance, ask_distance = number(row[12]), number(row[13])
        trade_count = number(row[14])
        executed_bid, executed_ask = number(row[15]), number(row[16])
        valid = row[17].lower() == "true" and mid > 0.0
        second = ts % 86_400
        contiguous = previous is not None and ts == previous[0] + 1

        for simulator in simulators:
            simulator.advance(ts, mid, valid, previous)

        if not contiguous:
            flow.clear()

        in_signal_window = (
            valid
            and ENTRY_FROM <= second <= ENTRY_TO
            and observed_spread <= 1.25
        )
        slot = (source, second)

        # A1: subtract same-second executions from gross depth decreases.
        adjusted_bid = max(0.0, bid_cancel - executed_bid)
        adjusted_ask = max(0.0, ask_cancel - executed_ask)
        bid_total = bid_add + adjusted_bid
        ask_total = ask_add + adjusted_ask
        lwi = (
            adjusted_ask / ask_total - adjusted_bid / bid_total
            if bid_total > 0.0 and ask_total > 0.0
            else None
        )
        if lwi is not None and OPEN <= second < CLOSE:
            history = lwi_history[slot]
            z = zscore(lwi, history)
            if in_signal_window and z is not None and abs(z) >= 2.0:
                a1.signal(
                    ts,
                    execution.LONG if z > 0.0 else execution.SHORT,
                    25.0,
                    37.5,
                    5,
                )
            history.append(lwi)

        # A2: same dimensionless last-snapshot asymmetry, seconds horizon.
        denominator = bid_distance + ask_distance
        if in_signal_window and bid_distance > 0.0 and ask_distance > 0.0:
            slope = (ask_distance - bid_distance) / denominator
            if abs(slope) >= 0.07:
                a2.signal(
                    ts,
                    execution.LONG if slope > 0.0 else execution.SHORT,
                    20.0,
                    30.0,
                    10,
                )

        # A3: same cheap-impact z and five-reading persistent-flow rule.
        aggressive = buy + sell
        lam = abs(move) / aggressive if aggressive > 0.0 else None
        if valid and aggressive > 0.0:
            flow.append((ts, source, buy, sell, delta))
        else:
            flow.clear()
        if lam is not None and OPEN <= second < CLOSE:
            transformed = math.log1p(lam)
            history = lambda_history[slot]
            z = zscore(transformed, history)
            persistent = None
            if len(flow) == 5 and flow[-1][0] - flow[0][0] == 4:
                total = sum(item[2] + item[3] for item in flow)
                signed = sum(item[4] for item in flow)
                active = [item[4] for item in flow if item[4] != 0.0]
                if total > 0.0 and signed != 0.0 and active:
                    sign = 1.0 if signed > 0.0 else -1.0
                    imbalance = abs(signed) / total
                    agreement = sum(sign * item > 0.0 for item in active) / len(active)
                    if imbalance >= 0.2 and agreement >= 0.6:
                        persistent = execution.LONG if sign > 0.0 else execution.SHORT
            if in_signal_window and z is not None and z <= -0.5 and persistent:
                a3.signal(ts, persistent, 20.0, 30.0, 10)
            history.append(transformed)

        # A5: same transformed effort/result test, translated to five seconds.
        if trade_count > 0.0 and aggressive > 0.0 and move != 0.0:
            transformed = (
                math.log1p(abs(move)),
                math.log1p(trade_count),
                math.log1p(aggressive),
            )
            histories = tuple(history[slot] for history in air_histories)
            zs = tuple(zscore(value, history) for value, history in zip(transformed, histories))
            if (
                in_signal_window
                and all(value is not None for value in zs)
                and zs[0] >= 1.0
                and zs[1] <= 0.0
                and zs[2] <= 0.0
            ):
                a5.signal(
                    ts,
                    execution.SHORT if move > 0.0 else execution.LONG,
                    10.0,
                    10.0,
                    5,
                )
            for value, history in zip(transformed, histories):
                history.append(value)

        previous = (ts, mid)
        if count % 1_000_000 == 0:
            print(f"processed {count:,} one-second rows", flush=True)

    for simulator in simulators:
        simulator.finish(previous)
    print(f"processed {count:,} one-second rows")
    report(simulators)


if __name__ == "__main__":
    main()
