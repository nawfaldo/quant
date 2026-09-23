"""The six non-Maroy BTC strategies against the three-sleeve book.

`portfolio_fourth_sleeve` ended on a negative with a reason attached: every Maroy
variant correlates 0.42-0.73 with `BTC Maroy Ladder` because all sixteen share
one noise-boundary entry and differ only in the exit. Adding another is buying
more of a bet the book already holds. The conclusion named its own remedy -- a
fourth sleeve has to come from outside that family.

These are outside it. Donchian trend, VWAP, two opening-range variants, an RTH
momentum rule and an hourly momentum rule: six different entries, resolved by
`cross_portfolio`'s machinery, which spies on each research module's `quantity()`
to recover per-unit fills that carry no sizing.

WHY THESE ARE THE INTERESTING CANDIDATES. Their parameters were sealed on
2018-2024, so over 2025-2026 they are genuinely out of sample -- unlike the two
NQ incumbents, which were tuned on roughly the 2025 span. A BTC family that helps
here is being tested, not remembered.

WHAT THIS CANNOT DO, and it is the reason nothing below is a promotion. None of
the six is compiled in Rust, so none can be run as a real fourth slot on the
engine. Everything here is a Python replay of closed trades, and a closed-trade
replay of this very book put its drawdown at 21% where the engine says 37% --
the difference is open risk, which the engine marks every bar and a trade log
cannot see. So drawdown figures below are a floor, not an estimate.

That is survivable for the question actually being asked. Correlation is
scale-invariant and needs no drawdown at all, and a like-for-like comparison
(three sleeves replayed the same way, against three-plus-one replayed the same
way) understates both sides identically. What it cannot support is a claim about
the book's real drawdown, so no such claim is made: a candidate that passes here
earns a port to Rust and a measurement, nothing more.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import portfolio_exposure as px
from sandbox.research import portfolio_fourth_sleeve as f4


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_btc_families.json")

#: The window all nine members can trade together: NQ level-two history starts
#: 2025-02-12, which is later than anything on the BTC side.
WINDOW_FROM, WINDOW_TO = "2025-02-12", "2026-08-01"

CANDIDATES = ("BTC Donchian", "BTC VWAP", "BTC ORB Trail",
              "BTC ORB", "BTC RTH Momentum", "BTC Hourly Momentum")


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def load_fills(names):
    """Per-unit fills for each BTC family, via `cross_portfolio`'s resolvers."""
    from sandbox.research import cross_portfolio as cross

    wanted = set(names)
    fills = {}

    families_wanted = [(f, n) for f, n in (("trend", "BTC Donchian"),
                                           ("vwap", "BTC VWAP"),
                                           ("orb", "BTC ORB Trail"))
                       if n in wanted]
    if families_wanted:
        print("  resolving BTC session members...", flush=True)
        bars, ctx = cross.families.context("validate")
        for family, name in families_wanted:
            fills[name] = cross.btc_family_fills(family, bars, ctx)

    if {"BTC ORB", "BTC RTH Momentum"} & wanted:
        print("  resolving BTC 30-minute members...", flush=True)
        half_hour = cross.rth.bars_30m("oos")
        if "BTC ORB" in wanted:
            fills["BTC ORB"] = cross.btc_orb_fills(
                half_hour, cross.orbmr.context(half_hour, "oos"))
        if "BTC RTH Momentum" in wanted:
            fills["BTC RTH Momentum"] = cross.btc_rth_fills(
                half_hour, cross.rth.context(half_hour, "oos"))

    if "BTC Hourly Momentum" in wanted:
        print("  resolving BTC hourly member...", flush=True)
        hourly = cross.base.hourly_bars("oos")
        fills["BTC Hourly Momentum"] = cross.btc_hourly_fills(
            hourly, cross.base.indicators(hourly),
            cross.consistency.vix_prior_by_bar(hourly, "oos"),
            cross.consistency.trailing_annual_volatility(hourly),
        )
    return fills


