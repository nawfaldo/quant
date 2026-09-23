"""What the compiled strategies do now, against what they did this morning.

`defaults_test.json` holds the pre-change measurement: its `compiled` rows are
the constants `live_trade/src/strategies/idk/` carried before 2026-08-05. This script
re-measures whatever is registered *today* over the same pinned window and prints
the two side by side, so "what changed" is a table rather than a recollection.

It re-measures rather than reusing the defaults test's `a-priori` rows, which
would be close but wrong: OFI's entry window moved again afterwards, when the
Rust file's `LAST_ENTRY_MINUTE` became `EXIT_MINUTE - TIME_STOP_MINUTES` and the
replica's hardcoded 900 was corrected to 925.

Nothing here selects anything, so nothing is charged to `trials.json`.

    py -B -m sandbox.research.compiled_state
"""
import argparse
import json
import os
from pathlib import Path

from dataclasses import replace

from sandbox import strategies, trials
from sandbox.research import defaults_test as dt
from sandbox.research import forward_test as ft

BEFORE_PATH = Path(__file__).resolve().parent.parent / "results" / "defaults_test.json"

#: Map the defaults-test label back to a strategy name.
BEFORE_LABEL = "compiled"

#: The constants the Rust files carried before 2026-08-05, **with the leverage
#: the engine actually applied**.
#:
#: `defaults_test.json`'s `compiled` rows are not a like-for-like risk baseline:
#: they were produced before the replica leverage drift was found, so OFI ran at
#: 1.0 there against a compiled 2.0. Comparing today's drawdown against those
#: rows flatters the change on exactly the axis a safety question cares about, so
#: this arm re-measures the old configuration at the size it really traded.
#:
#: HDR is unaffected -- it has no `ENTRY_LEVERAGE` and was 1.0 in both -- and is
#: included anyway so both rows come from one code path.
PRIOR = {
    "Hourly Delta Reversal": {
        "leverage": 1.0,
        "params": {"buy_delta": 50, "sell_delta": 300, "rr": 1.25,
                   "short_trend_days": 35, "skip_thursday": True, "k": 0.2},
    },
    "Deep OFI Momentum": {
        "leverage": 2.0,
        "params": {"ml_gate": True, "entry_from": 660, "entry_to": 900},
    },
}


def _before_rows():
    if not BEFORE_PATH.exists():
        return {}
    payload = json.loads(BEFORE_PATH.read_text())
    return {row["strategy"]: row
            for row in payload["rows"]
            if row["label"].endswith(BEFORE_LABEL)}


FIELDS = (
    ("trades", "trades", "{:>7.0f}"),
    ("pnl", "pnl", "{:>9.2f}"),
    ("pf", "pf", "{:>6.2f}"),
    ("msharpe", "mSharpe", "{:>8.2f}"),
    ("max_dd", "maxDD", "{:>8.2f}"),
    ("pos_rate", "pos", "{:>6.2f}"),
    ("max_loss_streak", "strk", "{:>5.0f}"),
    ("points_per_trade", "pts/tr", "{:>8.2f}"),
    ("edge_t", "t", "{:>6.2f}"),
)


def _line(label, stat):
    cells = "".join(fmt.format(stat.get(key, 0)) for key, _h, fmt in FIELDS)
    return f"  {label:<22}{cells}"


def _header():
    heads = ""
    for _key, head, fmt in FIELDS:
        width = ""
        for char in fmt.split(">")[1]:
            if not char.isdigit():
                break
            width += char
        heads += f"{head:>{int(width)}}"
    return f"  {'':<22}{heads}"


def _at_leverage(name, overrides, leverage, n_trials):
    """`dt.evaluate`, but with the execution leverage the engine really used."""
    strategy = strategies.get(name)
    original = strategy.execution
    strategy.execution = replace(original, leverage=leverage)
    try:
        return dt.evaluate(name, f"{name} -- prior", overrides, n_trials)
    finally:
        strategy.execution = original


def run(out_path=None):
    before = _before_rows()
    rows = []
    for name in ft.STRATEGIES:
        n_trials = trials.total(name)
        now = dt.evaluate(name, f"{name} -- now", {}, n_trials)
        prior = PRIOR[name]
        was_live = _at_leverage(name, prior["params"], prior["leverage"], n_trials)
        rows.append({"strategy": name, "before": before.get(name),
                     "was_live": was_live, "prior_leverage": prior["leverage"],
                     "now": now})

    for window in ("full", "oos"):
        title = ("full sample" if window == "full"
                 else f"from {dt.OOS_FROM} (out-of-sample span)")
        print(f"\n{title} .. {dt.PINNED_TO}, ${dt.INITIAL:,.0f}")
        print(_header())
        print("  " + "-" * 82)
        for row in rows:
            name, lev = row["strategy"][:20], row["prior_leverage"]
            print(_line(f"{name} was x{lev:g}", row["was_live"][window]))
            print(_line(f"{name} now", row["now"][window]))
            print()

    result = {
        "pinned_to": dt.PINNED_TO,
        "oos_from": dt.OOS_FROM,
        "before_source": str(BEFORE_PATH.name),
        "rows": rows,
        "trials_charged": 0,
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="sandbox/results/compiled_state.json")
    run(out_path=parser.parse_args().out)


if __name__ == "__main__":
    main()
