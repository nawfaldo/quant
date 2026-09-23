"""Rank candidates on the BOOK's losing months and its underwater stretches.

WHY NOT STANDALONE RETURN, AND WHY NOT `loss_lift`. A sleeve that earns a lot on
average can still earn it in the months the book was already winning, which adds
return and does nothing for consistency. `loss_lift` misranked dampers twice
before ([[screen-dampers-on-losing-months]]). What actually shortens a drawdown
is a sleeve that makes money in the specific months this book lost money, so
that is what is measured here directly.

TWO SCORES, BECAUSE THEY ARE DIFFERENT COMPLAINTS.

  `lossR`   the candidate's R banked inside the book's LOSING months. Raises the
            positive-month count by turning a small loss into a small gain.
  `uwR`     its R banked while the book was UNDERWATER -- below its running peak,
            which is a longer and more continuous span than a losing calendar
            month. Shortens time-to-recovery rather than month count.

A sleeve can score well on one and badly on the other: a monthly boundary is an
accounting artefact and a drawdown is not.

R is `gross / distance` after live-execution cost, so it is comparable across
sleeves and years regardless of sizing ([[min-lot-pinned-sleeves-do-not-compound]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_dampers
"""
from __future__ import annotations

import argparse
import math
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs


def month_of(ts):
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{d.year}-{d.month:02d}"


def stat(rs):
    n = len(rs)
    if n == 0:
        return 0, 0.0, 0.0
    m = sum(rs) / n
    return n, m, sum(rs)


def underwater_spans(marked):
    """`[(lo, hi)]` timestamp ranges where the marked curve is below its peak."""
    peak, start, spans = None, None, []
    for ts, value in marked:
        if peak is None or value >= peak:
            if start is not None:
                spans.append((start, ts))
                start = None
            peak = value if peak is None else max(peak, value)
        elif start is None:
            start = ts
    if start is not None:
        spans.append((start, marked[-1][0]))
    return spans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())

    lo, hi = fr._bounds("full")
    members = [state["members"][k] for k in ecs.BOOK]
    book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk)
    series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                  ecs.CANON_INITIAL)
    losing = {k for k, v in series.items() if v["return_pct"] < 0}
    spans = underwater_spans(book["marked"])
    uw_days = sum(b - a for a, b in spans) / 86_400.0
    print(f"BOOK {len(ecs.BOOK)} sleeves @ {args.risk}: {positive}/{total} "
          f"positive months, mSharpe {sharpe:.2f}, "
          f"MTM dd {book['mtm_dd_pct']:.2f}%, underwater {uw_days:.0f} days "
          f"in {len(spans)} spans")
    print("losing months: " + ", ".join(
        f"{k} {series[k]['return_pct']:+.2f}%" for k in sorted(losing)))

    def in_spans(ts):
        for a, b in spans:
            if a <= ts <= b:
                return True
        return False

    rows = []
    for key, log in state["logs"].items():
        loss_r, uw_r, all_r = [], [], []
        for t in log:
            if t.get("distance", 0) <= 0 or not (lo <= t["exit_ts"] < hi):
                continue
            r = t["gross"] / t["distance"]
            all_r.append(r)
            if month_of(t["exit_ts"]) in losing:
                loss_r.append(r)
            if in_spans(t["exit_ts"]):
                uw_r.append(r)
        if not all_r:
            continue
        n_l, m_l, s_l = stat(loss_r)
        n_u, m_u, s_u = stat(uw_r)
        rows.append({"key": key, "held": key in ecs.BOOK,
                     "n_loss": n_l, "lossR": s_l, "mean_loss": m_l,
                     "n_uw": n_u, "uwR": s_u, "mean_uw": m_u,
                     "allR": sum(all_r)})

    print(f"\nCANDIDATES ranked on the book's losing months "
          f"(not held; positive on BOTH scores)")
    print(f"  {'sleeve':30}{'lossR':>9}{'n':>5}{'meanR':>8}"
          f"{'uwR':>9}{'n':>5}{'meanR':>8}{'allR':>9}")
    cand = [r for r in rows if not r["held"]
            and r["lossR"] > 0 and r["uwR"] > 0]
    cand.sort(key=lambda r: -(r["lossR"] + r["uwR"]))
    for r in cand[:args.top]:
        print(f"  {r['key']:30}{r['lossR']:>9.1f}{r['n_loss']:>5}"
              f"{r['mean_loss']:>8.3f}{r['uwR']:>9.1f}{r['n_uw']:>5}"
              f"{r['mean_uw']:>8.3f}{r['allR']:>9.1f}")

    print(f"\nHELD sleeves, same scores -- negative lossR is a sleeve that "
          f"deepens the book's bad months")
    print(f"  {'sleeve':30}{'lossR':>9}{'n':>5}{'meanR':>8}"
          f"{'uwR':>9}{'n':>5}{'meanR':>8}{'allR':>9}")
    for r in sorted((r for r in rows if r["held"]), key=lambda r: r["lossR"]):
        print(f"  {r['key']:30}{r['lossR']:>9.1f}{r['n_loss']:>5}"
              f"{r['mean_loss']:>8.3f}{r['uwR']:>9.1f}{r['n_uw']:>5}"
              f"{r['mean_uw']:>8.3f}{r['allR']:>9.1f}")


if __name__ == "__main__":
    main()