def standalone_daily(fills, initial=px.INITIAL, margin=0.25, step=0.01):
    """One member alone on its own balance -> (daily returns, summary).

    Sizing mirrors `cross_portfolio.replay`: `equity * risk / stop`, capped by
    margin and floored at one lot step, so a member that cannot afford a step
    simply does not trade.
    """
    events = sorted(fills, key=lambda f: f[0])
    equity, peak, worst = initial, initial, 0.0
    opening, closing = {}, {}
    taken = 0
    for entry_ts, exit_ts, points, stop, price, risk in events:
        if equity <= 0 or not stop or stop <= 0 or not price or price <= 0:
            continue
        raw = min(equity * (risk or 0.0) / stop, equity / margin / price)
        quantity = int(raw / step) * step
        if quantity < step:
            continue
        day = day_of(exit_ts)
        opening.setdefault(day, equity)
        equity += points * quantity
        closing[day] = equity
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 100.0 * (peak - equity) / peak)
        taken += 1
    returns = {day: closing[day] / opening[day] - 1.0
               for day in closing if opening[day] > 0}
    return returns, {
        "return_pct": round(100.0 * (equity - initial) / initial, 2),
        "closed_dd_pct": round(worst, 2),
        "trades": taken,
        "final": round(equity, 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    lo = day_of(int(datetime.strptime(WINDOW_FROM, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp()))
    hi = day_of(int(datetime.strptime(WINDOW_TO, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp()))

    incumbents, book = f4.incumbent_streams()
    incumbents = {name: {d: r for d, r in stream.items() if lo <= d <= hi}
                  for name, stream in incumbents.items()}
    print(f"BOOK (shipped)  return {book['return_pct']:.2f}%  "
          f"maxDD {book['max_dd_pct']:.2f}%  sharpe {book['sharpe']:.2f}")
    print(f"window {WINDOW_FROM} .. {WINDOW_TO}\n")

    fills = load_fills(CANDIDATES)

    print()
    print("=" * 104)
    print("CORRELATION + STANDALONE -- six non-Maroy BTC strategies, sealed on 2018-2024")
    print("=" * 104)
    short = [n.replace("NQ ", "").replace("BTC ", "")[:12] for n in f4.INCUMBENTS]
    print(f"  {'strategy':<22}" + "".join(f"{s:>14}" for s in short)
          + f"{'mean|rho|':>11}{'ret':>9}{'closedDD':>10}{'n':>6}{'days':>6}")

    rows = []
    for name in CANDIDATES:
        if name not in fills:
            print(f"  {name:<22}  unavailable")
            continue
        windowed = [f for f in fills[name] if lo <= day_of(f[0]) <= hi]
        returns, summary = standalone_daily(windowed)
        cells, cors, days = "", [], 0
        for incumbent in f4.INCUMBENTS:
            rho, overlap = f4.correlation(returns, incumbents[incumbent])
            days = max(days, overlap)
            cells += f"{rho if rho is not None else '-':>14}"
            if rho is not None:
                cors.append(abs(rho))
        mean_abs = round(statistics.fmean(cors), 3) if cors else None
        print(f"  {name:<22}{cells}{mean_abs if mean_abs is not None else '-':>11}"
              f"{summary['return_pct']:8.1f}%{summary['closed_dd_pct']:9.2f}%"
              f"{summary['trades']:6}{days:6}")
        rows.append({"name": name, "mean_abs_rho": mean_abs, "days": days,
                     "correlations": {i: f4.correlation(returns, incumbents[i])[0]
                                      for i in f4.INCUMBENTS},
                     **summary})
    report = {"window": [WINDOW_FROM, WINDOW_TO], "candidates": rows,
              "book": {f: book[f] for f in px.FIELDS}}

    print()
    print("  Ladder correlation is the column that matters: it is the one the")
    print("  Maroy family failed on (0.42-0.73). Anything near zero here is a")
    print("  genuinely different bet rather than a repackaged one.")

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- #
# does a candidate actually help the book
# --------------------------------------------------------------------------- #

MARGIN, STEP = 0.25, 0.01


def incumbent_units(book, initial=px.INITIAL):
    """The shipped book's trades as size-independent units.

    `k = quantity / equity_at_entry` is recovered from the engine's own run, so
    it already carries the volatility overlay and the Ladder's 2x -- this is the
    shipped book, not the raw sleeves. Both sizing legs are linear in equity, so
    `k` is a property of the trade and can be re-applied against a different
    balance path.
    """
    ordered = sorted(book["trades_log"], key=lambda t: t["xt"])
    times, values, equity = [], [], initial
    for trade in ordered:
        equity += trade["pnl"]
        times.append(trade["xt"])
        values.append(equity)

    def balance_before(ts):
        import bisect
        index = bisect.bisect_left(times, ts)
        return initial if index == 0 else values[index - 1]

    units = []
    for trade in book["trades_log"]:
        if trade["qty"] <= 0:
            continue
        base = balance_before(trade["et"])
        if base <= 0:
            continue
        units.append((trade["et"], trade["xt"], trade["pnl"] / trade["qty"],
                      trade["qty"] / base, trade.get("s", "")))
    return units


def candidate_units(fills):
    """A BTC family's fills as the same `(entry, exit, points, k, name)` shape."""
    out = []
    for entry_ts, exit_ts, points, stop, price, risk in fills:
        if not stop or stop <= 0 or not price or price <= 0:
            continue
        out.append((entry_ts, exit_ts, points,
                    min((risk or 0.0) / stop, 1.0 / (MARGIN * price)), "candidate"))
    return out


def replay(units, initial=px.INITIAL, step=STEP, prices=None):
    """One balance, entries sized off it, exits booked to it, in event order.

    Also reports peak *aggregate* notional across concurrently open positions.
    That number is the health warning on everything else this function returns:
    each strategy caps its own margin per trade, nothing caps the sum, and the
    closed-trade drawdown cannot see open risk at all. When aggregate leverage
    climbs, the return column keeps compounding and the drawdown column does not
    move -- which is not a free lunch, it is the model failing to price the risk
    it just took on.
    """
    import bisect
    units = sorted(units, key=lambda u: (u[0], u[1]))
    prices = prices or {}
    equity, peak, worst = initial, initial, 0.0
    pending, cursor = [], 0
    opening, closing = {}, {}
    month_open, month_close = {}, {}
    taken, peak_gross = 0, 0.0
    while cursor < len(units) or pending:
        next_entry = units[cursor][0] if cursor < len(units) else None
        next_exit = pending[0][0] if pending else None
        if next_exit is not None and (next_entry is None or next_exit <= next_entry):
            exit_ts, quantity, points, _price = pending.pop(0)
            day = day_of(exit_ts)
            month = day[:7]
            opening.setdefault(day, equity)
            month_open.setdefault(month, equity)
            equity += points * quantity
            closing[day] = equity
            month_close[month] = equity
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, 100.0 * (peak - equity) / peak)
            continue
        entry_ts, exit_ts, points, k, _name = units[cursor]
        cursor += 1
        if equity <= 0 or k <= 0:
            continue
        quantity = int(k * equity / step) * step
        if quantity < step:
            continue
        taken += 1
        bisect.insort(pending, (exit_ts, round(quantity, 8), points,
                                prices.get((entry_ts, exit_ts), 0.0)))
        gross = sum(q * p for _t, q, _pt, p in pending)
        if equity > 0:
            peak_gross = max(peak_gross, gross / equity)
    return {
        "return_pct": round(100.0 * (equity - initial) / initial, 2),
        "closed_dd_pct": round(worst, 2),
        "trades": taken,
        "final": round(equity, 2),
        "peak_gross_leverage": round(peak_gross, 2),
        "monthly": [
            {"month": m, "opening": round(month_open[m], 2),
             "closing": round(month_close[m], 2),
             "return_pct": round(100.0 * (month_close[m] / month_open[m] - 1.0), 2)
             if month_open[m] > 0 else 0.0}
            for m in sorted(month_close)
        ],
    }


def helps_the_book(weights=(0.5, 1.0, 2.0)):
    """Three sleeves, then three plus each candidate, replayed identically.

    Both arms are closed-trade replays, so both understate drawdown by the same
    mechanism and the *difference* is meaningful even though the level is not.
    """
    incumbents, book = f4.incumbent_streams()
    base_units = incumbent_units(book)
    prices = {(t["et"], t["xt"]): t["ep"] for t in book["trades_log"]}
    fills = load_fills(CANDIDATES)

    lo = WINDOW_FROM
    hi = WINDOW_TO
    base = replay(base_units, prices=prices)
    print()
    print("=" * 104)
    print("AS A FOURTH SLEEVE -- like-for-like closed-trade replay on one balance")
    print("=" * 104)
    print("  closed-trade drawdown understates the engine's mark-to-market figure")
    print("  (21% vs 37% on this book); read the columns against each other only.\n")
    print(f"  {'book':<34}{'return':>10}{'closedDD':>11}{'gross lev':>11}{'trades':>8}")
    print(f"  {'three sleeves (shipped)':<34}{base['return_pct']:9.2f}%"
          f"{base['closed_dd_pct']:10.2f}%{base['peak_gross_leverage']:10.2f}x"
          f"{base['trades']:8}")

    rows = []
    for name in CANDIDATES:
        if name not in fills:
            continue
        windowed = [f for f in fills[name] if lo <= day_of(f[0]) <= hi]
        units = candidate_units(windowed)
        if not units:
            continue
        candidate_prices = {(f[0], f[1]): f[4] for f in windowed}
        for weight in weights:
            scaled = [(e, x, p, k * weight, n) for e, x, p, k, n in units]
            result = replay(base_units + scaled,
                            prices={**prices, **candidate_prices})
            label = f"+ {name} x{weight:g}"
            print(f"  {label:<34}{result['return_pct']:9.2f}%"
                  f"{result['closed_dd_pct']:10.2f}%"
                  f"{result['peak_gross_leverage']:10.2f}x{result['trades']:8}"
                  f"   ret {result['return_pct'] - base['return_pct']:+7.2f}"
                  f"  dd {result['closed_dd_pct'] - base['closed_dd_pct']:+6.2f}")
            rows.append({"candidate": name, "weight": weight, **result,
                         "return_delta": round(result["return_pct"] - base["return_pct"], 2),
                         "dd_delta": round(result["closed_dd_pct"] - base["closed_dd_pct"], 2)})
    return {"base": base, "rows": rows}


if os.environ.get("BTC_FAMILIES_BOOK"):
    outcome = helps_the_book()
    with open(os.path.join(os.path.dirname(__file__),
                           "portfolio_btc_families_book.json"), "w") as handle:
        json.dump(outcome, handle, indent=1)
