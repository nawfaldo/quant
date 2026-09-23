"""Nine strategies across two symbols in one $1,000 account.

Six BTC session strategies and three NQ level-two strategies, sharing a single
balance the way `backtest::run_combined` shares one: `execution.size` sets every
position to `equity * risk / stop` off the *live* balance, so an OFI trade
is sized by an equity the BTC Donchian trades have already moved. Joint
compounding is why the combined PnL is not the sum of the standalone ones.

WHY THIS IS NOT A SERVER RUN. The Rust engine cannot express this portfolio.
`prepare_strategies` rejects the combination outright — "BTC strategies cannot be
combined with NQ strategies" — and, more fundamentally, a run resolves to exactly
one `expected_symbol` and loads one symbol's bars, so there is nowhere for a BTC
strategy and an NQ strategy to receive their own prices in the same run. Making
this an engine feature means per-strategy bar routing over a merged timeline,
which is a real change to shared loading code. This module answers the question
with the validated sandbox replicas instead.

WINDOW. NQ level-two history begins 2025-02-12, so that is the earliest date all
nine members can trade together; the BTC members have history back to 2017 that
is simply excluded here. Everything below covers 2025-02-12 .. 2026-07-30, about
17.5 months, and no member's parameters were selected on a window that excludes
it — the BTC cells were sealed on 2018-2024 and the NQ ones tuned on roughly this
very span. So the BTC contributions here are out of sample and the NQ ones are
not, which is the single most important caveat in the output.

METHOD. Every member is resolved once to *per-unit* trades carrying no sizing:
points, stop distance, entry price and its own compiled risk fraction. Those are
then replayed in entry order against one shared equity. For the BTC members that
is done by spying on each research module's `quantity()` — forcing one unit makes
`pnl == points` and makes every signal fire, and the k-th call pairs with the
k-th chronological entry. The pairing is asserted rather than assumed.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

from sandbox import data, execution, metrics, strategies
from sandbox.research import btc_consistency_research as consistency
from sandbox.research import btc_families_research as families
from sandbox.research import btc_orb_mean_reversion_research as orbmr
from sandbox.research import btc_rth_research as rth
from sandbox.research import btc_strategy_research as base
from sandbox.research import es_strategy_research as es


INITIAL = 1_000.0
RESEARCH = os.path.dirname(__file__)

WINDOW_FROM = "2025-02-12"
WINDOW_TO = "2026-08-01"

ALL_BTC = ("BTC Donchian", "BTC VWAP", "BTC ORB Trail",
           "BTC ORB", "BTC RTH Momentum", "BTC Hourly Momentum")
ALL_NQ = ("Hourly Delta Reversal", "Deep OFI Momentum")
ALL_MEMBERS = ALL_BTC + ALL_NQ

#: Short aliases so a member can be dropped from the command line without
#: retyping its display name.
ALIASES = {
    "donchian": "BTC Donchian", "vwap": "BTC VWAP", "orbtrail": "BTC ORB Trail",
    "orb": "BTC ORB", "rth": "BTC RTH Momentum", "hourly": "BTC Hourly Momentum",
    "hdr": "Hourly Delta Reversal",
    "ofi": "Deep OFI Momentum",
}


def sealed(name):
    with open(os.path.join(RESEARCH, name), encoding="utf-8") as handle:
        return json.load(handle)


@contextmanager
def spying(module, attribute, replacement):
    original = getattr(module, attribute)
    setattr(module, attribute, replacement)
    try:
        yield
    finally:
        setattr(module, attribute, original)


def per_unit(run, quantity_module, quantity_attribute, summarize_module):
    """Run a research backtest for per-unit trades plus each entry's sizing inputs.

    Returns `[(entry_ts, exit_ts, points, stop, price)]`, one per trade, with the
    strategy's own risk fraction attached by the caller.
    """
    calls = []
    trades = []
    original_summarize = getattr(summarize_module, "summarize")

    def quantity(equity, price, stop, risk=None):
        calls.append((price, stop, risk))
        return 1.0

    def summarize(captured, drawdown, final):
        trades.extend(captured)
        return original_summarize(captured, drawdown, final)

    with spying(quantity_module, quantity_attribute, quantity), \
            spying(summarize_module, "summarize", summarize):
        run()

    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    if len(ordered) != len(calls):
        raise SystemExit(
            f"per-unit pairing broke: {len(calls)} sizing calls vs {len(ordered)} "
            "trades; an entry was gated after quantity() was consulted"
        )
    out = []
    for trade, (price, stop, risk) in zip(ordered, calls):
        out.append((trade["entry_ts"], trade["exit_ts"], trade["points"], stop,
                    price, risk))
    return out


# --------------------------------------------------------------------------- #
# members
# --------------------------------------------------------------------------- #


def btc_family_fills(family, bars, ctx):
    params = dict(sealed("btc_families_selection.json")["families"][family]["params"])
    fills = per_unit(
        lambda: families.backtest(family, bars, ctx, params,
                                  lo=metrics.split_ts(WINDOW_FROM),
                                  hi=metrics.split_ts(WINDOW_TO)),
        es, "quantity", es,
    )
    # `btc_families_research` pins risk itself and scales it by volatility, so the
    # spy already captured the effective fraction per entry.
    return fills


def btc_orb_fills(bars, ctx):
    params = dict(sealed("btc_orb_mean_reversion_selection.json")["families"]["orb"]["params"])
    return per_unit(
        lambda: orbmr.backtest("orb", bars, ctx, params,
                               lo=metrics.split_ts(WINDOW_FROM),
                               hi=metrics.split_ts(WINDOW_TO)),
        rth, "quantity", base,
    )


def btc_rth_fills(bars, ctx):
    params = dict(sealed("btc_rth_selection.json")["params"])
    return per_unit(
        lambda: rth.backtest(bars, ctx, params,
                             lo=metrics.split_ts(WINDOW_FROM),
                             hi=metrics.split_ts(WINDOW_TO)),
        rth, "quantity", base,
    )


def btc_hourly_fills(bars, indicators, vix, volatility):
    params = dict(sealed("btc_consistency_selection.json")["params"])
    return per_unit(
        lambda: consistency.backtest(bars, indicators, vix, volatility, params,
                                     lo=metrics.split_ts(WINDOW_FROM),
                                     hi=metrics.split_ts(WINDOW_TO)),
        consistency, "quantity", base,
    )


def nq_fills(name, bars):
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    context = strategy.context()
    merged = strategy.all_params(strategy.defaults)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, merged))
    lo, hi = metrics.split_ts(WINDOW_FROM), metrics.split_ts(WINDOW_TO)
    return [(f.entry_ts, f.exit_ts, f.points, f.stop, f.price, ex.risk)
            for f in execution.resolve(bars, signals, ex)
            if lo <= f.entry_ts < hi]


# --------------------------------------------------------------------------- #
# shared account
# --------------------------------------------------------------------------- #

MARGIN = 0.25
STEP = 0.01


def replay(members, initial=INITIAL):
    """Replay every member's per-unit trades against one compounding balance.

    Entry order drives sizing and exits are applied as they land, so a position
    opened while three others are live is sized by the equity those three have
    produced so far. Trades too small to round to one step are skipped, exactly
    as each strategy would skip them.
    """
    events = []
    for name, fills in members.items():
        for entry_ts, exit_ts, points, stop, price, risk in fills:
            events.append((entry_ts, exit_ts, points, stop, price, risk, name))
    events.sort(key=lambda e: e[0])

    equity = peak = initial
    max_dd = 0.0
    queue = []
    settled = []
    for entry_ts, exit_ts, points, stop, price, risk, name in events:
        queue.sort()
        while queue and queue[0][0] <= entry_ts:
            _exit, pnl, member, opened = queue.pop(0)
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 1.0)
            settled.append((opened, pnl, member))
        if equity <= 0 or price <= 0 or stop <= 0:
            continue
        raw = min(equity * risk / stop, equity / MARGIN / price)
        quantity = math.floor(raw / STEP) * STEP
        if quantity < STEP:
            continue
        queue.append((exit_ts, points * quantity, name, entry_ts))
    for _exit, pnl, member, opened in sorted(queue):
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 1.0)
        settled.append((opened, pnl, member))
    return settled, equity, 100.0 * max_dd


def month_of(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{dt.year}-{dt.month:02d}"


def summarize(settled, equity, max_dd, initial=INITIAL):
    pnls = [pnl for _, pnl, _ in settled]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    months = {}
    for ts, pnl, _ in settled:
        months[month_of(ts)] = months.get(month_of(ts), 0.0) + pnl
    monthly = list(months.values())
    return {
        "pnl": equity - initial,
        "return_pct": 100.0 * (equity - initial) / initial,
        "trades": len(pnls),
        "pf": (wins / losses) if losses else (999.0 if wins else 0.0),
        "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else 0.0,
        "max_dd_pct": max_dd,
        "monthly_sharpe": (statistics.fmean(monthly) / statistics.pstdev(monthly))
                          if len(monthly) > 1 and statistics.pstdev(monthly) else 0.0,
        "positive_months": sum(1 for v in monthly if v > 0),
        "n_months": len(monthly),
        "months": months,
    }


def correlation(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    xs, ys = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def parse_members(argv):
    """Member list from `--only a,b` or `--drop a,b`, defaulting to all nine."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None,
                        help=f"comma-separated subset of {sorted(ALIASES)}")
    parser.add_argument("--drop", default=None, help="comma-separated members to omit")
    args = parser.parse_args(argv)

    def resolve(text):
        out = []
        for token in text.split(","):
            key = token.strip().lower().replace(" ", "")
            if key in ALIASES:
                out.append(ALIASES[key])
            elif token.strip() in ALL_MEMBERS:
                out.append(token.strip())
            else:
                raise SystemExit(f"unknown member {token!r}; expected {sorted(ALIASES)}")
        return out

    if args.only:
        chosen = resolve(args.only)
    elif args.drop:
        dropped = set(resolve(args.drop))
        chosen = [name for name in ALL_MEMBERS if name not in dropped]
    else:
        chosen = list(ALL_MEMBERS)
    return [name for name in ALL_MEMBERS if name in set(chosen)]


