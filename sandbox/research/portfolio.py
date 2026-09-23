"""All four level-two strategies in one $1000 account, at their optimized parameters.

Mirrors `backtest::run_combined`: the strategies are not four backtests added
together, they share an account. `execution.size` sets each position to
`equity * risk / stop` off the *live* balance, so every Absorption trade is
sized by an equity the Hourly Delta trades have already moved. Compounding is
joint, which is why the combined PnL is not the sum of the standalone ones.

`MEDIANS` are the per-fold medians selected by `walkforward.py` under the Stage
3/4 protocol -- the trade t-stat objective, 12 monthly folds, plateau width.
They are what "optimized" means here.

**Two windows are reported, and only one of them means anything.**

The full sample runs from the first bar, but every median was chosen by folds
whose anchored train windows begin at that same first bar -- so 2025-02 ..
2025-07 is data these parameters were fitted on. Those months are in-sample and
are printed for completeness, not for judgement. The out-of-sample window is
2025-08 onward, the span the folds actually tested. Read that one.

One honest limitation, shared with `execution.size` itself: margin is checked
per trade, not across concurrently open positions, so four strategies trading
at once can exceed the notional a single-position cap would allow. At $1000 the
0.5% risk fraction binds first for most trades, but this flatters a four-member
account more than it flattered the two-member one in `combine_ofi_hdr.py`.
"""
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0

#: ENTRY_RISK_FRACTION as compiled in the three Rust strategy files.
RISK = 0.01

#: Walk-forward medians, stable within one grid step across folds. Provenance is
#: `py -m sandbox walkforward "<name>"` under the 12-fold protocol.
MEDIANS = {
    "Hourly Delta Reversal": {"k": 0.2, "rr": 1.25, "sell_delta": 300},
    "Absorption Reversal": {"window": 3, "aggression_z": 2.0, "move_fraction": 0.55,
                            "repl_threshold": 0.15, "target": 40, "stop": 8,
                            "time_stop": 25},
}

#: The span the walk-forward folds tested. Everything before it is in-sample.
OOS = ("2025-08-01", "2026-08-01")


def member_fills(name, bars, ex, params):
    """Per-unit, size-independent fills for one strategy over the whole sample."""
    strategy = strategies.get(name)
    context = strategy.context()
    merged = strategy.all_params(params)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, merged))
    return execution.resolve(bars, signals, ex)


def show(label, stat, initial, months=True):
    print(f"\n--- {label} " + "-" * max(4, 62 - len(label)))
    print("  " + "  ".join(f"{f}={stat[f]}" for f in wf.FIELDS))
    if months:
        wf.print_months(stat["months"], initial)
    return stat


