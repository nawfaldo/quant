"""Trade logs for every candidate sleeve NOT in canon, per window start, in the
shape `_p99_lab` merges (`.cache/p99_extra_<window>.pkl`).

    py -m sandbox.research._p99_pool 2022-01-01 2224,2226
    py -m sandbox.research._p99_pool 2025-01-01 2526
"""
import platform

platform._wmi = None

import os
import pickle
import sys
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import tt_book
from sandbox.research.fill_models import exness as le

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")


def ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


if __name__ == "__main__":
    start, windows = sys.argv[1], sys.argv[2].split(",")
    lo, hi = ts(start), ts("2026-08-21")
    ecs.ef.IS_END, ecs.ef.OOS_END = lo, hi
    ecs.MAPS_OVERRIDE = le.maps_path("bars")
    ecs.TICK_COSTS = True
    ecs.BROKER_STOPS = False
    canon = set(ecs.BOOK)
    out = {"members": [], "logs": {}, "bars_by": {}, "ctx_by": {}}

    # the TikTok pool
    tt_keys = sorted(k for k in tt_book.POOL if k not in canon)
    got = tt_book.logs(tt_keys, lo, hi, live=True)
    for key, cell in got["cells"].items():
        out["members"].append({"symbol": cell["symbol"], "family": cell["family"],
                               "params": cell["params"], "tt": True})
        out["logs"][key] = cell["log"]
    out["bars_by"].update(got["bars30"])
    out["ctx_by"].update(got["ctx"])
    print(f"TT pool: {len(got['cells'])} cells", flush=True)

    # the 30m cfd_families survivors that pass the module's own gates
    rows = [r for r in ecs.candidates() if f"{r['symbol']}:{r['family']}" not in canon]
    print(f"30m pool: {len(rows)} cells", flush=True)
    for row in rows:
        key = f"{row['symbol']}:{row['family']}"
        try:
            ecs.ef.resolve(row["symbol"], allow_stale=True)
            _r, log, bars, ctx = ecs.sleeve_trades(row)
        except (Exception, SystemExit) as error:          # noqa: BLE001
            print(f"  skip {key}: {error}", flush=True)
            continue
        out["members"].append(row)
        out["logs"][key] = log
        out["bars_by"].setdefault(row["symbol"], bars)
        out["ctx_by"].setdefault(row["symbol"], {"cfg": ctx["cfg"],
                                                 "symbol": ctx.get("symbol", row["symbol"])})
        ecs._SLEEVE_MEMO.clear()
    for window in windows:
        with open(os.path.join(CACHE, f"p99_extra_{window}.pkl"), "wb") as handle:
            pickle.dump(out, handle, protocol=5)
    print(f"wrote {len(out['members'])} candidates for {windows}", flush=True)
