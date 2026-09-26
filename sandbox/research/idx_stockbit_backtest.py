"""One IDX stock, one simple strategy, traded the way a Stockbit account trades it.

LAYOUT, AS IN `exness_combined_strategies`. A strategy is a signal function in
`STRATEGIES`; a sleeve is `"<symbol>:<strategy>"`; `BOOK` names the sleeve(s)
this module runs and `PARAMS` holds each sleeve's settings. Parameters live
here, not on the command line, so a result is always reproducible from the file.

A signal function is `signal(index, bars, ctx, params, in_position)` and
returns `1` (buy), `-1` (sell) or `None`. It is read on bar i's CLOSE and filled
at bar i+1's OPEN ([[entry-must-be-next-bar-open]]). Long only, so `-1` only
ever closes a position.

WHAT MAKES IT REALISTIC FOR STOCKBIT / IDX.

* Fees: 0.15% on buys, 0.25% on sells (the sell side includes the 0.1% final
  sales tax). Both are parameters -- check them against your own Stockbit fee
  schedule.
* Long only. Retail IDX accounts cannot short.
* Whole lots only (1 lot = 100 shares), and a buy must fit in cash including
  its fee.
* IDX tick sizes (fraksi harga). A market order fills one tick worse than the
  bar's open: the buy at open + 1 tick, the sell at open - 1 tick.
* Auto-rejection (ARA/ARB), with IDX's actual rule history (see
  `AUTO_REJECT_BELOW`). Limits are set off the previous session's close and
  rounded inward to a valid tick. A buy whose fill bar OPENS at the upper limit
  (ARA) does not fill -- the offer side is empty and you join the bid queue --
  and a sell whose fill bar opens at the lower limit (ARB) does not either. Both
  stay pending and try again at the next bar's open. A one-tick slippage never
  pushes a fill through a limit.
* Cash dividends: the stored prices are unadjusted, so the ex-date price drop
  is real. A position held into an ex-date is credited the dividend, after tax:
  10% final tax before 2020-11-02, 0% from then on (UU Cipta Kerja / PP 9/2021
  exempts an individual's dividends that are reinvested in Indonesia within
  three years, which a trading account that keeps its cash in IDX stocks does).
  Dividends are read from
  `idx_<symbol>_dividends`, which `tools/tradingview_idx.py` fills.
* Guards: TradingView back-adjusts splits and rights issues, which puts prices
  off the tick grid (AKRA, ISAT, MDKA, MEDC, UNVR, MAPI, ESSA). Such a stock is
  refused, as is any bar-to-bar move larger than an auto-reject band allows.
  TLKM passes: every price since 2018 is on a valid tick and inside its limits.

NOT MODELLED: the payment date (cash is credited on the ex-date, 3-4 weeks
early), T+2 settlement (Stockbit lets sale proceeds buy again at once),
a fill later INSIDE a bar that opened locked, and slippage beyond one tick. On an
illiquid stock one tick is optimistic.

    py -B -m sandbox.research.idx_stockbit_backtest run
    py -B -m sandbox.research.idx_stockbit_backtest run tlkm:donchian
"""

from __future__ import annotations

import argparse
import math
from datetime import date, datetime, timezone

from sandbox import parquet_store as store

LOT = 100
BUY_FEE = 0.0015
SELL_FEE = 0.0025          # includes the 0.1% final sales tax

#: 10% final tax on dividends until the omnibus-law exemption for reinvested
#: dividends (received from 2020-11-02). Keyed on the ex-date as an approximation.
DIVIDEND_TAX = 0.10
DIVIDEND_TAX_EXEMPT_FROM = date(2020, 11, 2)

#: Upper auto-rejection (ARA) by reference price, unchanged since 2016:
#: (upper bound of the price band, inclusive; limit as a fraction).
AUTO_REJECT_BANDS = ((200, 0.35), (5000, 0.25), (math.inf, 0.20))

#: Lower auto-rejection (ARB) history, (effective date, fraction). None means
#: symmetric: the same band-based limit as ARA.
AUTO_REJECT_BELOW = (
    (date(2016, 1, 1), None),
    (date(2020, 3, 10), 0.10),   # COVID: first cut
    (date(2020, 3, 13), 0.07),   # COVID: asymmetric 7%
    (date(2023, 6, 5), 0.15),    # normalisation phase I
    (date(2023, 9, 4), None),    # phase II: symmetric again
    (date(2025, 4, 8), 0.15),    # April 2025 sell-off; still in force
)
MIN_PRICE = 50                   # regular-board floor


def tick_size(price):
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def price_limits(day, reference):
    """(ARA, ARB) prices for `day`, off the previous session's close."""
    band = next(limit for top, limit in AUTO_REJECT_BANDS if reference <= top)
    below = band
    for effective, rule in AUTO_REJECT_BELOW:
        if day >= effective:
            below = band if rule is None else rule
    upper = reference * (1 + band)
    upper -= upper % tick_size(upper)          # round down to a tick
    lower = reference * (1 - below)
    step = tick_size(lower)
    lower = math.ceil(lower / step) * step     # round up to a tick
    return upper, max(lower, MIN_PRICE)


