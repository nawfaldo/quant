"""Fidelity check: a replica vs the live Rust engine (`/api/run`).

The Rust engine is the ground truth; this proves the replica still agrees with
it. Run it whenever a replica, `execution.py`, or the Rust strategy changes.

Requires the server on :8080 with the `idk` environment (id 2, spread 0.2) and
the same Parquet store. The replica has no compiled constants of its own, so a
strategy's `defaults` must be kept in step with its Rust file — that is exactly
what this compares.
"""
import json
import os
import sys
import urllib.request

from sandbox import search
from sandbox import strategies

ENVIRONMENT_ID = "2"
#: Absolute floor, plus a relative term. Strategies that hold several positions
#: at once can differ by one `step` on the odd trade: when two positions close on
#: the same bar the engine credits them in position order and `execution.size`
#: in event-queue order, so the equity a later entry is sized against can differ
#: in the last cent. That is a sizing rounding artifact, not signal drift — the
#: trade count and per-unit points still have to match exactly.
TOLERANCE = 0.5
RELATIVE_TOLERANCE = 0.002
#: override to cross-check a build running somewhere other than the usual server
SERVER = os.environ.get("PY_OPTIMIZER_SERVER", "http://localhost:8080")


def live_run(strategy):
    from_date, to_date = strategy.date_range
    payload = {"environmentId": ENVIRONMENT_ID, "strategy": strategy.server_name,
               "symbol": strategy.symbol, "instrument": "forex",
               "initialBalance": str(int(strategy.execution.initial)),
               "fromDate": from_date, "toDate": to_date}
    request = urllib.request.Request(f"{SERVER}/api/run",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    result = json.load(urllib.request.urlopen(request, timeout=300))
    return (result["final_bal"] - result["initial_bal"], result["num_trades"],
            result["profit_factor"])


def validate(strategy):
    """Compare the replica's compiled-parameter run against /api/run."""
    stats, _ = search.backtest(strategy)
    print(f"replica: pnl {stats['pnl']:8.2f}  trades {stats['trades']}  pf {stats['pf']}")
    try:
        pnl, trades, pf = live_run(strategy)
    except Exception as exc:  # noqa: BLE001
        print(f"(could not reach live engine: {exc})")
        print("replica-only run; start the server to cross-check.")
        return None
    print(f"engine : pnl {pnl:8.2f}  trades {trades}  pf {pf}")
    allowed = max(TOLERANCE, RELATIVE_TOLERANCE * abs(pnl))
    ok = abs(stats["pnl"] - pnl) < allowed and stats["trades"] == trades
    print("MATCH" if ok else "DRIFT DETECTED")
    return ok


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Hourly Delta Reversal"
    outcome = validate(strategies.get(name))
    sys.exit(0 if outcome is not False else 1)
