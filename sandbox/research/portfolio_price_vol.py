"""The engine's own volatility-target signal, ported so it can actually be run.

`live_trade/src/sizing/volatility_target.rs` implements exactly the idea this study
needed -- EWMA variance of daily returns, multiplier = target / annualised
volatility, capped, with a warm-up floor of 1.0. It was not used, and it could
not have been, for three independent reasons:

  * `RunRequest::position_sizing()` returns `Ok(None)` unconditionally, with a
    comment saying why: the registered strategies size internally and generic
    lot/leverage inputs must not alter their risk. `cfg.sizing` is therefore
    always `None`, `VolTarget::new` at `engine.rs:300` is never reached outside
    its unit tests, and `volTarget` / `volHalflife` / `volMaxMult` /
    `volMinDays` are parsed off the request and discarded. Verified: `BTC Maroy
    Ladder` returns +18.21% / 8.56% DD / 0.0148 average size with and without
    `volTarget` set, identical to the last decimal.
  * `/api/combine` hard-sets `sizing: None` and `vol_target: None` when it
    builds its `RunRequest`, so the path is unreachable on combined runs
    specifically, which is the only kind of run this study makes.
  * Even reachable, it would not compose. The multiplier is applied in the
    `else` branch of entry sizing, where `base_lot * leverage * multiplier`
    *replaces* the strategy's own quantity rather than scaling it. For sleeves
    that size at a risk fraction of equity, that discards the risk model
    instead of overlaying on it.

WHAT IS STILL WORTH TAKING FROM IT. Its *signal* is not the one
`portfolio_regime_control` used, and the difference is real:

  * this module (and the Rust one): EWMA, halflife-weighted, of **NQ's daily
    price returns** -- how violent the market is;
  * `rule_volatility`: flat-window standard deviation of the **book's own daily
    P&L returns** -- how violent the strategies' results are.

Those can disagree sharply. A book can be losing steadily in a calm market, or
flat in a violent one. The price signal is also the more standard construction
and has an honest advantage: it is measurable before the book has any track
record at all, so it needs no shadow book and cannot be contaminated by the
overlay it drives.

So this runs the engine's construction, on the engine's parameter defaults and
around them, through the exposure schedule -- and scores it against both the
constant and the P&L-volatility rule, on the warm 2026 segment and the full
continuous window.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import portfolio_exposure as px
from sandbox.research import portfolio_sizing_mix as mix
from sandbox.research import portfolio_warm_oos as warm


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_price_vol.json")

#: `VolTargetConfig::default()` in the Rust module.
RUST_DEFAULTS = {"target": 20.0, "halflife": 20.0, "max_mult": 3.0, "min_days": 30}


def daily_closes(symbol="nq", source="level_two"):
    """Last close of each calendar day, the series `VolTarget::on_bar` sees.

    The Rust side folds a day into the EWMA when the day *changes*, using that
    day's final close, so this reproduces the same close-to-close series.
    """
    closes = {}
    bars = data.load_bars(source, symbol)
    ts_index, close_index = data.TS, data.C
    for bar in bars:
        day = datetime.fromtimestamp(bar[ts_index], timezone.utc).strftime("%Y-%m-%d")
        closes[day] = bar[close_index]
    return sorted(closes.items())


def multipliers(closes, target, halflife, max_mult, min_days):
    """`VolTarget::multiplier()` per day, using only strictly earlier closes.

    Returns `{day: multiplier}`. Days before the warm-up floor sit at 1.0,
    exactly as the Rust module does.
    """
    lam = 0.5 ** (1.0 / halflife)
    variance, seen, previous = 0.0, 0, None
    out = {}
    for day, close in closes:
        # The multiplier in force *today* is computed from days already folded
        # in, so it is recorded before today's close updates the variance.
        if seen < min_days or variance <= 0.0:
            out[day] = 1.0
        else:
            annualised = (variance * 252.0) ** 0.5
            out[day] = min(max_mult, (target / 100.0) / annualised) if annualised > 0 else 1.0
        if previous is not None and previous > 0 and close > 0:
            ret = close / previous - 1.0
            variance = ret * ret if seen == 0 else lam * variance + (1.0 - lam) * ret * ret
            seen += 1
        previous = close
    return out


def schedule(values, sleeves=px.SLEEVES, cap=None):
    """Compact the per-day multipliers into the engine's step-function form."""
    points, prev = {}, None
    for day in sorted(values):
        factor = values[day] if cap is None else min(values[day], cap)
        factor = round(float(factor), 4)
        if factor != prev:
            points[day] = factor
            prev = factor
    return {name: dict(points) for name in sleeves}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    closes = daily_closes()
    print(f"NQ daily closes: {len(closes)} days, {closes[0][0]} .. {closes[-1][0]}")

    report = {"rust_defaults": RUST_DEFAULTS}

    # The control frontier, and the P&L-volatility rule, both measured the same
    # way as in `portfolio_warm_oos` so the three are directly comparable.
    # A very small constant can leave the 2026 segment with no closed trade at
    # all, in which case there is no segment statistic to match against and the
    # constant simply does not compete at that drawdown.
    control = {}
    for factor in [round(0.05 * i, 2) for i in range(1, 21)]:
        result = px.run(px.flat(factor), px.FULL)
        segment = warm.segment_stats(result)
        if segment is not None:
            control[factor] = (result, segment)

    def matched(drawdown, warm_side):
        best = None
        for factor, (full, seg) in control.items():
            stats = seg if warm_side else full
            value = stats["max_dd_pct"]
            if value <= drawdown + 1e-9:
                if best is None or stats["return_pct"] > best[1]["return_pct"]:
                    best = (factor, stats)
        return best

    print()
    print("=" * 100)
    print("PRICE VOLATILITY (the engine's construction) -- NQ daily returns, EWMA")
    print("=" * 100)
    print(f"  {'candidate':<34} {'full ret':>9} {'full DD':>8} {'edge':>7}   "
          f"{'2026 ret':>9} {'2026 DD':>8} {'edge':>7}")
    rows = []
    for target in (8, 10, 12, 15, 20, 25):
        for halflife in (10, 20, 40):
            values = multipliers(closes, target, halflife,
                                 RUST_DEFAULTS["max_mult"], RUST_DEFAULTS["min_days"])
            # Capped at 1.0: this study is about cutting drawdown, and letting a
            # rule lever *above* the compiled size is a different question. The
            # Rust default cap of 3.0 is reported separately below.
            result = px.run(schedule(values, cap=1.0), px.FULL)
            seg = warm.segment_stats(result)
            label = f"price vol t={target},hl={halflife}"

            full_match = matched(result["max_dd_pct"], False)
            seg_match = matched(seg["max_dd_pct"], True)
            full_edge = result["return_pct"] - full_match[1]["return_pct"] if full_match else None
            seg_edge = seg["return_pct"] - seg_match[1]["return_pct"] if seg_match else None
            print(f"  {label:<34} {result['return_pct']:8.2f}% {result['max_dd_pct']:7.2f}% "
                  f"{full_edge:+7.2f}   {seg['return_pct']:8.2f}% {seg['max_dd_pct']:7.2f}% "
                  f"{seg_edge:+7.2f}")
            rows.append({"target": target, "halflife": halflife, "label": label,
                         "full": {f: result[f] for f in px.FIELDS}, "segment": seg,
                         "full_edge": round(full_edge, 2) if full_edge is not None else None,
                         "segment_edge": round(seg_edge, 2) if seg_edge is not None else None})
    report["price_vol"] = rows

    full_wins = sum(1 for r in rows if (r["full_edge"] or 0) > 0)
    seg_wins = sum(1 for r in rows if (r["segment_edge"] or 0) > 0)
    print(f"\n  beating the matched constant: full window {full_wins}/{len(rows)}, "
          f"warm 2026 {seg_wins}/{len(rows)}")
    report["win_rate"] = {"full": full_wins, "segment": seg_wins, "cells": len(rows)}

    # The Rust defaults exactly as compiled, uncapped, for the record.
    values = multipliers(closes, **{k: RUST_DEFAULTS[k] for k in
                                    ("target", "halflife", "max_mult", "min_days")})
    result = px.run(schedule(values), px.FULL)
    seg = warm.segment_stats(result)
    print(f"\n  VolTargetConfig::default() as compiled (target=20%, hl=20, cap=3.0):")
    print(f"    full {result['return_pct']:.2f}% / {result['max_dd_pct']:.2f}%   "
          f"warm 2026 {seg['return_pct']:.2f}% / {seg['max_dd_pct']:.2f}%")
    report["rust_default_run"] = {"full": {f: result[f] for f in px.FIELDS}, "segment": seg}

    print()
    print("=" * 100)
    print("HEAD TO HEAD -- price volatility vs the book's own P&L volatility")
    print("=" * 100)
    print(f"  {'rule':<34} {'full ret':>9} {'full DD':>8}   {'2026 ret':>9} {'2026 DD':>8}")
    best_price = max((r for r in rows if r["full_edge"] is not None),
                     key=lambda r: r["full_edge"])
    print(f"  {'best price-vol: ' + best_price['label']:<34} "
          f"{best_price['full']['return_pct']:8.2f}% {best_price['full']['max_dd_pct']:7.2f}%   "
          f"{best_price['segment']['return_pct']:8.2f}% {best_price['segment']['max_dd_pct']:7.2f}%")
    pnl = px.run(mix.vol_schedule({"window": 60, "target": 12, "cap": 1.0}, px.FULL), px.FULL)
    pnl_seg = warm.segment_stats(pnl)
    print(f"  {'P&L-vol w=60,t=12 (recommended)':<34} "
          f"{pnl['return_pct']:8.2f}% {pnl['max_dd_pct']:7.2f}%   "
          f"{pnl_seg['return_pct']:8.2f}% {pnl_seg['max_dd_pct']:7.2f}%")
    ctl = px.run(px.flat(0.5), px.FULL)
    ctl_seg = warm.segment_stats(ctl)
    print(f"  {'control flat k=0.5':<34} "
          f"{ctl['return_pct']:8.2f}% {ctl['max_dd_pct']:7.2f}%   "
          f"{ctl_seg['return_pct']:8.2f}% {ctl_seg['max_dd_pct']:7.2f}%")
    report["head_to_head"] = {
        "price_vol": best_price,
        "pnl_vol": {"full": {f: pnl[f] for f in px.FIELDS}, "segment": pnl_seg},
        "control": {"full": {f: ctl[f] for f in px.FIELDS}, "segment": ctl_seg},
    }

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()


def per_market(target, halflife, max_mult=3.0, min_days=30, cap=1.0):
    """Each sleeve throttled by *its own* market's volatility.

    The Rust module, and everything above, derives one multiplier from symbol 0
    -- NQ -- and applies it to all three sleeves, including a BTC one. That is a
    real modelling error and not merely an inelegance: NQ and BTC volatility
    regimes are not the same series, so the Ladder is being sized by the wrong
    market. This builds the same EWMA per market and routes each sleeve to its
    own, which is the version that would be defensible live.
    """
    nq = multipliers(daily_closes("nq", "level_two"), target, halflife, max_mult, min_days)
    btc = multipliers(daily_closes("btc", "ohlcv"), target, halflife, max_mult, min_days)
    out = {}
    for name, values in ((px.SLEEVES[0], nq), (px.SLEEVES[1], nq), (px.SLEEVES[2], btc)):
        out[name] = schedule(values, sleeves=(name,), cap=cap)[name]
    return out
