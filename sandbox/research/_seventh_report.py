"""Score one membership on live execution, per era, off the frontier cache.

WHY THIS AND NOT `build`. `build` regenerates every sleeve's trades before it
replays them, which is thirty minutes a membership and makes comparing two books
a morning's work. The frontier cache already holds those logs -- generated once,
at canon settings, on live execution -- so a book here is a `replay` and nothing
else, and two memberships are seconds apart.

IT IS NOT A SECOND MODEL. `_replay` is `_mc_live_frontier`'s, which is
`exness_combined_strategies.replay` with the frontier's shared marking cache;
the numbers are the ones `build` would print for the same window.

THREE ERAS, EACH FROM ITS OWN `CANON_INITIAL`. `is` ends where the parameter search's data
ended, so it is fitted and `oos` is not. They are NOT chained: a chained second
era starts from whatever equity the first left, which lets a cell that only
worked early flatter the whole line ([[cold-start-oos-fakes-regime-edges]]).

Per-sleeve detail is printed for every era, always
([[show-per-sleeve-detail-every-time]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_report
    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_report \
        --drop a:b --add c:d --risk 0.135
"""
from __future__ import annotations

import argparse
import pickle

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs


def _load():
    with open(fr.cache_path(), "rb") as handle:
        return pickle.load(handle)


def _drawdown(marked):
    peak, worst = None, 0.0
    for _ts, value in marked:
        peak = value if peak is None else max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return 100.0 * worst


def era_row(state, members, lo, hi, risk_scale):
    fr._STATE = state
    book = fr._replay(members, lo=lo, hi=hi, risk_scale=risk_scale)
    series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                  ecs.CANON_INITIAL)
    worst = min((r["return_pct"] for r in series.values()), default=0.0)
    return book, {"return_pct": book["return_pct"],
                  "mtm_dd_pct": book["mtm_dd_pct"],
                  "closed_dd_pct": book["max_dd_pct"],
                  "final": book["final"], "trades": book["trades"],
                  "monthly_sharpe": sharpe, "positive": positive,
                  "months": total, "worst_month": worst,
                  "below_min": sum(book["below_broker_minimum"].values())}


def report(drop, add, risk_scale):
    state = _load()
    fr._STATE = state
    keys = [k for k in ecs.BOOK if k not in set(drop)] + list(add)
    missing = [k for k in keys if k not in state["members"]]
    if missing:
        raise SystemExit(f"not in the frontier cache: {', '.join(missing)}")
    members = [state["members"][k] for k in keys]
    mc.pin_external_window(live.window())

    print(f"{len(keys)} sleeves   risk_scale {risk_scale}   "
          f"${ecs.CANON_INITIAL:,.0f}   live execution")
    print(f"  dropped: {', '.join(drop) or '-'}")
    print(f"  added:   {', '.join(add) or '-'}")

    books = {}
    print(f"\n{'era':7}{'window':26}{'return':>11}{'MTM dd':>9}"
          f"{'clsd dd':>9}{'final':>11}{'trades':>8}{'mSharpe':>9}"
          f"{'pos mo':>9}{'worst mo':>10}")
    for era in ("is", "oos", "full"):
        lo, hi = fr._bounds(era)
        book, row = era_row(state, members, lo, hi, risk_scale)
        books[era] = (book, row)
        span = f"{fr.ERAS[era][0]}..{fr.ERAS[era][1]}"
        print(f"{era:7}{span:26}{row['return_pct']:>+10.1f}%"
              f"{row['mtm_dd_pct']:>8.1f}%{row['closed_dd_pct']:>8.1f}%"
              f"{row['final']:>11,.0f}{row['trades']:>8,}"
              f"{row['monthly_sharpe']:>9.2f}"
              f"{row['positive']:>5}/{row['months']:<3}"
              f"{row['worst_month']:>+9.1f}%")

    for era in ("is", "oos"):
        book, _row = books[era]
        print(f"\n  {era.upper()} per sleeve"
              f"{'':6}{'P&L $':>10}{'share':>8}{'MTM dd$':>10}"
              f"{'ddEvt%':>9}{'n':>7}")
        rows = book["by_sleeve"]
        total = sum(max(r["pnl"], 0.0) for r in rows.values()) or 1.0
        for name in sorted(rows, key=lambda k: -rows[k]["pnl"]):
            row = rows[name]
            print(f"  {name:30}{row['pnl']:>10,.0f}"
                  f"{100 * row['pnl'] / total:>7.1f}%"
                  f"{row.get('mtm_dd_usd', 0.0):>10,.0f}"
                  f"{100 * (row.get('dd_event_share') or 0.0):>8.1f}%"
                  f"{row.get('trades', 0):>7,}")

    book, _row = books["full"]
    series, _s, _p, _t = ecs.monthly(book["settled"], ecs.CANON_INITIAL)
    losing = [(k, v["return_pct"]) for k, v in series.items()
              if v["return_pct"] < 0]
    print(f"\n  FULL window losing months: {len(losing)} of {len(series)}")
    for key, value in losing:
        print(f"    {key}  {value:>+7.2f}%")
    return books


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", default="")
    parser.add_argument("--add", default="")
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    live.arm()
    split = lambda s: tuple(x for x in s.replace(" ", "").split(",") if x)
    report(split(args.drop), split(args.add), args.risk)


if __name__ == "__main__":
    main()
