"""Does the live model's delay match what the server actually does?

MEASURED, NOT ASSUMED. `_seventh_publish` polled gbpjpy every two seconds on
2026-09-07 and timed five consecutive arrivals: a complete Dukascopy minute bar
is served a MEDIAN 0.6 SECONDS after it closes, at most 2.4.

THE BAKED 78s TIMED A DIFFERENT EVENT AND THIS MODULE ONCE REPEATED THE ERROR.
Dukascopy serves the minute STILL BEING TRADED -- asked at 11:10:48 it hands
back a bar stamped 11:10 -- so watching the raw newest index catches a PARTIAL
bar, and measuring that to the bar's OPEN gives 78 seconds. `write_rows` has
always dropped partial bars, so the daemon never saw them; only the measurement
did. Filter to complete bars the way the daemon does and the answer is 0.6s.

SO THE MODEL IS PESSIMISTIC, NOT OPTIMISTIC. `precompute` charges
`MEASURED_LAG_SECONDS[feed] + BRIDGE_QUEUE_SECONDS` = 79s for Dukascopy. The
real chain is:

    30m bar closes             10:00:00
    Dukascopy publishes        10:00:00.6   (+0.6s, measured)
    chase probe picks it up    10:00:01.6   (+1.6s, 2s probe interval)
    runtime 500ms poll         10:00:01.9
    bridge queue + order_send  10:00:02.9   (+3s)

Roughly 3 seconds against a charged 79. Every live-execution figure in this
repository is therefore a LOWER bound on the book, not an upper one.

This prices that gap: the same book, the same trades, at the modelled delay and
at the measured one, on the broker's own 1m bars.

    py -m sandbox.research._seventh_serverlag
"""
from __future__ import annotations

import argparse
import json
import math
import os

from sandbox.research import exness_combined_strategies as cs
from sandbox.research import cfd_families as ef
from sandbox.research import exness_latency as lat
from sandbox.research.fill_models import exness as le

#: The feed daemon's tick, copied from `idk_market_live_data_feeds`.
TICK_OFFSET_SECONDS = 5.0
TICK_PERIOD_SECONDS = 60.0

SERVER_MAPS = os.path.join(os.path.dirname(le.__file__),
                           "exness_maps_serverlag.json")


#: MEASURED 2026-09-07 by `_seventh_publish` at 2-second resolution, gbpjpy,
#: five consecutive arrivals: median 0.6s after the bar closes, max 2.4s.
#:
#: THE BAKED 78s TIMED THE WRONG EVENT. Dukascopy serves the minute STILL BEING
#: TRADED -- asked at 11:10:48 it returns a bar stamped 11:10 -- so watching the
#: raw newest index catches a partial bar and, measured to that bar's OPEN,
#: reads as 78 seconds. `write_rows` has always dropped those, so the daemon
#: never saw them; only the measurement did. Filtering to complete bars the way
#: the daemon does gives 0.6s.
MEASURED_PUBLISH_SECONDS = {"dukascopy": 0.6, "binance": 2.0}

#: What the runtime adds after the store has the bar: the 500ms poll (half of
#: one on average) plus `BRIDGE_QUEUE_SECONDS`.
RUNTIME_SECONDS = 0.25


def true_entry_delay(feed, chase_interval=2.0):
    """Seconds from bar close to order, with the chasing daemon.

    The chase probes every `chase_interval`, so a bar waits half of one on
    average rather than the remainder of a minute.
    """
    return (MEASURED_PUBLISH_SECONDS.get(feed, 0.6) + chase_interval / 2.0
            + RUNTIME_SECONDS + le.BRIDGE_QUEUE_SECONDS)


def stored_after(publish_seconds):
    """Seconds from bar close until the feed daemon has actually written it.

    The daemon wakes at `TICK_OFFSET_SECONDS` past each minute, so a bar
    published `publish_seconds` after its close waits for the next such tick.
    Bar closes here are always on a 30-minute boundary, hence on a whole
    minute, which is what makes this a fixed quantity rather than a
    distribution.
    """
    ticks = math.ceil((publish_seconds - TICK_OFFSET_SECONDS)
                      / TICK_PERIOD_SECONDS)
    return ticks * TICK_PERIOD_SECONDS + TICK_OFFSET_SECONDS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the server-lag maps even if present")
    args = parser.parse_args()

    symbols = sorted({k.split(":")[0] for k in cs.BOOK})
    print(f"{'symbol':9}{'feed':11}{'baked':>8}{'measured':>10}"
          f"{'modelled':>10}{'true':>8}{'model overcharges':>19}")
    plan = {}
    for symbol in symbols:
        feed = lat.FEED.get(symbol, "dukascopy")
        publish = lat.MEASURED_LAG_SECONDS[feed]
        modelled = publish + le.BRIDGE_QUEUE_SECONDS
        true = true_entry_delay(feed)
        plan[symbol] = (modelled, true)
        print(f"{symbol:9}{feed:11}{publish:>7.0f}s"
              f"{MEASURED_PUBLISH_SECONDS.get(feed, 0.6):>9.1f}s"
              f"{modelled:>9.0f}s{true:>7.1f}s{modelled - true:>18.0f}s")

    if os.path.exists(SERVER_MAPS) and not args.rebuild:
        print(f"\nserver-lag maps present: {SERVER_MAPS}")
    else:
        print(f"\nbuilding server-lag maps ...", flush=True)
        out = {}
        for symbol in symbols:
            if not le.has_feed(symbol, "bars"):
                print(f"  {symbol}: no broker table -- skipped")
                continue
            ef.resolve(symbol, allow_stale=True)
            bars = le._bars_for(symbol)
            stamps = [b[ef.TS] for b in bars]
            opens = {b[ef.TS]: b[ef.O] for b in bars}
            _modelled, true = plan[symbol]
            spreads = le.spread_map(symbol, stamps, "bars")
            entries = le.price_map(symbol, opens, true, "bars")
            # The exit is a market order one whole BAR after the signal, then
            # the same chain again -- so it inherits the same understatement.
            exits = le.price_map(symbol, opens,
                                 le.bar_seconds(bars) + true, "bars")
            le.release_feed("bars")
            out[symbol] = {
                "feed": lat.FEED.get(symbol, "dukascopy"), "source": "bars",
                "lag_seconds": true - le.BRIDGE_QUEUE_SECONDS,
                "queue_seconds": le.BRIDGE_QUEUE_SECONDS,
                "bar_seconds": le.bar_seconds(bars),
                "exit_delay_seconds": le.bar_seconds(bars) + true,
                "bars": len(bars),
                "spread_bp": {str(k): round(v, 6) for k, v in spreads.items()},
                "entry": {str(k): v for k, v in entries.items()},
                "exit": {str(k): v for k, v in exits.items()},
            }
            print(f"  {symbol}: entry +{true:.0f}s, "
                  f"exit +{le.bar_seconds(bars) + true:.0f}s, "
                  f"{len(entries):,} repriced", flush=True)
        with open(SERVER_MAPS, "w", encoding="utf-8") as handle:
            json.dump(out, handle)
        print(f"wrote {SERVER_MAPS} "
              f"({os.path.getsize(SERVER_MAPS) / 1e6:.0f} MB)")

    print(f"\nRun the book against each to price the difference:")
    print(f"  py -m sandbox.research._seventh_sealed_live --from 2025-01-01")
    print(f"  MAPS={SERVER_MAPS} ...")


if __name__ == "__main__":
    main()
