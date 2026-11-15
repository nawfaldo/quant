"""The5ers' fill path: the Exness readers, pointed at `the5ers_*_1m`.

Same execution path as FundedNext (`fill_models.fundednext`): the runtime
decides on the vendor bar, waits the publish lag and the bridge queue, and
sends a market order; exits are late market orders. What differs is read from
`cfd_families.BROKERS['the5ers']` by the shared readers:

  * the minute table (`the5ers_<broker symbol>_1m`, NY wall clock -- the
    server is NY + 7, `tools/exness_import_1m.THE5ERS_OFFSET_EXCEPTIONS`);
  * a zero M1 spread is a real quote (raw majors quote bid == ask);
  * the M1 spread column is the minute's MINIMUM, lifted to the tick median by
    `m1_spread_lift_points` (`measure_m1_spread_lift` re-measures it);
  * the daily break (`cfd_families.THE5ERS_CLOSED_*`) is no fill.

`load_maps` is not shared: the precomputed tick maps exist for Exness only.
"""

from sandbox.research.fill_models.exness import (  # noqa: F401
    BRIDGE_QUEUE_SECONDS,
    clock_offset,
    exit_delay_seconds,
    has_minutes,
    minute_table,
    price_by_bar_1m,
    release_minutes,
    spread_by_bar_1m,
)

TERMINAL = r"C:\Program Files\Five Percent Online MetaTrader 5\terminal64.exe"


def load_maps(path=None, source="bars"):
    raise SystemExit(
        "no precomputed The5ers fill maps -- the combined book's "
        "EXNESS_TICK_COSTS path has only been built for Exness")


def measure_m1_spread_lift(symbols, start, end):
    """`{broker_symbol: points}`: median over minutes of (median tick spread -
    M1 spread column), read straight off the terminal for `[start, end)` (real
    UTC datetimes). Market data only; never sends an order."""
    import MetaTrader5 as mt5  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if not mt5.initialize(path=TERMINAL):
        raise SystemExit(f"cannot reach The5ers MT5: {mt5.last_error()}")
    out = {}
    try:
        for name in symbols:
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
            ticks = mt5.copy_ticks_range(name, start, end, mt5.COPY_TICKS_INFO)
            rates = mt5.copy_rates_range(name, mt5.TIMEFRAME_M1, start, end)
            if info is None or ticks is None or rates is None or not len(ticks):
                continue
            spread = (ticks["ask"] - ticks["bid"]) / info.point
            minute = ticks["time"] // 60
            order = np.argsort(minute, kind="stable")
            minute, spread = minute[order], spread[order]
            cuts = np.flatnonzero(np.diff(minute)) + 1
            med = {int(m[0]): float(np.median(s))
                   for m, s in zip(np.split(minute, cuts), np.split(spread, cuts))}
            col = {int(r["time"]) // 60: float(r["spread"]) for r in rates}
            gaps = [med[k] - col[k] for k in med.keys() & col.keys()]
            if gaps:
                out[name.lower()] = round(float(np.median(gaps)), 2)
    finally:
        mt5.shutdown()
    return out
