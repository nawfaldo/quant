"""When Dukascopy actually publishes a minute bar, at 2-second resolution.

WHY NOT `exness_latency measure`. That samples every 30 seconds, so a bar it
sees could have arrived any time in the preceding half minute -- which is the
same order as the quantity being measured. Its `78s` also disagrees with this
file's own arithmetic depending on whether the lag is taken to the bar's OPEN or
its CLOSE, and 30-second sampling cannot separate 18 from 78.

This polls every two seconds and records the instant the newest bar index
advances, so the reading is the publish delay plus at most one poll interval.

IT MEASURES THE SAME THING THE CHASE USES: seconds past the minute, because that
is what `sleep_to_chase_window` needs to aim at. The lag from bar close is
printed beside it since that is what `MEASURED_LAG_SECONDS` means.

Only an OPEN market answers this -- a shut one serves its last bar forever
([[weekend-spreads-are-not-tradeable]]).

    py -m sandbox.research._seventh_publish --minutes 5
"""
from __future__ import annotations

import argparse
import statistics
import time
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="gbpjpy")
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    import dukascopy_python as dk
    from dukascopy_python import instruments as ins
    import logging
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    probes = {"gbpjpy": ins.INSTRUMENT_FX_CROSSES_GBP_JPY,
              "eurjpy": ins.INSTRUMENT_FX_CROSSES_EUR_JPY,
              "usdjpy": ins.INSTRUMENT_FX_MAJORS_USD_JPY}
    instrument = probes[args.symbol]

    print(f"polling {args.symbol} every {args.interval:g}s for "
          f"{args.minutes:g} min -- watching for the newest bar to advance")
    print(f"{'observed at':>14}{'newest bar':>14}{'past minute':>13}"
          f"{'lag from close':>16}")
    deadline = time.time() + args.minutes * 60.0
    seen, readings = None, []
    while time.time() < deadline:
        now = datetime.now(tz=UTC)
        try:
            frame = dk.fetch(instrument, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_BID,
                             now - timedelta(minutes=10), now)
        except Exception as error:  # noqa: BLE001
            print(f"  fetch failed: {type(error).__name__}: {error}")
            time.sleep(args.interval)
            continue
        if frame is None or not len(frame):
            time.sleep(args.interval)
            continue
        # THE NEWEST INDEX IS NOT THE NEWEST COMPLETE BAR. Dukascopy hands back
        # the minute still being traded -- asked at 11:10:48 it returns a bar
        # stamped 11:10, forty-eight seconds into it -- so watching the raw
        # index times the appearance of a PARTIAL bar and reads as a negative
        # lag. `write_rows` drops exactly these, so the daemon never sees them
        # and neither should this. Same filter, same event.
        minute_now = int(now.timestamp()) // 60 * 60
        complete = [i for i in frame.index
                    if int(i.to_pydatetime().replace(tzinfo=UTC).timestamp())
                    < minute_now]
        if not complete:
            time.sleep(args.interval)
            continue
        newest = complete[-1].to_pydatetime().replace(tzinfo=UTC)
        if seen is not None and newest > seen:
            close = newest + timedelta(minutes=1)
            lag = (now - close).total_seconds()
            past = now.second + now.microsecond / 1e6
            readings.append((past, lag))
            print(f"{now:%H:%M:%S}{'':>6}{newest:%H:%M}{'':>8}"
                  f"{past:>12.1f}s{lag:>15.1f}s")
        seen = newest
        time.sleep(args.interval)

    if not readings:
        print("\nno bar arrived -- market shut, or the poll window was short")
        return
    past = [p for p, _l in readings]
    lags = [l for _p, l in readings]
    print(f"\n{len(readings)} arrival(s)")
    print(f"  seconds past the minute : median {statistics.median(past):.1f}  "
          f"min {min(past):.1f}  max {max(past):.1f}")
    print(f"  lag from bar close      : median {statistics.median(lags):.1f}s  "
          f"min {min(lags):.1f}s  max {max(lags):.1f}s")
    print(f"\n  baked MEASURED_LAG_SECONDS['dukascopy'] = 78.0s")
    print(f"  a fixed :05 tick would have waited "
          f"{(60.0 - statistics.median(past) + 5.0) % 60.0:.0f}s longer than "
          f"the chase")


if __name__ == "__main__":
    main()
