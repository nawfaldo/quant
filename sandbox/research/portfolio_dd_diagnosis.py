"""Why NQ Deep OFI Momentum + NQ Hourly Delta Reversal + BTC Maroy Ladder draws
down ~37% when its parts draw down 23% and 8%.

The three sleeves share one $1,000 forex account on the Rust engine
(`/api/combine`), so this module does not model anything -- it reads the engine's
own tagged trade log and takes it apart. Each trade now carries `s`, the slot
that opened it, which is what makes an exact per-sleeve split of a *combined*
run possible: the sleeve's trades as they were actually sized by the shared
balance, not as they would have been sized standalone.

WHAT THE STANDALONE NUMBERS DO NOT SAY. A sleeve's standalone max drawdown is
measured on an account only it trades. Put three sleeves on one balance with
fixed-fraction sizing and each one still risks its own fraction of the *whole*
account, so the book carries the sum of the three risk budgets, and the
drawdowns add to the extent the sleeves lose together. Diversification only
subtracts from that sum; it never makes the total smaller than the largest part.
The quantities printed below separate those two effects -- how much of the 37%
is "three risk budgets stacked" and how much is "they lost on the same days".

The rest of the gap is joint compounding, and the lot floor underneath it. A
sleeve inside the book is sized off a balance the *other* sleeves have already
moved, so its positions are larger in the stretches where the book has been
winning and its own drawdown is correspondingly larger in dollars than the same
signal produces alone. Quantisation pushes the same way at the bottom: a sleeve
that wants 0.004 BTC on a $1,000 account is floored to 0.01 or to nothing, and
on a balance that has grown it is neither. Both effects mean a sleeve's
standalone figure is a floor on what it contributes here, not an estimate of it
-- which is why the per-sleeve rows below are computed from the sleeve's trades
*as the shared account actually sized them* rather than from a standalone run.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import portfolio_exposure as px


INITIAL = px.INITIAL
SLEEVES = px.SLEEVES
FROM, TO = px.FULL

#: 2025 is in sample -- both NQ sleeves were tuned on roughly this span -- and
#: 2026 is the only window none of the three was selected on.
IS_TO = px.IS[1]
OOS_FROM = px.OOS[0]

OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_dd_diagnosis.json")


def combine(strategies, initial=INITIAL, frm=FROM, to=TO, refresh=False):
    """One untouched shared-account engine run.

    Delegates to `portfolio_exposure.run` rather than posting directly, and that
    is load-bearing: the server reads its exposure schedule from a file on every
    run, so a module that does not write that file inherits whatever the last
    sweep left behind. Routing every engine call through the one function that
    always writes it is what stops a diagnosis from silently reporting some
    volatility-targeted book's numbers as the baseline.
    """
    return px.run(None, window=(frm, to), sleeves=strategies,
                  initial=initial, refresh=refresh)


# --------------------------------------------------------------------------
# curves


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def equity_curve(trades, initial=INITIAL):
    """Close-to-close equity, keyed by exit day, in exit order.

    The engine books a trade's PnL at its exit, so replaying the tagged log in
    exit order reproduces the engine's own balance path exactly.
    """
    equity = initial
    out = []
    for trade in sorted(trades, key=lambda t: t["xt"]):
        equity += trade["pnl"]
        out.append((day_of(trade["xt"]), equity))
    return out


def drawdown(curve, initial=INITIAL):
    """Max and average drawdown percent over an equity path."""
    peak = initial
    worst = 0.0
    troughs = []
    current = 0.0
    for _day, equity in curve:
        if equity > peak:
            if current > 0:
                troughs.append(current)
            peak, current = equity, 0.0
        else:
            current = max(current, 100.0 * (peak - equity) / peak)
            worst = max(worst, current)
    if current > 0:
        troughs.append(current)
    return {
        "max_dd_pct": round(worst, 2),
        "avg_dd_pct": round(statistics.fmean(troughs), 2) if troughs else 0.0,
    }


def daily_returns(trades, initial=INITIAL):
    """Fractional return per calendar day from a sleeve's own equity path."""
    equity = initial
    out = {}
    start = {}
    for trade in sorted(trades, key=lambda t: t["xt"]):
        day = day_of(trade["xt"])
        start.setdefault(day, equity)
        equity += trade["pnl"]
        out[day] = equity
    return {day: out[day] / start[day] - 1.0 for day in out}


