"""Re-run every sealed strategy in results/exness/ at one broker, params fixed.

    CFD_BROKER=fundednext py -m sandbox.research._broker_compare
    CFD_BROKER=exness     py -m sandbox.research._broker_compare

No re-selection: each file's own `params` are scored as sealed, on the same
holdout (2025-01-01 .. 2026-08-16) and on 2024 alone -- the only in-sample year
FundedNext has minute data for. Writes one JSON line per strategy to
`results/broker_compare_<broker>.jsonl`, appending, and skips files already
done so an interrupted run resumes. `--only sym_bar,...` limits the groups.
"""
import argparse
import glob
import json
import os
import platform
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone

platform._wmi = None

from sandbox.research import cfd_families as ef  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
Y2024 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
KEEP = ("return_pct", "max_dd_pct", "trades", "pf", "win_rate", "monthly_sharpe",
        "breakeven_bp", "gross_bp_per_trade", "edge_vs_drift_t_stat", "fill_rate")


def brief(result):
    return {k: result.get(k) for k in KEEP}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    out_path = os.path.join(RESULTS, f"broker_compare_{ef.BROKER}.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as handle:
            done = {json.loads(line)["file"] for line in handle if line.strip()}
    quoted = {s for s, r in ef.load_specs()["symbols"].items() if "error" not in r}

    groups = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(RESULTS, "exness", "*.json"))):
        name = os.path.basename(path)
        if name.startswith("SURVIVORS"):
            continue
        with open(path, encoding="utf-8") as handle:
            sealed = json.load(handle)
        if sealed["symbol"] not in quoted or name in done:
            continue
        groups[(sealed["symbol"], sealed["bar_minutes"])].append((name, sealed))
    wanted = {token for token in args.only.split(",") if token}
    todo = [(k, v) for k, v in sorted(groups.items())
            if not wanted or f"{k[0]}_{k[1]}" in wanted]
    print(f"{ef.BROKER}: {sum(len(v) for _, v in todo)} strategies in "
          f"{len(todo)} groups -> {out_path}", flush=True)

    for (symbol, bar), items in todo:
        started = time.time()
        try:
            ef.BAR_MINUTES = bar
            ef.resolve(symbol, allow_stale=True)
            ef.prewarm(symbol, "validate", bar)
            families = {sealed["family"] for _, sealed in items}
            bars, ctx = ef.context(symbol, "validate", bar, families)
        except (Exception, SystemExit) as error:  # noqa: BLE001
            print(f"  {symbol} {bar}m: cannot build -- {error}", flush=True)
            with open(out_path, "a", encoding="utf-8") as handle:
                for name, sealed in items:
                    handle.write(json.dumps({"file": name, "symbol": symbol, "bar": bar,
                                             "family": sealed["family"], "broker": ef.BROKER,
                                             "error": str(error)[:300]}) + "\n")
            continue
        for name, sealed in items:
            row = {"file": name, "symbol": symbol, "bar": bar,
                   "family": sealed["family"], "broker": ef.BROKER}
            try:
                params = ef.rehydrate(sealed["params"])
                row["oos"] = brief(ef.backtest(sealed["family"], bars, ctx, params,
                                               lo=ef.IS_END, hi=ef.OOS_END))
                row["y2024"] = brief(ef.backtest(sealed["family"], bars, ctx, params,
                                                 lo=Y2024, hi=ef.IS_END))
            except (Exception, SystemExit) as error:  # noqa: BLE001
                row["error"] = f"{type(error).__name__}: {error}"[:300]
                traceback.print_exc()
            with open(out_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        print(f"  {symbol} {bar}m: {len(items)} done in {time.time() - started:.0f}s",
              flush=True)


if __name__ == "__main__":
    main()