def sell_locked(open_price, lower):
    """A sell cannot fill when the open sits at a real ARB limit.

    The Rp50 floor is not one: a stock parked at the floor (BUMI spent years
    at Rp50) still trades there, so a sell at the floor fills. Treating it as
    a lock froze every BUMI position of 2016-2019 in place.
    """
    return open_price <= lower and lower > MIN_PRICE


def dividend_tax(ex_date):
    return 0.0 if ex_date >= DIVIDEND_TAX_EXEMPT_FROM else DIVIDEND_TAX

SPLIT_GUARD = 0.40  # no IDX auto-reject band allows a 40% move between bars


# ---- strategies -------------------------------------------------------------

def donchian_signal(index, bars, ctx, params, in_position):
    """Buy a close above the prior `channel`-bar high; sell a close below the
    prior `exit_channel`-bar low. On 4h there are two bars a day, so 40/20 is a
    20-day breakout with a 10-day exit."""
    close = bars[index][C]
    if not in_position:
        upper = ctx["high"][params["channel"]][index]
        return 1 if upper is not None and close > upper else None
    lower = ctx["low"][params["exit_channel"]][index]
    return -1 if lower is not None and close < lower else None


def donchian_context(bars, params):
    return {
        "high": {params["channel"]: prior_extreme(bars, H, params["channel"], max)},
        "low": {params["exit_channel"]: prior_extreme(bars, L, params["exit_channel"], min)},
    }


class Strategy:
    __slots__ = ("signal", "context")

    def __init__(self, signal, context):
        self.signal = signal
        self.context = context


STRATEGIES = {
    "donchian": Strategy(donchian_signal, donchian_context),
}

#: The sleeves this module runs. One symbol, one strategy.
BOOK = ("tlkm:donchian",)

#: Each sleeve's settings. `timeframe` picks the `idx_<symbol>_<timeframe>` table.
PARAMS = {
    "tlkm:donchian": {"timeframe": "4h", "channel": 40, "exit_channel": 20},
}

#: Account and Stockbit costs, shared by every sleeve.
START = date(2018, 1, 1)
CAPITAL = 10_000_000  # rupiah

TS, O, H, L, C, V = range(6)


def prior_extreme(bars, field, length, pick):
    """`pick` of `field` over the `length` bars BEFORE each bar, else None."""
    return [pick(bar[field] for bar in bars[i - length:i]) if i >= length else None
            for i in range(len(bars))]


def load_bars(symbol: str, timeframe: str, start: date, trim: bool = False) -> list[tuple]:
    """(datetime, open, high, low, close, volume), Jakarta wall-clock.

    `trim=True` keeps only the clean tail instead of refusing the stock:
    TradingView back-adjusts every bar BEFORE a split or rights issue, so the
    bars after the last off-tick price (and after the last split-sized jump)
    are real prices. Importing 4h back to 2016 put eight more stocks in that
    state whose history from 2018 on is clean.
    """
    rows = store.read_bars(f"idx_{symbol.lower()}_{timeframe}", bar_minutes=1)
    bars = [
        (datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None), o, h, l, c, v)
        for ts, o, h, l, c, v in rows
    ]
    bars = [bar for bar in bars if bar[0].date() >= start]
    if trim:
        off = max((i for i, bar in enumerate(bars)
                   if any(x % tick_size(x) for x in bar[1:5])), default=-1)
        jump = max((i for i in range(1, len(bars))
                    if abs(bars[i][1] / bars[i - 1][4] - 1) > SPLIT_GUARD), default=0)
        bars = bars[max(off + 1, jump):]
    # TradingView back-adjusts for splits and rights issues. Adjusted prices land
    # off the IDX tick grid, and on them ticks, lots and auto-reject limits are
    # all wrong -- so a stock whose history was adjusted is refused, not traded.
    off_tick = sum(1 for bar in bars for x in bar[1:5] if x % tick_size(x))
    if off_tick > 0.01 * 4 * len(bars):
        raise RuntimeError(
            f"{symbol}: {off_tick:,} prices are off the IDX tick grid, so the history "
            "is split/rights-adjusted and cannot be traded realistically"
        )
    for prev, bar in zip(bars, bars[1:]):
        if abs(bar[1] / prev[4] - 1) > SPLIT_GUARD:
            raise RuntimeError(
                f"{symbol}: {prev[4]} -> {bar[1]} at {bar[0]} looks like a split; "
                "unadjusted data cannot be traded across it"
            )
    return bars


