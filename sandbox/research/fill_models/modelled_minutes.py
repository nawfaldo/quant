"""Modelled broker minutes for symbols whose Exness M1 history is too short.

WHY. `fr40`, `stoxx50`, `aus200`, `ethbtc`, `audjpy`, `eurusd` and `usdcad` have
Exness minute bars from 2026-06/08 only -- the terminal serves its last 100,000 bars and no more
([[mt5-maxbars-caps-broker-history]]) -- so `cfd_families` had no cost data for
any in-sample year and refused them. This builds the missing years so they can
be SCREENED. Every result priced here is flagged `modelled_cost` in its seal and
must be re-checked on real fills before it goes near the book.

WHAT A BROKER MINUTE IS USED FOR. Two things (`fill_models.exness`): the spread
charged at entry, and the price path over the feed lag and the one-bar exit
delay, taken as a RATIO so the level cancels. The path is the vendor's own 1m
bars -- they correlate 0.99 with the broker where both exist -- so only the
spread has to be modelled.

THE SPREAD MODEL, AND WHY IT IS NOT A CONSTANT OR A VOLATILITY CURVE.

    spread(t) = level(symbol, month(t)) * shape(symbol, slot(t))

Measured 2026-09-27 on the peers with long real histories: the broker's spread
is dominated by PRICING REGIMES, not by volatility. Every index CFD stepped down
4-5x in the same month (de40 2.3 -> 0.45 bp, uk100 5.0 -> 1.0, jp225 2.7 -> 0.6,
hk50 8.3 -> 1.9 in 2025-09) and again in 2026-08, and uk100 stepped up 4x in
2022-12. The daily log-spread against log-realised-vol slope was -0.57..+0.42
with |corr| <= 0.33 -- no usable volatility law. The three months a short
symbol has are all inside the cheapest regime, so carrying them backwards
would price 2023-2025 at a fifth of what the peers actually paid.

    level   the symbol's own real months set its ratio to each class peer;
            each earlier month is that ratio times the peer's real median that
            month, and the median across peers is taken. A broker-wide step
            therefore appears in the model the month it appeared at Exness.
    shape   the symbol's own real spread by New York half-hour (and weekend
            vs weekday for crypto), as a MEAN over the overall median -- the
            mean, because the expected cost of an entry is the mean spread of
            its minute, and the thin hours' blowouts belong in it.

`validate()` hides all but the last three months of each index peer, models it
from the others, and compares the monthly mean spread it predicts against the
real one.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

#: Symbol -> the peers its level is read from, and the first month to model.
#: The index peers' Exness tables start 2022-07-31; ethusd's 2020 spreads read
#: 174 bp and are not a regime anyone traded, so crypto starts in 2021.
MODELLED = {
    "fr40": (("de40", "uk100", "jp225", "hk50"), "2022-08"),
    "stoxx50": (("de40", "uk100", "jp225", "hk50"), "2022-08"),
    "aus200": (("de40", "uk100", "jp225", "hk50"), "2022-08"),
    "ethbtc": (("ethusd", "btc"), "2021-01"),
    # Exness tables from 2026-06-17 only; the FX peers all start 2020-01.
    "audjpy": (("audusd", "eurjpy", "gbpjpy", "gbpusd", "usdjpy"), "2020-01"),
    "eurusd": (("audusd", "eurjpy", "gbpjpy", "gbpusd", "usdjpy"), "2020-01"),
    "usdcad": (("audusd", "eurjpy", "gbpjpy", "gbpusd", "usdjpy"), "2020-01"),
}
#: Which calibration a modelled symbol takes.
KIND = {"fr40": "index", "stoxx50": "index", "aus200": "index",
        "ethbtc": "crypto", "audjpy": "forex", "eurusd": "forex",
        "usdcad": "forex"}
CRYPTO = frozenset(("ethbtc", "ethusd", "btc"))

#: v3 (mean level per in/out-of-session bucket). `validate` pooled the raw
#: model at 1.00/0.95 (index in/out), 1.04/1.02 (forex), 1.00/1.00 (crypto);
#: the level is DIVIDED by the class figure below.
#:
#: WHAT `modelled_cost_check` FOUND, 2026-09-27 -- a sibling with full real
#: history rebuilt from its last 92 days, the same cells scored on real and on
#: modelled fills; cost per trade modelled/real, rank correlation of returns:
#:
#:   index   de40 0.96/1.00, uk100 0.99/1.00, jp225 0.73/0.97 (rth);
#:           0.63-1.10 and 0.94-0.98 in the `day` window. Good enough to screen.
#:   forex   audusd 0.95/0.73, gbpusd 0.58/0.75, usdjpy 0.31/0.93 -- each pair
#:           had its own wide-spread spells (usdjpy ~4 bp through 2021-23) that
#:           no peer shares, so a modelled pair can be priced 1-3x too cheap.
#:   crypto  ethusd 2.57/0.69 from btc alone: too expensive and loosely ranked.
#:
#: v1 (medians) and v2 (one all-day level) charged 0.26-0.81x the real cost per
#: trade on indices and FX; see `monthly_levels`.
#: FOREX CARRIES A SAFETY MARGIN, by operator choice 2026-09-27. Its spread
#: calibration (1.03) is right on monthly means, but the trade-cost check put
#: modelled FX at 0.31-0.95x the real cost per trade, median 0.58 across
#: audusd/gbpusd/usdjpy in rth and day. 1.03 x 0.58 = 0.60 divides the level
#: by that measured shortfall, i.e. FX spreads x1.7 -- which makes a modelled
#: pair about right on the median sibling and still cheap on usdjpy's.
CALIBRATION = {"index": 0.98, "crypto": 1.00, "forex": 0.60}
MODEL_VERSION = "v3"
#: Real months needed before a symbol's ratio to a peer is trusted.
MIN_OVERLAP_MONTHS = 2


def _month(stamps):
    return np.array([dt.datetime.fromtimestamp(int(t), dt.timezone.utc)
                     .strftime("%Y-%m") for t in stamps])


def _slot(stamps, crypto):
    minute = (stamps // 60) % 1440
    slot = minute // 30
    if crypto:
        weekend = ((stamps // 86_400 + 3) % 7) >= 5
        slot = slot + 48 * weekend
    return slot


def rth_of(symbol):
    """The symbol's cash session in real New York minutes (may wrap)."""
    from sandbox.research import cfd_families as ef  # noqa: PLC0415
    session = ef.SESSION.get(symbol)
    if session is None:
        have = ef.coverage(symbol)
        session = ef.derive_session(symbol, have["table"], have["source"])
    return ef.window_clock(session, ef.SHIFT_HOURS.get(symbol, 0), "rth")[2]


