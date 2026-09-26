"""Add one symbol's spread/entry/exit maps to the existing bars maps file."""
import json, os, sys
from sandbox.research.fill_models import exness as le
from sandbox.research import exness_latency as lat
from sandbox.research import cfd_families as ef

SOURCE = "bars"
targets = sys.argv[1:]
path = le.maps_path(SOURCE)
with open(path, encoding="utf-8") as h:
    out = json.load(h)
for symbol in targets:
    if not le.has_feed(symbol, SOURCE):
        print(f"{symbol}: no {SOURCE} table -- skipped", flush=True)
        continue
    ef.resolve(symbol, allow_stale=True)
    bars = le._bars_for(symbol)
    feed = lat.FEED.get(symbol, "dukascopy")
    seconds = lat.MEASURED_LAG_SECONDS[feed]
    stamps = [b[ef.TS] for b in bars]
    spreads = le.spread_map(symbol, stamps, SOURCE)
    opens = {b[ef.TS]: b[ef.O] for b in bars}
    entries = le.price_map(symbol, opens, seconds + le.BRIDGE_QUEUE_SECONDS, SOURCE)
    exits = le.price_map(symbol, opens, le.exit_delay_seconds(bars, seconds), SOURCE)
    le.release_feed(SOURCE)
    out[symbol] = {
        "feed": feed, "source": SOURCE, "lag_seconds": seconds,
        "queue_seconds": le.BRIDGE_QUEUE_SECONDS,
        "bar_seconds": le.bar_seconds(bars),
        "exit_delay_seconds": le.exit_delay_seconds(bars, seconds),
        "bars": len(bars),
        "spread_bp": {str(k): round(v, 6) for k, v in spreads.items()},
        "entry": {str(k): v for k, v in entries.items()},
        "exit": {str(k): v for k, v in exits.items()},
    }
    print(f"{symbol}: {feed} +{seconds:g}s -- {len(spreads):,} priced, "
          f"{len(entries):,} refilled, {len(exits):,} exit-repriced of "
          f"{len(bars):,} bars", flush=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as h:
    json.dump(out, h)
os.replace(tmp, path)
print(f"{len(out)} symbols -> {path} ({os.path.getsize(path)/1e6:.1f} MB)")
