"""Realised comparison for the es:regime_breakout replacements."""
from __future__ import annotations
import pickle
from datetime import datetime, timezone
from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

ES = "es:regime_breakout"
SETS = (("canon 22 (with es)", ()),
        ("-es (21)", ()),
        ("-es +EP", ("ethusd:pullback",)),
        ("-es +HK", ("hk50:level_confluence",)),
        ("-es +climax", ("jp225:climax",)),
        ("-es +EP+HK", ("ethusd:pullback", "hk50:level_confluence")))


def ts(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def main():
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    windows = (("IS", "2022-01-01", "2025-01-01"),
               ("OOS", "2025-01-01", "2026-08-21"),
               ("2026", "2026-01-01", "2026-08-21"),
               ("full", "2022-01-01", "2026-08-21"))
    header = f"{'book':20}{'n':>4}"
    for name, _a, _b in windows:
        header += f"{name + ' ret':>11}{'dd':>7}{'mo':>7}{'uw':>6}"
    print(header)
    for label, add in SETS:
        keys = list(ecs.BOOK) if label.startswith("canon") else \
            [k for k in ecs.BOOK if k != ES] + list(add)
        members = [state["members"][k] for k in keys]
        line = f"{label:20}{len(keys):>4}"
        for _n, lo_t, hi_t in windows:
            lo, hi = ts(lo_t), ts(hi_t)
            b = fr._replay(members, lo=lo, hi=hi, risk_scale=ecs.CANON_RISK_SCALE)
            _s, _sh, pos, tot = ecs.monthly(b["settled"], ecs.CANON_INITIAL)
            curve = [(t - lo, v) for t, v in b["marked"]]
            line += (f"{b['return_pct']:>+10,.0f}%{b['mtm_dd_pct']:>6.1f}%"
                     f"{pos:>4}/{tot:<2}{mc.underwater(curve):>6.0f}")
        print(line, flush=True)


if __name__ == "__main__":
    main()
