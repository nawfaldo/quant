"""Monthly, yearly and per-sleeve breakdown of the book under an exposure schedule.

Defaults to the recommended overlay -- price volatility targeting, 12% annual
target, 40-day halflife, capped at 1.0 -- and prints the untouched book beside it
so every figure has its baseline.

READ THE PERCENTAGES AS COMPOUNDING, NOT AS DOLLARS. The account is one balance
and every sleeve sizes off it, so a month's percent return is measured against
the balance standing at that month's open, not against the initial $1,000. The
dollar column and the percent column therefore tell different stories on purpose:
$100 in early 2025 is a 10% month, $100 in mid-2026 is a 5% one.

PER-SLEEVE PnL IS NOT A STANDALONE BACKTEST. `contribution` splits realised PnL
by the slot that earned it, and each sleeve's share includes its part of joint
compounding -- its gains were booked against a balance the other two had already
moved. The columns sum to the account total; they are not what any sleeve would
have made alone. The `own DD` column is that sleeve's PnL stream replayed on its
own $1,000, which is its contribution to the book's risk.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from sandbox.research import portfolio_exposure as px
from sandbox.research import portfolio_price_vol as pv


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_report.json")
RECOMMENDED = {"target": 12, "halflife": 40, "max_mult": 3.0, "min_days": 30}


#: Short names accepted by `--scale`, so a sleeve can be re-weighted without
#: retyping its display name.
ALIASES = {"ofi": px.SLEEVES[0], "hdr": px.SLEEVES[1], "ladder": px.SLEEVES[2]}


def parse_scales(entries):
    """`["ladder=3"]` -> `{"BTC Maroy Ladder": 3.0}`."""
    out = {}
    for entry in entries:
        name, _, value = entry.partition("=")
        key = ALIASES.get(name.strip().lower(), name.strip())
        if key not in px.SLEEVES:
            raise SystemExit(f"unknown sleeve {name!r}; expected one of "
                             f"{sorted(ALIASES)} or a full display name")
        out[key] = float(value)
    return out


def month_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")


def monthly_curve(trades, initial=px.INITIAL):
    """`[(month, opening, closing, pnl_by_sleeve)]` in month order."""
    by_month = defaultdict(lambda: defaultdict(float))
    for trade in trades:
        by_month[month_of(trade["xt"])][trade.get("s", "?")] += trade["pnl"]
    rows, equity = [], initial
    for month in sorted(by_month):
        opening = equity
        equity += sum(by_month[month].values())
        rows.append((month, opening, equity, dict(by_month[month])))
    return rows


def yearly(rows):
    """Compound the monthly closes into calendar years."""
    out = {}
    for month, opening, closing, _split in rows:
        year = month[:4]
        if year not in out:
            out[year] = [opening, closing]
        else:
            out[year][1] = closing
    return out


def report_schedule(label, schedule, sleeves=px.SLEEVES):
    result = px.run(schedule, px.FULL)
    rows = monthly_curve(result["trades_log"])

    print()
    print("=" * 94)
    print(f"{label}")
    print("=" * 94)
    print(f"  return {result['return_pct']:.2f}%   max drawdown {result['max_dd_pct']:.2f}%   "
          f"sharpe {result['sharpe']:.2f}   trades {result['trades']}   "
          f"final ${result['final']:,.2f}")

    short = [name.replace("NQ ", "").replace("BTC ", "") for name in sleeves]
    print()
    print(f"  {'month':<9}{'opening':>10}{'closing':>10}{'return':>9}   "
          + "".join(f"{name[:13]:>14}" for name in short))
    for month, opening, closing, split in rows:
        pct = 100.0 * (closing / opening - 1.0) if opening > 0 else 0.0
        cells = "".join(f"{split.get(name, 0.0):>14.2f}" for name in sleeves)
        print(f"  {month:<9}{opening:>10.2f}{closing:>10.2f}{pct:>8.2f}%   {cells}")

    print()
    print(f"  {'year':<9}{'opening':>10}{'closing':>10}{'return':>9}")
    years = yearly(rows)
    for year in sorted(years):
        opening, closing = years[year]
        pct = 100.0 * (closing / opening - 1.0) if opening > 0 else 0.0
        note = "  (through 2026-08-05)" if year == "2026" else ""
        print(f"  {year:<9}{opening:>10.2f}{closing:>10.2f}{pct:>8.2f}%{note}")

    print()
    print(f"  {'sleeve':<28}{'PnL':>11}{'share':>9}{'own DD':>9}{'avg DD':>9}{'trades':>8}")
    for row in result["contribution"]:
        print(f"  {row['strategy']:<28}{row['pnl']:>11.2f}{row['share_pct']:>8.1f}%"
              f"{row['max_drawdown']:>8.2f}%{row['avg_drawdown']:>8.2f}%{row['trades']:>8}")

    return {
        "headline": {f: result[f] for f in px.FIELDS},
        "final": result["final"],
        "monthly": [{"month": m, "opening": round(o, 2), "closing": round(c, 2),
                     "return_pct": round(100.0 * (c / o - 1.0), 2) if o > 0 else 0.0,
                     "by_sleeve": {k: round(v, 2) for k, v in s.items()}}
                    for m, o, c, s in rows],
        "yearly": {y: {"opening": round(v[0], 2), "closing": round(v[1], 2),
                       "return_pct": round(100.0 * (v[1] / v[0] - 1.0), 2) if v[0] > 0 else 0.0}
                   for y, v in years.items()},
        "contribution": result["contribution"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=float, default=RECOMMENDED["target"])
    parser.add_argument("--halflife", type=float, default=RECOMMENDED["halflife"])
    parser.add_argument("--baseline", action="store_true",
                        help="also print the untouched book")
    parser.add_argument("--scale", action="append", default=[], metavar="SLEEVE=X",
                        help="multiply one sleeve's exposure, e.g. ladder=3. "
                             "Applied on top of the volatility overlay.")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    schedule = pv.schedule(
        pv.multipliers(pv.daily_closes(), args.target, args.halflife,
                       RECOMMENDED["max_mult"], RECOMMENDED["min_days"]),
        cap=1.0)
    scales = parse_scales(args.scale)
    if scales:
        schedule = {name: {day: round(factor * scales.get(name, 1.0), 4)
                           for day, factor in points.items()}
                    for name, points in schedule.items()}
        for name, value in scales.items():
            print(f"  sleeve scale: {name} x{value:g}")

    out = {"params": {"target": args.target, "halflife": args.halflife, "cap": 1.0,
                      "scales": scales}}
    out["overlay"] = report_schedule(
        f"PRICE VOL t={args.target:g}, hl={args.halflife:g}, cap=1.0", schedule)
    if args.baseline:
        out["baseline"] = report_schedule("BASELINE -- no overlay", None)

    with open(args.out, "w") as handle:
        json.dump(out, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
