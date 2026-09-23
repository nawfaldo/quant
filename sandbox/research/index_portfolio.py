"""Five index sleeves sharing one USD 1,000 account, simulated jointly.

The five are the holdout's best cells by return at a realistic spread:
``aus200 momentum``, ``fr40 gap``, ``de40 gap``, ``hk50 gap`` and ``aus200 pdr``.

NO PER-SLEEVE CAP, as requested.  Every sleeve sizes its own trades off the
*shared* balance at the full 1.5% stop risk, so five simultaneous positions put
7.5% of the account at risk rather than 1.5%.  The only thing that binds is the
account-level margin ceiling: a sleeve cannot open a position the account's free
margin will not carry.  That is what makes this different from five separate
accounts, and it is deliberately the only brake left in the model.

WHY A JOINT LOOP AND NOT FIVE SUMMED RETURN STREAMS.  Adding daily P&L series
together hid half the drawdown last time ([[blend-model-understates-portfolio-drawdown]])
because it nets sleeves that are simultaneously under water and it never sees
open risk.  Here the sleeves step through one merged timeline, equity is marked
to market on every bar of every symbol, and the drawdown is taken from that mark
([[engine-drawdown-is-mark-to-market]]).  A summed-stream figure is printed
alongside purely to show the size of the gap.

Timestamps: each sleeve's bars carry its instrument's clock shift, so the merged
timeline is ordered on ``ts - shift`` (real time) while every signal keeps
reading the shifted clock it was selected on.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import index_families_research as ix
from sandbox.research.usoil_families_research import exit_plan, random_side

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
TS, O, H, L, C, V = range(6)

SLEEVES = [("aus200", "momentum"), ("fr40", "gap"), ("de40", "gap"),
           ("hk50", "gap"), ("aus200", "pdr")]

REAL_BP = {"aus200": 0.77, "de40": 0.54, "fr40": 0.79, "hk50": 2.26,
           "stoxx50": 1.22, "uk100": 0.70, "jp225": 1.56}

WINDOWS = {
    "in_sample_2020_2024": (int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()),
                            int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())),
    "holdout_2025_2026": (int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()),
                          int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())),
    "untouched_2018_2019": (int(datetime(2018, 3, 1, tzinfo=timezone.utc).timestamp()),
                            int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())),
}


def load(sleeves):
    out = []
    for symbol, family in sleeves:
        path = os.path.join(RESULTS, f"index_families_{symbol}.json")
        params = json.load(open(path, encoding="utf-8"))["families"][family]["params"]
        bars, ctx = ix.context(symbol, "validate")
        out.append({"symbol": symbol, "family": family, "params": params,
                    "bars": bars, "ctx": ctx, "shift": ctx["shift"],
                    "position": None, "pending": None, "traded_day": None,
                    "state": {}, "realized": 0.0, "trades": []})
    return out


def timeline(sleeves, lo, hi):
    events = []
    for index, sleeve in enumerate(sleeves):
        shift = sleeve["shift"]
        for i, bar in enumerate(sleeve["bars"]):
            real = bar[TS] - shift
            if lo <= real < hi:
                events.append((real, index, i))
    events.sort()
    return events


def run(sleeves, lo, hi, initial=1_000.0, cost="real", null_seed=None,
        per_sleeve_risk=ix.RISK_FRACTION):
    """One shared account. Returns portfolio stats and per-sleeve attribution."""
    for s in sleeves:
        s.update(position=None, pending=None, traded_day=None, state={},
                 realized=0.0, trades=[])
    balance = initial
    last_close = {}
    peak_equity = initial
    max_dd = 0.0
    equity_curve = []
    margin_blocked = 0

    def open_margin():
        total = 0.0
        for s in sleeves:
            p = s["position"]
            if p is not None:
                price = last_close.get(s["symbol"], p["entry"])
                total += p["lots"] * price * s["ctx"]["cfg"]["multiplier"] * ix.MARGIN_FRACTION
        return total

    def unrealized():
        total = 0.0
        for s in sleeves:
            p = s["position"]
            if p is not None:
                price = last_close.get(s["symbol"], p["entry"])
                total += p["side"] * (price - p["entry"]) * p["lots"] * s["ctx"]["cfg"]["multiplier"]
        return total

    for real, index, i in timeline(sleeves, lo, hi):
        s = sleeves[index]
        bars, ctx, params = s["bars"], s["ctx"], s["params"]
        cfg = ctx["cfg"]
        bar = bars[i]
        ts = bar[TS]
        opened, closed = cfg["session"]
        day, minute = ts // 86_400, ts % 86_400 // 60
        last_close[s["symbol"]] = bar[C]
        spread_bp = REAL_BP[s["symbol"]] if cost == "real" else 0.0
        spread_pts = 0.0 if cost == "real" else ix.SPREAD_POINTS[s["symbol"]]

        position = s["position"]
        if position is not None:
            side, price, reason = position["side"], None, None
            if minute >= closed:
                price, reason = bar[O], "session"
            else:
                stop = position["stop"]
                if (side == 1 and bar[L] <= stop) or (side == -1 and bar[H] >= stop):
                    price, reason = (min(bar[O], stop) if side == 1 else max(bar[O], stop)), "stop"
                elif position["target"] is not None:
                    target = position["target"]
                    if (side == 1 and bar[H] >= target) or (side == -1 and bar[L] <= target):
                        price, reason = (max(bar[O], target) if side == 1 else min(bar[O], target)), "target"
                if price is None and position["max_bars"] is not None \
                        and i - position["index"] >= position["max_bars"]:
                    price, reason = bar[O], "time"
            if price is None and position["trail"] is not None and ctx["atr"][i]:
                atr = ctx["atr"][i]
                if side == 1:
                    position["best"] = max(position["best"], bar[C])
                    position["stop"] = max(position["stop"], position["best"] - position["trail"] * atr)
                else:
                    position["best"] = min(position["best"], bar[C])
                    position["stop"] = min(position["stop"], position["best"] + position["trail"] * atr)
            elif price is not None:
                gross = side * (price - position["entry"])
                points = gross - ix.cost_price(cfg, position["entry"], spread_pts, spread_bp)
                pnl = points * position["lots"] * cfg["multiplier"]
                balance += pnl
                s["realized"] += pnl
                s["trades"].append({"entry_ts": position["ts"] - s["shift"], "exit_ts": real,
                                    "pnl": pnl, "side": side, "reason": reason,
                                    "lots": position["lots"]})
                s["position"] = position = None

        if s["position"] is None and s["pending"] is not None:
            pending = s["pending"]
            if day == pending["day"] and minute < closed:
                equity_now = balance + unrealized()
                free = max(0.0, equity_now - open_margin())
                lots = ix.quantity(balance, bar[O], pending["distance"],
                                   pending["realized"], ctx)
                margin_lot = bar[O] * cfg["multiplier"] * ix.MARGIN_FRACTION
                affordable = free / margin_lot if margin_lot > 0 else 0.0
                step = cfg["volume_step"]
                lots = min(lots, math.floor(affordable / step + 1e-10) * step)
                if lots + 1e-10 >= cfg["volume_min"]:
                    target, max_bars, trail = exit_plan(params["exit_mode"], pending["distance"])
                    side = pending["side"]
                    s["position"] = {"side": side, "entry": bar[O], "ts": ts, "index": i,
                                     "lots": round(lots, 8), "stop": bar[O] - side * pending["distance"],
                                     "target": None if target is None else bar[O] + side * target,
                                     "max_bars": max_bars, "trail": trail, "best": bar[O]}
                    s["traded_day"] = day
                else:
                    margin_blocked += 1
            s["pending"] = None

        if s["position"] is None and s["pending"] is None and s["traded_day"] != day \
                and opened <= minute < closed and ix.accepts_vol(ctx, i, params["vol_mode"]):
            side = ix.SIGNALS[s["family"]](i, bars, ctx, params, s["state"])
            if side is not None and null_seed is not None:
                side = random_side(ts, null_seed + 100 * index)
            atr, realized_vol = ctx["atr"][i], ctx["volatility"][i]
            if side is not None and atr and realized_vol is not None \
                    and ix.accepts_trend(bar[C], ctx, i, side, params["trend"]):
                s["pending"] = {"side": side, "day": day, "atr": atr,
                                "distance": params["stop_atr"] * atr,
                                "realized": realized_vol}

        equity = balance + unrealized()
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, (peak_equity - equity) / peak_equity if peak_equity > 0 else 1.0)
        equity_curve.append((real, equity))

    final = balance + unrealized()
    trades = [t for s in sleeves for t in s["trades"]]
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    months = {}
    for t in trades:
        m = datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc)
        months[f"{m.year}-{m.month:02d}"] = months.get(f"{m.year}-{m.month:02d}", 0.0) + t["pnl"]
    monthly = list(months.values())
    mean = statistics.fmean(monthly) if monthly else 0.0
    sd = statistics.pstdev(monthly) if len(monthly) > 1 else 0.0
    return {
        "final": round(final, 2),
        "return_pct": round(100.0 * (final - initial) / initial, 2),
        "max_dd_pct": round(100.0 * max_dd, 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "monthly_sharpe": round(mean / sd, 3) if sd else 0.0,
        "positive_months": sum(1 for v in monthly if v > 0),
        "n_months": len(monthly),
        "margin_blocked_entries": margin_blocked,
        "attribution": {f"{s['symbol']}_{s['family']}":
                        {"pnl": round(s["realized"], 2), "trades": len(s["trades"])}
                        for s in sleeves},
        "equity_curve": equity_curve,
    }


def standalone(symbol, family, params, lo, hi, initial, cost):
    bars, ctx = ix.context(symbol, "validate")
    kw = ({"spread_bp": REAL_BP[symbol]} if cost == "real"
          else {"spread_points": ix.SPREAD_POINTS[symbol]})
    return ix.backtest(family, bars, ctx, params, lo=lo, hi=hi, initial=initial, **kw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", choices=("real", "requested"), default="real")
    parser.add_argument("--balance", type=float, default=1_000.0)
    parser.add_argument("--windows", nargs="+", default=list(WINDOWS))
    args = parser.parse_args()

    sleeves = load(SLEEVES)
    print(f"five sleeves, one USD {args.balance:,.0f} account, no per-sleeve cap, "
          f"{args.cost} costs\n")
    for name in args.windows:
        lo, hi = WINDOWS[name]
        joint = run(sleeves, lo, hi, initial=args.balance, cost=args.cost)
        print(f"== {name}")
        print(f"   portfolio   {joint['return_pct']:+8.1f}%  dd {joint['max_dd_pct']:5.1f}%  "
              f"n={joint['trades']:4d}  PF {joint['pf']:.2f}  mSharpe {joint['monthly_sharpe']:+.2f}  "
              f"months {joint['positive_months']}/{joint['n_months']}  "
              f"margin-blocked {joint['margin_blocked_entries']}")
        alone = {}
        for symbol, family in SLEEVES:
            params = json.load(open(os.path.join(RESULTS, f"index_families_{symbol}.json"),
                                    encoding="utf-8"))["families"][family]["params"]
            r = standalone(symbol, family, params, lo, hi, args.balance, args.cost)
            alone[f"{symbol}_{family}"] = r
        print(f"   {'sleeve':<20} {'alone@1k':>9} {'alone dd':>9} {'in book':>9} {'trades':>7}")
        for key, r in alone.items():
            share = joint["attribution"][key]
            print(f"   {key:<20} {r['return_pct']:+9.1f} {r['max_dd_pct']:9.1f} "
                  f"{100*share['pnl']/args.balance:+9.1f} {share['trades']:7d}")
        naive = sum(r["return_pct"] for r in alone.values())
        worst = max(r["max_dd_pct"] for r in alone.values())
        print(f"   naive sum of standalone returns {naive:+.1f}%  "
              f"(worst single-sleeve dd {worst:.1f}%)")
        print(f"   joint result                    {joint['return_pct']:+.1f}%  "
              f"dd {joint['max_dd_pct']:.1f}%\n")

        flips = [run(sleeves, lo, hi, initial=args.balance, cost=args.cost, null_seed=s)
                 for s in (1, 2, 3, 4, 5)]
        rets = sorted(round(f["return_pct"], 1) for f in flips)
        dds = sorted(round(f["max_dd_pct"], 1) for f in flips)
        print(f"   coin-flip book (5 seeds)  returns {rets}  dds {dds}")
        print(f"   margin over null max      {joint['return_pct']-max(rets):+.1f}\n")


if __name__ == "__main__":
    main()
