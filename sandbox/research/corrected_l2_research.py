"""Re-run fixed A1/A2/A3/A5 minute configurations with raw-tick exits.

No grid search or parameter selection occurs here.  A1 additionally reports the
available execution-adjusted withdrawal proxy; true standing-depth mode becomes
available after rebuilding ``nq_l2_features_1s`` with the updated builder.
"""
from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import strategies
from sandbox import tick_paths

NAMES = (
    "Liquidity Withdrawal (adds-only)",
    "Book Slope Asymmetry",
    "Kyle Lambda Continuation",
    "Air Pocket Fade",
)


def signals_for(strategy, bars, context, overrides=None):
    params = strategy.all_params(overrides)
    out = []
    for group in strategy.groups():
        out.extend(strategy.signals(bars, context, group, params))
    return out


def score(fills, ex):
    stat = metrics.stats(execution.size(fills, ex), initial=ex.initial)
    raw = sum(fill.points for fill in fills)
    return {
        "trades": len(fills),
        "points": round(raw, 2),
        "zero_cost_points": round(raw + ex.entry_cost * len(fills), 2),
        "pnl": stat["pnl"],
        "pf": stat["pf"],
        "positive_months": f"{stat['pos_months']}/{stat['n_months']}",
    }


def main():
    bars = data.load_bars()
    features = data.load_l2_features()
    print(
        f"{'strategy':<42} {'model':<22} {'trades':>7} {'points':>10} "
        f"{'0spread':>10} {'pnl':>10} {'pf':>7} {'pos':>7}"
    )
    for name in NAMES:
        strategy = strategies.get(name)
        context = strategy.context()
        variants = [("minute stop-first", None)]
        if name == "Liquidity Withdrawal (adds-only)":
            variants.extend(
                (
                    ("raw-tick ambiguous", None),
                    (
                        "tick + adjusted cancels",
                        {"withdrawal_mode": "execution_adjusted"},
                    ),
                )
            )
        for label, overrides in variants:
            signals = signals_for(strategy, bars, context, overrides)
            if label == "minute stop-first":
                fills = execution.resolve(bars, signals, strategy.execution)
            else:
                fills, _diagnostic = tick_paths.resolve(
                    bars, signals, strategy.execution, features
                )
            stat = score(fills, strategy.execution)
            print(
                f"{name:<42} {label:<22} {stat['trades']:>7} "
                f"{stat['points']:>10.2f} {stat['zero_spread_points']:>10.2f} "
                f"{stat['pnl']:>10.2f} {stat['pf']:>7.3f} "
                f"{stat['positive_months']:>7}"
            )
        if name != "Liquidity Withdrawal (adds-only)":
            signals = signals_for(strategy, bars, context)
            fills, diagnostic = tick_paths.resolve(
                bars, signals, strategy.execution, features
            )
            stat = score(fills, strategy.execution)
            print(
                f"{name:<42} {'raw-tick ambiguous':<22} {stat['trades']:>7} "
                f"{stat['points']:>10.2f} {stat['zero_spread_points']:>10.2f} "
                f"{stat['pnl']:>10.2f} {stat['pf']:>7.3f} "
                f"{stat['positive_months']:>7}"
            )
            print(
                f"  tick minutes: {diagnostic['resolved_minutes']}/"
                f"{diagnostic['ambiguous_minutes']} resolved"
            )


if __name__ == "__main__":
    main()
