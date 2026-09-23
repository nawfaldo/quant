"""The surviving crypto sleeves on ONE $1,000 account, 2019-2026.

Sleeves, as sealed by `crypto_families_research`:

    btckrw   donchian   margin over its own coin flip +48.2
    ethusd   vwap       margin over its own coin flip +46.6
    dogeusd  momentum   margin over its own coin flip +33.1  (borderline)
    ethbtc   orb        margin over its own coin flip +27.5  (borderline)

NO HOLDOUT. This run spans 2019-01-01 to 2026-08-07 continuously and reports the
two windows separately, as requested. That means 2019-2024 is the window these
three cells were *selected on*, so its numbers are in-sample and not evidence of
anything; only 2025-2026 was untouched, and it was already spent as the holdout
in the parent study. Nothing here is a fresh test. It answers "what would the
combination have done", not "does the combination work".

WHY THIS IS A REAL SIMULATION AND NOT THREE CURVES ADDED TOGETHER. Summing
per-sleeve return streams previously hid half a portfolio's drawdown
([[blend-model-understates-portfolio-drawdown]]), because it silently assumes
the sleeves never draw down at the same moment and that each is sized off its
own private equity. Here there is one `cash` balance, every position is sized
off the *shared* equity at the moment it opens, and the three sleeves compete
for the same money. A third sleeve therefore adds a whole extra risk budget
rather than diversifying one away -- [[combined-book-stacks-risk-budgets]].

DRAWDOWN IS MARKED TO MARKET. Equity is revalued every 30-minute bucket as
`cash + open unrealised P&L`, so a day where all three sleeves are underwater
intraday shows up even if each later closes green. Closed-trade drawdown
reported 21% where the engine reported 37% on an earlier study
([[engine-drawdown-is-mark-to-market]]); both are printed below so the gap is
visible rather than assumed away.

LEVERAGE. The account has unlimited leverage, so the margin ceiling is removed
entirely and the only limits on size are the risk budget and the lot step. This
matters most for btckrw, whose 0.01 BTC step is a ~600 dollar position: on a
1,000 dollar account that step, not the signal, decides whether a trade happens
at all -- [[lot-granularity-fakes-low-drawdown]].
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import crypto_families_research as cf


#: A sleeve is a (symbol, family) pair, so one symbol may contribute several
#: strategies. This default is the book built up over the earlier passes; the
#: last two carry weaker evidence than the first three (margins over their own
#: coin flips of +33.1 and +27.5 against +84.6/+48.2/+46.6, which with three
#: seeds sit inside the null's error bar).
DEFAULT_BOOK = (("btckrw", "donchian"), ("ethusd", "vwap"),
                ("dogeusd", "momentum"), ("ethbtc", "orb"))

SLEEVES = DEFAULT_BOOK


def key(symbol, family):
    return f"{symbol}:{family}"

INITIAL = 1_000.0
#: Unlimited leverage: not zero, because the ceiling divides by it. This is
#: small enough that the risk leg always binds first.
NO_MARGIN = 1e-12

START = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
SPLIT = cf.IS_END
END = cf.OOS_END


_VIX = {}


def vix_daily():
    """`{unix_day: VIX close}`, for sleeves carrying a volatility ceiling.

    `btc_donchian.rs` sits out above VIX 25.0. Omitting that gate does not just
    add trades -- it adds trades in precisely the regimes the compiled strategy
    judged unfit, so the omission is biased, not neutral.
    """
    if not _VIX:
        rows = cf.data.query(
            "SELECT cast(timestamp as long) d, last(close) c FROM vix_1d "
            "SAMPLE BY 1d ALIGN TO CALENDAR")
        _VIX.update({int(r[0]) // 1_000_000 // 86_400: float(r[1])
                     for r in rows if r[1] is not None})
    return _VIX


def accepts_vix(ts, ceiling):
    """Yesterday's VIX close strictly under `ceiling`; unknown days are blocked.

    The prior close, not the same day's, because a session opening at 09:30
    cannot know where VIX will settle that afternoon.

    Fails CLOSED on a missing reading, matching `btc_donchian.rs`, whose gate is
    `prior_vix(day).is_some_and(|vix| vix < MAX_VIX)` -- an absent VIX is `None`
    and `is_some_and` is false. An earlier version here failed open and used
    `<=`, which let through sessions the compiled strategy sits out.
    """
    if ceiling is None:
        return True
    table = vix_daily()
    day = ts // 86_400
    for back in range(1, 8):
        value = table.get(day - back)
        if value is not None:
            return value < ceiling
    return False


def sleeve_params(symbol, family):
    with open(cf.output_path(symbol), encoding="utf-8") as handle:
        payload = json.load(handle)
    winner = payload["families"][family]
    if winner is None:
        raise SystemExit(f"{symbol}: {family} has no sealed cell")
    return dict(winner["params"])


def load_sleeves(book=None, overrides=None):
    """`{'symbol:family': (bars, ctx, params, {ts: index}, symbol, family)}`.

    Bars and context are built once per *symbol* and shared by every family on
    it -- nine families on one symbol read exactly the same series, and
    rebuilding it nine times would dominate the run.

    `overrides` supplies parameters directly, keyed `'symbol:family'`, for cells
    that have no sealed selection file. BTC Donchian is the case that needs it:
    it is compiled in `btc_donchian.rs`, not selected by this study, so its
    parameters come from the Rust constants rather than from a JSON winner.
    """
    book = book or SLEEVES
    overrides = overrides or {}
    shared = {}
    out = {}
    for symbol, family in book:
        if symbol not in shared:
            bars, ctx = cf.context(symbol, "validate")
            shared[symbol] = (bars, ctx, {bar[cf.TS]: i
                                          for i, bar in enumerate(bars)})
        bars, ctx, index_of = shared[symbol]
        name = key(symbol, family)
        params = overrides.get(name) or sleeve_params(symbol, family)
        out[name] = (bars, ctx, params, index_of, symbol, family)
    return out


def simulate(sleeves, risk_pct, lo=START, hi=END, initial=INITIAL,
             spread_bp=None, sleeve_filter=None, weekdays_only=False):
    """One account, three sleeves, marked to market every bucket.

    The per-bar ordering inside each sleeve is copied from
    `crypto_families_research.backtest` unchanged -- flatten, stop, target, time
    stop, then trail; a pending entry fills at the next bucket's open. What
    differs is only that `equity` is shared and that quantity is computed
    against it at the moment of the fill.

    `weekdays_only` blocks entries on Saturday and Sunday. It deliberately does
    NOT remove weekend bars from the series: ATR, the EMAs, the VWAP, the
    Donchian channels and the prior-session anchors keep reading them, because
    the weekend is real trading that really moved the price and a Monday
    breakout level that ignored it would be fictional. So this is a filter on
    when the book is *allowed to act*, not a rewrite of what it sees.

    Positions already flatten at every session close, so nothing carries into a
    weekend either way; the filter only removes Saturday and Sunday entries.
    """
    if spread_bp is None:
        spread_bp = cf.default_spread_bps()
    active = [k for k in sleeves if sleeve_filter is None or k in sleeve_filter]
    size_mode = f"riskvol_{risk_pct}pct"

    timeline = sorted({bar[cf.TS] for k in active
                       for bar in sleeves[k][0] if lo <= bar[cf.TS] < hi})
    cash = initial
    peak_closed = peak_mtm = initial
    dd_closed = dd_mtm = 0.0
    trades = []
    equity_curve = []
    book = {k: {"position": None, "pending": None, "traded_day": None,
                "state": {}} for k in active}

    for ts in timeline:
        day = ts // 86_400
        minute = ts % 86_400 // 60

        for sleeve in active:
            bars, ctx, params, index_of, symbol, family = sleeves[sleeve]
            index = index_of.get(ts)
            if index is None:
                continue
            bar = bars[index]
            slot = book[sleeve]
            position, pending = slot["position"], slot["pending"]

            if position is not None:
                side = position["side"]
                price = reason = None
                if minute >= cf.SESSION_CLOSE_MINUTE:
                    price, reason = bar[cf.O], "session"
                else:
                    stop = position["stop"]
                    if (side == 1 and bar[cf.L] <= stop) or (side == -1 and bar[cf.H] >= stop):
                        price = min(bar[cf.O], stop) if side == 1 else max(bar[cf.O], stop)
                        reason = "stop"
                    elif position["target"] is not None:
                        target = position["target"]
                        if (side == 1 and bar[cf.H] >= target) or (side == -1 and bar[cf.L] <= target):
                            price = (max(bar[cf.O], target) if side == 1
                                     else min(bar[cf.O], target))
                            reason = "target"
                    if (price is None and position["max_bars"] is not None
                            and index - position["index"] >= position["max_bars"]):
                        price, reason = bar[cf.O], "time"
                if price is None:
                    if position["trail"] is not None and ctx["atr"][index]:
                        atr = ctx["atr"][index]
                        if side == 1:
                            position["best"] = max(position["best"], bar[cf.C])
                            position["stop"] = max(
                                position["stop"],
                                position["best"] - position["trail"] * atr)
                        else:
                            position["best"] = min(position["best"], bar[cf.C])
                            position["stop"] = min(
                                position["stop"],
                                position["best"] + position["trail"] * atr)
                else:
                    gross = side * (price - position["entry"])
                    net = gross - position["spread"]
                    pnl = net * position["quantity"] * position["fx"]
                    cash += pnl
                    quantity = position["quantity"]
                    trades.append({
                        "sleeve": sleeve, "symbol": symbol,
                        "entry_ts": position["ts"], "exit_ts": ts,
                        "side": side, "pnl": pnl, "reason": reason,
                        "quantity": quantity,
                        "points_per_unit": (pnl / quantity) if quantity else 0.0,
                        "units_per_dollar": (quantity / position["equity_at_entry"]
                                             if position["equity_at_entry"] > 0 else 0.0),
                    })
                    slot["position"] = position = None

            if position is None and pending is not None:
                if day == pending["day"] and minute < cf.SESSION_CLOSE_MINUTE:
                    fx = cf.fx_at(ctx["fx"], ctx["fx_base"], ts)
                    amount = cf.quantity(size_mode, cash, bar[cf.O],
                                         pending["distance"], pending["realized"],
                                         ctx, fx, NO_MARGIN)
                    if amount >= ctx["step"]:
                        side, entry = pending["side"], bar[cf.O]
                        target, max_bars, trail = cf.exit_plan(
                            params["exit_mode"], pending["distance"])
                        slot["position"] = {
                            "side": side, "entry": entry, "ts": ts, "index": index,
                            "quantity": amount, "fx": fx,
                            # Recorded so a sleeve can be re-based onto another
                            # book's balance: sizing is linear in equity, so
                            # `quantity / equity_at_entry` is the strategy's own
                            # sizing decision expressed independently of the
                            # account it happened to run on.
                            "equity_at_entry": cash,
                            "spread": entry * spread_bp / 1e4,
                            "stop": entry - side * pending["distance"],
                            "target": None if target is None else entry + side * target,
                            "max_bars": max_bars, "trail": trail, "best": entry,
                        }
                        slot["traded_day"] = day
                slot["pending"] = pending = None

            if (slot["position"] is None and pending is None
                    and slot["traded_day"] != day
                    and cf.SESSION_OPEN_MINUTE <= minute < cf.SESSION_CLOSE_MINUTE
                    and not ((weekdays_only or params.get("weekdays_only"))
                             and cf.es.weekday(ts) >= 5)
                    and accepts_vix(ts, params.get("max_vix"))
                    and cf.accepts_vol(ctx, index, params["vol_mode"])):
                side = cf.SIGNALS[family](index, bars, ctx, params, slot["state"])
                atr, realized = ctx["atr"][index], ctx["volatility"][index]
                if (side is not None and atr is not None and realized is not None
                        and cf.accepts_trend(bar[cf.C], ctx, index, side,
                                             params["trend"])):
                    slot["pending"] = {"side": side, "day": day, "atr": atr,
                                       "distance": params["stop_atr"] * atr,
                                       "realized": realized}

        # Mark to market: every open sleeve revalued at this bucket's close.
        open_pnl = 0.0
        for sleeve in active:
            position = book[sleeve]["position"]
            if position is None:
                continue
            bars, ctx, _params, index_of, _sym, _fam = sleeves[sleeve]
            index = index_of.get(ts)
            if index is None:
                continue
            move = position["side"] * (bars[index][cf.C] - position["entry"])
            open_pnl += (move - position["spread"]) * position["quantity"] * position["fx"]

        equity = cash + open_pnl
        peak_closed = max(peak_closed, cash)
        peak_mtm = max(peak_mtm, equity)
        dd_closed = max(dd_closed, (peak_closed - cash) / peak_closed)
        dd_mtm = max(dd_mtm, (peak_mtm - equity) / peak_mtm)
        equity_curve.append((ts, equity))

    return {"trades": trades, "final": cash, "equity_curve": equity_curve,
            "max_dd_closed_pct": round(100.0 * dd_closed, 2),
            "max_dd_mtm_pct": round(100.0 * dd_mtm, 2)}


def window_stats(run, lo, hi, initial):
    """Return, drawdown and consistency over `[lo, hi)` of an existing run."""
    curve = [(ts, eq) for ts, eq in run["equity_curve"] if lo <= ts < hi]
    if not curve:
        return {}
    start = curve[0][1]
    peak = start
    drawdown = 0.0
    for _ts, equity in curve:
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 0.0)
    trades = [t for t in run["trades"] if lo <= t["entry_ts"] < hi]
    months = {}
    for trade in trades:
        moment = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        months.setdefault(f"{moment.year}-{moment.month:02d}", 0.0)
        months[f"{moment.year}-{moment.month:02d}"] += trade["pnl"]
    monthly = list(months.values())
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    deviation = statistics.pstdev(monthly) if len(monthly) > 1 else 0.0
    years = max(1e-9, (curve[-1][0] - curve[0][0]) / (365.25 * 86_400))
    return {
        "start_equity": round(start, 2),
        "end_equity": round(curve[-1][1], 2),
        "return_pct": round(100.0 * (curve[-1][1] - start) / start, 2),
        "cagr_pct": round(100.0 * ((curve[-1][1] / start) ** (1 / years) - 1), 2),
        "max_dd_mtm_pct": round(100.0 * drawdown, 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else 0.0,
        "monthly_sharpe": round(statistics.fmean(monthly) / deviation, 3)
                          if deviation else 0.0,
        "positive_months": sum(1 for v in monthly if v > 0),
        "n_months": len(monthly),
        "by_sleeve": {k: round(sum(t["pnl"] for t in trades if t["sleeve"] == k), 2)
                      for k in sorted({t["sleeve"] for t in trades})},
    }


def annual_table(run, initial):
    """Calendar-year return and marked-to-market drawdown of the shared account."""
    by_year = {}
    for ts, equity in run["equity_curve"]:
        year = datetime.fromtimestamp(ts, tz=timezone.utc).year
        entry = by_year.setdefault(year, {"first": equity, "last": equity,
                                          "peak": equity, "dd": 0.0})
        entry["last"] = equity
        entry["peak"] = max(entry["peak"], equity)
        entry["dd"] = max(entry["dd"], (entry["peak"] - equity) / entry["peak"])
    return {str(year): {
        "return_pct": round(100.0 * (v["last"] - v["first"]) / v["first"], 2),
        "max_dd_mtm_pct": round(100.0 * v["dd"], 2)} for year, v in sorted(by_year.items())}


def correlation(run):
    """Monthly P&L correlation between sleeves -- the diversification claim."""
    names = sorted({t["sleeve"] for t in run["trades"]})
    months = {}
    for trade in run["trades"]:
        moment = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        stamp = f"{moment.year}-{moment.month:02d}"
        months.setdefault(stamp, {n: 0.0 for n in names})[trade["sleeve"]] += trade["pnl"]
    stamps = sorted(months)
    series = {n: [months[s][n] for s in stamps] for n in names}
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                out[f"{a} / {b}"] = round(
                    statistics.correlation(series[a], series[b]), 3)
            except statistics.StatisticsError:
                out[f"{a} / {b}"] = None
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", default="0.5,0.75,1.0,1.5",
                        help="per-sleeve risk fractions to ladder, in percent")
    parser.add_argument("--spread-bp", type=float, default=None)
    args = parser.parse_args()
    spread_bp = args.spread_bp if args.spread_bp is not None else cf.default_spread_bps()
    ladder = [float(v) for v in args.risk.split(",")]

    print(f"loading {len(SLEEVES)} sleeves: "
          + ", ".join(key(s, f) for s, f in SLEEVES))
    sleeves = load_sleeves()
    print(f"entry spread {spread_bp:.5f}bp; unlimited leverage (margin cap removed); "
          f"${INITIAL:,.0f} shared account\n")

    report = {}
    header = (f"{'risk':>6} {'FULL ret%':>11} {'CAGR%':>8} {'dd mtm%':>9} "
              f"{'dd closed%':>11} {'2019-24 ret%':>13} {'dd%':>7} "
              f"{'2025-26 ret%':>13} {'dd%':>7} {'PF':>6} {'+mo':>8}")
    print(header)
    print("-" * len(header))
    for risk in ladder:
        run = simulate(sleeves, risk, spread_bp=spread_bp)
        full = window_stats(run, START, END, INITIAL)
        early = window_stats(run, START, SPLIT, INITIAL)
        late = window_stats(run, SPLIT, END, INITIAL)
        print(f"{risk:>5.2f}% {full['return_pct']:>11,.0f} {full['cagr_pct']:>8.1f} "
              f"{full['max_dd_mtm_pct']:>9.1f} {run['max_dd_closed_pct']:>11.1f} "
              f"{early['return_pct']:>13,.0f} {early['max_dd_mtm_pct']:>7.1f} "
              f"{late['return_pct']:>13,.1f} {late['max_dd_mtm_pct']:>7.1f} "
              f"{full['pf']:>6.2f} "
              f"{str(full['positive_months']) + '/' + str(full['n_months']):>8}")
        report[f"{risk}"] = {"full": full, "in_sample_2019_2024": early,
                             "out_of_sample_2025_2026": late,
                             "max_dd_closed_pct": run["max_dd_closed_pct"],
                             "annual": annual_table(run, INITIAL),
                             "sleeve_correlation": correlation(run)}

    chosen = report[f"{ladder[-1]}"]
    print("\nSLEEVE CORRELATION (monthly P&L, full sample):")
    print("  " + json.dumps(chosen["sleeve_correlation"], sort_keys=True))

    print(f"\nPER-YEAR, at {ladder[-1]}% risk a sleeve:")
    print(f"  {'year':>6} {'return %':>10} {'max dd (mtm) %':>16}")
    for year, detail in chosen["annual"].items():
        print(f"  {year:>6} {detail['return_pct']:>10.2f} "
              f"{detail['max_dd_mtm_pct']:>16.2f}")

    print(f"\nP&L BY SLEEVE at {ladder[-1]}% risk:")
    for window, label in (("in_sample_2019_2024", "2019-2024 (in sample)"),
                          ("out_of_sample_2025_2026", "2025-2026")):
        print(f"  {label:<24} " + "  ".join(
            f"{k}: {v:>10,.0f}" for k, v in
            sorted(chosen[window]["by_sleeve"].items())))

    destination = os.path.join(cf.RESULTS, "crypto_portfolio.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"sleeves": [list(s) for s in SLEEVES],
                   "initial": INITIAL, "spread_bp": spread_bp,
                   "leverage": "unlimited (margin ceiling removed)",
                   "no_holdout": "2019-2024 is the selection window; "
                                 "2025-2026 was spent as the parent study's holdout",
                   "ladder": report}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


if __name__ == "__main__":
    main()
