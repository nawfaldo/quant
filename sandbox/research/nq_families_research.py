"""Nine-family NQ study on native QuestDB 30-minute bars.

This is the commodity/crypto/index family protocol applied to ``nq_30m``.
Parameters are selected only on 2020-2024 and the selected cells are then
evaluated once on the sealed 2025-2026 holdout.  Account mechanics use the
repository's standard NQ Forex model: USD 1,000 initial equity, USD 1 per point
per lot, 0.01-lot steps, 25% margin (a 4x notional ceiling), 0.5% live-equity
stop risk, and a 0.2-point round-trip spread charged wholly at entry.

The implementation deliberately configures the established index-family
engine rather than copying it, keeping signals, exits, robustness gates,
cost sweeps and the coin-flip null control directly comparable.

    py -B -m sandbox.research.nq_families_research preflight
    py -B -m sandbox.research.nq_families_research select --workers 6
    py -B -m sandbox.research.nq_families_research validate
    py -B -m sandbox.research.nq_families_research why --workers 6
"""
from __future__ import annotations

import hashlib
import json
import os

from sandbox.research import index_families_research as engine


SYMBOL = "nq"
_ENGINE_INIT_WORKER = engine._init_worker
_ENGINE_SELECT = engine.select


def _init_worker(symbol, phase):
    """Reapply adapter globals in Windows ``spawn`` worker processes."""
    configure()
    _ENGINE_INIT_WORKER(symbol, phase)


def _select(symbol, workers, spread_points):
    """Run selection, then seal the truthful Forex sizing description."""
    _ENGINE_SELECT(symbol, workers, spread_points)
    path = engine.output_path(symbol)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.pop("seal_sha256", None)
    payload["protocol"]["sizing"] = (
        "0.5% volatility-throttled stop risk, Forex 0.01 lots, "
        "25% margin (4x notional ceiling)"
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def configure():
    """Install the NQ Forex contract into the shared family engine."""
    engine.INITIAL_BALANCE = 1_000.0
    engine.RISK_FRACTION = 0.005
    engine.INSTRUMENTS = {
        SYMBOL: engine._instrument(
            "nq_30m",
            (9 * 60 + 30, 16 * 60),
            multiplier=1.0,
            volume_min=0.01,
            pip_size=1.0,
            tick_size=0.01,
            tick_value=0.01,
            volume_step=0.01,
            volume_max=300.0,
        )
    }
    engine.SPREAD_POINTS = {SYMBOL: 0.2}
    engine._init_worker = _init_worker
    engine.select = _select
    engine.output_path = lambda symbol: os.path.join(
        engine.RESULTS, f"nq_families_{symbol}.json"
    )


def main():
    configure()
    engine.main()


if __name__ == "__main__":
    main()
