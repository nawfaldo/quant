"""Reconcile the Python book's trade dump against the Rust port's, sleeve by
sleeve.

Both sides write the same shape under `DUMP_TRADES`. This lines them up on
`(sleeve, entry_ts)` so a disagreement says WHICH kind it is: a trade one side
took and the other did not, or the same trade sized or priced differently.

    DUMP_TRADES=py.json py -m sandbox.research._canon_book
    DUMP_TRADES=rs.json cargo run --release --bin exness_book -- \
        2025-01-01 2026-08-20 400
    py -m sandbox.research._diff_trades py.json rs.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

#: The Rust dump names sleeves by DISPLAY name and the Python by `symbol:family`.
DISPLAY = {
    "NQ OFI": "nq:ofi",
    "NQ Drift VWAP": "nq:drift_vwap",
    "NQ Volatility Breakout": "nq:volatility_breakout",
    "NQ Level Confluence": "nq:level_confluence",
    "NQ SAR": "nq:sar",
    "USDJPY Volume Thrust": "usdjpy:volume_thrust",
    "USDJPY Pullback": "usdjpy:pullback",
    "USDJPY Aroon": "usdjpy:aroon",
    "AUDUSD Z-score": "audusd:zscore",
    "JP225 Break Retest": "jp225:break_retest",
    "JP225 Volume Thrust": "jp225:volume_thrust",
    "ETHUSD Confluence": "ethusd:confluence",
    "ETHUSD Volatility Breakout": "ethusd:volatility_breakout",
    "ETHUSD MACD Histogram": "ethusd:macd_hist",
    "ETHUSD Idio Break": "ethusd:idio_break",
    "ETHUSD OBV Break": "ethusd:obv_break",
    "GBPJPY Trap": "gbpjpy:trap",
    "GBPUSD OBV Break": "gbpusd:obv_break",
    "UKOIL XMA Cross": "ukoil:xma_cross",
    "UKOIL Level Confluence": "ukoil:level_confluence",
    "EURJPY Two Stage": "eurjpy:two_stage",
    "EURJPY Volatility Breakout": "eurjpy:volatility_breakout",
    "UK100 Gated Fade": "uk100:gated_fade",
    "BTC XMA Ribbon": "btc:xma_ribbon",
    "XNIUSD CCI": "xniusd:cci",
}


def when(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M")


#: Hours `exness_families.all_bars` adds to a market's timestamps before
#: anything sees them. Python's trade log is stamped on the SHIFTED clock and the
#: Rust dump on the real one, so the two have to be aligned before they can be
#: compared -- otherwise every JP225 trade reads as present on one side only.
SHIFT_HOURS = {"jp225": 6}


def load(path, rename, shift):
    out = defaultdict(dict)
    for trade in json.load(open(path, encoding="utf-8")):
        sleeve = rename.get(trade["s"], trade["s"])
        offset = shift * SHIFT_HOURS.get(sleeve.split(":", 1)[0], 0) * 3_600
        out[sleeve][trade["et"] + offset] = trade
    return out


def main():
    left, right = sys.argv[1], sys.argv[2]
    py = load(left, {}, 0)
    rs = load(right, DISPLAY, 1)

    # The EARLIEST disagreement, which is the only one worth chasing: every
    # later one is downstream of it through the shared balance, because a
    # differently sized trade leaves a different equity for the next entry.
    first = None
    for sleeve in set(py) | set(rs):
        a, b = py.get(sleeve, {}), rs.get(sleeve, {})
        for ts in set(a) | set(b):
            if ts not in a or ts not in b:
                kind = "only python" if ts in a else "only rust"
            elif abs(a[ts]["qty"] - b[ts]["qty"]) > 1e-9:
                kind = f"qty {a[ts]['qty']:.4f} vs {b[ts]['qty']:.4f}"
            elif abs(a[ts]["pnl"] - b[ts]["pnl"]) > 0.005:
                kind = f"pnl {a[ts]['pnl']:+.2f} vs {b[ts]['pnl']:+.2f}"
            else:
                continue
            if first is None or ts < first[0]:
                first = (ts, sleeve, kind)
    if first:
        print(f"FIRST DIVERGENCE  {when(first[0])}  {first[1]}  {first[2]}\n")

    print(f"{'sleeve':28}{'py n':>7}{'rs n':>7}{'only py':>9}{'only rs':>9}"
          f"{'qty':>7}{'pnl':>7}{'py $':>11}{'rs $':>11}")
    total_py = total_rs = 0.0
    for sleeve in sorted(set(py) | set(rs)):
        a, b = py.get(sleeve, {}), rs.get(sleeve, {})
        only_a = sorted(set(a) - set(b))
        only_b = sorted(set(b) - set(a))
        shared = sorted(set(a) & set(b))
        qty = sum(1 for ts in shared
                  if abs(a[ts]["qty"] - b[ts]["qty"]) > 1e-9)
        pnl = sum(1 for ts in shared
                  if abs(a[ts]["pnl"] - b[ts]["pnl"]) > 0.005)
        sum_a = sum(t["pnl"] for t in a.values())
        sum_b = sum(t["pnl"] for t in b.values())
        total_py += sum_a
        total_rs += sum_b
        mark = "" if (not only_a and not only_b and not qty and not pnl) else " <"
        print(f"{sleeve:28}{len(a):>7}{len(b):>7}{len(only_a):>9}{len(only_b):>9}"
              f"{qty:>7}{pnl:>7}{sum_a:>11,.2f}{sum_b:>11,.2f}{mark}")
        for ts in only_a[:4]:
            print(f"    only python  {when(ts)}  qty {a[ts]['qty']:.4f}  "
                  f"pnl {a[ts]['pnl']:+.2f}")
        for ts in only_b[:4]:
            print(f"    only rust    {when(ts)}  qty {b[ts]['qty']:.4f}  "
                  f"pnl {b[ts]['pnl']:+.2f}")
        for ts in shared:
            if abs(a[ts]["qty"] - b[ts]["qty"]) > 1e-9:
                print(f"    qty  {when(ts)}  py {a[ts]['qty']:.4f}  "
                      f"rs {b[ts]['qty']:.4f}")
                break
        for ts in shared:
            if abs(a[ts]["pnl"] - b[ts]["pnl"]) > 0.005:
                print(f"    pnl  {when(ts)}  py {a[ts]['pnl']:+.2f}  "
                      f"rs {b[ts]['pnl']:+.2f}  "
                      f"qty {a[ts]['qty']:.4f}/{b[ts]['qty']:.4f}")
                break
    print(f"{'TOTAL':28}{sum(len(v) for v in py.values()):>7}"
          f"{sum(len(v) for v in rs.values()):>7}{'':>9}{'':>9}{'':>7}{'':>7}"
          f"{total_py:>11,.2f}{total_rs:>11,.2f}")


if __name__ == "__main__":
    main()
