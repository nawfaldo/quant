"""The combined book, priced at FundedNext. Same engine as
`exness_combined_strategies`; only the broker and its inputs change.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user**, and every reported result must
carry the per-sleeve table -- both rules from `exness_combined_strategies`
apply unchanged.

WHAT THIS IS. `exness_combined_strategies` picks sleeves that do not lose
together from the sealed `cfd_families` cells and replays them on one shared,
marked-to-market balance. Every piece of that is broker-neutral except the
inputs, so this module reuses it whole and swaps:

  broker      `CFD_BROKER=fundednext` is forced before `cfd_families` loads,
              so every sleeve is priced off `fundednext_*_1m` fills, the
              FundedNext spread snapshot and FundedNext's commission.
  pool        `results/fundednext/` -- the sealed cells in `results/exness/`
              with `out_of_sample` REPLACED by the same parameters re-scored at
              FundedNext (`survivors` writes it). Selection ranks and gates on
              those FundedNext holdout numbers, never on the Exness ones. The
              in-sample block and the coin-flip null are kept as sealed: the
              parameters, the vendor bars they decide on and the null do not
              depend on the broker.
  dropped     the EXTERNAL level-two sleeves (Databento/Bookmap NQ, an Exness
              research path), the precomputed Exness tick-cost maps, and the
              per-sleeve SHOWN_EQUITY / SLEEVE_SCALE / SIZING_EQUITY_CAP /
              MARGIN_EQUITY_FLOOR tables -- those were tuned to push Exness
              sleeves over a 0.01 lot floor on a $400-$500 account.
  account     $6,000, the FundedNext balance, instead of $500.
  output      `results/fundednext_combined_strategies.json`.

WHAT FUNDEDNEXT CANNOT TRADE. No crypto, stocks, XAL/XNI or gold crosses, so
every ETHUSD, BTC and xau-cross sleeve of the Exness canon is out of reach.

NO EXNESS EXCLUSIONS ARE INHERITED. The Exness book's `EXCLUDE_SYMBOLS` and
`EXCLUDE` are operator decisions about that account (feeds it does not carry,
sleeves pinned or dropped on its record), so here both are empty and every
cell FundedNext can price is eligible, `nq` and `es` included. The Exness
module also switches `es` to `es_30m` and an 08:30 session at import; that is
undone below so an es cell runs on the data and session it was sealed on.

THINGS THIS DOES NOT MODEL. FundedNext is a prop account: it fails on a daily
loss limit and a maximum loss from the starting balance. The replay reports
marked-to-market drawdown exactly as the Exness book does, but it does not
stop the account at a breach -- read `--target-dd` against FundedNext's limits.
Commission is FundedNext's published rate, not a measurement.

    py -m sandbox.research.fundednext_combined_strategies survivors
    py -m sandbox.research.fundednext_combined_strategies build
    py -m sandbox.research.fundednext_combined_strategies build --null
    py -m sandbox.research.fundednext_combined_strategies size
    py -m sandbox.research.fundednext_combined_strategies build --members canon
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

# BEFORE ANY `cfd_families` IMPORT. The broker profile binds at import, so a
# process that already imported it for Exness cannot be switched afterwards.
if os.environ.get("CFD_BROKER", "fundednext") != "fundednext":
    raise SystemExit(f"CFD_BROKER={os.environ['CFD_BROKER']}; this module "
                     f"prices FundedNext only")
os.environ["CFD_BROKER"] = "fundednext"

from sandbox.research import cfd_families as ef  # noqa: E402

if ef.BROKER != "fundednext":
    raise SystemExit("cfd_families was imported for another broker earlier in "
                     "this process; run this module in a fresh interpreter")

# The Exness module rebinds `es` at import (PREFER_30M, an 08:30 session).
# Record what cfd_families had first so it can be put back.
_ES_SESSION = ef.SESSION.get("es")
_ES_PREFER_30M = "es" in ef.PREFER_30M

from sandbox.research import exness_combined_strategies as base  # noqa: E402

if _ES_SESSION is None:
    ef.SESSION.pop("es", None)
else:
    ef.SESSION["es"] = _ES_SESSION
if not _ES_PREFER_30M:
    ef.PREFER_30M.discard("es")
# Its `_context` also checks the es_1m -> Yahoo es_30m handoff before every es
# context. With es back on es_1m that handoff is not read, so the check is off.
base._validate_es_transition = lambda: None

RESULTS = ef.RESULTS
SOURCE_DIR = os.path.join(RESULTS, "exness")
SURVIVOR_DIR = os.path.join(RESULTS, "fundednext")
OUT_PATH = os.path.join(RESULTS, "fundednext_combined_strategies.json")

#: The FundedNext book. Empty until a `build` over the FundedNext pool is
#: accepted; `--members canon` runs whatever is recorded here.
BOOK = ()

base.OUT_PATH = OUT_PATH
base.SURVIVOR_DIR = SURVIVOR_DIR
base.BOOK = BOOK
base.CANON_INITIAL = 6_000.0
base.EXTERNAL = ()
base.EXCLUDE_SYMBOLS = set()
base.EXCLUDE = {}
base.SHOWN_EQUITY = {}
base.SLEEVE_SCALE = {}
base.SIZING_EQUITY_CAP = {}
base.MARGIN_EQUITY_FLOOR = {}
base.TICK_COSTS = False
# `standalone` bound the Exness result path as a default argument at import.
_defaults = list(base.standalone.__defaults__)
_defaults[_defaults.index(base.os.path.join(RESULTS, "exness_combined_strategies.json"))] = OUT_PATH
base.standalone.__defaults__ = tuple(_defaults)


def _key(cell):
    """What a FundedNext re-score depends on: params, window, costs, fills."""
    spec_stamp = os.path.getmtime(ef.SPEC_PATH)
    table = ef.broker_minute_table(cell["symbol"])
    blob = json.dumps([cell["symbol"], cell["family"], cell["bar_minutes"],
                       cell["params"], ef.IS_END, ef.OOS_END, spec_stamp,
                       ef.data._table_fingerprint([table])], sort_keys=True,
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def survivors():
    """Write `results/fundednext/`: every sealed cell FundedNext can price,
    with its holdout re-scored at FundedNext and params left as sealed.

    Re-scores only what changed (params, window, spread snapshot or the
    FundedNext minute table), so a re-run after an import is cheap.
    """
    os.makedirs(SURVIVOR_DIR, exist_ok=True)
    quoted = {s for s, r in ef.load_specs()["symbols"].items() if "error" not in r}
    groups = {}
    for name in sorted(os.listdir(SOURCE_DIR)):
        if not name.endswith(".json") or name.startswith("SURVIVORS"):
            continue
        with open(os.path.join(SOURCE_DIR, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        if cell["symbol"] not in quoted:
            continue
        target = os.path.join(SURVIVOR_DIR, name)
        key = _key(cell)
        if os.path.exists(target):
            with open(target, encoding="utf-8") as handle:
                if json.load(handle).get("fundednext", {}).get("key") == key:
                    continue
        groups.setdefault((cell["symbol"], cell["bar_minutes"]), []).append(
            (name, cell, key))
    print(f"{sum(len(v) for v in groups.values())} cells to re-score at "
          f"FundedNext in {len(groups)} groups -> {SURVIVOR_DIR}", flush=True)
    for (symbol, bar), items in sorted(groups.items()):
        ef.BAR_MINUTES = bar
        try:
            ef.resolve(symbol, allow_stale=True)
            ef.prewarm(symbol, "validate", bar)
            bars, ctx = ef.context(symbol, "validate", bar,
                                   {cell["family"] for _, cell, _ in items})
        except (Exception, SystemExit) as error:  # noqa: BLE001
            print(f"  {symbol} {bar}m: skipped -- {error}", flush=True)
            continue
        for name, cell, key in items:
            params = ef.rehydrate(cell["params"])
            try:
                oos = ef.backtest(cell["family"], bars, ctx, params,
                                  lo=ef.IS_END, hi=ef.OOS_END)
            except (Exception, SystemExit) as error:  # noqa: BLE001
                print(f"  {name}: skipped -- {type(error).__name__}: {error}",
                      flush=True)
                continue
            out = dict(cell)
            out["exness_out_of_sample"] = cell["out_of_sample"]
            out["out_of_sample"] = oos
            out["fundednext"] = {
                "key": key, "account": ef.PROFILE["account"],
                "cost_model": ef.PROFILE["cost_model"],
                "fills_from": ef.broker_minute_table(symbol),
                "holdout": [ef.IS_END, ef.OOS_END],
                "note": "out_of_sample re-scored at FundedNext with the sealed "
                        "params; in_sample and null_control kept as sealed",
            }
            with open(os.path.join(SURVIVOR_DIR, name), "w",
                      encoding="utf-8") as handle:
                json.dump(out, handle, indent=2, sort_keys=True)
                handle.write("\n")
        print(f"  {symbol} {bar}m: {len(items)} re-scored", flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "survivors":
        survivors()
        return
    if not os.path.isdir(SURVIVOR_DIR) or not os.listdir(SURVIVOR_DIR):
        raise SystemExit("no FundedNext pool yet -- run `survivors` first")
    base.main()


if __name__ == "__main__":
    main()