def in_rth(stamps, rth):
    """0/1 per broker-table stamp (New York wall clock): inside the session."""
    minute = (stamps // 60) % 1440
    opened, closed = rth
    if opened < closed:
        return ((minute >= opened) & (minute < closed)).astype(np.int8)
    return ((minute >= opened) | (minute < closed)).astype(np.int8)


def monthly_levels(stamps, spreads, rth):
    """`{bucket: {YYYY-MM: mean spread bp}}`, bucket 1 inside the cash session
    and 0 outside it; each month clipped at its own 99.5th percentile so one
    glitched print cannot move it.

    THE MEAN, NOT THE MEDIAN (v2): a trade pays the average spread of its
    minute, and the average carries the wide spells a median flattens away.

    TWO BUCKETS (v3): the sibling check (`modelled_cost_check`) found v2 still
    charging 0.29-0.65x the real cost per trade in RTH. Under the old pricing an
    index CFD's spread was roughly flat through the day; today's is tight in
    session and wide at night. One all-day level times today's intraday shape
    carried "the session is cheap" back into years when it was not, and most
    trades are in session. Each bucket now follows its own peer history.
    """
    stamps, spreads = stamps[::5], spreads[::5]
    months = _month(stamps)
    buckets = in_rth(stamps, rth)
    out = {0: {}, 1: {}}
    for bucket in (0, 1):
        for m in np.unique(months[buckets == bucket]):
            month = spreads[(months == m) & (buckets == bucket)]
            if len(month) >= 20:
                out[bucket][m] = float(
                    np.minimum(month, np.percentile(month, 99.5)).mean())
    return out


def level_series(symbol, own, peers):
    """`{month: modelled level}` for ONE bucket, from the peers' months and the
    symbol's own overlap. `own` and each of `peers` are `{month: mean}`. A
    month where the symbol has real data keeps it."""
    ratios = {}
    for name, months in peers.items():
        overlap = [m for m in own if m in months and months[m] > 0]
        if len(overlap) >= MIN_OVERLAP_MONTHS:
            ratios[name] = float(np.median([own[m] / months[m] for m in overlap]))
    if not ratios:
        return {}
    out = {}
    for month in sorted({m for months in peers.values() for m in months}):
        guesses = [ratios[n] * peers[n][month] for n in ratios
                   if month in peers[n]]
        if guesses:
            out[month] = float(np.median(guesses))
    out.update(own)
    return out


def levels(symbol, own, peers):
    """`{bucket: {month: level}}`; a bucket no peer can price falls back to
    the other bucket's level times the symbol's own bucket ratio."""
    out = {b: level_series(symbol, own[b], {p: v[b] for p, v in peers.items()})
           for b in (0, 1)}
    if not out[0] and not out[1]:
        raise SystemExit(f"{symbol}: no peer overlaps its real months")
    for b in (0, 1):
        if not out[b] and own[b] and own[1 - b]:
            ratio = float(np.median([own[b][m] / own[1 - b][m]
                                     for m in own[b] if m in own[1 - b]]))
            out[b] = {m: v * ratio for m, v in out[1 - b].items()}
    return out


def shape(symbol, stamps, spreads, rth):
    """`{(bucket, slot): mean spread / the bucket's mean}` from real minutes."""
    slots = _slot(stamps, symbol in CRYPTO)
    buckets = in_rth(stamps, rth)
    out = {}
    for bucket in (0, 1):
        inside = buckets == bucket
        if not inside.any():
            continue
        middle = float(spreads[inside].mean())
        for slot in np.unique(slots[inside]):
            values = spreads[inside & (slots == slot)]
            if len(values) >= 20:
                out[(bucket, int(slot))] = float(values.mean()) / middle
    return out


def modelled_spreads(symbol, stamps, level, profile, rth):
    """Spread bp per stamp; a slot the real data never saw gets 1.0 x level."""
    months = _month(stamps)
    slots = _slot(stamps, symbol in CRYPTO)
    buckets = in_rth(stamps, rth)
    return np.array([level[b].get(m, np.nan) * profile.get((b, int(s)), 1.0)
                     for m, s, b in zip(months, slots, buckets)])


def validate(real_minutes, peers, keep_months=3):
    """Leave-one-out on a peer class: hide all but the last `keep_months` of
    each, model the rest from the others, compare monthly MEAN spreads per
    bucket. `real_minutes` maps symbol -> (stamps, spreads). Rows are
    `(symbol, month, bucket, modelled, real)`, before any calibration."""
    rths = {s: rth_of(s) for s in peers}
    means = {s: monthly_levels(*real_minutes[s], rths[s]) for s in peers}
    rows = []
    for target in peers:
        stamps, spreads = real_minutes[target]
        months = _month(stamps)
        recent = sorted(set(months))[-keep_months:]
        seen = np.isin(months, recent)
        own = {b: {m: v for m, v in means[target][b].items() if m in recent}
               for b in (0, 1)}
        level = levels(target, own,
                       {p: means[p] for p in peers if p != target})
        profile = shape(target, stamps[seen], spreads[seen], rths[target])
        hidden_stamps = stamps[~seen][::10]
        model = modelled_spreads(target, hidden_stamps, level, profile,
                                 rths[target])
        truth = spreads[~seen][::10]
        hidden = months[~seen][::10]
        buckets = in_rth(hidden_stamps, rths[target])
        for m in np.unique(hidden):
            for b in (0, 1):
                sel = (hidden == m) & (buckets == b) & np.isfinite(model)
                if sel.sum() >= 20:
                    rows.append((target, m, b, float(model[sel].mean()),
                                 float(truth[sel].mean())))
    return rows


def synthetic_minutes(symbol, real_stamps, real_spreads, vendor, peer_levels):
    """`(stamps, opens, spreads)` for every vendor minute from the model's start
    month up to the first real broker minute.

    `vendor` is `(stamps, opens)` from the vendor's 1m table -- same New York
    wall clock as the broker table, so no clock conversion is needed.
    `peer_levels` is `{peer: monthly_levels(...)}`.
    """
    _peers, start = MODELLED[symbol]
    rth = rth_of(symbol)
    own = monthly_levels(real_stamps, real_spreads, rth)
    level = levels(symbol, own, peer_levels)
    factor = CALIBRATION[KIND[symbol]]
    level = {b: {m: v / factor for m, v in series.items() if m not in own[b]}
             for b, series in level.items()}
    profile = shape(symbol, real_stamps, real_spreads, rth)
    stamps, opens = vendor
    first = int(dt.datetime.strptime(start, "%Y-%m")
                .replace(tzinfo=dt.timezone.utc).timestamp())
    keep = (stamps >= first) & (stamps < real_stamps[0]) & (opens > 0)
    stamps, opens = stamps[keep], opens[keep]
    spreads = modelled_spreads(symbol, stamps, level, profile, rth)
    good = np.isfinite(spreads)
    return stamps[good], opens[good], spreads[good]
