"""Engine runner for per-sleeve exposure schedules, and the control they must beat.

Every number here comes from `/api/combine` -- the three sleeves trading one
$1,000 balance, with the engine's own mark-to-market drawdown. That last word is
the reason this is not done in Python: the book's headline drawdown is measured
on equity *including open positions*, and a replay of closed trades alone puts
it at 21% where the engine says 37%. A drawdown study that cannot see open risk
is measuring the wrong quantity.

THE CONTROL. Turning every sleeve down by a constant `k` is the null hypothesis
and it is a strong one: sizing is linear in equity, so `k` moves return and
drawdown together and leaves Sharpe almost exactly where it was. Any regime rule
that claims to cut drawdown has to be compared with the constant that reaches
the *same* drawdown, not with the untouched book -- otherwise "cuts drawdown from
37% to 20%" is being credited to the regime logic when a scalar does it for free.

WINDOWS. 2025 is in sample and 2026 is out of sample, as asked. One caveat has
to travel with every number below: both NQ sleeves were themselves selected on
roughly the 2025 span, so fitting an overlay there is fitting on top of an
already-fitted book, and 2025 flatters nothing -- it is the window that holds the
entire drawdown and almost none of the return.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request


#: MUST TRACK `live_trade/src/main.rs`, which reads `PORT` and falls back to 4000.
#: This said 8080, which no longer answers.
SERVER = f"http://127.0.0.1:{os.environ.get('PORT', '4000')}"
ENVIRONMENT = "2"
INITIAL = 1_000.0
SLEEVES = ("NQ Deep OFI Momentum", "NQ Hourly Delta Reversal", "BTC Maroy Ladder")

FULL = ("2025-01-01", "2026-08-05")
IS = ("2025-01-01", "2025-12-31")
OOS = ("2026-01-01", "2026-08-05")

#: The file the server reads. It is set once, at server start, via
#: SLEEVE_EXPOSURE_SCHEDULE; this module rewrites it between runs and the engine
#: re-reads it per run, which is why one warm server can sweep candidates.
SCHEDULE = os.path.join(os.path.dirname(__file__), "..", ".cache",
                        "exposure_schedule.json")
CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache", "exposure")

#: The server binary. Its modification time goes into every cache key, so a
#: rebuild invalidates the cache instead of silently serving results from the
#: previous binary. That is not hypothetical: compiling the exposure policy into
#: Rust changed the book from +130.53%/37.49% to +143.09%/18.36%, and a cache
#: keyed only on the request kept handing back the old figure as the baseline
#: while freshly-run candidates used the new one.
BINARY = os.path.join(os.path.dirname(__file__), "..", "..", "live_trade", "target",
                      "debug", "live_trade.exe")


def _build_id():
    try:
        return int(os.path.getmtime(BINARY))
    except OSError:
        return 0

FIELDS = ("return_pct", "max_dd_pct", "sharpe", "trades")


def _post(path, payload):
    request = urllib.request.Request(
        SERVER + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=3600) as response:
        return json.loads(response.read())


def run(schedule=None, window=FULL, sleeves=SLEEVES, initial=INITIAL, refresh=False):
    """One engine run of the book under `schedule`, cached by schedule + window.

    `schedule` is `{sleeve: {"default": m, "YYYY-MM-DD": m, ...}}`; `None` runs
    the book untouched. The cache key covers the schedule, so a candidate and
    the control can never collide.
    """
    schedule = schedule or {}
    payload = {
        "environmentId": ENVIRONMENT,
        "strategies": list(sleeves),
        "symbol": "nq",
        "instrument": "forex",
        "initialBalance": str(initial),
        "fromDate": window[0],
        "toDate": window[1],
    }
    key = hashlib.sha1(
        json.dumps({"r": payload, "s": schedule, "b": _build_id()},
                   sort_keys=True).encode()
    ).hexdigest()[:16]
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    if not refresh and os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)

    os.makedirs(os.path.dirname(SCHEDULE), exist_ok=True)
    with open(SCHEDULE, "w") as handle:
        json.dump(schedule, handle)
    body = _post("/api/combine", payload)
    result = {
        "return_pct": round(body["net_growth"], 2),
        "max_dd_pct": round(body["max_drawdown"], 2),
        "sharpe": round(body["sharpe"], 2),
        "trades": body["num_trades"],
        "final": round(body["final_bal"], 2),
        "dd_peak": body["max_drawdown_peak_date"],
        "dd_trough": body["max_drawdown_trough_date"],
        "contribution": body.get("contribution", []),
        "monthly": body.get("contribution_monthly", []),
        "trades_log": body["trades"],
    }
    with open(path, "w") as handle:
        json.dump(result, handle)
    return result


def flat(factor, sleeves=SLEEVES):
    """The control: every sleeve held at one constant."""
    return {name: {"default": factor} for name in sleeves}


def weights(mapping):
    """A constant, but a different one per sleeve."""
    return {name: {"default": value} for name, value in mapping.items()}


def steps(per_sleeve):
    """`{sleeve: [(day, factor), ...]}` -> a schedule the engine understands."""
    out = {}
    for name, points in per_sleeve.items():
        entry = {}
        for day, factor in points:
            entry[day] = round(float(factor), 4)
        out[name] = entry
    return out


def line(label, result, width=26):
    return (f"  {label:<{width}} return {result['return_pct']:8.2f}%  "
            f"maxDD {result['max_dd_pct']:6.2f}%  sharpe {result['sharpe']:5.2f}  "
            f"n={result['trades']}")


def show(label, results, width=26):
    """One candidate across the three windows."""
    parts = []
    for tag in ("full", "is", "oos"):
        r = results[tag]
        parts.append(f"{r['return_pct']:7.1f}% /{r['max_dd_pct']:6.2f}%")
    return f"  {label:<{width}} " + "   ".join(parts)


def evaluate(schedule, refresh=False):
    """A candidate on all three windows."""
    return {
        "full": run(schedule, FULL, refresh=refresh),
        "is": run(schedule, IS, refresh=refresh),
        "oos": run(schedule, OOS, refresh=refresh),
    }


HEADER = (f"  {'candidate':<26} " + "   ".join(
    f"{name:>16}" for name in ("FULL ret/DD", "2025 IS ret/DD", "2026 OOS ret/DD")))
