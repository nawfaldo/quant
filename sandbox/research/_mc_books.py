"""Monte Carlo an ARBITRARY membership on live fills.

`_mc_live` does this for `ecs.BOOK`; this does it for a membership named on the
command line, so the jp225 replacement candidates can be compared against the
base book they have to beat under the same 1,000 paths.

    BOOK_NAME=base   py -m sandbox.research._mc_books run --paths 1000
    BOOK_NAME=seat3  py -m sandbox.research._mc_books run --paths 1000
    BOOK_NAME=seat4  py -m sandbox.research._mc_books run --paths 1000

Everything else -- block bootstrap, chaining, the marking cache -- is
`exness_combined_montecarlo` untouched.
"""
import os
import sys
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research.fill_models import exness as le
from sandbox.research import exness_combined_montecarlo as mc

SOURCE = "bars"
BASE = [k for k in ecs.BOOK if not k.startswith("jp225:") and k not in ecs.EXTERNAL]
BOOKS = {
    "base":  BASE,
    "seat3": BASE + ["usdjpy:aroon", "ethusd:break_retest", "eurjpy:swing_ma"],
    "seat4": BASE + ["usdjpy:aroon", "ethusd:break_retest",
                     "eurjpy:xma_cross", "eurjpy:swing_ma"],
}
NAME = os.environ.get("BOOK_NAME", "base")
#: `BOOK_EXTRA=a:b,c:d` scores BASE plus those cells under NAME, so a finalist
#: from a search can go straight to Monte Carlo without a code change.
EXTRA = [k for k in os.environ.get("BOOK_EXTRA", "").split(",") if k]
KEYS = (BASE + [k for k in EXTRA if k not in BASE]) if EXTRA else BOOKS.get(NAME)
#: `BOOK_KEYS=a:b,c:d,...` names the WHOLE membership explicitly. Needed once
#: `BOOK` itself moved: BASE is derived from it, so after a seating it already
#: contains the cells a comparison wants to add or remove.
if os.environ.get("BOOK_KEYS"):
    KEYS = [k for k in os.environ["BOOK_KEYS"].split(",") if k]
if KEYS is None:
    raise SystemExit(f"unknown BOOK_NAME {NAME!r} and no BOOK_KEYS/BOOK_EXTRA given")
#: `symbol:family@we` seats a cell that ENTERS ON SATURDAY AND SUNDAY ONLY. Its
#: trade list is the ordinary cell's with weekday entries dropped -- the same
#: trades `EXNESS_ENTRY_DAYS=sat,sun` produces for a session-holding family,
#: because every position is flattened at its own session end and none carries
#: into the weekend. The key must not also be seated as an every-day cell.
WEEKEND = {k[:-3] for k in KEYS if k.endswith("@we")}
KEYS = [k[:-3] if k.endswith("@we") else k for k in KEYS]
if len(set(KEYS)) != len(KEYS):
    raise SystemExit(f"a cell is seated twice: {sorted(k for k in KEYS if KEYS.count(k) > 1)}")

_PIN = mc.pin_external_window


#: Window start. Empty means canon's 2025-01-01.
#:
#: MOVING IT REBINDS `ef.IS_END`, which is what `blocks_of` tiles from AND what
#: `vol_target` is keyed on, so a 2022 run is a slightly different book rather
#: than the same book over more history ([[vol-target-is-keyed-on-is-end]]).
#: Read each run against its OWN `actual`, never across windows. Bars before a
#: symbol's broker map keep the constant spread, so a longer window is a LOWER
#: BOUND on execution cost.
START = os.environ.get("MC_START", "")


def arm():
    """Every global `_mc_live.arm` sets, plus this book's own cache/output."""
    if START:
        ecs.ef.IS_END = int(datetime.fromisoformat(START)
                            .replace(tzinfo=timezone.utc).timestamp())

    def pin(span=None):
        _PIN(span or ((START or "2025-01-01"), ecs.CANON_DATA_END))

    mc.pin_external_window = pin
    ecs.MAPS_OVERRIDE = le.maps_path(SOURCE)
    ecs.TICK_COSTS = True
    ecs.BROKER_STOPS = False
    ecs.FORCE_MINIMUM_LOT = True
    ecs.UNCAPPED = True
    ecs.GLOBAL_SIZING_CAP = 1500.0
    ecs.MAX_DD_CONCENTRATION = None
    ecs.FAIR_CAP = False
    ecs._TICK_COST_CACHE.clear()
    ecs._SLEEVE_MEMO.clear()
    tag = f"_{START[:4]}" if START else ""
    mc.CACHE = os.path.join(ecs.CACHE_DIR, f"mc_books_{NAME}{tag}.pkl")
    mc.OUT_PATH = os.path.join(mc.RESULTS, f"mc_books_{NAME}{tag}.json")


def prepare():
    """`mc.prepare` for THIS membership, contexts dropped to `cfg` as there."""
    rows = ecs.candidate_rows_exact(KEYS)
    missing = [k for k in KEYS if k not in rows]
    if missing:
        raise SystemExit(f"{NAME}: no sealed survivor row for {missing}")
    members, logs, bars_by, ctx_by = [], {}, {}, {}
    for key in KEYS:
        member = rows[key]
        if "params" in member:
            member["params"] = ecs._retuple(member["params"])
        members.append(member)
        ecs.ef.resolve(member["symbol"], allow_stale=True)
        _r, log, bars, ctx = ecs.sleeve_trades(member)
        if key in WEEKEND:
            # 1970-01-01 was a Thursday: +3 makes Monday 0, Saturday 5.
            log = [t for t in log if (t["entry_ts"] // 86400 + 3) % 7 >= 5]
            print(f"  {key}: weekend entries only, {len(log)} trades", flush=True)
        logs[key] = log
        bars_by[member["symbol"]] = bars
        # Contexts are 1 GB across a book and `replay` reads only `cfg`.
        ctx_by[member["symbol"]] = {"cfg": ctx["cfg"],
                                    "symbol": ctx.get("symbol", member["symbol"])}
        ecs._SLEEVE_MEMO.clear()
    return {"members": members, "logs": logs,
            "bars_by": bars_by, "ctx_by": ctx_by}


_MC_INIT = mc._init_worker


def _init_worker(cache_path, block_days):
    arm()
    _MC_INIT(cache_path, block_days)


if __name__ == "__main__":
    arm()
    mc.prepare = prepare
    mc._init_worker = _init_worker
    print(f"MONTE CARLO  book={NAME}  {len(KEYS)} sleeves  "
          f"maps={os.path.basename(ecs.MAPS_OVERRIDE)}", flush=True)
    sys.argv = ["mc"] + sys.argv[1:]
    mc.main()