def load_dividends(symbol: str) -> dict[date, float]:
    """Ex-date -> gross cash dividend per share, from `idx_<symbol>_dividends`."""
    table = f"idx_{symbol.lower()}_dividends"
    if not store.has_table(table):
        return {}
    stamps, chunk = store.scan(table, columns=["amount"])
    return {
        datetime.fromtimestamp(ns // 1_000_000_000, tz=timezone.utc).date(): amount
        for ns, amount in zip(stamps.tolist(), chunk["amount"].to_pylist())
    }


def backtest(strategy, bars, dividends, params, capital=CAPITAL, buy_fee=BUY_FEE, sell_fee=SELL_FEE):
    ctx = strategy.context(bars, params)
    cash, shares = float(capital), 0
    trades, equity_curve = [], []
    open_trade = None
    pending = None  # "buy" or "sell", placed on a bar's close
    fees_paid = dividends_paid = 0.0
    last_day = None
    reference = previous_close = None  # previous session's close sets the limits
    limit_waits = 0

    for i, (moment, o, h, l, c, _v) in enumerate(bars):
        day = moment.date()
        # A new session: credit every ex-date between the last session and this one.
        if shares and last_day is not None and day != last_day:
            for ex_date, amount in dividends.items():
                if last_day < ex_date <= day:
                    net = amount * shares * (1 - dividend_tax(ex_date))
                    cash += net
                    dividends_paid += net
                    open_trade["dividends"] += net
        if day != last_day:
            reference = previous_close
        last_day = day
        upper, lower = price_limits(day, reference) if reference else (math.inf, 0.0)

        # Fill the order placed on the previous bar's close, at this bar's open.
        if pending == "buy" and o >= upper:
            limit_waits += 1  # opened at ARA: no offers, wait for the next bar
        elif pending == "buy":
            price = min(o + tick_size(o), upper)
            lots = int(cash // (price * LOT * (1 + buy_fee)))
            if lots > 0:
                shares = lots * LOT
                fee = price * shares * buy_fee
                cash -= price * shares + fee
                fees_paid += fee
                open_trade = {"entry_time": moment, "entry": price, "shares": shares,
                              "cost": price * shares + fee, "dividends": 0.0}
            pending = None
        elif pending == "sell" and sell_locked(o, lower):
            limit_waits += 1  # opened at ARB: no bids, wait for the next bar
        elif pending == "sell":
            price = max(o - tick_size(o), lower)
            fee = price * shares * sell_fee
            proceeds = price * shares - fee
            cash += proceeds
            fees_paid += fee
            open_trade.update(exit_time=moment, exit=price,
                              pnl=proceeds + open_trade["dividends"] - open_trade["cost"])
            open_trade["return_pct"] = 100 * open_trade["pnl"] / open_trade["cost"]
            trades.append(open_trade)
            shares, open_trade, pending = 0, None, None

        # Decide on this bar's close.
        if pending is None:
            signal = strategy.signal(i, bars, ctx, params, shares > 0)
            if signal == 1 and not shares:
                pending = "buy"
            elif signal == -1 and shares:
                pending = "sell"

        equity_curve.append((moment, cash + shares * c))
        previous_close = c

    return trades, equity_curve, fees_paid, dividends_paid, shares, limit_waits


def summarise(trades, curve, capital, fees, dividends, still_open):
    peak, max_dd = capital, 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    final = curve[-1][1]
    years = (curve[-1][0] - curve[0][0]).days / 365.25
    wins = [t for t in trades if t["pnl"] > 0]

    yearly, prev = {}, capital
    for moment, equity in curve:
        yearly[moment.year] = equity
    by_year = []
    for year, equity in yearly.items():
        by_year.append((year, 100 * (equity / prev - 1)))
        prev = equity

    return {
        "period": f"{curve[0][0]:%Y-%m-%d} to {curve[-1][0]:%Y-%m-%d}",
        "final_equity": final,
        "total_return_pct": 100 * (final / capital - 1),
        "cagr_pct": 100 * ((final / capital) ** (1 / years) - 1) if years > 0 else math.nan,
        "max_drawdown_pct": 100 * max_dd,
        "trades": len(trades),
        "win_rate_pct": 100 * len(wins) / len(trades) if trades else math.nan,
        "avg_trade_pct": sum(t["return_pct"] for t in trades) / len(trades) if trades else math.nan,
        "fees_paid": fees,
        "dividends_received": dividends,
        "position_open_at_end": bool(still_open),
        "by_year_pct": by_year,
    }


def run(sleeve: str) -> None:
    symbol, name = sleeve.split(":")
    params = PARAMS[sleeve]
    bars = load_bars(symbol, params["timeframe"], START)
    dividends = load_dividends(symbol)
    trades, curve, fees, divs, still_open, waits = backtest(STRATEGIES[name], bars, dividends, params)
    stats = summarise(trades, curve, CAPITAL, fees, divs, still_open)
    stats["fills_delayed_by_limit"] = waits

    print(f"{sleeve} {params}  {stats['period']}  capital Rp {CAPITAL:,.0f}")
    for key, value in stats.items():
        if key in ("period", "by_year_pct"):
            continue
        print(f"  {key:22} {value:,.2f}" if isinstance(value, float) else f"  {key:22} {value}")
    print("  by year: " + "  ".join(f"{y} {r:+.1f}%" for y, r in stats["by_year_pct"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="IDX backtest with Stockbit costs")
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="run BOOK, or one named sleeve")
    runner.add_argument("sleeve", nargs="?", choices=sorted(PARAMS))
    args = parser.parse_args()
    for sleeve in [args.sleeve] if args.sleeve else BOOK:
        run(sleeve)


if __name__ == "__main__":
    main()
