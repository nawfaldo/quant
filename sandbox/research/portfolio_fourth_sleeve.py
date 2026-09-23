"""Which Maroy variant diversifies the three-sleeve book, and what adding it does.

THE QUESTION. The shipped book is `NQ Deep OFI Momentum` + `NQ Hourly Delta
Reversal` + `BTC Maroy Ladder`. Sixteen Maroy exit configurations exist in
`maroy_intraday_momentum` (the paper's Tables 1, 9, 10 and 11 plus the Section
4.1 time-only reference); four of them are compiled in Rust as `BTC Maroy Time`,
`BTC Maroy Boundary`, `BTC Maroy VWAP` and `BTC Maroy Ladder`. This screens all
sixteen for correlation against the three incumbents and then measures the
survivors as a real fourth sleeve.

WHY CORRELATION IS ONLY THE SCREEN. The book's 37.5% drawdown was never a
correlation problem -- the three sleeves are already at rho -0.14, +0.01, -0.14 --
it was three risk budgets stacked on one balance. So a fourth sleeve adds a
fourth budget, and a low correlation buys a *discount* on that, never an
exemption. Ranking on correlation alone would pick whichever variant is closest
to noise. Every candidate that survives the screen is therefore run through the
engine as a genuine fourth slot and judged on what the book's drawdown and return
actually do.

WHY THE SHORTLIST IS THE COMPILED ONES. All sixteen are screened, but only the
four already in Rust can be measured on the engine -- shared balance, real joint
compounding, mark-to-market drawdown. A sandbox-only variant would have to be
scored on a Python approximation, and a closed-trade replay of this book put its
drawdown at 21% where the engine says 37%. So a sandbox-only winner is
reported as "worth porting", not as a measured result.

WHAT THE ENGINE DOES WITH A FOURTH SLEEVE. `sizing::book_exposure` activates on
the three incumbents being present and leaves any other strategy in the run at
its own compiled size. A fourth Maroy sleeve therefore joins at 1.0 while the
incumbents keep their volatility targeting and the Ladder keeps its 2x -- which
is the intended behaviour and is pinned by
`a_non_sleeve_is_untouched_inside_the_book`.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import portfolio_exposure as px


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_fourth_sleeve.json")

#: Maroy variants that exist as compiled Rust strategies, so they can be run as a
#: real fourth slot rather than approximated.
COMPILED = ("BTC Maroy Time", "BTC Maroy Boundary", "BTC Maroy VWAP")

INCUMBENTS = px.SLEEVES


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def daily_returns_from_trades(trades, initial=px.INITIAL):
    """Fractional return per day from one sleeve's own PnL stream."""
    equity, opening, closing = initial, {}, {}
    for trade in sorted(trades, key=lambda t: t["xt"]):
        day = day_of(trade["xt"])
        opening.setdefault(day, equity)
        equity += trade["pnl"]
        closing[day] = equity
    return {
        day: closing[day] / opening[day] - 1.0
        for day in closing
        if opening[day] > 0
    }


def daily_returns_from_curve(curve):
    """Fractional return per day from a `run_config` equity curve.

    Curve rows are `(epoch_day, ts, equity)`, so the day has to be formatted the
    same way the engine's trade log is or the two series share no keys at all --
    which is exactly what a first run of this screen produced, silently: sixteen
    candidates each reporting zero overlapping days rather than an error.
    """
    out = {}
    for (_pday, _pts, previous), (_day, ts, current) in zip(curve, curve[1:]):
        if previous > 0:
            out[day_of(ts)] = current / previous - 1.0
    return out


