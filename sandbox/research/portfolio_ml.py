"""Five strategies in one $1000 account, with the ML-gated OFI variant.

Same shape as `portfolio.py`, with these differences:

  * the OFI member is the registered ML-gated strategy. It needs no special
    handling here any more: `strategies/ofi_momentum.py` applies the frozen gate
    itself, as `ofi_momentum.rs` does, so it goes through `rule_fills` like the
    others.
  * sizing reads each member's own `Execution.risk` and `Execution.leverage`,
    which now mirror the `ENTRY_RISK_FRACTION` and `ENTRY_LEVERAGE` compiled into
    each Rust file. `execution.size` takes one risk for the whole account, so its
    event queue is re-implemented here per fill; the logic is otherwise identical.
  * **Absorption Reversal runs at 2x leverage**, from its own
    `Execution.leverage` mirroring `ENTRY_LEVERAGE` in the Rust file. It scales
    the raw quantity before the step rounding, so the risk-fraction term and the
    margin cap both double.
  * **Noise Momentum 2** joins, via `noise_momentum_2_replica`. It does not fit
    the `Signal`/`Fill` pipeline -- notional sizing with no stop distance, and a
    laddered half-exit -- so it is simulated bar by bar and emits legs sized as a
    fraction of live equity.

The other four members run at the parameters compiled into their Rust files
today (`Strategy.defaults`), which for the three rule strategies are also their
walk-forward medians.

Limitations, inherited and real:
  * margin is checked per trade, not across concurrently open positions, so five
    strategies trading at once can exceed a single-position notional cap. This
    now matters considerably more than it did with three: Noise Momentum 2 sizes
    off `equity / margin` rather than off a stop, so it alone can carry the
    account's whole notional, and doubling Absorption's size compounds it.
  * the ML model was trained through 2026-07-23 on this sample, so *no* window
    here is genuinely out of sample for the OFI member.
  * Noise Momentum 2 reads `ohlcv` bars, which start earlier than the
    `level_two` bars the other four need, so the full-sample table opens with
    months only it traded. The out-of-sample window is unaffected.
  * 2x leverage on Absorption Reversal is an override with no walk-forward
    behind it. Its drawdown contribution scales linearly with it.
"""
from dataclasses import replace
import math

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox.research import noise_momentum_2_replica as nm2
from sandbox import strategies
from sandbox import walkforward as wf

INITIAL = 1000.0

#: member -> registry name, or None for the strategy simulated locally. Risk and
#: leverage are read off each strategy's `Execution` rather than restated here:
#: one stale copy of ENTRY_RISK_FRACTION is what made the earlier runs of this
#: script disagree with the engine.
MEMBERS = {
    "Deep OFI Momentum": "Deep OFI Momentum",
    "Hourly Delta Reversal": "Hourly Delta Reversal",
    "Absorption Reversal": "Absorption Reversal",
    "Noise Momentum 2": None,
}

#: The span the three rule strategies' walk-forward folds tested. Earlier months
#: are data their medians were fitted on.
OOS = ("2025-08-01", "2026-08-01")


def rule_fills(name, bars, ex):
    strategy = strategies.get(name)
    context = strategy.context()
    params = strategy.all_params(None)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    return execution.resolve(bars, signals, ex)


def size_account(entries, margin, step, initial=INITIAL):
    """`execution.size` generalised: per-entry sizing rules in one shared account.

    `entries` are `(entry_ts, exit_ts, member, quantity_fn, points)` where
    `quantity_fn(equity)` returns the units to trade. Equity compounds across
    members, so an entry is sized by every trade that closed before it opened.

    Returns `(sized, per_member)`; a member's slice is its contribution *inside*
    this account, not its standalone result.
    """
    equity = initial
    queue = []      # (exit_ts, pnl, entry_ts, member)
    sized, per_member = [], {}

    def settle(until):
        nonlocal equity
        queue.sort(key=lambda row: row[0])
        while queue and queue[0][0] <= until:
            _exit_ts, pnl, entry_ts, owner = queue.pop(0)
            equity += pnl
            sized.append((entry_ts, pnl))
            per_member.setdefault(owner, []).append((entry_ts, pnl))

    # Legs of one laddered position share an entry timestamp; grouping keeps
    # them on a single sizing decision rather than two.
    for entry_ts, group in _grouped(entries):
        settle(entry_ts)
        for _ts, exit_ts, member, quantity_fn, points in group:
            quantity = quantity_fn(equity)
            if quantity < step:
                continue
            queue.append((exit_ts, points * quantity, entry_ts, member))
    settle(math.inf)
    sized.sort()
    return sized, per_member


