"""Zarattini & Aziz (2023) 5-minute Opening Range Breakout, ported to BTCUSD.

Implements `ssrn-4416622` -- "Can Day Trading Really Be Profitable? Evidence of
Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading
Strategy vs. Benchmark in the US Stock Market" -- on BTCUSD with a $1,000 forex
account. The authors state they deliberately did not optimise anything, so
nothing is refitted here either: this measures what the QQQ rule is worth on an
asset they never looked at.

RULE (Section 2, Table 1). Take the session's first 5-minute candle. If it
closed up, go long at the open of the second 5-minute candle; if it closed down,
go short; if open == close it is a doji and the day is skipped. The stop is the
first candle's low for a long and its high for a short, so R = |entry - stop|.
The target is 10R. Whatever is unresolved by the session close is liquidated
there.

SIZING (Section 2). Shares = int(min(A * 0.01 / R, 4 * A / P)): risk 1% of the
account over the distance to the stop, capped at 4x leverage. Both legs are
kept -- the leverage cap is not an implementation detail, it is the paper's own
headline finding, since it is what stops most trades reaching full size.

SESSION. BTC has no open, so the opening range must be imposed. Both readings
run: `rth` uses 09:30-16:00 New York, matching every earlier BTC family in this
directory, and `full` uses the 00:00-24:00 calendar day, which anchors the range
at midnight. They are different strategies and are reported side by side.

CLOCK. `btc_1m` stores New York wall-clock despite the `Z` suffix. RTH is
therefore minutes 570-960.

CAUSALITY. The side is fixed by the first candle's close and filled at the open
of the next candle, which the paper specifies and which is one bar later than
the information used. The stop and target are both known before the entry bar
opens. When a single minute touches both, the stop is taken: OHLC cannot order
the two, and assuming the 10R target won first is how a backtest invents money.

COSTS. `idk` forex conventions: the 0.2 spread charged wholly at entry, point
value 1.0, quantity step 0.01 BTC. The paper's $0.0005/share commission is an
equities number and is not carried over. One trade per session means turnover is
low, so unlike the VWAP family this rule is not especially cost-sensitive --
`--cost-bps` and `--cost-sweep` measure that rather than assume it.

LOT GRANULARITY. One step is 0.01 BTC. At 4x on $1,000 with BTC at $60,000 the
target size is 0.067 BTC, so the step costs a few percent rather than the half
it costs a 1x account -- but `--balance` re-runs the identical rule on a larger
account, and `notional_use_pct` reports what was actually deployed, because a
result that only exists at $1,000 is an artefact of the step.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data


OUTPUT = os.path.join(os.path.dirname(__file__), "zarattini_orb_result.json")

INITIAL = 1_000.0
SPREAD = 0.2          # charged wholly at entry, as in execution.py
POINT_VALUE = 1.0     # Instrument::Forex
QUANTITY_STEP = 0.01  # QUANTITY_STEP (Forex)
MAX_LEVERAGE = 4.0    # the paper's broker cap; also idk's 25% margin
RISK_FRACTION = 0.01  # the paper's 1% of account per trade
TARGET_R = 10.0       # the paper's 10R profit target

#: When non-zero, overrides the fixed spread with a proportional cost in basis
#: points of notional, charged at entry. Set by `--cost-bps`.
COST_BPS = 0.0

#: New York minutes. RTH matches btc_orb / btc_rth_momentum / btc_families.
SESSIONS = {"rth": (570, 960), "full": (0, 1440)}

#: BTC trades every calendar day, so a year is 365 sessions, not 252.
SESSIONS_PER_YEAR = 365

#: The paper's opening range, in minutes.
RANGE_MINUTES = 5


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


def load_sessions(session="rth", symbol="btc"):
    """Minute bars folded into per-day sessions, in chronological order.

    Bars are kept in a gap-padded array indexed by minute offset, because the
    opening range is defined by clock position: offsets 0-4 are the first
    candle whether or not the feed printed all five.
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
        bucket = by_day.setdefault(ts // 86_400, [None] * span)
        bucket[minute - start_min] = (
            ts, float(row[1]), float(row[2]), float(row[3]),
            float(row[4]), float(row[5])
        )

    sessions = []
    for day in sorted(by_day):
        bars = by_day[day]
        present = [b for b in bars if b is not None]
        if len(present) < span // 2:
            continue
        sessions.append({
            "day": day, "ts": present[0][0], "bars": bars, "span": span,
            "open": present[0][1], "close": present[-1][4],
        })
    return sessions


def opening_range(bars, minutes=RANGE_MINUTES):
    """The first candle as (open, high, low, close), or None if it is missing.

    Built from the minute bars at offsets 0..minutes-1 rather than from a
    resampled feed, so a gap inside the range shrinks it instead of silently
    borrowing a later minute's price.
    """
    window = [b for b in bars[:minutes] if b is not None]
    if not window:
        return None
    return (window[0][1], max(b[2] for b in window),
            min(b[3] for b in window), window[-1][4])


# --------------------------------------------------------------------------- #
# backtest
# --------------------------------------------------------------------------- #


def _cost(price):
    """The entry's cost in price units."""
    return price * COST_BPS / 10_000.0 if COST_BPS else SPREAD


def _pnl(side, entry, exit_price, quantity):
    """Cost charged wholly at entry, matching execution.py's spread convention."""
    cost = _cost(entry)
    if side == "long":
        return (exit_price - (entry + cost)) * quantity * POINT_VALUE
    return ((entry - cost) - exit_price) * quantity * POINT_VALUE


def _quantity(equity, price, risk, sizing):
    """The paper's share formula, rounded down to a tradeable lot.

    `flat` is not the paper. It holds one step regardless of equity and risk, so
    the rule's raw per-trade edge can be read without the sizing, the leverage
    cap, or compounding in the way.
    """
    if sizing == "flat":
        return QUANTITY_STEP
    if price <= 0 or equity <= 0 or risk <= 0:
        return 0.0
    by_risk = equity * RISK_FRACTION / risk
    by_leverage = MAX_LEVERAGE * equity / price
    return math.floor(min(by_risk, by_leverage) / QUANTITY_STEP) * QUANTITY_STEP


def run(sessions, session_name, initial=None, sizing="equity",
        minutes=RANGE_MINUTES, target_r=TARGET_R):
    """The paper's rule over every session. Returns trades and daily equity."""
    equity = initial if initial is not None else INITIAL
    start_equity = equity
    curve = []
    trades = []
    exit_reasons = {}
    deployed = []       # notional / equity at entry

    for session in sessions:
        bars = session["bars"]
        opening = opening_range(bars, minutes)
        entry_bar = next((i for i in range(minutes, session["span"])
                          if bars[i] is not None), None)

        if opening is not None and entry_bar is not None:
            range_open, range_high, range_low, range_close = opening
            # A doji has no direction, so the paper skips the day outright.
            side = ("long" if range_close > range_open
                    else "short" if range_close < range_open else None)
            if side is not None:
                entry = bars[entry_bar][1]
                stop = range_low if side == "long" else range_high
                risk = (entry - stop) if side == "long" else (stop - entry)
                # A gap beyond the opening range past the stop leaves R <= 0:
                # the trade is already stopped before it starts.
                if risk > 0:
                    quantity = _quantity(equity, entry, risk, sizing)
                    if quantity >= QUANTITY_STEP:
                        target = (entry + target_r * risk if side == "long"
                                  else entry - target_r * risk)
                        equity += _trade(
                            trades, exit_reasons, deployed, bars, entry_bar,
                            side, entry, stop, target, quantity, risk, equity)

        curve.append((session["day"], session["ts"], equity))
        if equity <= 0 and sizing != "flat":
            break

    return {
        "trades": trades, "curve": curve, "exit_reasons": exit_reasons,
        "deployed": deployed, "initial": start_equity,
    }


def _trade(trades, exit_reasons, deployed, bars, entry_bar, side, entry, stop,
           target, quantity, risk, equity):
    """Walk the session from the entry bar and book the first exit. Returns PnL.

    The entry bar itself is live: the fill is its open, so the rest of that
    minute can legitimately reach either level.
    """
    exit_price, reason, exit_ts = None, None, None
    for index in range(entry_bar, len(bars)):
        bar = bars[index]
        if bar is None:
            continue
        _ts, bar_open, high, low, close, _volume = bar
        hit_stop = low <= stop if side == "long" else high >= stop
        hit_target = high >= target if side == "long" else low <= target
        # Stop first: one minute's OHLC cannot say which came first, and
        # awarding the 10R would manufacture the entire edge.
        if hit_stop:
            exit_price = min(bar_open, stop) if side == "long" else max(bar_open, stop)
            reason, exit_ts = "stop", bar[0]
            break
        if hit_target:
            exit_price = max(bar_open, target) if side == "long" else min(bar_open, target)
            reason, exit_ts = "target", bar[0]
            break
        exit_price, exit_ts, reason = close, bar[0], "session_close"

    if exit_price is None:
        return 0.0

    pnl = _pnl(side, entry, exit_price, quantity)
    points = (exit_price - entry) if side == "long" else (entry - exit_price)
    trades.append({
        "ts": bars[entry_bar][0], "exit_ts": exit_ts, "side": side, "pnl": pnl,
        "quantity": quantity, "reason": reason,
        "points": points, "entry_price": entry,
        # PnL in units of the risk taken, which is how the paper reports it:
        # -1 on a stop, +10 on the target, in between on an EoD liquidation.
        "r_multiple": points / risk,
    })
    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    deployed.append(quantity * entry / equity if equity > 0 else 0.0)
    return pnl


def buy_and_hold(sessions, initial=None):
    """Same account, bought at the first session open and never touched."""
    equity = initial if initial is not None else INITIAL
    quantity = math.floor(equity / sessions[0]["open"] / QUANTITY_STEP) * QUANTITY_STEP
    cash = equity - quantity * sessions[0]["open"]
    curve = [(s["day"], s["ts"], cash + quantity * s["close"]) for s in sessions]
    return {"trades": [], "curve": curve, "exit_reasons": {},
            "deployed": [], "initial": equity}


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def _gross_edge(trades):
    """Mean return per trade in bps of entry price, gross of cost, and its t-stat.

    Sizing, leverage and the spread all wash out of this, and bps rather than
    points is what makes 2018's $4,000 BTC comparable with 2025's $100,000 one.
    It is the only number that answers 'does the rule predict direction'.
    """
    if not trades:
        return {"gross_bps_per_trade": None, "gross_t_stat": None,
                "avg_r_multiple": None}
    edges = [10_000 * t["points"] / t["entry_price"] for t in trades]
    mean = statistics.fmean(edges)
    deviation = statistics.pstdev(edges) if len(edges) > 1 else 0.0
    t_stat = mean / deviation * math.sqrt(len(edges)) if deviation else 0.0
    return {
        "gross_bps_per_trade": round(mean, 3),
        "gross_t_stat": round(t_stat, 2),
        "avg_r_multiple": round(statistics.fmean(t["r_multiple"] for t in trades), 3),
    }


def summarise(result, label):
    equities = [row[2] for row in result["curve"]]
    trades = result["trades"]
    initial = result["initial"]
    if len(equities) < 2:
        return {"strategy": label, "trades": 0}

    returns = [current / previous - 1.0 if previous > 0 else 0.0
               for previous, current in zip(equities, equities[1:])]

    final = equities[-1]
    years = len(returns) / SESSIONS_PER_YEAR
    cagr = ((final / initial) ** (1 / years) - 1.0) if years > 0 and final > 0 else -1.0
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
        "total_return_pct": round(100 * (final / initial - 1.0), 1),
        "cagr_pct": round(100 * cagr, 1),
        "sharpe": round(sharpe, 2),
        "ann_vol_pct": round(100 * volatility * math.sqrt(SESSIONS_PER_YEAR), 1),
        "max_drawdown_pct": round(100 * drawdown, 1),
        "trades": len(trades),
        "long_pct": (round(100 * sum(1 for t in trades if t["side"] == "long")
                           / len(trades), 1) if trades else 0.0),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0.0,
        # How much of the account the 4x cap and the 0.01 BTC step let it deploy.
        "notional_use_pct": (round(100 * statistics.fmean(result["deployed"]), 1)
                             if result["deployed"] else None),
        **_gross_edge(trades),
        "exit_reasons": result["exit_reasons"],
    }


