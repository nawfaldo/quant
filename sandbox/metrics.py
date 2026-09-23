"""Ranking metrics over sized trades. Strategy-agnostic.

The sweep ranks on monthly consistency rather than total PnL, so everything here
is organised around the monthly series and a train/holdout split.
"""
from datetime import datetime, timezone

INITIAL = 10_000.0


def month_key(ts):
    # Timestamps are New York wall-clock as fake UTC, so read them back as UTC.
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{dt.year}-{dt.month:02d}"


def monthly(sized):
    months = {}
    for entry_ts, pnl in sized:
        key = month_key(entry_ts)
        months[key] = months.get(key, 0.0) + pnl
    return dict(sorted(months.items()))


def split_ts(iso_date):
    """Epoch seconds for an ISO date, in the fake-UTC convention bars use."""
    return int(datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc).timestamp())


def calendar(months):
    """`months` with gaps filled by 0.0, so rolling windows span adjacent months.

    A month in which a strategy took no trades is a zero, not an absence: without
    the fill, a 3-month window could silently straddle a hole and read as a
    contiguous run.
    """
    if not months:
        return []
    keys = sorted(months)
    year, month = (int(part) for part in keys[0].split("-"))
    last_year, last_month = (int(part) for part in keys[-1].split("-"))
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(months.get(f"{year}-{month:02d}", 0.0))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def consistency(months):
    """Monthly-consistency panel: how evenly the PnL arrives, not how much.

    A strategy can post a fine total and a fine profit factor while earning it in
    two months and bleeding through the rest. Each measure here is designed to
    fail that case:

      * `pos_rate`         share of months in profit
      * `max_loss_streak`  longest run of consecutive losing months — the drawdown
                           you would have to sit through to keep trading it
      * `worst_quarter`    worst rolling 3-month sum; a bad patch that compounds
                           shows up here even when no single month looks awful
      * `top_month_share`  fraction of all gross profit from the single best
                           month; near 1.0 means one month *is* the strategy
    """
    series = calendar(months)
    if not series:
        return {"pos_rate": 0.0, "max_loss_streak": 0,
                "worst_quarter": 0.0, "top_month_share": 0.0}

    streak = worst_streak = 0
    for value in series:
        streak = streak + 1 if value < 0 else 0
        worst_streak = max(worst_streak, streak)

    windows = [sum(series[i:i + 3]) for i in range(max(1, len(series) - 2))]
    gross = sum(v for v in series if v > 0)
    return {
        "pos_rate": round(sum(1 for v in series if v > 0) / len(series), 3),
        "max_loss_streak": worst_streak,
        "worst_quarter": round(min(windows), 2),
        "top_month_share": round(max(series) / gross, 3) if gross > 0 else 0.0,
    }


def segment(sized, lo=None, hi=None):
    """`sized` trades whose *entry* falls in `[lo, hi)`, in entry order.

    Walk-forward folds are timestamp slices of a trade list. Entry time is the
    boundary that matters: a fold owns the decisions made inside it, and a
    position opened on the last afternoon of a window is flattened at that
    session's close by `execution.resolve` anyway.
    """
    return sorted((ts, pnl) for ts, pnl in sized
                  if (lo is None or ts >= lo) and (hi is None or ts < hi))


def pad(months, lo, hi):
    """`months` widened to cover `[lo, hi)`, missing months entered as 0.0.

    A window in which a strategy chose not to trade is a flat month, not a
    month that does not exist. Without this, a walk-forward fold that selects
    nothing would vanish from the monthly series instead of diluting it, and
    `pos_rate` would be computed over only the months that happened to trade.
    """
    out = dict(months)
    year, month = (int(p) for p in month_key(lo).split("-"))
    last_year, last_month = (int(p) for p in month_key(hi - 1).split("-"))
    while (year, month) <= (last_year, last_month):
        out.setdefault(f"{year}-{month:02d}", 0.0)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return dict(sorted(out.items()))


def stats(sized, split=None, initial=INITIAL, span=None):
    """Full metric bundle. `split` (epoch seconds) adds train/holdout PnL.

    `span` is an optional `(from, to)` in epoch seconds; months inside it with
    no trades are counted as flat rather than dropped.
    """
    months = monthly(sized)
    if span is not None:
        months = pad(months, *span)
    values = list(months.values())
    pnls = [pnl for _, pnl in sized]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)

    peak = equity = initial
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    mean = sum(values) / len(values) if values else 0.0
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 if values else 0.0

    out = {
        "pnl": round(sum(pnls), 2),
        "trades": len(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3) if pnls else 0.0,
        "pf": round(gross_win / gross_loss, 3) if gross_loss else (999.0 if gross_win else 0.0),
        "max_dd": round(max_dd, 2),
        "n_months": len(values),
        "pos_months": sum(1 for v in values if v > 0),
        "worst_month": round(min(values), 2) if values else 0.0,
        "best_month": round(max(values), 2) if values else 0.0,
        # Monthly Sharpe: the consistency measure the search actually ranks on.
        "msharpe": round(mean / std, 3) if std else 0.0,
        **consistency(months),
        "months": {k: round(v, 2) for k, v in months.items()},
    }
    if split is not None:
        out["train"] = round(sum(p for ts, p in sized if ts < split), 2)
        out["hold"] = round(sum(p for ts, p in sized if ts >= split), 2)
    return out