def correlation(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 10:
        return None, len(keys)
    xs, ys = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (round(cov / (vx * vy), 3) if vx and vy else None), len(keys)


def incumbent_streams():
    """Daily returns of the three sleeves, as the shipped book sizes them."""
    book = px.run(None, px.FULL)
    split = {name: [] for name in INCUMBENTS}
    for trade in book["trades_log"]:
        split.setdefault(trade.get("s", "?"), []).append(trade)
    return {name: daily_returns_from_trades(split[name]) for name in INCUMBENTS}, book


def python_candidates(session="rth"):
    """Daily returns for every Maroy configuration in `maroy_intraday_momentum`."""
    from sandbox.research import maroy_intraday_momentum as maroy

    lo = int(datetime.strptime(px.FULL[0], "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.strptime(px.FULL[1], "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    sessions = maroy.load_sessions(session)
    out = {}
    for config in maroy.CONFIGS:
        try:
            result = maroy.run_config(sessions, config, session, None, (lo, hi))
        except Exception as error:              # noqa: BLE001 - reported, not raised
            out[config["name"]] = {"error": str(error)}
            continue
        curve = result.get("curve") or []
        out[config["name"]] = {
            "family": config["family"],
            "returns": daily_returns_from_curve(curve),
            "trades": result.get("trades", 0),
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="rth")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    report = {}
    incumbents, book = incumbent_streams()
    print(f"BOOK (shipped)  return {book['return_pct']:.2f}%  "
          f"maxDD {book['max_dd_pct']:.2f}%  sharpe {book['sharpe']:.2f}")

    print()
    print("=" * 100)
    print("CORRELATION SCREEN -- every Maroy configuration against the three incumbents")
    print("=" * 100)
    short = [n.replace("NQ ", "").replace("BTC ", "")[:12] for n in INCUMBENTS]
    print(f"  {'configuration':<38}{'in rust':>9}" + "".join(f"{s:>14}" for s in short)
          + f"{'mean |rho|':>12}{'days':>7}")

    rows = []
    candidates = python_candidates(args.session)
    for name, entry in candidates.items():
        if "error" in entry:
            print(f"  {name:<38}{'':>9}  failed: {entry['error'][:50]}")
            continue
        cors, days = [], 0
        cells = ""
        for incumbent in INCUMBENTS:
            rho, overlap = correlation(entry["returns"], incumbents[incumbent])
            days = max(days, overlap)
            cells += f"{rho if rho is not None else '-':>14}"
            if rho is not None:
                cors.append(abs(rho))
        mean_abs = round(statistics.fmean(cors), 3) if cors else None
        compiled = compiled_name(name, entry["family"])
        print(f"  {name:<38}{compiled or '-':>9}{cells}"
              f"{mean_abs if mean_abs is not None else '-':>12}{days:>7}")
        rows.append({"name": name, "family": entry["family"], "compiled": compiled,
                     "mean_abs_rho": mean_abs, "days": days,
                     "trades": entry["trades"]})
    report["screen"] = rows

    print()
    print("=" * 100)
    print("AS A FOURTH SLEEVE -- the compiled variants, run on the engine")
    print("=" * 100)
    print(f"  {'book':<44}{'return':>10}{'maxDD':>9}{'sharpe':>8}{'trades':>8}")
    base = px.run(None, px.FULL)
    print(f"  {'three sleeves (shipped)':<44}{base['return_pct']:9.2f}%"
          f"{base['max_dd_pct']:8.2f}%{base['sharpe']:8.2f}{base['trades']:8}")

    added = []
    for extra in COMPILED:
        members = (*INCUMBENTS, extra)
        result = px.run(None, px.FULL, sleeves=members)
        label = "+ " + extra
        print(f"  {label:<44}{result['return_pct']:9.2f}%"
              f"{result['max_dd_pct']:8.2f}%{result['sharpe']:8.2f}{result['trades']:8}")
        contribution = {row["strategy"]: row for row in result["contribution"]}
        added.append({
            "extra": extra,
            "return_pct": result["return_pct"],
            "max_dd_pct": result["max_dd_pct"],
            "sharpe": result["sharpe"],
            "trades": result["trades"],
            "contribution": result["contribution"],
            "extra_pnl": contribution.get(extra, {}).get("pnl"),
            "extra_dd": contribution.get(extra, {}).get("max_drawdown"),
        })
    report["four_sleeve"] = added

    # The decisive table. A fourth sleeve that only helps on 2025 has only
    # helped on the window the incumbents were themselves fitted on.
    print()
    print("=" * 100)
    print("BY WINDOW -- 2025 is in sample; 2026 is warmed on a continuous run and sliced")
    print("=" * 100)
    from sandbox.research import portfolio_warm_oos as warm

    print(f"  {'book':<30}{'FULL ret/DD':>21}{'2025 ret/DD':>21}{'2026 ret/DD':>21}")
    windows = []
    for label, members in [("three sleeves (shipped)", INCUMBENTS)] + [
        ("+ " + extra, (*INCUMBENTS, extra)) for extra in COMPILED
    ]:
        full = px.run(None, px.FULL, sleeves=members)
        in_sample = px.run(None, px.IS, sleeves=members)
        segment = warm.segment_stats(full)
        print(f"  {label:<30}{full['return_pct']:12.2f}%/{full['max_dd_pct']:6.2f}%"
              f"{in_sample['return_pct']:12.2f}%/{in_sample['max_dd_pct']:6.2f}%"
              f"{segment['return_pct']:12.2f}%/{segment['max_dd_pct']:6.2f}%")
        windows.append({"book": label, "full": {f: full[f] for f in px.FIELDS},
                        "is": {f: in_sample[f] for f in px.FIELDS},
                        "oos_warm": segment})
    report["by_window"] = windows

    print()
    print("  per-sleeve inside each four-sleeve book")
    for row in added:
        print(f"\n  + {row['extra']}")
        for entry in row["contribution"]:
            print(f"      {entry['strategy']:<28} pnl {entry['pnl']:9.2f}  "
                  f"share {entry['share_pct']:5.1f}%  ownDD {entry['max_drawdown']:6.2f}%  "
                  f"n={entry['trades']}")

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


def compiled_name(name, family):
    """Map a sandbox configuration onto its Rust strategy, when it has one."""
    if name == "Time only":
        return "Time"
    if family == "Ladder":
        return "Ladder"
    if family == "VWAP":
        return "VWAP"
    if family.startswith("Boundary with different exit") and "VWAP" not in family:
        return "Boundary"
    return None


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- #
# the fourth sleeve *under the overlay*
# --------------------------------------------------------------------------- #

def overlay_schedule(extra=None, extra_weight=1.0):
    """The shipped policy expressed as a schedule, optionally covering a fourth
    sleeve.

    The compiled policy in `sizing::book_exposure` only knows the three
    incumbents, so a fourth sleeve joins a normal run at its own compiled size --
    unthrottled through exactly the 2025 volatility that the overlay exists to
    stand down in. Comparing that against overlaid incumbents is not a fair test
    of the sleeve, it is a test of the overlay.

    `SLEEVE_EXPOSURE_SCHEDULE` takes precedence over the compiled policy, so this
    rebuilds the same rule in Python and extends it to the newcomer. Both arms of
    the comparison then run under a schedule, which is what makes them
    comparable.

    ONE FIDELITY NOTE, and it is why the absolute numbers here do not equal the
    shipped ones. The compiled policy folds the engine's own NQ stream, whose
    90-day preroll means warm-up is finished before the window opens. This
    schedule is built from the level-two table, which starts 2025-02-12, so its
    first thirty sessions sit at 1.0. Every arm below carries that identically,
    so the *comparison* is sound even though the level is a little different.
    """
    from sandbox.research import portfolio_price_vol as pv

    values = pv.multipliers(pv.daily_closes(), 12, 40, 3.0, 30)
    base = pv.schedule(values, sleeves=INCUMBENTS, cap=1.0)
    ladder = INCUMBENTS[2]
    out = {
        name: (
            {day: round(factor * (2.0 if name == ladder else 1.0), 4)
             for day, factor in points.items()}
            | {"default": 2.0 if name == ladder else 1.0}
        )
        for name, points in base.items()
    }
    if extra:
        out[extra] = {day: round(factor * extra_weight, 4)
                      for day, factor in next(iter(base.values())).items()}
        out[extra]["default"] = extra_weight
    return out


def overlay_comparison():
    """Every candidate at 1x and 2x *under the overlay*, against the same
    three-sleeve arm."""
    from sandbox.research import portfolio_warm_oos as warm

    print()
    print("=" * 100)
    print("UNDER THE OVERLAY -- the fourth sleeve volatility-targeted like the rest")
    print("=" * 100)
    print(f"  {'book':<34}{'FULL ret/DD':>21}{'2025 ret/DD':>21}{'2026 ret/DD':>21}")

    rows = []
    arms = [("three sleeves", INCUMBENTS, None, 1.0)]
    for extra in COMPILED:
        arms.append((f"+ {extra} x1", (*INCUMBENTS, extra), extra, 1.0))
        arms.append((f"+ {extra} x2", (*INCUMBENTS, extra), extra, 2.0))
    for label, members, extra, weight in arms:
        schedule = overlay_schedule(extra, weight)
        full = px.run(schedule, px.FULL, sleeves=members)
        in_sample = px.run(schedule, px.IS, sleeves=members)
        segment = warm.segment_stats(full)
        print(f"  {label:<34}{full['return_pct']:12.2f}%/{full['max_dd_pct']:6.2f}%"
              f"{in_sample['return_pct']:12.2f}%/{in_sample['max_dd_pct']:6.2f}%"
              f"{segment['return_pct']:12.2f}%/{segment['max_dd_pct']:6.2f}%")
        rows.append({"book": label, "extra": extra, "weight": weight,
                     "full": {f: full[f] for f in px.FIELDS},
                     "is": {f: in_sample[f] for f in px.FIELDS},
                     "oos_warm": segment,
                     "contribution": full["contribution"]})
    return rows


if os.environ.get("FOURTH_SLEEVE_OVERLAY"):
    rows = overlay_comparison()
    with open(os.path.join(os.path.dirname(__file__),
                           "portfolio_fourth_sleeve_overlay.json"), "w") as handle:
        json.dump(rows, handle, indent=1)
