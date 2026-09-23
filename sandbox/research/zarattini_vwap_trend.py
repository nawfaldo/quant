"""Zarattini & Aziz (2023) VWAP Trend Trading, ported to BTCUSD.

Implements `ssrn-4631351` -- "Volume Weighted Average Price (VWAP): The Holy
Grail for Day Trading Systems" -- on BTCUSD with a $1,000 forex account. The
paper's rule is deliberately tiny, so nothing here is fitted: the whole point is
what the QQQ result is worth on an asset the authors never looked at.

RULE (Section 3). Anchor an intraday VWAP at the session open. Let the first
bar of the session close, then take a position in the direction of price versus
VWAP: long if the bar closed above, short if below. Stay in that position until
a bar *closes* on the other side of VWAP -- an intrabar poke through the line is
explicitly not an exit -- at which point flip to the other side. The account is
therefore in the market from the second bar of the session to the last, always
long or always short, and flat overnight.

SIZING (Section 3.3). The stop is the moving VWAP, so risk per trade is unknown
at entry and a fixed-fractional rule cannot be formed. The paper uses 100% of
available equity with no leverage, which is what this module does.

SESSION. BTC has no open, so the paper's intraday frame must be imposed. Both
readings run: `rth` uses 09:30-16:00 New York, matching every earlier BTC family
in this directory, and `full` uses the 00:00-24:00 calendar day, the honest 24/7
reading. They anchor VWAP at different times and are different strategies; they
are reported side by side, never averaged.

CLOCK. `btc_1m` stores New York wall-clock despite the `Z` suffix. RTH is
therefore minutes 570-960.

CAUSALITY. The side is decided from the close of bar i and filled at the open of
bar i+1 (`--fill next_open`, the default). The paper's own text fills the exit at
the closing price of the bar that triggered it, which is the price used to make
the decision; `--fill close` reproduces that and is reported as a sensitivity,
not as the headline.

COSTS. `idk` forex conventions: the 0.2 spread is charged wholly at entry, point
value 1.0, quantity step 0.01 BTC. A flip is one exit and one entry, so it pays
the spread once. 0.2 points on a $50,000 asset is 0.004 bps, which is not a
crypto cost -- `--cost-bps` and `--spread-sweep` exist because the only honest
statement about a strategy that reverses this often is its break-even cost.

LOT GRANULARITY. One step is 0.01 BTC. On a $1,000 account at $60,000 that is a
$600 minimum ticket, so "100% of equity" is really 60% and the shortfall is not
risk control, it is an unfillable bet. `--balance` re-runs the identical rule on
a larger account; if the result only exists at $1,000 it is an artefact of the
step, and `notional_use_pct` in the summary says how much of the account was
actually deployed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data


OUTPUT = os.path.join(os.path.dirname(__file__), "zarattini_vwap_trend_result.json")

INITIAL = 1_000.0
SPREAD = 0.2          # charged wholly at entry, as in execution.py
POINT_VALUE = 1.0     # Instrument::Forex
QUANTITY_STEP = 0.01  # QUANTITY_STEP (Forex)

#: When non-zero, overrides the fixed spread with a proportional cost in basis
#: points of notional, charged at entry. Set by `--cost-bps`.
COST_BPS = 0.0

#: New York minutes. RTH matches btc_orb / btc_rth_momentum / btc_families.
SESSIONS = {"rth": (570, 960), "full": (0, 1440)}

#: BTC trades every calendar day, so a year is 365 sessions, not 252.
SESSIONS_PER_YEAR = 365


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


def load_sessions(session="rth", symbol="btc"):
    """Minute bars folded into per-day sessions, in chronological order.

    Unlike the noise-area families, this strategy has no cross-session profile
    to align, so sessions carry a plain list of present bars rather than a
    gap-padded offset array. A day with less than half its minutes is dropped:
    a VWAP anchored in a feed gap is not the paper's VWAP.
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
        by_day.setdefault(ts // 86_400, []).append(
            (ts, float(row[1]), float(row[2]), float(row[3]),
             float(row[4]), float(row[5]))
        )

    sessions = []
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < span // 2:
            continue
        sessions.append({
            "day": day, "ts": bars[0][0], "bars": bars,
            "open": bars[0][1], "close": bars[-1][4],
        })
    return sessions


def vwap_series(bars):
    """VWAP through bar i inclusive, by position.

    The paper's definition: cumulative sum of HLC/3 weighted by volume, anchored
    at the session open. Index i is the level a trader could read once bar i has
    printed, which is exactly when the paper's rule is evaluated.
    """
    out = [0.0] * len(bars)
    price_sum = volume_sum = 0.0
    for index, bar in enumerate(bars):
        typical = (bar[2] + bar[3] + bar[4]) / 3.0
        if bar[5] > 0:
            price_sum += typical * bar[5]
            volume_sum += bar[5]
        out[index] = price_sum / volume_sum if volume_sum > 0 else typical
    return out


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