def yearly(result):
    by_year = {}
    for _day, ts, equity in result["curve"]:
        by_year.setdefault(datetime.fromtimestamp(ts, timezone.utc).year, []).append(equity)
    out = {}
    previous = result["initial"]
    for year in sorted(by_year):
        last = by_year[year][-1]
        out[year] = round(100 * (last / previous - 1.0), 1) if previous > 0 else None
        previous = last
    return out


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


HEADER = (f"{'variant':26}{'ret%':>11}{'CAGR%':>9}{'Sharpe':>8}{'MDD%':>8}"
          f"{'trades':>8}{'win%':>7}{'PF':>7}{'use%':>7}{'avg R':>8}"
          f"{'gross bps':>11}{'t':>8}")


def _print(summary):
    def cell(value):
        return value if value is not None else "-"

    # A flat-size run holds 0.01 BTC whatever the account is worth, so its
    # "equity" is a running sum of dollars, not a return series.
    flat = summary.get("sizing") == "flat"
    print(f"{summary['strategy']:26}"
          f"{cell(None if flat else summary['total_return_pct']):>11}"
          f"{cell(None if flat else summary['cagr_pct']):>9}"
          f"{cell(None if flat else summary['sharpe']):>8}"
          f"{cell(None if flat else summary['max_drawdown_pct']):>8}"
          f"{summary['trades']:>8}{summary['win_rate_pct']:>7}"
          f"{cell(summary['profit_factor']):>7}"
          f"{cell(summary['notional_use_pct']):>7}"
          f"{cell(summary['avg_r_multiple']):>8}"
          f"{cell(summary['gross_bps_per_trade']):>11}"
          f"{cell(summary['gross_t_stat']):>8}")


