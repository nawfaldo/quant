"""How long does level-two information survive on NQ?

The minute-bar screens found nothing.  Three explanations were on the table:
costs, bad data, or a horizon mismatch.  This separates them by measuring the
predictive power of the book at its native resolution -- straight off
`nq_l2_features_1s`, no bars, no bracket, no spread, no position limits -- at
horizons from one second to five minutes.

If the effect is present at seconds and gone by a minute, then neither cost nor
data quality is the reason the minute-bar work failed: the information is real
and it is consumed before a bar closes.

The unit throughout is NQ points.  One tick is 0.25.  The mean quoted spread on
this sample is 0.70.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections import defaultdict

import numpy as np

HORIZONS = (1, 2, 5, 15, 30, 60, 300)
TICK = 0.25
QUESTDB = "http://127.0.0.1:9000/exec"
Z_CUT = 1.5


def query(sql):
    url = f"{QUESTDB}?query={urllib.parse.quote(sql)}"
    with urllib.request.urlopen(url, timeout=300) as fh:
        payload = json.load(fh)
    if "dataset" not in payload:
        raise RuntimeError(payload)
    return payload["dataset"]


def sessions(limit, stride):
    rows = query(
        "SELECT cast(timestamp_floor('d',timestamp) as string) d "
        "FROM nq_l2_features_1s WHERE source='dbento' "
        "AND timestamp < '2026-01-01' GROUP BY d ORDER BY d"
    )
    days = [r[0][:10] for r in rows]
    return days[::stride][:limit]


def load_day(day):
    sql = (
        "SELECT cast(timestamp as long) ts, midprice, top1_imbalance, "
        "top5_imbalance, trade_delta, aggressive_buy_volume, "
        "aggressive_sell_volume, bid_add_volume, ask_add_volume "
        f"FROM nq_l2_features_1s WHERE source='dbento' AND timestamp IN '{day}' "
        "AND book_valid=true AND midprice>0 "
        "AND hour(timestamp)>=10 AND hour(timestamp)<15 "
        "ORDER BY timestamp"
    )
    return query(sql)


def signals_from(rows):
    """Per-second signals, each using only that second's completed data."""
    mid = np.array([r[1] for r in rows], dtype=float)
    imb1 = np.array([r[2] or 0.0 for r in rows], dtype=float)
    imb5 = np.array([r[3] or 0.0 for r in rows], dtype=float)
    delta = np.array([r[4] or 0.0 for r in rows], dtype=float)
    aggb = np.array([r[5] or 0.0 for r in rows], dtype=float)
    aggs = np.array([r[6] or 0.0 for r in rows], dtype=float)
    badd = np.array([r[7] or 0.0 for r in rows], dtype=float)
    aadd = np.array([r[8] or 0.0 for r in rows], dtype=float)
    return mid, {
        "top1_imbalance": imb1,
        "top5_imbalance": imb5,
        "trade_delta": delta,
        "agg_ratio": (aggb - aggs) / (aggb + aggs + 1e-9),
        "add_side": (badd - aadd) / (badd + aadd + 1e-9),
    }


def zscore(x):
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-12 else np.zeros_like(x)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--stride", type=int, default=7)
    parser.add_argument("--out", default="sandbox/results/l2_decay.json")
    args = parser.parse_args()

    days = sessions(args.days, args.stride)
    print(f"sampling {len(days)} sessions, 10:00-15:00, 1-second rows")

    # per (signal, horizon): accumulate per-session long-short means
    acc = defaultdict(list)
    ic = defaultdict(list)
    total = 0
    for day in days:
        rows = load_day(day)
        if len(rows) < 3000:
            continue
        total += len(rows)
        mid, sigs = signals_from(rows)
        for name, raw in sigs.items():
            z = zscore(raw)
            hi, lo = z >= Z_CUT, z <= -Z_CUT
            if hi.sum() < 50 or lo.sum() < 50:
                continue
            for h in HORIZONS:
                fwd = np.full(len(mid), np.nan)
                fwd[:-h] = mid[h:] - mid[:-h]
                ok = ~np.isnan(fwd)
                if (hi & ok).sum() < 30 or (lo & ok).sum() < 30:
                    continue
                acc[(name, h)].append(
                    (float(fwd[hi & ok].mean()), int((hi & ok).sum()),
                     float(fwd[lo & ok].mean()), int((lo & ok).sum()))
                )
                good = ok & ~np.isnan(z)
                ic[(name, h)].append(float(np.corrcoef(z[good], fwd[good])[0, 1]))
    print(f"{total:,} one-second observations\n")

    results = []
    header = "".join(f"{str(h) + 's':>10}" for h in HORIZONS)
    print(f"long-short edge in NQ points  (1 tick = {TICK}, mean spread = 0.70)")
    print(f"{'signal':<18}{header}")
    for name in ("top1_imbalance", "top5_imbalance", "trade_delta",
                 "agg_ratio", "add_side"):
        cells = ""
        for h in HORIZONS:
            vals = acc.get((name, h))
            if not vals:
                cells += f"{'n/a':>10}"
                continue
            # observation-weighted across sessions
            hi_sum = sum(m * n for m, n, _, _ in vals)
            hi_n = sum(n for _, n, _, _ in vals)
            lo_sum = sum(m * n for _, _, m, n in vals)
            lo_n = sum(n for _, _, _, n in vals)
            edge = hi_sum / hi_n - lo_sum / lo_n
            cells += f"{edge:>+10.4f}"
            results.append({"signal": name, "horizon_s": h, "edge_points": edge,
                            "ic": float(np.mean(ic[(name, h)])),
                            "sessions": len(vals)})
        print(f"{name:<18}{cells}")

    print(f"\ninformation coefficient (corr with forward move)")
    print(f"{'signal':<18}{header}")
    for name in ("top1_imbalance", "top5_imbalance", "trade_delta",
                 "agg_ratio", "add_side"):
        cells = ""
        for h in HORIZONS:
            vals = ic.get((name, h))
            cells += f"{np.mean(vals):>+10.4f}" if vals else f"{'n/a':>10}"
        print(f"{name:<18}{cells}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"days": days, "z_cut": Z_CUT, "results": results}, fh, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
