"""Session-VWAP band sweep-and-reclaim scalp, priced on the Pro CFD account.

Runs on either index: ``--symbol es`` (US500 / ``es_1m``) or ``--symbol nq``
(USTEC / ``nq_1m``). ES was tested first and has NO gross edge -- 144 cells all
negative, and the fitted cell still loses out of sample at zero cost -- so the
NQ run is the same rule on a different tape, not a rescue attempt.

The rule is the one described in IQCapital's "$150M VWAP strategy" interview
(https://www.youtube.com/watch?v=XWJlBBikUc0).  The video itself carries no
written rule set -- YouTube serves no transcript and the description is promo
copy -- so the mechanic below is the operator's reading of it: price wicks
through a session-VWAP band (the "manipulation" / stop run), closes back inside
it (the reclaim), and the trade is taken with the band's own VWAP as target.

    vwap[i]   = session cum(typical * volume) / cum(volume)
    sd[i]     = session population stdev of (close - vwap), causal
    lower[i]  = vwap[i] - k * sd[i]      upper[i] = vwap[i] + k * sd[i]

    LONG  when low[i]  < lower[i] and close[i] > lower[i]
    SHORT when high[i] > upper[i] and close[i] < upper[i]

Entry is bar i+1's OPEN.  A signal built from bar i's close cannot fill at bar
i's open; doing so on this engine once fabricated +12 points a trade.

Exit is the live session VWAP as target, a daily-ATR-fraction stop, and a forced
flatten on the session's last bar.  When a bar spans both the stop and the
target the stop is assumed, because one-minute bars cannot order the two.

CLOCK.  Both tables are New York wall-clock: in each the only fully absent hour
is 17, which is the 17:00-18:00 ET CME break.  RTH is therefore minutes 570-960,
NOT the 510/900 the older ES modules in this directory still use.

PRICES.  The series is ratio back-adjusted, so 2008 quotes 907 where ES traded
near 900 in real terms.  Ratio adjustment preserves *returns*, which is why cost
is charged in basis points of the bar price rather than in fixed points: a fixed
0.20-point charge would be a different real cost in every year of the sample.

COST.  ``combined_book.PRO_SPREAD_BP`` has no ES/US500 row, so the Pro account's
measured in-session index spread for NQ -- 0.300 bp of notional, no commission,
account 416209807 -- stands in for it.  Charged wholly at entry, as everywhere
else in this sandbox, plus ``combined_book.SLIPPAGE_POINTS``.

CONTROLS.  Two, both mandatory here.  A coin-flip null takes the identical entry
timestamps and sizes with a random direction, because random-direction searches
on this engine have scored +622% at t=4.19.  A buy-and-hold leg is scored over
the same window, because three past survivors beat their null and still lost to
simply holding the instrument.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import itertools
import json
import math
import os
import random
import statistics

from sandbox import data
from sandbox.research import combined_book as book
from sandbox.research import es_noise_momentum_research as base

def output_path(symbol):
    return os.path.join(os.path.dirname(__file__),
                        f"{symbol}_vwap_sweep_reclaim_result.json")


#: New York wall clock. 09:30 and 16:00. BOTH tables are NY: in each one the
#: only fully absent hour is 17, which is the 17:00-18:00 ET CME break.
SESSION_OPEN_MINUTE = 9 * 60 + 30
SESSION_CLOSE_MINUTE = 16 * 60
#: A session must be near-complete to be tradeable; holidays run short.
MIN_SESSION_BARS = 300

INITIAL = base.INITIAL
RISK_FRACTION = 0.01
ATR_LOOKBACK = 14

#: The two contracts are NOT interchangeable, and margin is where they differ
#: most: Exness quotes US500 at 0.25% (400x) and USTEC at 25% (4x). Sizing takes
#: the smaller of the risk leg and the margin leg, so on NQ the margin leg can
#: bind where on ES it never does.
#:
#: `spread_bp` is the Pro account's measured in-session median (416209807, no
#: commission). NQ's is measured on NQ itself; ES has no row in
#: `combined_book.PRO_SPREAD_BP`, so NQ's figure stands in for it.
INSTRUMENTS = {
    "es": {"table": "es_1m", "contract": "Exness US500 CFD",
           "spread_bp": book.PRO_SPREAD_BP["nq"], "spread_bp_measured_on": "nq (proxy)",
           "point_value": 1.0, "margin": 0.0025, "step": 0.01, "min_qty": 0.03},
    "nq": {"table": "nq_1m", "contract": "Exness USTEC CFD",
           "spread_bp": book.PRO_SPREAD_BP["nq"], "spread_bp_measured_on": "nq",
           "point_value": 1.0, "margin": 0.25, "step": 0.01, "min_qty": 0.01},
}

#: 2008-2017 is left out. It is the most heavily back-adjusted end of the series
#: and it predates the volume profile the reclaim rule reads.
WARMUP_START = "2017-01-01"
IS = ("2018-01-01", "2024-01-01")
OOS = ("2024-01-01", "2026-07-01")

TS, O, H, L, C, V = range(6)

AXES = {
    "band_k": (1.5, 2.0, 2.5, 3.0),
    "stop_atr": (0.10, 0.15, 0.25),
    #: Minutes of session that must have elapsed before a band is trusted. The
    #: first prints have almost no dispersion, so sd is meaninglessly small.
    "warmup_minutes": (15, 30),
    "max_trades_per_session": (1, 2),
    "side_mode": ("both", "long", "short"),
}


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def bars_1m(symbol):
    """RTH one-minute bars, New York clock, cached against the table itself."""
    table = INSTRUMENTS[symbol]["table"]
    key = f"{symbol}_vwap|{WARMUP_START}|{data._table_fingerprint([table])}"

    def build():
        rows = data.query(
            "SELECT cast(timestamp as long) ts,open,high,low,close,volume "
            f"FROM {table} WHERE timestamp >= '{WARMUP_START}' "
            f"AND ((hour(timestamp)={SESSION_OPEN_MINUTE // 60} "
            f"AND minute(timestamp)>={SESSION_OPEN_MINUTE % 60}) "
            f"OR (hour(timestamp)>{SESSION_OPEN_MINUTE // 60} "
            f"AND hour(timestamp)<{SESSION_CLOSE_MINUTE // 60})) "
            "ORDER BY timestamp"
        )
        return [[int(r[0]) // 1_000_000, *(float(v) for v in r[1:])] for r in rows]

    return [tuple(row) for row in data._cached(f"{symbol}_vwap_1m", key, build)]


def sessions(bars):
    """Group into RTH days and attach a causal daily ATR.

    ATR is the prior sessions' RTH true range, so the size of a trade never
    reads its own day's range.
    """
    grouped = {}
    for bar in bars:
        minute = bar[TS] % 86_400 // 60
        if SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE:
            grouped.setdefault(bar[TS] // 86_400, []).append(bar)

    out = []
    ranges = []
    previous_close = None
    for day in sorted(grouped):
        day_bars = sorted(grouped[day])
        if len(day_bars) < MIN_SESSION_BARS:
            continue
        high = max(bar[H] for bar in day_bars)
        low = min(bar[L] for bar in day_bars)
        atr = statistics.fmean(ranges[-ATR_LOOKBACK:]) if len(ranges) >= ATR_LOOKBACK else None
        # Bands are parameter-free, so they are built once here rather than
        # 144 times inside the grid: the sweep is 84M bar visits otherwise.
        out.append({"day": day, "bars": tuple(day_bars), "atr": atr,
                    "bands": vwap_bands(day_bars)})
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close),
                             abs(low - previous_close))
        ranges.append(true_range)
        previous_close = day_bars[-1][C]
    return out


def vwap_bands(day_bars):
    """Causal (vwap, sd) per bar. Both are computed from bars 0..i inclusive."""
    cum_pv = cum_v = 0.0
    sum_d = sum_d2 = 0.0
    out = []
    for index, bar in enumerate(day_bars):
        volume = max(bar[V], 0.0)
        typical = (bar[H] + bar[L] + bar[C]) / 3.0
        cum_pv += typical * volume
        cum_v += volume
        # A zero-volume minute leaves VWAP where it was rather than undefined.
        vwap = cum_pv / cum_v if cum_v > 0.0 else (out[-1][0] if out else bar[C])
        deviation = bar[C] - vwap
        sum_d += deviation
        sum_d2 += deviation * deviation
        n = index + 1
        variance = max(0.0, sum_d2 / n - (sum_d / n) ** 2)
        out.append((vwap, math.sqrt(variance)))
    return out


# --------------------------------------------------------------------------- #
# strategy
# --------------------------------------------------------------------------- #
def signals(session, params):
    """(entry_index, side) pairs. `entry_index` is the bar the fill happens on."""
    day_bars = session["bars"]
    bands = session["bands"]
    open_minute = day_bars[0][TS] % 86_400 // 60
    found = []
    for i, bar in enumerate(day_bars[:-1]):
        minute = bar[TS] % 86_400 // 60
        if minute - open_minute < params["warmup_minutes"]:
            continue
        vwap, sd = bands[i]
        if sd <= 0.0:
            continue
        lower, upper = vwap - params["band_k"] * sd, vwap + params["band_k"] * sd
        side = None
        if bar[L] < lower and bar[C] > lower:
            side = 1
        elif bar[H] > upper and bar[C] < upper:
            side = -1
        if side is None or not base.accepts_side(side, params["side_mode"]):
            continue
        found.append((i + 1, side))
    return found


#: Entries depend only on the band, the warmup and the side filter, so the
#: 144-cell grid contains just 24 distinct signal sets and the 400 null draws
#: contain none of their own. Memoised on those three axes.
_SIGNALS = {}


def signals_for(session_list, params):
    """`{id(session): [(entry_index, side), ...]}`, built once per signal cell."""
    key = (params["band_k"], params["warmup_minutes"], params["side_mode"],
           id(session_list))
    if key not in _SIGNALS:
        _SIGNALS[key] = {id(s): signals(s, params) for s in session_list}
    return _SIGNALS[key]


def quantity(equity, price, stop_distance, risk_fraction, cost, inst):
    """Lots under `inst`'s own contract constraints.

    Two independent caps, and which one binds is the difference between the
    instruments: the risk leg sizes the stop to `risk_fraction` of equity, the
    margin leg to what the broker will collateralise. US500's 0.25% margin
    never binds; USTEC's 25% frequently does.
    """
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    loss_per_lot = (stop_distance + cost) * inst["point_value"]
    risk_sized = equity * risk_fraction / loss_per_lot
    margin_sized = equity / (price * inst["point_value"] * inst["margin"])
    sized = math.floor(min(risk_sized, margin_sized) / inst["step"]) * inst["step"]
    return sized if sized + 1e-12 >= inst["min_qty"] else 0.0


def pnl_points(side, entry, exit_price, amount, inst):
    return side * (exit_price - entry) * amount * inst["point_value"]


def run(session_list, params, lo, hi, spread_bp, slippage, inst,
        flip=None, risk_fraction=RISK_FRACTION):
    """Replay `params`. `flip` is a Random; when given, sides are coin flips.

    Drawdown is marked to market: every bar the position is open contributes its
    adverse excursion, because closed-trade replay understated this engine by
    16 percentage points once already.
    """
    lo_ts = int(datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp())
    hi_ts = int(datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp())
    equity = peak = INITIAL
    maximum_drawdown = 0.0
    trades = []
    signal_cache = signals_for(session_list, params)

    for session in session_list:
        day_bars = session["bars"]
        if not (lo_ts <= day_bars[0][TS] < hi_ts) or session["atr"] is None:
            continue
        found = signal_cache[id(session)]
        bands = session["bands"]
        taken = 0
        blocked_until = -1
        for entry_index, side in found:
            if taken >= params["max_trades_per_session"] or entry_index <= blocked_until:
                continue
            if flip is not None:
                side = flip.choice((1, -1))
            entry_bar = day_bars[entry_index]
            price = entry_bar[O]
            cost = spread_bp / 1e4 * price + slippage
            stop_distance = params["stop_atr"] * session["atr"]
            if stop_distance <= 0.0:
                continue
            amount = quantity(equity, price, stop_distance, risk_fraction, cost, inst)
            if amount <= 0.0:
                continue
            entry = price + side * cost   # whole cost charged at entry
            stop = price - side * stop_distance

            exit_price, exit_index, reason = None, len(day_bars) - 1, "session_close"
            worst = price
            for j in range(entry_index, len(day_bars)):
                bar = day_bars[j]
                worst = min(worst, bar[L]) if side == 1 else max(worst, bar[H])
                marked = equity + pnl_points(side, entry, worst, amount, inst)
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - marked) / peak if peak > 0 else 1.0)
                target = bands[j][0]
                hit_stop = bar[L] <= stop if side == 1 else bar[H] >= stop
                # The target is only reachable from the losing side of VWAP.
                hit_target = ((side == 1 and bar[H] >= target and price < target)
                              or (side == -1 and bar[L] <= target and price > target))
                if hit_stop:
                    exit_price, exit_index, reason = stop, j, "stop"
                    break
                if hit_target:
                    exit_price, exit_index, reason = target, j, "vwap"
                    break
            if exit_price is None:
                exit_price = day_bars[-1][C]

            pnl = pnl_points(side, entry, exit_price, amount, inst)
            equity += pnl
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown,
                                   (peak - equity) / peak if peak > 0 else 1.0)
            trades.append({"entry_ts": entry_bar[TS], "exit_ts": day_bars[exit_index][TS],
                           "side": side, "quantity": amount, "pnl": pnl, "reason": reason})
            taken += 1
            blocked_until = exit_index
    return base.summarize(trades, equity, maximum_drawdown)


def buy_and_hold(session_list, lo, hi, spread_bp, slippage, inst):
    """Hold one UNLEVERED position from the first RTH open to the last close."""
    lo_ts = int(datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp())
    hi_ts = int(datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp())
    window = [s for s in session_list if lo_ts <= s["bars"][0][TS] < hi_ts]
    if not window:
        return None
    entry_price = window[0]["bars"][0][O]
    exit_price = window[-1]["bars"][-1][C]
    cost = spread_bp / 1e4 * entry_price + slippage
    # Notional equals the account, so this reads the instrument's own return.
    # Sizing it at the broker's margin instead would be 400x on US500 and
    # reported +28,000% -- a number about margin, not about the index.
    amount = INITIAL / (entry_price * inst["point_value"])
    entry = entry_price + cost
    peak = INITIAL
    maximum_drawdown = 0.0
    for session in window:
        for bar in session["bars"]:
            peak = max(peak, INITIAL + pnl_points(1, entry, bar[H], amount, inst))
            marked = INITIAL + pnl_points(1, entry, bar[L], amount, inst)
            maximum_drawdown = max(maximum_drawdown,
                                   (peak - marked) / peak if peak > 0 else 1.0)
    equity = INITIAL + pnl_points(1, entry, exit_price, amount, inst)
    return {"return_pct": round(100.0 * (equity / INITIAL - 1.0), 2),
            "final": round(equity, 2), "quantity": round(amount, 2),
            "max_dd_pct": round(100.0 * maximum_drawdown, 2)}


def null_band(session_list, params, lo, hi, spread_bp, slippage, inst, draws, seed=17):
    """Coin-flip control: identical entries and sizes, random direction."""
    rng = random.Random(seed)
    samples = [run(session_list, params, lo, hi, spread_bp, slippage, inst,
                   flip=random.Random(rng.getrandbits(32)))["return_pct"]
               for _ in range(draws)]
    samples.sort()
    return {
        "draws": draws,
        "mean_return_pct": round(statistics.fmean(samples), 2),
        "p05": round(samples[int(0.05 * draws)], 2),
        "p50": round(statistics.median(samples), 2),
        "p95": round(samples[min(draws - 1, int(0.95 * draws))], 2),
        "max": round(samples[-1], 2),
        "_samples": samples,
    }


def percentile_of(value, samples):
    return round(100.0 * sum(s < value for s in samples) / len(samples), 1)


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def candidates():
    return [dict(zip(AXES, values)) for values in itertools.product(*AXES.values())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=tuple(INSTRUMENTS), default="es")
    parser.add_argument("--spread-bp", type=float, default=None,
                        help="Pro in-session spread, bp of notional, charged at "
                             "entry; defaults to the instrument's own figure")
    parser.add_argument("--slippage", type=float, default=book.SLIPPAGE_POINTS)
    parser.add_argument("--null-draws", type=int, default=200)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    inst = INSTRUMENTS[args.symbol]
    if args.spread_bp is None:
        args.spread_bp = inst["spread_bp"]

    session_list = sessions(bars_1m(args.symbol))
    print(f"{args.symbol}: {len(session_list)} RTH sessions "
          f"{datetime.utcfromtimestamp(session_list[0]['bars'][0][TS]):%Y-%m-%d} .. "
          f"{datetime.utcfromtimestamp(session_list[-1]['bars'][0][TS]):%Y-%m-%d}")

    grid = candidates()
    scored = []
    for index, params in enumerate(grid, 1):
        stat = run(session_list, params, *IS, args.spread_bp, args.slippage, inst)
        scored.append((params, stat))
        print(f"  [{index:>3}/{len(grid)}] k={params['band_k']} "
              f"stop={params['stop_atr']} warm={params['warmup_minutes']} "
              f"n={params['max_trades_per_session']} {params['side_mode']:<6} "
              f"IS {stat['return_pct']:>9.2f}%  pf {stat['pf']:.3f}  "
              f"dd {stat['max_dd_pct']:.1f}%  t {stat['trades']}")

    scored.sort(key=lambda item: item[1]["return_pct"], reverse=True)
    best_params, best_is = scored[0]
    print(f"\nbest IS cell: {best_params}")

    best_oos = run(session_list, best_params, *OOS, args.spread_bp, args.slippage, inst)
    null_is = null_band(session_list, best_params, *IS, args.spread_bp,
                        args.slippage, inst, args.null_draws)
    null_oos = null_band(session_list, best_params, *OOS, args.spread_bp,
                         args.slippage, inst, args.null_draws)
    hold_is = buy_and_hold(session_list, *IS, args.spread_bp, args.slippage, inst)
    hold_oos = buy_and_hold(session_list, *OOS, args.spread_bp, args.slippage, inst)

    # Is there anything there at all before the broker takes a cut? On ES the
    # answer was no, which is what made the cost model irrelevant.
    gross_is = run(session_list, best_params, *IS, 0.0, 0.0, inst)
    gross_oos = run(session_list, best_params, *OOS, 0.0, 0.0, inst)

    payload = {
        "rule": "session VWAP band sweep + reclaim, VWAP target, ATR stop",
        "source": "https://www.youtube.com/watch?v=XWJlBBikUc0",
        "symbol": args.symbol,
        "table": f"{inst['table']} (New York wall clock, ratio back-adjusted)",
        "cost": {"spread_bp": args.spread_bp, "slippage_points": args.slippage,
                 "spread_bp_measured_on": inst["spread_bp_measured_on"],
                 "account": "Exness Pro 416209807",
                 "contract": f"{inst['contract']}, USD {inst['point_value']}/point/lot, "
                             f"{inst['step']} step, {inst['min_qty']} min, "
                             f"{inst['margin'] * 100:g}% margin"},
        "windows": {"is": IS, "oos": OOS},
        "grid_cells": len(grid),
        "best_params": best_params,
        "is": best_is,
        "oos": best_oos,
        "gross": {"is": gross_is, "oos": gross_oos},
        "controls": {
            "coin_flip_is": {**{k: v for k, v in null_is.items() if k != "_samples"},
                             "strategy_percentile":
                                 percentile_of(best_is["return_pct"], null_is["_samples"])},
            "coin_flip_oos": {**{k: v for k, v in null_oos.items() if k != "_samples"},
                              "strategy_percentile":
                                  percentile_of(best_oos["return_pct"], null_oos["_samples"])},
            "buy_and_hold_is": hold_is,
            "buy_and_hold_oos": hold_oos,
        },
        "top_is_cells": [{"params": p, "return_pct": s["return_pct"], "pf": s["pf"],
                          "trades": s["trades"], "max_dd_pct": s["max_dd_pct"]}
                         for p, s in scored[:args.top]],
    }
    out = output_path(args.symbol)
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\nIS  {best_is['return_pct']:>9.2f}%  pf {best_is['pf']:.3f}  "
          f"dd {best_is['max_dd_pct']:.1f}%  trades {best_is['trades']}")
    print(f"OOS {best_oos['return_pct']:>9.2f}%  pf {best_oos['pf']:.3f}  "
          f"dd {best_oos['max_dd_pct']:.1f}%  trades {best_oos['trades']}")
    print(f"zero-cost IS {gross_is['return_pct']:>9.2f}%  pf {gross_is['pf']:.3f}   "
          f"OOS {gross_oos['return_pct']:>9.2f}%  pf {gross_oos['pf']:.3f}")
    print(f"coin-flip IS  median {null_is['p50']:>8.2f}%  p95 {null_is['p95']:>8.2f}%  "
          f"strategy at pct {payload['controls']['coin_flip_is']['strategy_percentile']}")
    print(f"coin-flip OOS median {null_oos['p50']:>8.2f}%  p95 {null_oos['p95']:>8.2f}%  "
          f"strategy at pct {payload['controls']['coin_flip_oos']['strategy_percentile']}")
    print(f"buy & hold IS {hold_is['return_pct']:>9.2f}%   OOS {hold_oos['return_pct']:>9.2f}%")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    book.apply_cost_model("pro")
    main()