def cost_sweep(names, args, costs):
    """Total return at each cost, in bps of notional, plus each break-even."""
    global COST_BPS
    report = {"cost_bps": costs, "sessions": {}}
    for name in names:
        sessions = load_sessions(name, args.symbol)
        width = 26 + 11 * len(costs) + 13
        print(f"\n{'=' * width}")
        print(f"TOTAL RETURN % BY COST (bps of notional)  --  "
              f"{args.symbol.upper()}USD session={name}  {len(sessions)} sessions")
        print(f"{'=' * width}")
        print(f"{'variant':26}" + "".join(f"{c:>11g}" for c in costs)
              + f"{'break-even':>13}")
        print("-" * width)

        returns = []
        for cost in costs:
            COST_BPS = cost
            returns.append(summarise(
                run(sessions, name, initial=args.balance), "orb")["total_return_pct"])
        # Return is monotone in cost, so the first sign change is the crossing.
        breakeven = None
        for (low, low_ret), (high, high_ret) in zip(
                zip(costs, returns), zip(costs[1:], returns[1:])):
            if low_ret > 0 >= high_ret:
                breakeven = low + (high - low) * low_ret / (low_ret - high_ret)
                break
        if breakeven is None and returns[0] <= 0:
            breakeven = 0.0
        label = f"{breakeven:.2f}" if breakeven is not None else f">{costs[-1]:g}"
        print(f"{'5-minute ORB':26}" + "".join(f"{v:>11.1f}" for v in returns)
              + f"{label:>13}")
        report["sessions"][name] = {"returns_pct": returns,
                                    "breakeven_bps": breakeven}

    COST_BPS = 0.0
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", choices=[*SESSIONS, "both"], default="both")
    parser.add_argument("--symbol", default="btc")
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--balance", type=float, default=INITIAL)
    parser.add_argument("--range-minutes", type=int, default=RANGE_MINUTES,
                        help="the paper uses 5; other values are not the paper")
    parser.add_argument("--target-r", type=float, default=TARGET_R,
                        help="the paper uses 10; other values are not the paper")
    parser.add_argument("--cost-bps", type=float, default=0.0,
                        help="replace the fixed spread with this many bps of notional")
    parser.add_argument("--spread", type=float, default=None,
                        help="spread in points, charged wholly at entry")
    parser.add_argument("--cost-sweep", default=None,
                        help="comma-separated costs in bps; finds the break-even "
                             "and skips the per-variant tables")
    args = parser.parse_args()

    global COST_BPS, SPREAD
    COST_BPS = args.cost_bps
    if args.spread is not None:
        SPREAD = args.spread
    names = list(SESSIONS) if args.session == "both" else [args.session]

    if args.cost_sweep:
        cost_sweep(names, args, [float(v) for v in args.cost_sweep.split(",")])
        return

    report = {
        "paper": "Zarattini & Aziz (2023), Can Day Trading Really Be Profitable? "
                 "Opening Range Breakout (ssrn-4416622)",
        "symbol": f"{args.symbol}usd",
        "initial_balance": args.balance,
        "cost": SPREAD if not COST_BPS else f"{COST_BPS} bps of notional",
        "instrument": "forex (point value 1.0, step 0.01, 4x leverage cap)",
        "parameters": f"the paper's own: {args.range_minutes}-minute range, "
                      f"{args.target_r}R target, 1% risk -- a transfer test",
        "sessions": {},
    }

    for name in names:
        sessions = load_sessions(name, args.symbol)
        window = (
            datetime.fromtimestamp(sessions[0]["ts"], timezone.utc).date().isoformat(),
            datetime.fromtimestamp(sessions[-1]["ts"], timezone.utc).date().isoformat(),
        )
        print(f"\n{'=' * len(HEADER)}")
        print(f"{args.symbol.upper()}USD  session={name} "
              f"({SESSIONS[name][0]}-{SESSIONS[name][1]} NY minutes)  "
              f"{len(sessions)} sessions  {window[0]} .. {window[1]}  "
              f"${args.balance:,.0f}")
        print(f"{'=' * len(HEADER)}")
        print(HEADER)
        print("-" * len(HEADER))

        rows = []
        for sizing in ("equity", "flat"):
            result = run(sessions, name, initial=args.balance, sizing=sizing,
                         minutes=args.range_minutes, target_r=args.target_r)
            summary = summarise(result, f"ORB size={sizing}")
            summary["sizing"] = sizing
            summary["yearly_pct"] = yearly(result)
            rows.append(summary)
            _print(summary)

        hold = buy_and_hold(sessions, initial=args.balance)
        summary = summarise(hold, "Buy and hold")
        summary["yearly_pct"] = yearly(hold)
        rows.append(summary)
        _print(summary)

        report["sessions"][name] = {
            "window": window, "sessions": len(sessions), "results": rows,
        }

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
