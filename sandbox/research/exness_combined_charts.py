"""PNG charts for a book built by `exness_combined_strategies`.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user, and neither does a PNG on disk.**
Paste the numbers this prints into the reply and hand over the file paths
([[paste-results-into-the-reply]]). A chart is not a result on its own -- the
per-sleeve table from `exness_combined_strategies build` is still mandatory
alongside it ([[show-per-sleeve-detail-every-time]]).

WHAT THIS DRAWS, AND WHY IT IS A SEPARATE MODULE.

`exness_combined_strategies.build` writes a JSON payload but strips `settled`
and `curve` out of it before saving -- only the marked equity path survives. The
Monte Carlo needs the per-trade ledger, so this module re-runs the book with
`replay` wrapped, keeps everything, and caches the result. Re-plotting after
that is instant; the expensive part is the book run itself.

    python -m sandbox.research.exness_combined_charts equity
    python -m sandbox.research.exness_combined_charts montecarlo
    python -m sandbox.research.exness_combined_charts all --paths 20000

Charts land in `sandbox/results/charts/`.

THE THREE RESAMPLINGS, AND WHAT EACH ONE IS BLIND TO.

  `shuffle`   Permutes the trade order. Compounding a permutation of the same
              multiplicative returns lands on the SAME final equity every time,
              so this says nothing about return dispersion and the module does
              not pretend otherwise -- it is reported for path risk only.

  `resample`  Draws trades with replacement. This is the widest of the three
              and the least trustworthy: it treats 4,796 trades from eleven
              sleeves sharing one balance as independent draws, which they are
              not, so both its tails are wider than the book warrants.

  `block`     Stationary bootstrap of DAILY marked returns, mean block 5 days.
              Keeps short-run clustering and keeps open-position marks, so its
              drawdowns are the closest of the three to what the engine reports.
              This is the one to quote.

WHAT NONE OF THEM CAN DO.

Every draw reuses returns the book actually realised in the window. A bootstrap
cannot invent a regime that is absent from the sample, so the tails here are the
tails OF THIS WINDOW and not a bound on future risk. Two specific distortions
are worth stating out loud whenever these charts are reported:

  * Resampling spreads out drawdown CONCENTRATION. On the canon book one sleeve
    bore 81% of the worst marked fall; shuffling its trades in among everyone
    else's makes the simulated drawdowns look better behaved than a repeat of
    that one event would be ([[book-drawdown-is-one-intraday-position]]).
  * Simulated paths compound END-OF-DAY marks, so they understate the intraday
    extreme. On the canon book the realised gap is 9.9% daily against 12.9%
    intrabar -- about 30% more drawdown once you look inside the bar. Apply that
    uplift to every simulated drawdown percentile
    ([[engine-drawdown-is-mark-to-market]]).

And a Monte Carlo is NOT a control. It resamples the book's own trades, so it
cannot tell you whether the book beats noise; only `build --null` does that
([[coin-flip-control-beats-real-signals]], [[run-controls-only-when-asked]]).
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import pickle
import random
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from sandbox.research import exness_combined_strategies as ecs

CHARTS = os.path.join(ecs.RESULTS, "charts")
CACHE = os.path.join(ecs.RESULTS, "charts", "_book_cache")

#: Ink. Deliberately not matplotlib's default cycle: equity is one blue, loss is
#: one red, and the second Monte Carlo method gets a green that is separable
#: from both under the common colour-vision deficiencies.
INK = "#12171c"
MUTED = "#79838d"
RULE = "#dde1e6"
EQUITY = "#2a78d6"
LOSS = "#e34948"
ALT = "#1baf7a"
BAND = "#2a78d6"


# ---------------------------------------------------------------- book replay

def run_book(members=ecs.BOOK, risk_scale=0.7, gross_cap=None, limit=None,
             sizing_cap=1500.0, refresh=False):
    """The book, with the pieces `build` drops.

    `build` returns a payload whose `book` entry has `settled` and `curve`
    removed, and the Monte Carlo needs the trade ledger. So `ecs.replay` is
    wrapped for the duration of the call and the richest result -- the one with
    the most trades, which is always the combined book rather than a standalone
    sleeve -- is kept.
    """
    key = json.dumps({"m": list(members), "r": risk_scale, "g": gross_cap,
                      "s": sizing_cap}, sort_keys=True)
    tag = hashlib_key(key)
    path = os.path.join(CACHE, f"{tag}.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as handle:
            return pickle.load(handle)

    captured = {}
    real_replay = ecs.replay

    def replay(*args, **kwargs):
        out = real_replay(*args, **kwargs)
        if out["trades"] >= captured.get("trades", -1):
            captured.clear()
            captured.update(out)
        return out

    ecs.GLOBAL_SIZING_CAP = sizing_cap
    ecs.replay = replay
    try:
        ecs.build(limit or len(members), ecs.MAX_DOWN_RHO, ecs.MAX_LOSS_LIFT,
                  risk_scale=risk_scale, gross_cap=gross_cap,
                  members_exact=tuple(members))
    finally:
        ecs.replay = real_replay

    if not captured:
        raise SystemExit("replay was never called -- no book was built")
    book = {"settled": captured["settled"], "marked": captured["marked"],
            "curve": captured["curve"],
            "summary": {k: v for k, v in captured.items()
                        if k not in ("settled", "marked", "curve")},
            "members": list(members), "risk_scale": risk_scale,
            "gross_cap": gross_cap, "sizing_cap": sizing_cap}
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as handle:
        pickle.dump(book, handle)
    return book


def hashlib_key(text):
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------- reshaping

def _day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def series(book, initial=ecs.ef.INITIAL_BALANCE):
    """Daily marked equity, daily returns, drawdown path, per-trade returns.

    Per-trade returns are `pnl / equity AT ENTRY`, not `pnl / equity at close`.
    Lots were sized off the live balance at entry, so that is the fraction the
    trade actually risked; normalising on the closing balance would silently
    rescale every trade that overlapped another one.
    """
    marked = sorted(book["marked"], key=lambda p: p[0])
    stamps = [p[0] for p in marked]
    values = [p[1] for p in marked]

    def equity_at(ts):
        return values[max(bisect.bisect_right(stamps, ts) - 1, 0)]

    by_day = {}
    for ts, equity in marked:
        by_day[_day(ts)] = equity
    days = sorted(by_day)
    daily = [by_day[d] for d in days]

    returns, prev = [], initial
    for value in daily:
        returns.append(value / prev - 1.0)
        prev = value

    drawdown, peak = [], initial
    for value in daily:
        peak = max(peak, value)
        drawdown.append(-100.0 * (peak - value) / peak)

    trades = []
    for position in sorted(book["settled"], key=lambda p: p["exit_ts"]):
        equity = equity_at(position["entry_ts"])
        if equity > 0:
            trades.append(position["pnl"] / equity)

    months = sorted({d[:7] for d in days})
    month_end = {}
    for d in days:
        month_end[d[:7]] = by_day[d]
    monthly, prev = [], initial
    for month in months:
        monthly.append(100.0 * (month_end[month] / prev - 1.0))
        prev = month_end[month]

    return {"days": days, "daily": daily, "returns": returns,
            "drawdown": drawdown, "trades": trades,
            "months": months, "monthly": monthly, "initial": initial}


def max_drawdown(path):
    peak, worst = path[0], 0.0
    for value in path:
        if value > peak:
            peak = value
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def percentile(values, q):
    values = sorted(values)
    i = q * (len(values) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return values[lo] + (values[hi] - values[lo]) * (i - lo)


# ---------------------------------------------------------------- monte carlo

QUANTILES = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
DD_QUANTILES = (0.05, 0.5, 0.75, 0.95, 0.99)


def monte_carlo(data, paths=10000, block=5.0, seed=7, checkpoints=60):
    """All three resamplings, plus the percentile envelope for the fan chart.

    The envelope is accumulated at ~60 checkpoints rather than by keeping every
    path: 10,000 full paths of 592 days is half a gigabyte of Python floats for
    a picture that is 60 points wide.
    """
    initial = data["initial"]
    trade_r = data["trades"]
    daily_r = data["returns"]
    n_days, n_trades = len(daily_r), len(trade_r)

    # --- shuffle: order permuted, product therefore fixed -------------------
    rng = random.Random(seed + 1)
    order = list(trade_r)
    shuffle_dd, final = [], initial
    for _ in range(paths):
        rng.shuffle(order)
        equity, path = initial, [initial]
        for r in order:
            equity *= 1.0 + r
            path.append(equity)
        shuffle_dd.append(100.0 * max_drawdown(path))
        final = equity

    # --- resample: trades drawn with replacement ---------------------------
    rng = random.Random(seed + 2)
    resample_ret, resample_dd = [], []
    for _ in range(paths):
        equity, path = initial, [initial]
        for _ in range(n_trades):
            equity *= 1.0 + trade_r[rng.randrange(n_trades)]
            path.append(equity)
        resample_ret.append(100.0 * (equity - initial) / initial)
        resample_dd.append(100.0 * max_drawdown(path))

    # --- block: stationary bootstrap of daily marked returns ---------------
    rng = random.Random(seed + 3)
    restart = 1.0 / block
    marks = list(range(0, n_days + 1, max(1, n_days // checkpoints)))
    if marks[-1] != n_days:
        marks.append(n_days)
    block_ret, block_dd = [], []
    envelope = [[] for _ in marks]
    for _ in range(paths):
        equity, path = initial, [initial]
        i = rng.randrange(n_days)
        for _ in range(n_days):
            if rng.random() < restart:
                i = rng.randrange(n_days)
            equity *= 1.0 + daily_r[i]
            path.append(equity)
            i = (i + 1) % n_days
        block_ret.append(100.0 * (equity - initial) / initial)
        block_dd.append(100.0 * max_drawdown(path))
        for j, k in enumerate(marks):
            envelope[j].append(path[k])

    def summarise(returns, drawdowns, label, note):
        out = {"label": label, "note": note,
               "dd": {q: percentile(drawdowns, q) for q in DD_QUANTILES},
               "p_dd_over_20": sum(1 for x in drawdowns if x > 20) / paths,
               "p_dd_over_25": sum(1 for x in drawdowns if x > 25) / paths,
               "p_dd_over_50": sum(1 for x in drawdowns if x > 50) / paths,
               "dd_samples": drawdowns}
        if returns is not None:
            out["ret"] = {q: percentile(returns, q) for q in QUANTILES}
            out["mean_ret"] = sum(returns) / paths
            out["p_loss"] = sum(1 for x in returns if x < 0) / paths
            out["ret_samples"] = returns
        else:
            out["fixed_ret"] = 100.0 * (final - initial) / initial
        return out

    return {
        "paths": paths, "block_days": block, "seed": seed,
        "x": [k / n_days for k in marks],
        "envelope": {q: [percentile(col, q) for col in envelope]
                     for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        "shuffle": summarise(
            None, shuffle_dd, "Trade-order shuffle",
            "order permuted; final return is fixed by construction"),
        "resample": summarise(
            resample_ret, resample_dd, "Trade resample",
            "with replacement; assumes trade independence, so both tails are too wide"),
        "block": summarise(
            block_ret, block_dd, "Daily block bootstrap",
            f"stationary, mean block {block:g} days; keeps clustering and open marks"),
    }


# -------------------------------------------------------------------- drawing

def _style(ax, title=None, ylabel=None):
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    ax.grid(True, color=RULE, linewidth=0.7, alpha=0.7)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=INK, fontsize=11, loc="left", pad=8,
                     fontweight="bold")
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9)


def _money(value, _pos=None):
    if value >= 1000:
        return f"${value / 1000:g}k"
    return f"${value:,.0f}"


def _log_axis(ax, ticks, lo=None, hi=None):
    """A log y axis with OUR ticks and nothing else.

    matplotlib keeps its own minor decade labels on a log scale, so setting
    major ticks alone leaves `6 x 10^3` sitting between `$5k` and `$8k`. Both
    label sets have to be cleared explicitly.
    """
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(matplotlib.ticker.FixedLocator(ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(_money))
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_ylim(lo if lo is not None else min(ticks) * 0.92,
                hi if hi is not None else max(ticks) * 1.08)


def equity_png(book, data, out):
    """Equity on a log axis, with the drawdown path underneath it.

    Log, because a 7x book on a linear axis draws the first year as a flat line
    and the reader cannot see the shape of the thing that produced the number.
    Drawdown is a separate panel on a shared x rather than a second y axis --
    two scales on one frame is the single most misread chart there is.
    """
    days = [datetime.strptime(d, "%Y-%m-%d") for d in data["days"]]
    summary = book["summary"]
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(11, 6.4), dpi=170, sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.12})
    fig.patch.set_facecolor("white")

    top.plot(days, data["daily"], color=EQUITY, linewidth=1.6, zorder=3)
    top.fill_between(days, data["initial"], data["daily"], color=EQUITY,
                     alpha=0.10, zorder=2)
    _log_axis(top, [1000, 2000, 3000, 5000, 8000])
    top.plot([days[-1]], [data["daily"][-1]], "o", color=EQUITY, markersize=5,
             markeredgecolor="white", markeredgewidth=1.2, zorder=4)
    top.annotate(f"${data['daily'][-1]:,.0f}",
                 (days[-1], data["daily"][-1]),
                 textcoords="offset points", xytext=(-6, 10), ha="right",
                 fontsize=9.5, color=INK, fontweight="bold")
    _style(top, ylabel="equity, log")
    top.set_title(
        f"Book equity  ·  {len(book['members'])} sleeves  ·  "
        f"+{summary['return_pct']:.0f}%  ·  MTM drawdown "
        f"{summary['mtm_dd_pct']:.1f}%",
        color=INK, fontsize=12, loc="left", pad=10, fontweight="bold")

    bottom.fill_between(days, 0, data["drawdown"], color=LOSS, alpha=0.16)
    bottom.plot(days, data["drawdown"], color=LOSS, linewidth=1.0)
    bottom.axhline(0, color=RULE, linewidth=1)
    _style(bottom, ylabel="drawdown %")
    trough = summary.get("mtm_dd_trough")
    worst = abs(min(data["drawdown"]))
    if trough:
        # Marked BELOW the axis, not inside it: the drawdown path fills the
        # whole panel, so any in-axes annotation lands on top of the data.
        bottom.set_xlabel(
            f"worst marked fall {summary['mtm_dd_pct']:.1f}% intrabar "
            f"({worst:.1f}% on these daily marks), trough {trough}",
            color=MUTED, fontsize=8.5, labelpad=8)

    fig.text(0.5, 0.005,
             f"marked to market on every 30-minute bar  ·  "
             f"{data['days'][0]} to {data['days'][-1]}  ·  "
             f"{summary['trades']:,} trades  ·  "
             f"closed-trade drawdown {summary['max_dd_pct']:.1f}% is the "
             f"optimistic basis and should not be planned against",
             ha="center", fontsize=8, color=MUTED)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def monthly_png(data, out):
    """Monthly returns. Bars, because a month is a discrete bucket."""
    fig, ax = plt.subplots(figsize=(11, 3.2), dpi=170)
    fig.patch.set_facecolor("white")
    colors = [EQUITY if r >= 0 else LOSS for r in data["monthly"]]
    ax.bar(range(len(data["monthly"])), data["monthly"], color=colors,
           width=0.68)
    ax.axhline(0, color=RULE, linewidth=1)
    ax.set_xticks(range(0, len(data["months"]), 2))
    ax.set_xticklabels([m[2:] for m in data["months"][::2]], fontsize=8)
    positive = sum(1 for r in data["monthly"] if r > 0)
    _style(ax, title=f"Monthly return  ·  {positive} of {len(data['monthly'])} "
                     f"months positive", ylabel="%")
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def montecarlo_png(book, data, mc, out):
    """Fan, two histograms, and the percentile table as one sheet.

    The realised path is drawn on the fan for orientation only. It sits near the
    median BY CONSTRUCTION -- the bootstrap draws from that path's own returns --
    so it is not evidence of anything and the caption says so.
    """
    summary = book["summary"]
    realised_ret = summary["return_pct"]
    realised_dd = summary["mtm_dd_pct"]

    fig = plt.figure(figsize=(11, 10.4), dpi=170)
    fig.patch.set_facecolor("white")
    grid = fig.add_gridspec(3, 2, height_ratios=[1.5, 1, 1.0], hspace=0.46,
                            wspace=0.22, bottom=0.09, top=0.955)

    # --- fan ---------------------------------------------------------------
    fan = fig.add_subplot(grid[0, :])
    x = [t * len(data["days"]) for t in mc["x"]]
    env = mc["envelope"]
    fan.fill_between(x, env[0.05], env[0.95], color=BAND, alpha=0.13,
                     label="5th–95th percentile")
    fan.fill_between(x, env[0.25], env[0.75], color=BAND, alpha=0.24,
                     label="25th–75th")
    fan.plot(x, env[0.5], color=EQUITY, linewidth=1.8, label="median path")
    real_x = [i / (len(data["daily"]) - 1) * len(data["days"])
              for i in range(len(data["daily"]))]
    fan.plot(real_x, data["daily"], color=INK, linewidth=1.3, alpha=0.85,
             label="realised book")
    floor = min(env[0.05]) * 0.9
    ceiling = max(max(env[0.95]), max(data["daily"])) * 1.1
    ticks, step = [], 1000
    while step < ceiling:
        for mult in (1, 2, 5):
            if floor <= step * mult <= ceiling:
                ticks.append(step * mult)
        step *= 10
    _log_axis(fan, ticks or [1000, 10000], lo=floor, hi=ceiling)
    fan.set_xlabel("trading days", color=MUTED, fontsize=9)
    _style(fan, ylabel="equity, log")
    fan.set_title(
        f"Envelope of {mc['paths']:,} bootstrapped paths  ·  block "
        f"{mc['block_days']:g} days",
        color=INK, fontsize=12, loc="left", pad=10, fontweight="bold")
    legend = fan.legend(frameon=False, fontsize=8.5, loc="upper left",
                        labelcolor=MUTED)
    legend.get_frame().set_alpha(0)

    # --- histograms --------------------------------------------------------
    hist_ret = fig.add_subplot(grid[1, 0])
    top_ret = percentile(mc["block"]["ret_samples"], 0.995)
    _hist(hist_ret,
          [(mc["block"]["ret_samples"], EQUITY, "block bootstrap"),
           (mc["resample"]["ret_samples"], ALT, "trade resample")],
          0, max(top_ret, realised_ret * 1.2), realised_ret,
          f"realised +{realised_ret:.0f}%")
    _style(hist_ret, title="Final return", ylabel="share of paths")
    hist_ret.set_xlabel("% — rightmost bin holds everything above it",
                        color=MUTED, fontsize=8.5)

    hist_dd = fig.add_subplot(grid[1, 1])
    top_dd = max(percentile(mc["resample"]["dd_samples"], 0.995), realised_dd)
    _hist(hist_dd,
          [(mc["block"]["dd_samples"], EQUITY, "block bootstrap"),
           (mc["resample"]["dd_samples"], ALT, "trade resample")],
          0, top_dd * 1.1, realised_dd, f"realised {realised_dd:.1f}%")
    _style(hist_dd, title="Worst drawdown per path", ylabel="share of paths")
    hist_dd.set_xlabel("% — rightmost bin holds everything above it",
                       color=MUTED, fontsize=8.5)

    # --- table -------------------------------------------------------------
    #
    # The realised book is NOT a row here. It has no percentiles, and putting
    # its single value in a percentile grid parks it under whichever column
    # happens to be last -- which reads as "the book's p99", the exact opposite
    # of what it is. It goes in the title line instead.
    #
    table = fig.add_subplot(grid[2, :])
    table.axis("off")
    table.set_title(
        f"Percentiles across {mc['paths']:,} paths      "
        f"realised book:  +{realised_ret:.0f}% return, "
        f"{realised_dd:.1f}% max drawdown (intrabar)",
        color=INK, fontsize=10.5, loc="left", pad=14, fontweight="bold")
    header = ["method", "measure"] + [f"p{int(q * 100)}" for q in QUANTILES]
    rows = []
    for key in ("block", "resample", "shuffle"):
        item = mc[key]
        if "ret" in item:
            rows.append([item["label"], "return"]
                        + [f"{item['ret'][q]:.0f}%" for q in QUANTILES])
        else:
            rows.append([item["label"], "return"]
                        + ["fixed"] * len(QUANTILES))
        rows.append(["", "max drawdown"]
                    + [f"{item['dd'][q]:.1f}%" if q in DD_QUANTILES else "—"
                       for q in QUANTILES])
    widths = [0.20, 0.14] + [0.66 / len(QUANTILES)] * len(QUANTILES)
    drawn = table.table(cellText=rows, colLabels=header, loc="upper center",
                        cellLoc="right", colLoc="right", colWidths=widths)
    drawn.auto_set_font_size(False)
    drawn.set_fontsize(8)
    drawn.scale(1, 1.5)
    for (row, col), cell in drawn.get_celld().items():
        cell.set_edgecolor(RULE)
        cell.set_linewidth(0.6)
        cell.PAD = 0.04
        if row == 0:
            cell.set_text_props(color=MUTED, fontweight="bold")
            cell.set_facecolor("white")
        else:
            # Shade the block bootstrap, the one row pair worth quoting.
            cell.set_facecolor("#f0f4f8" if row <= 2 else "white")
            cell.set_text_props(color=INK)
        if col <= 1:
            cell.set_text_props(ha="left")

    uplift = realised_dd / max(abs(min(data["drawdown"])), 1e-9)
    fig.text(0.5, 0.050,
             "Every draw reuses this window's returns — a bootstrap cannot "
             "invent a regime the book never traded, and it spreads out the "
             "single-sleeve drawdown concentration the real book carries.",
             ha="center", fontsize=8.5, color=MUTED)
    fig.text(0.5, 0.030,
             "Simulated paths compound end-of-day marks, so every drawdown "
             "percentile above understates the intrabar figure — on this book "
             f"by about {uplift:.2f}x.",
             ha="center", fontsize=8.5, color=MUTED)
    fig.text(0.5, 0.010,
             "A Monte Carlo is not a control: it resamples the book's own "
             "trades. Only `build --null` measures the margin over noise.",
             ha="center", fontsize=8.5, color=MUTED, style="italic")

    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def _hist(ax, series_list, lo, hi, marker, marker_label, bins=44):
    width = (hi - lo) / bins
    for offset, (values, color, label) in enumerate(series_list):
        counts = [0] * bins
        for value in values:
            k = int((min(max(value, lo), hi - 1e-9) - lo) / width)
            counts[min(max(k, 0), bins - 1)] += 1
        share = [c / len(values) for c in counts]
        left = [lo + i * width + offset * width * 0.46 for i in range(bins)]
        ax.bar(left, share, width=width * 0.44, color=color, align="edge",
               label=label, alpha=0.9)
    peak = max(max(b.get_height() for b in ax.containers[i]) for i in
               range(len(series_list)))
    ax.set_ylim(0, peak * 1.22)          # headroom for the realised marker
    ax.set_xlim(lo, hi)
    ax.axvline(marker, color=INK, linewidth=1.4, linestyle=(0, (4, 3)))
    # The legend sits top-right, so the realised label hangs to the LEFT of its
    # rule wherever there is room for it -- otherwise the two collide, which is
    # exactly what happened when the label always ran rightwards.
    left = (marker - lo) / (hi - lo) > 0.25
    ax.annotate(marker_label, (marker, peak * 1.19),
                textcoords="offset points", xytext=(-6 if left else 6, 0),
                ha="right" if left else "left", va="top", fontsize=8,
                color=INK, fontweight="bold")
    ax.legend(frameon=False, fontsize=8, labelcolor=MUTED, loc="upper right")


# --------------------------------------------------------------------- report

def report(book, data, mc=None):
    """The numbers, for pasting into the reply. The PNG never reaches anyone."""
    summary = book["summary"]
    lines = [
        "",
        f"BOOK  {len(book['members'])} sleeves  risk_scale {book['risk_scale']}"
        f"  gross_cap {book['gross_cap'] or 'off'}",
        f"  window          {data['days'][0]} -> {data['days'][-1]}"
        f"  ({len(data['days'])} days, {summary['trades']:,} trades)",
        f"  return          +{summary['return_pct']:.1f}%"
        f"   final ${summary['final']:,.2f}",
        f"  MTM drawdown    {summary['mtm_dd_pct']:.2f}%"
        f"   (intrabar, trough {summary.get('mtm_dd_trough')})",
        f"  daily-marked dd {abs(min(data['drawdown'])):.2f}%"
        f"   <- what the Monte Carlo can see",
        f"  closed dd       {summary['max_dd_pct']:.2f}%   (optimistic)",
    ]
    if mc:
        lines += ["", f"MONTE CARLO  {mc['paths']:,} paths per method", ""]
        head = "  {:<24}{:>9}" + "{:>9}" * len(QUANTILES)
        lines.append(head.format("method / measure", "",
                                 *[f"p{int(q * 100)}" for q in QUANTILES]))
        for key in ("block", "resample", "shuffle"):
            item = mc[key]
            if "ret" in item:
                lines.append(head.format(
                    item["label"], "return",
                    *[f"{item['ret'][q]:.0f}%" for q in QUANTILES]))
            else:
                lines.append(head.format(item["label"], "return",
                                         *["fixed"] * len(QUANTILES)))
            lines.append(head.format(
                "", "max dd",
                *[f"{item['dd'][q]:.1f}%" if q in DD_QUANTILES else "-"
                  for q in QUANTILES]))
            lines.append(f"    {item['note']}")
        lines += [
            "",
            "  P(drawdown > 20%)  block "
            f"{mc['block']['p_dd_over_20']:.1%}   resample "
            f"{mc['resample']['p_dd_over_20']:.1%}   shuffle "
            f"{mc['shuffle']['p_dd_over_20']:.1%}",
            "  P(drawdown > 25%)  block "
            f"{mc['block']['p_dd_over_25']:.1%}   resample "
            f"{mc['resample']['p_dd_over_25']:.1%}   shuffle "
            f"{mc['shuffle']['p_dd_over_25']:.1%}",
            "  P(final < start)   block "
            f"{mc['block']['p_loss']:.2%}   resample "
            f"{mc['resample']['p_loss']:.2%}",
            "",
            "  Read the block bootstrap. The resample assumes trade "
            "independence and is too wide;",
            "  the shuffle cannot move the return at all. None of the three is "
            "a control -- run `build --null` for that.",
        ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="PNG equity curve and Monte Carlo for a combined book.")
    parser.add_argument("command", choices=("equity", "montecarlo", "all"))
    parser.add_argument("--members", default="canon",
                        help="'canon' for the recorded BOOK, or a "
                             "comma-separated list of symbol:family sleeves")
    parser.add_argument("--risk-scale", type=float, default=0.7)
    parser.add_argument("--gross-cap", type=float, default=None)
    parser.add_argument("--sizing-cap", type=float, default=1500.0)
    parser.add_argument("--paths", type=int, default=10000,
                        help="Monte Carlo paths per method")
    parser.add_argument("--block", type=float, default=5.0,
                        help="mean block length in days for the stationary "
                             "bootstrap")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--refresh", action="store_true",
                        help="re-run the book instead of using the cache")
    parser.add_argument("--out", default=CHARTS,
                        help="directory for the PNGs")
    parser.add_argument("--prefix", default=None,
                        help="filename prefix; defaults to the member set")
    args = parser.parse_args()

    members = (ecs.BOOK if args.members.strip().lower() == "canon"
               else tuple(s for s in args.members.replace(" ", "").split(",")
                          if s))
    prefix = args.prefix or ("canon" if members == ecs.BOOK else "book")
    os.makedirs(args.out, exist_ok=True)

    book = run_book(members, risk_scale=args.risk_scale,
                    gross_cap=args.gross_cap, sizing_cap=args.sizing_cap,
                    refresh=args.refresh)
    data = series(book)

    written = []
    if args.command in ("equity", "all"):
        written.append(equity_png(
            book, data, os.path.join(args.out, f"{prefix}_equity.png")))
        written.append(monthly_png(
            data, os.path.join(args.out, f"{prefix}_monthly.png")))

    mc = None
    if args.command in ("montecarlo", "all"):
        mc = monte_carlo(data, paths=args.paths, block=args.block,
                         seed=args.seed)
        written.append(montecarlo_png(
            book, data, mc,
            os.path.join(args.out, f"{prefix}_montecarlo.png")))

    print(report(book, data, mc))
    print("\nwrote")
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()