def main(argv=None):
    members = parse_members(argv)
    print("=" * 100)
    print(f"{len(members)} STRATEGIES, ONE ${INITIAL:,.0f} FOREX ACCOUNT   "
          f"{WINDOW_FROM} .. 2026-07-30   entry spread 0.20")
    print("=" * 100)
    print("  " + " + ".join(members))
    print("\n  BTC members are out of sample here (sealed on 2018-2024).")
    print("  NQ members were tuned on roughly this same window, so their")
    print("  contributions are in-sample and flattered. Read them differently.\n")

    fills = {}
    wanted = set(members)

    families_wanted = [(f, n) for f, n in (("trend", "BTC Donchian"),
                                           ("vwap", "BTC VWAP"),
                                           ("orb", "BTC ORB Trail"))
                       if n in wanted]
    if families_wanted:
        print("resolving BTC session members...", flush=True)
        btc_bars, btc_ctx = families.context("validate")
        for family, name in families_wanted:
            fills[name] = btc_family_fills(family, btc_bars, btc_ctx)

    if {"BTC ORB", "BTC RTH Momentum"} & wanted:
        print("resolving BTC 30-minute members...", flush=True)
        half_hour = rth.bars_30m("oos")
        if "BTC ORB" in wanted:
            fills["BTC ORB"] = btc_orb_fills(half_hour, orbmr.context(half_hour, "oos"))
        if "BTC RTH Momentum" in wanted:
            fills["BTC RTH Momentum"] = btc_rth_fills(half_hour,
                                                      rth.context(half_hour, "oos"))

    if "BTC Hourly Momentum" in wanted:
        print("resolving BTC hourly member...", flush=True)
        hourly = base.hourly_bars("oos")
        fills["BTC Hourly Momentum"] = btc_hourly_fills(
            hourly, base.indicators(hourly),
            consistency.vix_prior_by_bar(hourly, "oos"),
            consistency.trailing_annual_volatility(hourly),
        )

    nq_wanted = [name for name in ALL_NQ if name in wanted]
    if nq_wanted:
        print("resolving NQ members...", flush=True)
        nq_bars = data.load_cached_level_two_bars("nq")
        for name in nq_wanted:
            fills[name] = nq_fills(name, nq_bars)

    MEMBERS = members

    # ---------------------------------------------------------------- standalone
    print("\n" + "=" * 100)
    print("STANDALONE - each alone in its own $1,000 account, same window")
    print("=" * 100)
    print(f"  {'strategy':<24} {'pnl':>10} {'ret':>9} {'trades':>7} {'pf':>7} "
          f"{'dd':>8} {'mSh':>7} {'+mo':>7}")
    standalone = {}
    for name in MEMBERS:
        settled, equity, dd = replay({name: fills[name]})
        stat = summarize(settled, equity, dd)
        standalone[name] = stat
        print(f"  {name:<24} {stat['pnl']:>10.2f} {stat['return_pct']:>8.2f}% "
              f"{stat['trades']:>7} {stat['pf']:>7.3f} {stat['max_dd_pct']:>7.2f}% "
              f"{stat['monthly_sharpe']:>7.2f} "
              f"{stat['positive_months']:>3}/{stat['n_months']:<3}")

    # ---------------------------------------------------------------- combined
    settled, equity, dd = replay(fills)
    combined = summarize(settled, equity, dd)

    print("\n" + "=" * 100)
    print("COMBINED - one shared $1,000 account")
    print("=" * 100)
    naive = sum(standalone[n]["pnl"] for n in MEMBERS)
    print(f"  sum of {len(MEMBERS)} standalone PnLs".ljust(32) + f"{naive:>10.2f}")
    print(f"  combined (shared equity)      {combined['pnl']:>10.2f}   "
          f"({combined['pnl'] - naive:+.2f} from joint compounding)")
    print(f"  final equity                  {equity:>10.2f}   "
          f"({combined['return_pct']:+.2f}%)")
    print(f"  max drawdown {combined['max_dd_pct']:.2f}%   pf {combined['pf']:.3f}   "
          f"trades {combined['trades']}   win {combined['win_rate']:.3f}   "
          f"monthly Sharpe {combined['monthly_sharpe']:.2f}   "
          f"positive {combined['positive_months']}/{combined['n_months']}")

    # ------------------------------------------------- monthly contribution grid
    by_month = {}
    for ts, pnl, name in settled:
        by_month.setdefault(month_of(ts), {}).setdefault(name, 0.0)
        by_month[month_of(ts)][name] += pnl
    months = sorted(by_month)

    print("\n" + "=" * 100)
    print("MONTHLY CONTRIBUTION INSIDE THE SHARED ACCOUNT, $")
    print("=" * 100)
    short = {n: n.replace("BTC ", "").replace(" Momentum", "")[:9] for n in MEMBERS}
    header = "month   | " + " ".join(f"{short[n]:>9}" for n in MEMBERS) + " |     TOTAL"
    print(header)
    print("-" * len(header))
    running = INITIAL
    for month in months:
        row = by_month[month]
        total = sum(row.values())
        running += total
        cells = " ".join(f"{row.get(n, 0.0):>9.2f}" for n in MEMBERS)
        print(f"{month} | {cells} | {total:>9.2f}")
    print("-" * len(header))
    totals = {n: sum(by_month[m].get(n, 0.0) for m in months) for n in MEMBERS}
    print("TOTAL   | " + " ".join(f"{totals[n]:>9.2f}" for n in MEMBERS)
          + f" | {sum(totals.values()):>9.2f}")
    gross = sum(abs(v) for v in totals.values()) or 1.0
    print("share%  | " + " ".join(f"{100 * totals[n] / gross:>8.1f}%" for n in MEMBERS)
          + f" | {100 * sum(totals.values()) / gross:>8.1f}%")

    print("\n" + "=" * 100)
    print("CONTRIBUTION SUMMARY (inside the shared account)")
    print("=" * 100)
    print(f"  {'strategy':<24} {'contribution':>13} {'share':>8} {'trades':>8} "
          f"{'+months':>9}  standalone")
    counts = {}
    member_months = {}
    for ts, pnl, name in settled:
        counts[name] = counts.get(name, 0) + 1
        member_months.setdefault(name, {}).setdefault(month_of(ts), 0.0)
        member_months[name][month_of(ts)] += pnl
    for name in sorted(MEMBERS, key=lambda n: -totals[n]):
        positive = sum(1 for v in member_months.get(name, {}).values() if v > 0)
        seen = len(member_months.get(name, {}))
        print(f"  {name:<24} {totals[name]:>13.2f} "
              f"{100 * totals[name] / gross:>7.1f}% {counts.get(name, 0):>8} "
              f"{positive:>4}/{seen:<4}  {standalone[name]['pnl']:>9.2f}")

    print("\n  monthly PnL correlation between members (shared account):")
    printed = False
    for i, a in enumerate(MEMBERS):
        for b in MEMBERS[i + 1:]:
            rho = correlation(member_months.get(a, {}), member_months.get(b, {}))
            if rho is not None and abs(rho) >= 0.35:
                print(f"    {a:<24} vs {b:<24} {rho:+.3f}")
                printed = True
    if not printed:
        print("    nothing above |0.35| — the members are close to independent")


if __name__ == "__main__":
    main()
