"""Hourly Delta Reversal + Deep OFI Momentum in one $1000 account.

Mirrors `backtest::run_combined` / `engine::run_engines`: the strategies are not
two backtests added together, they share an account. That distinction is the
whole point of running this rather than summing two PnL columns --
`execution.size` sets each position to `equity * risk / stop` off the *live*
balance, so every OFI trade is sized by an equity that the Hourly Delta trades
have already moved, and vice versa. Compounding is joint.

Both replicas read `level_two` bars, so there is no source merging to do; the
Rust path's `merged_source` matters only when an OHLCV strategy joins.

One honest limitation, shared with `execution.size` itself: margin is checked
per trade, not across concurrently open positions. Two positions open at once
can therefore exceed the notional a single-position margin cap would allow. At
a $1000 account the binding constraint is the 0.5% risk fraction rather than
margin for most trades, so this is small -- but it is a real difference from a
broker, and it flatters the combined run slightly more than either standalone.
"""
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf
from dataclasses import replace

from sandbox import data
from sandbox import execution

INITIAL = 1000.0
MEMBERS = ["Hourly Delta Reversal", "Deep OFI Momentum"]
#: The window in which neither strategy's parameters were fit on the data.
#: Hourly Delta Reversal's walk-forward and Deep OFI Momentum's share these
#: folds, so this is the only span where a combined number means anything.
OOS = ("2025-10-01", "2026-08-01")


def member_fills(name, bars, ex):
    """Per-unit, size-independent fills for one strategy over the whole sample."""
    strategy = strategies.get(name)
    context = strategy.context()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, strategy.all_params(None)))
    return execution.resolve(bars, signals, ex)


def show(label, stat, initial, months=True):
    print(f"\n--- {label} " + "-" * max(4, 58 - len(label)))
    print("  " + "  ".join(f"{f}={stat[f]}" for f in wf.FIELDS))
    if months:
        wf.print_months(stat["months"], initial)
    return stat


def correlation(a, b):
    """Pearson correlation of two monthly series over their shared months."""
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    xs, ys = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def main():
    ex = replace(strategies.get(MEMBERS[0]).execution, initial=INITIAL)
    bars = data.load_bars("level_two", "nq")
    lo, hi = metrics.split_ts(OOS[0]), metrics.split_ts(OOS[1])

    print(f"combined account: {' + '.join(MEMBERS)}")
    print(f"  {len(bars)} bars, {data.bar_range('level_two', 'nq')}, "
          f"initial ${INITIAL:.0f}, Forex NQ, cost {ex.entry_cost} (spread {ex.spread} + slippage {ex.slippage} + commission)")

    # Each strategy's session flatten differs (16:00 vs 15:45), and that is a
    # property of the strategy, so fills are resolved under each one's own
    # execution before they are pooled.
    fills = {}
    for name in MEMBERS:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL)
        fills[name] = member_fills(name, bars, member_ex)

    standalone = {}
    for name in MEMBERS:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL)
        sized = execution.size(fills[name], member_ex)
        standalone[name] = metrics.stats(sized, initial=INITIAL)
        show(f"{name} alone", standalone[name], INITIAL, months=False)

    pooled = [f for name in MEMBERS for f in fills[name]]
    combined_sized = execution.size(pooled, ex)
    combined = metrics.stats(combined_sized, initial=INITIAL)

    print("\n" + "=" * 70)
    print("=== COMBINED, one $1000 account, full sample")
    print("=" * 70)
    show("combined full sample", combined, INITIAL)

    naive = sum(standalone[n]["pnl"] for n in MEMBERS)
    print(f"\n  sum of the two standalone PnLs: {naive:.2f}")
    print(f"  combined (shared equity):       {combined['pnl']:.2f}  "
          f"({combined['pnl'] - naive:+.2f} from joint compounding)")
    rho = correlation(standalone[MEMBERS[0]]["months"], standalone[MEMBERS[1]]["months"])
    if rho is not None:
        print(f"  monthly correlation between the two: {rho:+.3f}")

    print("\n" + "=" * 70)
    print(f"=== COMBINED, out of sample only ({OOS[0]} .. {OOS[1]})")
    print("=" * 70)
    oos_sized = execution.size([f for f in pooled if lo <= f.entry_ts < hi],
                               ex)
    oos = metrics.stats(oos_sized, initial=INITIAL, span=(lo, hi))
    show("combined out of sample", oos, INITIAL)
    for name in MEMBERS:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL)
        sized = execution.size([f for f in fills[name] if lo <= f.entry_ts < hi],
                               member_ex)
        stat = metrics.stats(sized, initial=INITIAL, span=(lo, hi))
        print(f"\n  {name} alone, out of sample: pnl {stat['pnl']:>8.2f} "
              f"pf {stat['pf']:.3f} mSh {stat['msharpe']:>5.2f} "
              f"pos_rate {stat['pos_rate']} streak {stat['max_loss_streak']}")

    # NQ's real spread is 0.75-1.0, not the environment's 0.2.
    print("\n--- combined at other spreads (full sample / out of sample) " + "-" * 4)
    print(f"  {'spread':>7} {'full pnl':>10} {'pf':>6} | {'oos pnl':>10} {'pf':>6} "
          f"{'mSh':>6} {'pos':>5}")
    for spread in (0.0, 0.2, 0.4, 0.5, 0.75, 1.0):
        cost_ex = replace(ex, slippage=spread, commission_per_lot=0.0)
        pool = []
        for name in MEMBERS:
            member_ex = replace(strategies.get(name).execution, initial=INITIAL,
                                slippage=spread, commission_per_lot=0.0)
            pool.extend(member_fills(name, bars, member_ex))
        full = metrics.stats(execution.size(pool, cost_ex), initial=INITIAL)
        window = metrics.stats(
            execution.size([f for f in pool if lo <= f.entry_ts < hi], cost_ex),
            initial=INITIAL, span=(lo, hi))
        print(f"  {spread:>7} {full['pnl']:>10.2f} {full['pf']:>6.3f} | "
              f"{window['pnl']:>10.2f} {window['pf']:>6.3f} "
              f"{window['msharpe']:>6.2f} {window['pos_rate']:>5.2f}")


if __name__ == "__main__":
    main()
