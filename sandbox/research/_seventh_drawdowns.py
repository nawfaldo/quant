"""Every drawdown episode the book actually had: depth, dates, duration.

A percentile ladder says how deep the tail is and nothing about WHEN. This walks
the marked equity curve and cuts it into episodes -- peak, trough, recovery --
so each fall can be dated, measured and counted.

MARKED, NOT CLOSED. The curve is `replay`'s mark-to-market series, so an episode
includes open positions rather than only booked trades; the closed figure is the
optimistic one on an intraday book ([[engine-drawdown-is-mark-to-market]]).

AN EPISODE IS PEAK -> TROUGH -> RECOVERY TO THAT PEAK. An episode still under
water at the end of the window is reported as unrecovered rather than closed at
the last bar, because its true depth and length are not yet known.

"Average drawdown" is reported two ways and they answer different questions:
the mean depth ACROSS EPISODES (how bad is a typical fall), and the mean of the
underwater series (how far below the high-water mark the account sits on an
average day, counting the zeros when it is at a high).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_drawdowns \
        --min-depth 3
"""
from __future__ import annotations

import argparse
import pickle
import statistics
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def episodes(marked):
    """`[{peak_ts, trough_ts, end_ts, depth_pct, ...}]`, deepest first."""
    out = []
    peak_v = peak_ts = None
    trough_v = trough_ts = None
    for ts, value in marked:
        if peak_v is None or value >= peak_v:
            if trough_v is not None and peak_v > 0:
                out.append({"peak_ts": peak_ts, "trough_ts": trough_ts,
                            "end_ts": ts, "peak": peak_v, "trough": trough_v,
                            "depth": 100.0 * (peak_v - trough_v) / peak_v,
                            "recovered": True})
            peak_v, peak_ts = value, ts
            trough_v = trough_ts = None
        elif trough_v is None or value < trough_v:
            trough_v, trough_ts = value, ts
    if trough_v is not None and peak_v and peak_v > 0:
        out.append({"peak_ts": peak_ts, "trough_ts": trough_ts,
                    "end_ts": marked[-1][0], "peak": peak_v,
                    "trough": trough_v,
                    "depth": 100.0 * (peak_v - trough_v) / peak_v,
                    "recovered": False})
    out.sort(key=lambda e: -e["depth"])
    return out


def underwater_mean(marked):
    peak, total = None, []
    for _ts, value in marked:
        peak = value if peak is None or value > peak else peak
        total.append(100.0 * (peak - value) / peak if peak > 0 else 0.0)
    return statistics.fmean(total) if total else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-depth", type=float, default=3.0)
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--sealed", action="store_true",
                        help="constant spread and vendor opens instead of the "
                             "broker maps; the frontier cache's LOGS are still "
                             "live-priced, so this only removes the replay-side "
                             "correction and is NOT a full sealed book -- run "
                             "`_seventh_sealed_live` for that")
    args = parser.parse_args()
    live.arm()
    if args.sealed:
        ecs.TICK_COSTS = False
        ecs.MAPS_OVERRIDE = None
        ecs._TICK_COST_CACHE.clear()
        mc._MARKING.clear()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    members = [state["members"][k] for k in ecs.BOOK]

    for name, lo_text, hi_text in (("FULL 2022-01 .. 2026-08",
                                    "2022-01-01", "2026-08-21"),
                                   ("OOS 2025-01 .. 2026-08",
                                    "2025-01-01", "2026-08-21")):
        lo, hi = stamp(lo_text), stamp(hi_text)
        book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk)
        marked = book["marked"]
        eps = episodes(marked)
        deep = [e for e in eps if e["depth"] >= args.min_depth]
        depths = [e["depth"] for e in eps]

        print(f"\n{'=' * 92}")
        print(f"{name}   {len(ecs.BOOK)} sleeves   risk {args.risk}   "
              f"${ecs.CANON_INITIAL:,.0f}   live execution")
        print(f"{'=' * 92}")
        print(f"  max MTM drawdown        {book['mtm_dd_pct']:.2f}%")
        print(f"  episodes                {len(eps):,} total, "
              f"{len(deep)} at or over {args.min_depth:.0f}%")
        print(f"  mean depth, all         {statistics.fmean(depths):.2f}%")
        print(f"  mean depth, >= {args.min_depth:.0f}%       "
              f"{statistics.fmean([e['depth'] for e in deep]):.2f}%"
              if deep else "")
        print(f"  median depth, >= {args.min_depth:.0f}%     "
              f"{statistics.median([e['depth'] for e in deep]):.2f}%"
              if deep else "")
        print(f"  mean underwater level   {underwater_mean(marked):.2f}%"
              f"   (average distance below the high-water mark, "
              f"counting days at a high)")

        print(f"\n  {'#':>3}  {'depth':>7}  {'peak':>12}  {'trough':>12}"
              f"  {'recovered':>12}  {'fall':>6}  {'back':>6}  {'equity $'}")
        for index, e in enumerate(deep, start=1):
            fall = (e["trough_ts"] - e["peak_ts"]) / 86_400.0
            back = ((e["end_ts"] - e["trough_ts"]) / 86_400.0
                    if e["recovered"] else float("nan"))
            back_text = f"{back:>5.0f}d" if e["recovered"] else "  none"
            print(f"  {index:>3}  {e['depth']:>6.2f}%  {day(e['peak_ts']):>12}"
                  f"  {day(e['trough_ts']):>12}"
                  f"  {(day(e['end_ts']) if e['recovered'] else 'UNRECOVERED'):>12}"
                  f"  {fall:>5.0f}d  {back_text}"
                  f"  {e['peak']:>9,.0f} -> {e['trough']:,.0f}")


if __name__ == "__main__":
    main()