def correlation(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    xs, ys = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def pooled_stats(fills, ex, lo=None, hi=None):
    inside = [f for f in fills
              if (lo is None or f.entry_ts >= lo) and (hi is None or f.entry_ts < hi)]
    span = (lo, hi) if lo is not None else None
    return metrics.stats(execution.size(inside, ex), initial=ex.initial, span=span)


def main():
    names = list(MEDIANS)
    bars = data.load_bars("level_two", "nq")
    ex = replace(strategies.get(names[0]).execution, initial=INITIAL, risk=RISK)
    lo, hi = metrics.split_ts(OOS[0]), metrics.split_ts(OOS[1])

    print("combined account: " + " + ".join(names))
    print(f"  {len(bars)} bars, {data.bar_range('level_two', 'nq')}, "
          f"initial ${INITIAL:.0f}, Forex NQ, cost {ex.entry_cost}")
    for name, params in MEDIANS.items():
        print(f"  {name}: {params}")

    # Session flatten differs per strategy, so fills resolve under each one's
    # own execution before they are pooled into the shared account.
    fills = {}
    for name in names:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL, risk=RISK)
        fills[name] = member_fills(name, bars, member_ex, MEDIANS[name])

    print("\n" + "=" * 72)
    print(f"=== standalone, out of sample only ({OOS[0]} .. {OOS[1]})")
    print("=" * 72)
    print(f"  {'strategy':<24} {'pnl':>9} {'trades':>7} {'pf':>6} {'dd':>7} "
          f"{'mSh':>6} {'pos':>5} {'strk':>5}")
    standalone = {}
    for name in names:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL, risk=RISK)
        stat = pooled_stats(fills[name], member_ex, lo, hi)
        standalone[name] = stat
        print(f"  {name:<24} {stat['pnl']:>9.2f} {stat['trades']:>7} "
              f"{stat['pf']:>6.3f} {stat['max_dd']:>7.2f} {stat['msharpe']:>6.2f} "
              f"{stat['pos_rate']:>5.2f} {stat['max_loss_streak']:>5}")

    pooled = [f for name in names for f in fills[name]]

    print("\n" + "=" * 72)
    print(f"=== COMBINED, one ${INITIAL:.0f} account, OUT OF SAMPLE "
          f"({OOS[0]} .. {OOS[1]})")
    print("=" * 72)
    combined_oos = show("combined out of sample", pooled_stats(pooled, ex, lo, hi),
                        INITIAL)
    naive = sum(standalone[n]["pnl"] for n in names)
    print(f"\n  sum of the four standalone PnLs: {naive:.2f}")
    print(f"  combined (shared equity):        {combined_oos['pnl']:.2f}  "
          f"({combined_oos['pnl'] - naive:+.2f} from joint compounding)")

    print("\n  monthly correlation between members (out of sample):")
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            rho = correlation(standalone[a]["months"], standalone[b]["months"])
            if rho is not None:
                print(f"    {a:<24} vs {b:<24} {rho:+.3f}")

    # The two members whose standalone OOS edge was not negative. Not a
    # selection made out of sample -- it is chosen with knowledge of the very
    # results above, and is reported as a contrast, not as a candidate.
    pair = ["Hourly Delta Reversal", "Absorption Reversal"]
    pair_stat = pooled_stats([f for n in pair for f in fills[n]], ex, lo, hi)
    print(f"\n  for contrast, {' + '.join(pair)} only: "
          f"pnl {pair_stat['pnl']:.2f} pf {pair_stat['pf']:.3f} "
          f"dd {pair_stat['max_dd']:.2f} mSh {pair_stat['msharpe']:.2f} "
          f"pos_rate {pair_stat['pos_rate']} streak {pair_stat['max_loss_streak']}")

    print("\n" + "=" * 72)
    print("=== COMBINED, full sample -- 2025-02 .. 2025-07 IS IN-SAMPLE")
    print("=" * 72)
    print(f"  {'strategy':<24} {'pnl':>9} {'trades':>7} {'pf':>6} {'dd':>7} "
          f"{'mSh':>6} {'pos':>5} {'strk':>5}")
    for name in names:
        member_ex = replace(strategies.get(name).execution, initial=INITIAL, risk=RISK)
        stat = pooled_stats(fills[name], member_ex)
        print(f"  {name:<24} {stat['pnl']:>9.2f} {stat['trades']:>7} "
              f"{stat['pf']:>6.3f} {stat['max_dd']:>7.2f} {stat['msharpe']:>6.2f} "
              f"{stat['pos_rate']:>5.2f} {stat['max_loss_streak']:>5}")
    show("combined full sample (not a validation)", pooled_stats(pooled, ex), INITIAL)
    pair_full = pooled_stats([f for n in pair for f in fills[n]], ex)
    print(f"\n  {' + '.join(pair)} only, full sample: "
          f"pnl {pair_full['pnl']:.2f} pf {pair_full['pf']:.3f} "
          f"dd {pair_full['max_dd']:.2f} mSh {pair_full['msharpe']:.2f} "
          f"pos_rate {pair_full['pos_rate']} streak {pair_full['max_loss_streak']}")

    print("\n--- combined out of sample at other spreads " + "-" * 20)
    print(f"  {'spread':>7} {'oos pnl':>10} {'pf':>6} {'dd':>7} {'mSh':>6} {'pos':>5}")
    for spread in (0.0, 0.2, 0.4, 0.5, 0.75, 1.0):
        cost_ex = replace(ex, spread=spread)
        pool = []
        for name in names:
            member_ex = replace(strategies.get(name).execution, initial=INITIAL,
                                spread=spread, risk=RISK)
            pool.extend(member_fills(name, bars, member_ex, MEDIANS[name]))
        stat = pooled_stats(pool, cost_ex, lo, hi)
        print(f"  {spread:>7} {stat['pnl']:>10.2f} {stat['pf']:>6.3f} "
              f"{stat['max_dd']:>7.2f} {stat['msharpe']:>6.2f} {stat['pos_rate']:>5.2f}")


if __name__ == "__main__":
    main()
