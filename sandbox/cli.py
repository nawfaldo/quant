"""Explore, backtest, sweep, and validate strategy replicas."""

import argparse
import json

from sandbox import metrics
from sandbox import purged_cv
from sandbox import search
from sandbox import strategies
from sandbox import validation
from sandbox import walkforward
from sandbox.paths import result_path


def _value(text):
    """Parse a friendly command-line scalar."""
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return text


def _override(text):
    """Parse NAME=VALUE once, close to argparse's error reporting."""
    name, separator, value = text.partition("=")
    if not separator or not name or not value:
        raise argparse.ArgumentTypeError("expected NAME=VALUE")
    return name, _value(value)


def _add_overrides(parser, help_text):
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        type=_override,
        metavar="NAME=VALUE",
        help=help_text,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="registered strategies")

    run_cmd = sub.add_parser("backtest", help="one configuration")
    run_cmd.add_argument("strategy")
    _add_overrides(run_cmd, "override a parameter; repeatable")

    sweep_cmd = sub.add_parser("sweep", help="grid sweep + robustness ranking")
    sweep_cmd.add_argument("strategy")
    _add_overrides(
        sweep_cmd,
        "override a fixed parameter (for example an evaluation window); repeatable",
    )
    sweep_cmd.add_argument("--top", type=int, default=25)
    sweep_cmd.add_argument("--split", default=search.DEFAULT_SPLIT,
                           help="train/holdout boundary (ISO date)")
    sweep_cmd.add_argument("--out", default=None,
                           help="JSON output path (default <strategy>_sweep.json)")
    sweep_cmd.add_argument("--min-trades", type=int, default=search.DEFAULT_MIN_TRADES,
                           help="minimum trades a candidate must produce")
    sweep_cmd.add_argument("--min-pos-months", type=int,
                           default=search.DEFAULT_MIN_POS_MONTHS,
                           help="minimum profitable months")
    sweep_cmd.add_argument("--max-loss-streak", type=int,
                           default=search.DEFAULT_MAX_LOSS_STREAK,
                           help="longest run of consecutive losing months allowed")
    sweep_cmd.add_argument("--max-top-month-share", type=float,
                           default=search.DEFAULT_MAX_TOP_MONTH_SHARE,
                           help="cap on the best month's share of gross profit")

    wf_cmd = sub.add_parser("walkforward",
                            help="anchored walk-forward selection (Stage 4)")
    wf_cmd.add_argument("strategy")
    wf_cmd.add_argument("--initial", type=float, default=walkforward.INITIAL)

    check_cmd = sub.add_parser("validate", help="replica vs live /api/run")
    check_cmd.add_argument("strategy")

    args = parser.parse_args()

    if args.command == "list":
        for name, strategy in sorted(strategies.REGISTRY.items()):
            print(f"{name}\n  bars={strategy.bars} symbol={strategy.symbol} "
                  f"axes={', '.join(sorted(strategy.grid))}")
        return

    strategy = strategies.get(args.strategy)

    if args.command == "backtest":
        stats, _ = search.backtest(strategy, dict(args.set))
        print(json.dumps(stats, indent=1))
    elif args.command == "sweep":
        strategy = strategy.configured(dict(args.set))
        slug = strategy.name.lower().replace(" ", "_")
        out = args.out or result_path(f"{slug}_sweep.json")
        search.sweep(strategy, top=args.top, out_path=out,
                     min_trades=args.min_trades, min_pos_months=args.min_pos_months,
                     max_loss_streak=args.max_loss_streak,
                     max_top_month_share=args.max_top_month_share,
                     split=metrics.split_ts(args.split))
    elif args.command == "walkforward":
        result = walkforward.run(strategy, initial=args.initial)
        median, stitched = walkforward.report(result)
        if median:
            walkforward.verify(result, median, "median configuration")
        # Stage 4b runs on the same fills: the walk-forward is blind to the
        # first third of the sample and the gate needs that coverage.
        cv = purged_cv.run(strategy, cells=result["cells"], ex=result["ex"])
        walkforward.gate(result, stitched, cv["blocks"])
    elif args.command == "validate":
        raise SystemExit(0 if validation.validate(strategy) is not False else 1)


if __name__ == "__main__":
    main()
