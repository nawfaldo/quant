"""Hourly Delta Reversal and Deep OFI Momentum sharing one $1,000 account.

Not two backtests added together. `execution.size` sets each position from the
*live* balance, so every OFI trade is sized by an equity the HDR trades have
already moved, and joint compounding means the combined PnL is not the sum of
the standalone ones. `execution.size` cannot express this directly because it
takes one `Execution`, and these two strategies do not share one: HDR risks 1.0%
at leverage 1.0, OFI risks 0.5% at leverage 2.0, exactly as their Rust files
compile. `_joint_size` below is that function with the account state pulled out
of the config and the per-fill terms left in.

**Why this is the NQ pair and not the five-strategy account that was asked for.**
The three BTC Maroy sleeves exist only in `live_trade/src/strategies/idk/*.rs`; there
is no `sandbox` replica to run them from, and the Rust engine's `run_combined`
-- which does support a multi-symbol shared account -- can only be reached
through a server that is currently running a binary predating today's changes.
Its OFI still carries the deleted ML gate and its HDR the old tuned constants, so
a five-way run through that API would report this morning's strategies. Rebuild,
restart, and `run_combined` is the faithful path for all five.

Two honest limits, both inherited from `execution.size`:

  * Margin is checked per trade, not across concurrently open positions, so two
    strategies holding at once can exceed what a single-position cap allows. At
    $1,000 the risk fraction usually binds first, but this flatters a two-member
    account more than a one-member one.
  * Contribution is reported as each strategy's realised PnL *inside the joint
    run*, which is the honest split, but the two are not independent: remove one
    and the other's sizing changes. The standalone columns are printed beside it
    so the interaction is visible rather than implied.

    py -B -m sandbox.research.nq_account
    py -B -m sandbox.research.nq_account --from 2025-02-01
"""
import argparse
import json
import math
import os
from dataclasses import replace

from sandbox import data, execution, metrics, strategies, walkforward

INITIAL = 1000.0
MEMBERS = ("Hourly Delta Reversal", "Deep OFI Momentum")
DEFAULT_FROM = "2025-02-01"


def _joint_size(tagged, initial):
    """`execution.size` over fills from several strategies on one balance.

    `tagged` is `[(name, fill, ex), ...]`. Each fill is sized by *its own*
    strategy's risk, leverage, margin and step, against the shared running
    equity. Returns `[(entry_ts, pnl, name), ...]`.
    """
    equity = initial
    queue = []      # (exit_ts, pnl, entry_ts, name)
    sized = []
    for name, fill, ex in sorted(tagged, key=lambda row: row[1].entry_ts):
        queue.sort()
        while queue and queue[0][0] <= fill.entry_ts:
            _exit_ts, pnl, entry_ts, owner = queue.pop(0)
            equity += pnl
            sized.append((entry_ts, pnl, owner))
        raw = min(equity * ex.risk / fill.stop,
                  equity / ex.margin / fill.price) * ex.leverage
        quantity = math.floor(raw / ex.step) * ex.step
        if quantity < ex.step:
            continue
        queue.append((fill.exit_ts, fill.points * quantity, fill.entry_ts, name))
    for _exit_ts, pnl, entry_ts, owner in sorted(queue):
        equity += pnl
        sized.append((entry_ts, pnl, owner))
    return sized


def member_fills(name, lo):
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    params = strategy.all_params()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    fills = [f for f in execution.resolve(bars, signals, ex) if f.entry_ts >= lo]
    return strategy, ex, fills, bars


def _monthly(sized):
    out = {}
    for ts, pnl, _name in sized:
        out[metrics.month_key(ts)] = out.get(metrics.month_key(ts), 0.0) + pnl
    return dict(sorted(out.items()))


def _curve_stats(sized, initial):
    """Drawdown and equity over the joint, time-ordered realised series."""
    equity = peak = initial
    max_dd = 0.0
    for _ts, pnl, _name in sorted(sized):
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return equity, max_dd


def run(lo_iso, out_path=None):
    lo = metrics.split_ts(lo_iso)
    tagged, standalone, span_hi = [], {}, 0
    for name in MEMBERS:
        strategy, ex, fills, bars = member_fills(name, lo)
        span_hi = max(span_hi, bars[-1][data.TS] + 60)
        tagged.extend((name, f, ex) for f in fills)
        stat = metrics.stats(execution.size(fills, ex), initial=INITIAL,
                             span=(lo, bars[-1][data.TS] + 60))
        standalone[name] = {
            "trades": stat["trades"], "pnl": stat["pnl"], "pf": stat["pf"],
            "max_dd": stat["max_dd"], "msharpe": stat["msharpe"],
            "months": stat["months"],
        }

    sized = _joint_size(tagged, INITIAL)
    final, max_dd = _curve_stats(sized, INITIAL)
    joint_months = _monthly(sized)
    by_member = {}
    for name in MEMBERS:
        rows = [(ts, pnl, n) for ts, pnl, n in sized if n == name]
        by_member[name] = {
            "trades": len(rows),
            "pnl": round(sum(pnl for _ts, pnl, _n in rows), 2),
            "months": {k: round(v, 2) for k, v in _monthly(rows).items()},
        }

    total = round(final - INITIAL, 2)
    months_all = sorted(set(joint_months) | {m for v in by_member.values()
                                             for m in v["months"]})

    print(f"\nShared ${INITIAL:,.0f} account -- {' + '.join(MEMBERS)}")
    print(f"  {lo_iso} .. {metrics.month_key(span_hi)}, spread 0.2, joint compounding")
    print(f"\n  final equity {final:,.2f}   net {total:+,.2f} "
          f"({total / INITIAL:+.1%})   max drawdown {max_dd:,.2f} "
          f"({max_dd / INITIAL:.1%})")

    print(f"\n  contribution inside the joint run")
    print(f"    {'strategy':<24}{'trades':>8}{'pnl':>11}{'share':>9}"
          f"     standalone (pnl / dd / pf)")
    for name in MEMBERS:
        m, s = by_member[name], standalone[name]
        share = m["pnl"] / total if total else 0.0
        print(f"    {name:<24}{m['trades']:>8}{m['pnl']:>11.2f}{share:>8.0%}"
              f"     {s['pnl']:>8.2f} / {s['max_dd']:>7.2f} / {s['pf']:.2f}")

    print(f"\n  monthly, joint account")
    print(f"    {'month':<10}" + "".join(f"{n.split()[0][:9]:>11}" for n in MEMBERS)
          + f"{'total':>11}{'equity':>12}")
    equity = INITIAL
    for month in months_all:
        cells = ""
        for name in MEMBERS:
            cells += f"{by_member[name]['months'].get(month, 0.0):>11.2f}"
        month_total = joint_months.get(month, 0.0)
        equity += month_total
        print(f"    {month:<10}{cells}{month_total:>11.2f}{equity:>12.2f}")

    result = {
        "members": list(MEMBERS), "initial": INITIAL, "from": lo_iso,
        "final_equity": round(final, 2), "net": total,
        "max_drawdown": round(max_dd, 2),
        "contribution": by_member, "standalone": standalone,
        "joint_months": {k: round(v, 2) for k, v in joint_months.items()},
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"\n  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="lo", default=DEFAULT_FROM)
    parser.add_argument("--out", default="sandbox/results/nq_account.json")
    args = parser.parse_args()
    run(args.lo, out_path=args.out)


if __name__ == "__main__":
    main()