def _close(trades, exit_reasons, position, price, ts, reason):
    """Book the close and return its PnL. `points` is gross of cost, so the
    rule's directional edge can be read without the sizing or the spread."""
    pnl = _pnl(position["side"], position["entry"], price, position["quantity"])
    sign = 1.0 if position["side"] == "long" else -1.0
    trades.append({
        "ts": position["entry_ts"], "exit_ts": ts, "side": position["side"],
        "pnl": pnl, "quantity": position["quantity"], "reason": reason,
        "points": sign * (price - position["entry"]),
        "entry_price": position["entry"],
    })
    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    return pnl


def _quantity(equity, price, leverage, sizing):
    """Section 3.3: the whole account, no leverage, rounded down to a lot.

    `flat` is not the paper. It holds one step regardless of equity so the run
    cannot compound itself to zero and stop trading, which is the only way to
    read the rule's raw per-trade edge over the whole sample.
    """
    if sizing == "flat":
        return QUANTITY_STEP
    if price <= 0 or equity <= 0:
        return 0.0
    return math.floor(equity * leverage / price / QUANTITY_STEP) * QUANTITY_STEP


def run(sessions, session_name, fill="next_open", initial=None, leverage=1.0,
        sizing="equity"):
    """The paper's rule over every session. Returns trades and daily equity.

    `fill` is where a decision taken at the close of bar i is executed:
    `next_open` at the open of bar i+1, `close` at that same closing price (the
    paper's literal text). A flip is booked as one closing trade and one opening
    trade at the same price, so the reversal pays exactly one spread.
    """
    equity = initial if initial is not None else INITIAL
    start_equity = equity
    curve = []
    trades = []
    exit_reasons = {}
    deployed = []       # notional / equity at each entry, for the lot-step check

    for session in sessions:
        bars = session["bars"]
        vwaps = vwap_series(bars)
        if len(bars) < 3:
            curve.append((session["day"], session["ts"], equity))
            continue

        position = None
        pending = "long" if bars[0][4] > vwaps[0] else "short"

        for index in range(1, len(bars)):
            bar = bars[index]
            last = index == len(bars) - 1

            # Act on the decision carried in from bar index-1.
            if pending is not None:
                price = bar[1] if fill == "next_open" else bars[index - 1][4]
                if position is not None:
                    equity += _close(trades, exit_reasons, position, price,
                                     bar[0], "vwap_flip")
                    position = None
                # The last bar is an exit only; opening there would be closed by
                # the session flatten in the same minute.
                if not last:
                    quantity = _quantity(equity, price, leverage, sizing)
                    if quantity >= QUANTITY_STEP:
                        position = {"side": pending, "entry": price,
                                    "quantity": quantity, "entry_ts": bar[0]}
                        deployed.append(quantity * price / equity if equity > 0 else 0.0)
                pending = None

            # Decide from this bar's close for the next bar. An intrabar poke
            # through VWAP is explicitly not an exit (Section 3.5).
            if position is not None and not last:
                above = bar[4] > vwaps[index]
                if (position["side"] == "long") != above:
                    pending = "long" if above else "short"

        if position is not None:
            equity += _close(trades, exit_reasons, position, bars[-1][4],
                             bars[-1][0], "session_close")

        curve.append((session["day"], session["ts"], equity))
        if equity <= 0 and sizing != "flat":
            break

    return {
        "trades": trades, "curve": curve, "exit_reasons": exit_reasons,
        "deployed": deployed, "initial": start_equity,
    }


def buy_and_hold(sessions, initial=None):
    """Same account, bought at the first session open and never touched.

    The paper's benchmark. Sized the same way, so it carries the same lot-step
    handicap and the comparison is like for like.
    """
    equity = initial if initial is not None else INITIAL
    quantity = _quantity(equity, sessions[0]["open"], 1.0, "equity")
    cash = equity - quantity * sessions[0]["open"]
    curve = [(s["day"], s["ts"], cash + quantity * s["close"]) for s in sessions]
    return {"trades": [], "curve": curve, "exit_reasons": {},
            "deployed": [], "initial": equity}


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def _gross_edge(trades):
    """Mean return per trade in bps of entry price, gross of cost, and its t-stat.

    Sizing and the spread both wash out of this, and bps rather than points is
    what makes 2018's $4,000 BTC comparable with 2025's $100,000 one. It is the
    only number in the summary that answers 'does the rule predict direction'.
    """
    if not trades or "entry_price" not in trades[0]:
        return {"gross_bps_per_trade": None, "gross_t_stat": None}
    edges = [10_000 * t["points"] / t["entry_price"] for t in trades]
    mean = statistics.fmean(edges)
    deviation = statistics.pstdev(edges) if len(edges) > 1 else 0.0
    t_stat = mean / deviation * math.sqrt(len(edges)) if deviation else 0.0
    return {"gross_bps_per_trade": round(mean, 3), "gross_t_stat": round(t_stat, 2)}


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
        "trades_per_session": round(len(trades) / len(equities), 2),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0.0,
        # Gross of cost and independent of sizing: the rule's directional edge.
        "gross_points_per_trade": (
            round(statistics.fmean(t["points"] for t in trades), 2) if trades else 0.0),
        **_gross_edge(trades),
        # How much of the account the 0.01 BTC step actually let it deploy.
        "notional_use_pct": (round(100 * statistics.fmean(result["deployed"]), 1)
                             if result["deployed"] else None),
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


