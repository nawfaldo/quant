"""The canon book replayed, with no selection and no admission gate.

WHY THIS EXISTS SEPARATELY FROM `build --members canon`. That command refuses to
run right now: `SESSION_ONLY` took the holding away from `jp225:swing_donchian`
and flattened `xalusd:gated_fade` at its close, and both are now non-positive on
the FULL 2018-2026 window -- which trips the stale-survivor gate, whose job is to
stop a truncated survivor entering a book silently. The two are not stale; the
flag changed them. Deciding whether that gate should still be a hard stop is an
operator call, so this reproduces the same replay beside it rather than editing
it.

IT IS NOT A SECOND MODEL. Every number below comes from `sleeve_trades`,
`external_trades` and `replay` in `exness_combined_strategies`, called with the
canon settings, which is exactly the sequence `build` runs after its gate.

    py -m sandbox.research._canon_book
    DUMP_TRADES=out.json py -m sandbox.research._canon_book
"""
from __future__ import annotations

import json
import os

from sandbox.research import exness_combined_strategies as cb
from sandbox.research import exness_families as ef


def main():
    cb.UNCAPPED = True
    cb.FORCE_MINIMUM_LOT = True

    keys = list(cb.BOOK)
    rows = cb.candidate_rows_exact([k for k in keys if k not in cb.EXTERNAL])
    members, logs, bars_by, ctx_by, standalone = [], {}, {}, {}, {}
    for key in keys:
        symbol, family = key.split(":", 1)
        if key in cb.EXTERNAL:
            members.append({"symbol": symbol, "family": family,
                            "external": True})
            logs[key] = cb.external_trades(
                key, window=("2025-01-01", cb.CANON_DATA_END))
            standalone[key] = {"return_pct": float("nan"),
                               "max_dd_pct": float("nan")}
            print(f"{key:28} {len(logs[key]):5d} trades  imported", flush=True)
            continue
        if key not in rows:
            raise SystemExit(f"{key}: no sealed survivor row")
        row = rows[key]
        ef.resolve(symbol, allow_stale=True)
        shown = cb.SHOWN_EQUITY.get(key, 1.0)
        result, log, bars, ctx = cb.sleeve_trades(row, shown=shown)
        members.append(row)
        logs[key] = log
        bars_by[symbol] = bars
        ctx_by[symbol] = ctx
        standalone[key] = result
        print(f"{key:28} {len(log):5d} trades  standalone "
              f"{result['return_pct']:+8.2f}%", flush=True)

    book = cb.replay(members, logs, bars_by, ctx_by,
                     scale=cb.SLEEVE_SCALE,
                     sizing_cap=cb.sizing_caps(members),
                     risk_scale=cb.CANON_RISK_SCALE,
                     gross_cap=cb.CANON_GROSS_CAP,
                     initial=cb.CANON_INITIAL)

    if os.environ.get("DUMP_TRADES"):
        with open(os.environ["DUMP_TRADES"], "w", encoding="utf-8") as handle:
            json.dump([{"s": t["sleeve"], "et": t["entry_ts"],
                        "xt": t["exit_ts"], "qty": t["lots"],
                        "ep": t["entry"], "points": t["points"],
                        "money": t["money_per_point"], "pnl": t["pnl"]}
                       for t in book["settled"]], handle)
        print(f"\nwrote {len(book['settled'])} trades to "
              f"{os.environ['DUMP_TRADES']}")

    print(f"\nCANON BOOK  25 sleeves  ${cb.CANON_INITIAL:,.0f}  "
          f"risk {cb.CANON_RISK_SCALE}  gross cap {cb.CANON_GROSS_CAP}")
    print(f"  final     ${book['final']:,.2f}")
    print(f"  return    {book['return_pct']:+.2f}%")
    print(f"  MTM dd    {book['mtm_dd_pct']:.2f}%   trough "
          f"{book['mtm_dd_trough']}")
    print(f"  closed dd {book['max_dd_pct']:.2f}%")
    print(f"  trades    {book['trades']}")
    print(f"  refused   {book.get('refused', 0)}  "
          f"below-minimum {sum(book.get('below_broker_minimum', {}).values())}")

    # MANDATORY per-sleeve detail: contribution, both drawdowns, and the
    # standalone figures beside them ([[show-per-sleeve-detail-every-time]]).
    print(f"\n{'sleeve':28}{'P&L $':>10}{'MTM dd$':>10}{'clsd dd$':>10}"
          f"{'n':>7}{'alone%':>9}{'alone dd':>10}")
    for key in keys:
        row, one = book["by_sleeve"][key], standalone[key]
        alone = ("imported" if one["return_pct"] != one["return_pct"]
                 else f"{one['return_pct']:+.1f}")
        alone_dd = ("-" if one["max_dd_pct"] != one["max_dd_pct"]
                    else f"{one['max_dd_pct']:.1f}")
        print(f"{key:28}{row['pnl']:>10,.2f}{row['mtm_dd_usd']:>10,.2f}"
              f"{row['closed_dd_usd']:>10,.2f}{row['trades']:>7}"
              f"{alone:>9}{alone_dd:>10}")


if __name__ == "__main__":
    main()
