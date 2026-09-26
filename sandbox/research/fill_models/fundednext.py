"""FundedNext's fill path: the Exness readers, pointed at `fundednext_*_1m`.

The execution path is the same one -- the runtime decides on the vendor bar,
waits the vendor's publish lag and the bridge queue, and sends a market order;
exits are late market orders because no broker-side stop is sent. What changes
is the broker's minute table the spread and prices are read from, and
`cfd_families.broker_minute_table` already names it from `CFD_BROKER`, as
`_point_size` reads the point off the FundedNext spec snapshot.

`load_maps` is the one piece that is not shared: the precomputed maps behind
the combined book's `EXNESS_TICK_COSTS` path exist for Exness only.
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


def load_maps(path=None, source="bars"):
    raise SystemExit(
        "no precomputed FundedNext fill maps -- the combined book's "
        "EXNESS_TICK_COSTS path has only been built for Exness")