HEADER = (f"{'variant':34}{'ret%':>11}{'CAGR%':>9}{'Sharpe':>8}{'MDD%':>8}"
          f"{'trades':>9}{'win%':>7}{'PF':>7}{'use%':>7}{'gross bps':>11}{'t':>8}")


def _print(summary):
    def cell(value):
        return value if value is not None else "-"

    # A flat-size run holds 0.01 BTC whatever the account is worth, so its
    # "equity" is a running sum of dollars, not a return series. Blanking those
    # columns keeps a -190% that means nothing out of the comparison.
    flat = summary.get("sizing") == "flat"
    print(f"{summary['strategy']:34}"
          f"{cell(None if flat else summary['total_return_pct']):>11}"
          f"{cell(None if flat else summary['cagr_pct']):>9}"
          f"{cell(None if flat else summary['sharpe']):>8}"
          f"{cell(None if flat else summary['max_drawdown_pct']):>8}"
          f"{summary['trades']:>9}"
          f"{summary['win_rate_pct']:>7}{cell(summary['profit_factor']):>7}"
          f"{cell(summary['notional_use_pct']):>7}"
          f"{cell(summary['gross_bps_per_trade']):>11}"
          f"{cell(summary['gross_t_stat']):>8}")


def spread_sweep(names, args, costs):
    """Total return at each cost, in bps of notional, plus each break-even.

    A fixed 0.2-point spread says nothing about a strategy that flips this often
    on a five-figure asset. The break-even cost is the number that transfers
    between venues.
    """
    global COST_BPS
    report = {"cost_bps": costs, "sessions": {}}
    for name in names:
        sessions = load_sessions(name, args.symbol)
        width = 34 + 11 * len(costs) + 13
        print(f"\n{'=' * width}")
        print(f"TOTAL RETURN % BY ROUND-TRIP COST (bps of notional)  --  "
              f"{args.symbol.upper()}USD session={name}  {len(sessions)} sessions")
        print(f"{'=' * width}")
        print(f"{'variant':34}" + "".join(f"{c:>11g}" for c in costs)
              + f"{'break-even':>13}")
        print("-" * width)

        rows = []
        for fill in ("next_open", "close"):
            returns = []
            for cost in costs:
                COST_BPS = cost
                returns.append(summarise(
                    run(sessions, name, fill=fill, initial=args.balance),
                    fill)["total_return_pct"])
            # Return is monotone in cost, so the first sign change is the only
            # crossing; interpolate it linearly.
            breakeven = None
            for (low, low_ret), (high, high_ret) in zip(
                    zip(costs, returns), zip(costs[1:], returns[1:])):
                if low_ret > 0 >= high_ret:
                    breakeven = low + (high - low) * low_ret / (low_ret - high_ret)
                    break
            if breakeven is None and returns[0] <= 0:
                breakeven = 0.0
            label = f"{breakeven:.2f}" if breakeven is not None else f">{costs[-1]:g}"
            print(f"{'fill=' + fill:34}"
                  + "".join(f"{value:>11.1f}" for value in returns)
                  + f"{label:>13}")
            rows.append({"fill": fill, "returns_pct": returns,
                         "breakeven_bps": breakeven})
        report["sessions"][name] = rows

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
    parser.add_argument("--leverage", type=float, default=1.0,
                        help="the paper uses 1.0 (Section 3.3); >1 is not the paper")
    parser.add_argument("--cost-bps", type=float, default=0.0,
                        help="replace the fixed spread with this many bps of notional")
    parser.add_argument("--spread", type=float, default=None,
                        help="spread in points, charged wholly at entry")
    parser.add_argument("--cost-sweep", default=None,
                        help="comma-separated costs in bps; finds each variant's "
                             "break-even and skips the per-variant tables")
    args = parser.parse_args()

    global COST_BPS, SPREAD
    COST_BPS = args.cost_bps
    if args.spread is not None:
        SPREAD = args.spread
    names = list(SESSIONS) if args.session == "both" else [args.session]

    if args.cost_sweep:
        spread_sweep(names, args, [float(v) for v in args.cost_sweep.split(",")])
        return

    report = {
        "paper": "Zarattini & Aziz (2023), VWAP: The Holy Grail for Day Trading "
                 "Systems (ssrn-4631351)",
        "symbol": f"{args.symbol}usd",
        "initial_balance": args.balance,
        "leverage": args.leverage,
        "cost": SPREAD if not COST_BPS else f"{COST_BPS} bps of notional",
        "instrument": "forex (point value 1.0, step 0.01)",
        "parameters": "none -- the paper's rule has no free parameter",
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
        for fill in ("next_open", "close"):
            for sizing in ("equity", "flat"):
                result = run(sessions, name, fill=fill, initial=args.balance,
                             leverage=args.leverage, sizing=sizing)
                summary = summarise(result, f"fill={fill} size={sizing}")
                summary["fill"], summary["sizing"] = fill, sizing
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
