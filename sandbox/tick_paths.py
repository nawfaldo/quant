"""Raw-tick replay for minute bars whose stop and target both traded.

The ordinary optimizer deliberately matches the Rust engine's conservative
stop-before-target rule.  Research diagnostics can call this module to replace
that assumption only where the minute OHLC is ambiguous.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sandbox import data
from sandbox import execution
from sandbox.data import H, L, O, TS


def ambiguous_minutes(bars, signals, ex):
    """Return exit-minute timestamps whose OHLC touched both bracket legs."""
    by_entry = defaultdict(list)
    for signal in signals:
        by_entry[bars[signal.index][TS]].append(signal)
    by_ts = {bar[TS]: bar for bar in bars}
    out = set()
    for fill in execution.resolve(bars, signals, ex):
        candidates = by_entry.get(fill.entry_ts, ())
        signal = next(
            (
                candidate
                for candidate in candidates
                if candidate.side == fill.side
                and candidate.stop == fill.stop
            ),
            None,
        )
        if signal is None:
            continue
        bar = by_ts[fill.exit_ts]
        if fill.side == execution.LONG:
            both = (
                bar[L] <= fill.price - signal.stop
                and bar[H] >= fill.price + signal.target
            )
        else:
            both = (
                bar[H] >= fill.price + signal.stop
                and bar[L] <= fill.price - signal.target
            )
        if both:
            out.add(fill.exit_ts)
    return out


def _literal(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000000Z"
    )


def _chunks(values, size=25):
    ordered = sorted(values)
    for index in range(0, len(ordered), size):
        yield ordered[index : index + size]


def load(minutes, feature_source, chunk_size=25):
    """Load ordered trade prices for selected minute timestamps.

    ``feature_source`` is the minute feature map returned by
    :func:`data.load_l2_features`; it keeps tick replay on the same feed chosen
    by the minute-bar merger.
    """
    grouped = defaultdict(list)
    for minute in sorted(minutes):
        source = feature_source.get(minute, {}).get("source")
        if source in {"dbento", "bm"}:
            grouped[source].append(minute)

    paths = defaultdict(list)
    for source, source_minutes in grouped.items():
        table = f"{source}_nq_ticks"
        for chunk in _chunks(source_minutes, chunk_size):
            clauses = []
            for minute in chunk:
                lo = _literal(minute)
                hi = _literal(minute + 60)
                clauses.append(f"(timestamp >= '{lo}' AND timestamp < '{hi}')")
            rows = data.query(
                f"SELECT cast(timestamp as long),price FROM {table} "
                f"WHERE {' OR '.join(clauses)} ORDER BY timestamp"
            )
            for timestamp, price in rows:
                minute = int(timestamp) // 1_000_000_000 // 60 * 60
                paths[minute].append(float(price))
    return dict(paths)


def resolve(bars, signals, ex, feature_source=None):
    """Resolve only ambiguous minute bars from raw ticks."""
    minutes = ambiguous_minutes(bars, signals, ex)
    features = feature_source if feature_source is not None else data.load_l2_features()
    paths = load(minutes, features)
    missing = sorted(minutes - paths.keys())
    fills = execution.resolve(bars, signals, ex, intrabar_prices=paths)
    return fills, {
        "ambiguous_minutes": len(minutes),
        "resolved_minutes": len(paths),
        "missing_minutes": missing,
    }