def correlation(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 5:
        return None
    xs, ys = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(cov / (vx * vy), 3) if vx and vy else None


def by_sleeve(result):
    out = {name: [] for name in SLEEVES}
    for trade in result["trades_log"]:
        out.setdefault(trade.get("s", "?"), []).append(trade)
    return out


# --------------------------------------------------------------------------
# the diagnosis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    report = {"window": [FROM, TO], "initial": INITIAL}

    book = combine(SLEEVES, refresh=args.refresh)
    curve = equity_curve(book["trades_log"])
    report["book"] = {
        "return_pct": book["return_pct"],
        "max_dd_pct": book["max_dd_pct"],
        "sharpe": book["sharpe"],
        "trades": book["trades"],
        "dd_peak": book["dd_peak"],
        "dd_trough": book["dd_trough"],
    }
    print(f"BOOK  return {book['return_pct']:.1f}%  maxDD {book['max_dd_pct']:.1f}%  "
          f"sharpe {book['sharpe']:.2f}  n={book['trades']}")
    print(f"      worst drawdown {book['dd_peak']} -> "
          f"{book['dd_trough']}")

    # 1. Each sleeve's own PnL stream, sized as the shared account actually
    #    sized it, replayed on its own $1,000. This is the sleeve's contribution
    #    to the book's risk -- not its standalone backtest.
    print("\n--- per-sleeve, as sized inside the book " + "-" * 24)
    splits = by_sleeve(book)
    rows = {}
    for name in SLEEVES:
        trades = splits[name]
        stats = drawdown(equity_curve(trades))
        pnl = sum(t["pnl"] for t in trades)
        rows[name] = {"pnl": round(pnl, 2), "trades": len(trades), **stats}
        print(f"  {name:<28} pnl {pnl:8.2f}  maxDD {stats['max_dd_pct']:5.2f}%  "
              f"avgDD {stats['avg_dd_pct']:5.2f}%  n={len(trades)}")
    report["in_book"] = rows

    stacked = sum(r["max_dd_pct"] for r in rows.values())
    print(f"\n  sum of the three risk budgets  {stacked:.2f}%")
    print(f"  the book actually drew          {book['max_dd_pct']:.2f}%")
    print(f"  diversification returned        {stacked - book['max_dd_pct']:.2f}%")
    report["stacked_dd_pct"] = round(stacked, 2)
    report["diversification_credit_pct"] = round(stacked - book["max_dd_pct"], 2)

    # 2. Every sub-book, so the superadditivity is visible rather than asserted.
    #    Each pair is run on the engine the same way the triple is.
    print("\n--- sub-books, each on its own $1,000 " + "-" * 27)
    subsets = {}
    for members in ((SLEEVES[0], SLEEVES[1]), (SLEEVES[0], SLEEVES[2]),
                    (SLEEVES[1], SLEEVES[2]), SLEEVES):
        label = " + ".join(name.replace("NQ ", "").replace("BTC ", "")
                           for name in members)
        result = combine(members)
        subsets[label] = {
            "return_pct": result["return_pct"],
            "max_dd_pct": result["max_dd_pct"],
            "sharpe": result["sharpe"],
        }
        print(f"  {label:<52} return {result['return_pct']:7.1f}%  "
              f"maxDD {result['max_dd_pct']:5.2f}%")
    report["subsets"] = subsets

    # 3. Correlation of daily results. This is the part a regime overlay can
    #    attack; the stacked risk budget is not.
    print("\n--- correlation of daily returns " + "-" * 32)
    streams = {name: daily_returns(splits[name]) for name in SLEEVES}
    correlations = {}
    for i, a in enumerate(SLEEVES):
        for b in SLEEVES[i + 1:]:
            rho = correlation(streams[a], streams[b])
            overlap = len(set(streams[a]) & set(streams[b]))
            correlations[f"{a} / {b}"] = {"rho": rho, "shared_days": overlap}
            print(f"  {a[:24]:<25} {b[:24]:<25} rho={rho}  shared days={overlap}")
    report["correlation"] = correlations

    # 4. When did the damage happen. A drawdown concentrated in one stretch is
    #    a regime question; one spread evenly over the sample is a sizing
    #    question, and only the second is fixed by turning the size down.
    print("\n--- monthly PnL by sleeve " + "-" * 39)
    print(f"  {'month':<9}" + "".join(f"{n.replace('NQ ','').replace('BTC ','')[:11]:>13}"
                                      for n in SLEEVES) + f"{'total':>10}")
    for row in book["monthly"]:
        cells = "".join(f"{row['by_strategy'].get(n, 0.0):>13.0f}" for n in SLEEVES)
        print(f"  {row['month']:<9}{cells}{row['total']:>10.0f}")
    report["monthly"] = book["monthly"]

    # 5. Where the drawdown sits in the sample. The book compounds, so a 37%
    #    drawdown on a $1,400 balance and a 37% drawdown on a $2,300 balance are
    #    very different dollar events -- and a percentage drawdown taken early,
    #    while the balance is small, is the one that is hardest to size away.
    peak_equity = INITIAL
    trough_equity = INITIAL
    peak = INITIAL
    worst = 0.0
    for _day, equity in curve:
        if equity > peak:
            peak = equity
        elif peak > 0 and 100.0 * (peak - equity) / peak > worst:
            worst = 100.0 * (peak - equity) / peak
            peak_equity, trough_equity = peak, equity
    print(f"\n  worst drawdown ran ${peak_equity:.0f} -> ${trough_equity:.0f} "
          f"(-${peak_equity - trough_equity:.0f})")
    report["worst_dd_dollars"] = {
        "peak": round(peak_equity, 2),
        "trough": round(trough_equity, 2),
    }

    # 6. In-sample / out-of-sample split of the same book, so every later claim
    #    has a baseline to be measured against on both windows.
    print("\n--- the split every overlay is judged on " + "-" * 24)
    for label, frm, to in (("2025 IS", FROM, IS_TO), ("2026 OOS", OOS_FROM, TO)):
        window = combine(SLEEVES, frm=frm, to=to)
        report[label.split()[1].lower()] = {
            "window": [frm, to],
            "return_pct": window["return_pct"],
            "max_dd_pct": window["max_dd_pct"],
            "sharpe": window["sharpe"],
            "trades": window["trades"],
        }
        print(f"  {label:<10} return {window['return_pct']:7.1f}%  "
              f"maxDD {window['max_dd_pct']:5.2f}%  sharpe {window['sharpe']:.2f}  "
              f"n={window['trades']}")

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
