"""Select, then validate, then the coin-flip null -- one symbol fully at a time.

WHY PER SYMBOL RATHER THAN PER STAGE. The whole queue is about thirteen hours:
`select` is ~6.7 minutes a symbol, `validate` about one, and `why` re-runs the
search three times over so it is ~2.2x `select`. That does not fit a night. If
the three stages ran as three passes, an eight-hour night would end with 35
selections, 20 validations and no nulls at all -- and a selection without its
null is not a result, it is the thing that has already returned +622% at t=4.19
from coin flips on this data ([[coin-flip-control-beats-real-signals]]).

Running each symbol end to end means an interrupted queue leaves a smaller
number of symbols that are actually finished. Ordered by where the study has
ever found anything, so the finished ones are the ones worth having.

WHAT EACH STAGE IS GATED ON.

`validate` scores every family whose `select` produced a winner -- a cell
clearing the profit-factor, drawdown, fill-rate and neighbour gates. It needs no
explicit restriction: `validate` already skips a family whose winner is `None`.

`why` is gated one step further, on families that ALSO made money on the
holdout. That is the subset the null can actually promote. A family that lost
money out of sample is settled by that alone -- `exness_families_report` never
consults the null to condemn one, only to bar one that made money -- so a null
over a losing family is compute spent on a question nothing will ask.

On ethusd that is 18 families rather than 19, and 27 to begin with; on symbols
where most families fail the holdout the saving is much larger, which is what
makes the full 35-symbol queue affordable in a night.

THE NULL IS WRITTEN TO THE `_sixth` FILENAME ON PURPOSE. `why` names its output
from the family set it was given, so a restricted run lands on
`..._null_<sym>_30m_<a+b+c...>.json` and `exness_families_report --scope sixth`
-- which looks for `..._null_<sym>_30m_sixth.json` -- would silently report
every family as `not-run`. Moving it is not a relabelling: the families missing
from the file are exactly those with no in-sample cell or no holdout profit, and
`null_best` returns `(None, None)` for an absent family, which the report
renders as `not-run` rather than as a pass. That is the honest rendering -- and
for the losing families the report has already settled them on their negative
holdout, without reaching for a null at all.

    python -m sandbox.research._sixth_pipeline --workers 16 --resume
    python -m sandbox.research._sixth_pipeline --symbols btc,nq --stages why
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from sandbox.research import cfd_families as ef
from sandbox.research._sixth_sweep import queue

SIXTH = set(ef.ALIASES["sixth"])
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATUS = os.path.join(ef.RESULTS, "SIXTH_PIPELINE_STATUS.json")


def select_path(symbol, bar):
    return ef.output_path(symbol, bar, SIXTH)


def null_path(symbol, bar, tag="sixth"):
    return os.path.join(
        ef.RESULTS,
        f"exness_families_null_{symbol}_{ef.label_bar(bar)}"
        f"{'_' + tag if tag else ''}.json")


def sealed(symbol, bar):
    return os.path.exists(select_path(symbol, bar))


def validated(symbol, bar):
    path = select_path(symbol, bar)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        return bool(json.load(handle).get("validation"))


def positive_families(symbol, bar):
    """Families whose `select` found a cell that cleared the gates."""
    path = select_path(symbol, bar)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return sorted(f for f, winner in payload["families"].items() if winner)


def positive_oos(symbol, bar):
    """Of those, the ones that also made money on the holdout.

    WHAT `why` IS RESTRICTED TO. This is the subset the null can actually
    promote: a family that lost money out of sample is settled by that alone,
    and `exness_families_report` consults the null only to BAR a family that
    made money, never to condemn one that did not. A null over a losing family
    is compute spent on a question nothing will ask.
    """
    path = select_path(symbol, bar)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    validation = payload.get("validation") or {}
    return sorted(f for f, entry in validation.items()
                  if (entry.get("oos") or {}).get("return_pct", 0) > 0)


def run(args_list, label):
    clock = time.time()
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m",
         "sandbox.research.cfd_families"] + args_list,
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    took = (time.time() - clock) / 60.0
    if result.returncode != 0 or "FAILED" in (result.stdout or ""):
        print(f"    {label} FAILED in {took:.1f} min "
              f"(exit {result.returncode})", flush=True)
        for line in ((result.stdout or "").strip().splitlines()[-8:]
                     + (result.stderr or "").strip().splitlines()[-8:]):
            print(f"      {line}", flush=True)
        return False, took, result.stdout or ""
    print(f"    {label} ok in {took:.1f} min", flush=True)
    return True, took, result.stdout or ""


def write_status(state):
    with open(STATUS, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--workers", type=int,
                        default=max(1, os.cpu_count() or 2))
    parser.add_argument("--stages", default="select,validate,why")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    stages = set(args.stages.replace(" ", "").split(","))
    bar = args.bar_minutes
    names = queue(args.symbols.replace(" ", "").split(",")
                  if args.symbols else None)
    started = time.time()
    state = {"started": time.strftime("%Y-%m-%d %H:%M:%S"),
             "bar_minutes": bar, "order": names, "symbols": {}}
    write_status(state)

    for index, symbol in enumerate(names, 1):
        elapsed = (time.time() - started) / 3600.0
        finished = [s for s, v in state["symbols"].items()
                    if v.get("stage") == "done"]
        rate = elapsed / len(finished) if finished else None
        eta = f", eta {(len(names) - index + 1) * rate:.1f}h" if rate else ""
        print(f"\n[{index}/{len(names)}] {symbol} "
              f"({elapsed:.1f}h elapsed{eta})", flush=True)
        entry = {"stage": "select"}
        state["symbols"][symbol] = entry
        write_status(state)

        common = ["--symbols", symbol, "--bar-minutes", str(bar),
                  "--workers", str(args.workers), "--stale-spreads"]

        if "select" in stages and not (args.resume and sealed(symbol, bar)):
            ok, took, _ = run(["select"] + common + ["--groups", "sixth"],
                              "select")
            entry["select_min"] = round(took, 1)
            if not ok or not sealed(symbol, bar):
                entry["stage"] = "select_failed"
                write_status(state)
                continue
        elif sealed(symbol, bar):
            print("    select already sealed", flush=True)

        selected = positive_families(symbol, bar)
        entry["positive_select"] = len(selected)
        entry["dead"] = len(SIXTH) - len(selected)

        if "validate" in stages and not (args.resume and validated(symbol, bar)):
            entry["stage"] = "validate"
            write_status(state)
            ok, took, _ = run(["validate"] + common + ["--groups", "sixth"],
                              "validate")
            entry["validate_min"] = round(took, 1)
            if not ok:
                entry["stage"] = "validate_failed"
                write_status(state)
                continue
        elif validated(symbol, bar):
            print("    validate already sealed", flush=True)

        # THE NULL IS GATED ON THE HOLDOUT, NOT ON THE SELECTION. See the module
        # docstring: a family that lost money out of sample is already settled,
        # and its null would be read by nothing.
        promoted = positive_oos(symbol, bar)
        entry["positive_oos"] = len(promoted)

        # ORDER MATTERS, AND GETTING IT WRONG MISREPORTS A CLEAN RESULT AS A
        # FAILURE. `validated` tests whether the validation dict is non-empty,
        # and it is legitimately empty when `select` found no winner at all --
        # `validate` iterates the winners and there are none. Testing it first
        # labelled ethbtc, where 0 of 27 families cleared the in-sample gates,
        # as `why_ungated`, which reads as "the pipeline skipped a stage" and
        # is really "there was nothing to score". So the empty-selection case
        # is settled before the missing-validation case.
        if "why" in stages and not selected:
            print("    why skipped: select found no winner in any family",
                  flush=True)
            entry["stage"] = "done"
            write_status(state)
            continue

        if "why" in stages and not validated(symbol, bar):
            print("    why SKIPPED: validation missing, cannot gate on it",
                  flush=True)
            entry["stage"] = "why_ungated"
            write_status(state)
            continue

        if "why" in stages and not promoted:
            print("    why skipped: nothing survived the holdout", flush=True)
            entry["stage"] = "done"
            write_status(state)
            continue

        if "why" in stages:
            if args.resume and os.path.exists(null_path(symbol, bar)):
                print("    null already present", flush=True)
                entry["stage"] = "done"
                write_status(state)
                continue
            entry["stage"] = "why"
            write_status(state)
            print(f"    why over {len(promoted)} of {len(selected)} "
                  f"selected families (positive holdout)", flush=True)
            ok, took, _ = run(["why"] + common + ["--families",
                                                  ",".join(promoted)], "why")
            entry["why_min"] = round(took, 1)
            if not ok:
                entry["stage"] = "why_failed"
                write_status(state)
                continue
            # Move the restricted null onto the `_sixth` name so the report
            # finds it. See the module docstring: absent families render as
            # `not-run`, which is correct for a family with no in-sample cell.
            produced = null_path(symbol, bar, ef.scope_tag(set(promoted)))
            wanted = null_path(symbol, bar)
            if os.path.exists(produced) and produced != wanted:
                os.replace(produced, wanted)
                entry["null_renamed_from"] = os.path.basename(produced)

        entry["stage"] = "done"
        write_status(state)

    state["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["hours"] = round((time.time() - started) / 3600.0, 2)
    done = [s for s, v in state["symbols"].items() if v.get("stage") == "done"]
    state["done"] = done
    write_status(state)
    print(f"\n=== {len(done)}/{len(names)} symbols complete in "
          f"{state['hours']}h ===", flush=True)
    print(f"status: {STATUS}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