def _grouped(entries):
    ordered = sorted(entries, key=lambda row: row[0])
    index = 0
    while index < len(ordered):
        stop = index
        while stop < len(ordered) and ordered[stop][0] == ordered[index][0]:
            stop += 1
        yield ordered[index][0], ordered[index:stop]
        index = stop


def risk_entry(fill, member, risk, leverage, margin, step):
    def quantity(equity):
        raw = min(equity * risk / fill.stop, equity / margin / fill.price) * leverage
        return math.floor(raw / step) * step
    return (fill.entry_ts, fill.exit_ts, member, quantity, fill.points)


def notional_entry(leg, member, step):
    """Noise Momentum 2 rounds the whole position, then trades `fraction` of it."""
    def quantity(equity):
        whole = round(equity * leg.qty_per_equity / step) * step
        return whole * leg.fraction if whole >= step else 0.0
    return (leg.entry_ts, leg.exit_ts, member, quantity, leg.points)


def window(rows, lo, hi):
    return [(ts, pnl) for ts, pnl in rows
            if (lo is None or ts >= lo) and (hi is None or ts < hi)]


def show(label, stat, initial):
    print(f"\n--- {label} " + "-" * max(4, 62 - len(label)))
    print("  " + "  ".join(f"{f}={stat[f]}" for f in wf.FIELDS))
    wf.print_months(stat["months"], initial)
    return stat


def main():
    names = list(MEMBERS)
    bars = data.load_bars("level_two", "nq")
    base_ex = strategies.get("Hourly Delta Reversal").execution
    cost, margin, step = base_ex.entry_cost, base_ex.margin, base_ex.step
    lo, hi = metrics.split_ts(OOS[0]), metrics.split_ts(OOS[1])

    print("combined account: " + " + ".join(names))
    print(f"  level_two {data.bar_range('level_two', 'nq')}, "
          f"ohlcv {data.bar_range('ohlcv', 'nq')}, "
          f"initial ${INITIAL:.0f}, Forex NQ, cost {cost}")

    entries = []
    for member, registry_name in MEMBERS.items():
        if registry_name is None:
            legs = nm2.run(data.load_bars("ohlcv", "nq"), cost)
            entries += [notional_entry(leg, member, step) for leg in legs]
            positions = len({leg.entry_ts for leg in legs})
            print(f"  {member}: {positions} positions, {len(legs)} legs, "
                  f"notional sizing (equity/margin)")
            continue
        member_ex = replace(strategies.get(registry_name).execution,
                            initial=INITIAL, slippage=cost, commission_per_lot=0.0)
        fills = rule_fills(registry_name, bars, member_ex)
        entries += [risk_entry(f, member, member_ex.risk, member_ex.leverage,
                               margin, step)
                    for f in fills]
        print(f"  {member}: {len(fills)} fills, risk {member_ex.risk}, "
              f"leverage {member_ex.leverage}")

    sized, per_member = size_account(entries, margin, step)

    for title, (a, b) in (
            (f"OUT OF SAMPLE ({OOS[0]} .. {OOS[1]})", (lo, hi)),
            ("FULL SAMPLE -- early months are in-sample", (None, None))):
        span = (a, b) if a is not None else None
        print("\n" + "=" * 72)
        print(f"=== COMBINED ${INITIAL:.0f} ACCOUNT, {title}")
        print("=" * 72)
        combined = show("combined", metrics.stats(window(sized, a, b),
                                                  initial=INITIAL, span=span),
                        INITIAL)

        print("\n  contribution inside this account "
              "(shared equity, so these are not standalone results):")
        print(f"  {'strategy':<24} {'pnl':>10} {'share':>7} {'trades':>7} "
              f"{'pf':>6} {'win':>6} {'best mo':>10} {'worst mo':>10}")
        total = combined["pnl"]
        for member in names:
            rows = window(per_member.get(member, []), a, b)
            if not rows:
                print(f"  {member:<24} {'--':>10}")
                continue
            stat = metrics.stats(rows, initial=INITIAL, span=span)
            share = 100 * stat["pnl"] / total if total else 0.0
            print(f"  {member:<24} {stat['pnl']:>10.2f} {share:>6.1f}% "
                  f"{stat['trades']:>7} {stat['pf']:>6.3f} "
                  f"{stat['win_rate']:>6.3f} {stat['best_month']:>10.2f} "
                  f"{stat['worst_month']:>10.2f}")


if __name__ == "__main__":
    main()
