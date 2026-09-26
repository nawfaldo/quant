"""Alpha-decay monitor for the canon book, one sleeve at a time.

After SharpeEdge, "When Should You Stop a Trading System? Alpha Decay Detection
in Python" (https://www.youtube.com/watch?v=CWOhChDdnn4). Every sleeve's trades
are split at `--live-start` into a BENCHMARK -- its record since `--bench-start`
-- and the LIVE stretch after it, and the live stretch is tested against the
benchmark with the video's statistics:

  dd      the live stretch's worst drawdown, in R          -> STOP
  cusum   the peak of a lower CUSUM on R standardised by the benchmark
  alpha   the worst 32-trade rolling mean of R minus the benchmark mean; 32 is
          the window that detects a 0.5-sigma shift at 95% / 80% power
          cusum AND alpha together = confirmed decay       -> STOP
  eq      the live stretch's mean R minus the benchmark's (the equity bands at
          today's trade count). Shown, never acted on -- the video only alerts.
  rate    live trade count against the benchmark's rate    -> PAUSE

EVERY P-VALUE COMES FROM ONE NULL, AND IT IS THE RIGHT ONE. Each null replicate
draws a benchmark AND a live stretch from the benchmark itself, by circular
block bootstrap, and scores the live one against the drawn benchmark's own mean
and sd -- exactly as the real live stretch is scored against the real
benchmark. So the error in the benchmark's mean is inside the null, not assumed
away, and every statistic is a whole-stretch extreme, which makes each p-value
already corrected for having looked at every trade.

WHAT WAS WRONG IN THE FIRST VERSION, fixed here, because each one manufactured
alarms on healthy sleeves:
  - a p95 drawdown curve checked at every trade stops 10-29% of benchmark-drawn
    paths, not 5%;
  - a rolling regression whose error comes from its own 32 trades calls a run
    of 32 ordinary -1R losers "certain" -- a trend sleeve has near-zero spread
    inside a winless window -- so healthy sleeves showed hundreds of
    "significant" windows;
  - a month ranking scored the unfinished month as if complete, and flags 10%
    of months by construction;
  - a Poisson trade-count test assumes trades arrive independently; they
    cluster, so it overstates significance;
  - nothing corrected for testing 24 rows at once. Holm now does, per test
    family, across every row printed.

VERDICTS, in this order:
  TOO FEW   under 10 live or 30 benchmark trades
  STOP      Holm-adjusted p < 0.05 on min(dd, max(cusum, alpha)) x 2
  PAUSE     Holm-adjusted p < 0.05 on the trade rate
  UNPROVEN  the benchmark's own mean R is not 2 standard errors above zero, so
            there is no established edge for decay to be measured from. It says
            nothing about whether the sleeve made money in the live stretch.
  OK        none of the above

`--selftest` proves the calibration on synthetic sleeves with regime-dependent
win rates: the stop must fire on no more than 5% of streams with no decay.

WHY R AND NOT DOLLARS. `points / distance` is a trade's outcome in units of
the risk it took, after the live-execution spread. A sleeve's dollars depend on
the shared balance and the minimum lot, which change over time for reasons that
are not the sleeve's ([[min-lot-pinned-sleeves-do-not-compound]]).

ONE COST MODEL ON BOTH SIDES. The live-fill maps cover a fixed span per symbol
(2020 or 2022-08 .. the last `precompute`); a trade outside it is filled on the
constant spread and the vendor open instead. Benchmark and live trades are both
clipped to the map span so a cheaper cost model cannot pass for a change in
edge -- the video's "check your costs" step.

THE JP225 CASE. `--case jp225` runs the six jp225 sleeves dropped on
2026-09-22; with the default split (benchmark 2022 .. go-live, live from
2026-09-07) it is the decision the operator took. jp225 fills stand on a vendor
table that correlates 0.23 with the broker
([[jp225-vendor-table-is-not-the-broker-market]]), so its R is itself suspect.

THE BENCHMARK IS NOT CLEAN. 2022-2024 is in-sample for every sleeve's
parameters and 2025-2026 is the window the survivor pool was screened on
([[exness-survivor-pool-is-oos-conditioned]]). A flattering benchmark makes the
tests MORE likely to fire, never less.

    py -m sandbox.alpha_decay
    py -m sandbox.alpha_decay --case jp225
    py -m sandbox.alpha_decay --bench-start 2025-01-01 --live-start 2026-01-01
    py -m sandbox.alpha_decay --selftest
    py -m sandbox.alpha_decay --detail          <- every p-value as well
    tools/check_alpha_decay.bat                 <- everything (--full)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
from scipy import stats as sps

JP225_DROPPED = ("jp225:cusum", "jp225:kalman", "jp225:momentum_stack",
                 "jp225:obv_divergence", "jp225:vol_regime",
                 "jp225:volume_thrust")

#: The account went live here; it is where the jp225 case splits.
GO_LIVE = "2026-09-07"

PATHS = 4000
CHUNK = 500
SEED = 7
LEVEL = 0.05
CUSUM_K = 0.5           # slack, in benchmark sigmas
SHIFT_SIGMA = 0.5       # smallest shift the rolling window must detect
Z_ALPHA, Z_POWER = 1.959964, 0.841621
WINDOW = int(math.ceil(((Z_ALPHA + Z_POWER) / SHIFT_SIGMA) ** 2))   # 32
MIN_BENCH = 30
MIN_LIVE = 10
EDGE_T = 2.0
RATE_BIN_DAYS = 30
MIN_RATE_BINS = 6
DAY = 86400


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Bootstrap machinery
# --------------------------------------------------------------------------

def optimal_block(x):
    """Politis-White (2004) block length, with Patton et al.'s (2009)
    correction, for the CIRCULAR block bootstrap used below.

    This is the `optimal_block_length` of `arch` (not installed here), its
    `b_cb` column: D = 4/3 g(0)^2, flat-top lag window, M = 2 m-hat.
    """
    x = np.asarray(x, float)
    n = len(x)
    if n < 8:
        return 1
    x = x - x.mean()
    kn = max(5, int(math.ceil(math.log10(n))))
    mmax = min(int(math.ceil(math.sqrt(n))) + kn, n - 1)
    bmax = int(math.ceil(min(3 * math.sqrt(n), n / 3)))
    gamma = np.array([np.dot(x[: n - k], x[k:]) / n for k in range(mmax + 1)])
    if gamma[0] <= 0:
        return 1
    rho = gamma[1:] / gamma[0]
    bound = 2.0 * math.sqrt(math.log10(n) / n)
    mhat = mmax
    for m in range(0, mmax - kn + 1):
        if np.all(np.abs(rho[m: m + kn]) < bound):
            mhat = m
            break
    big_m = min(2 * max(mhat, 1), mmax)
    lags = np.arange(-big_m, big_m + 1)
    t = np.abs(lags / big_m)
    flat = np.where(t <= 0.5, 1.0, np.where(t <= 1.0, 2.0 * (1.0 - t), 0.0))
    g = gamma[np.abs(lags)]
    big_g = np.sum(flat * np.abs(lags) * g)
    d_cb = 4.0 / 3.0 * np.sum(flat * g) ** 2
    if d_cb <= 0 or big_g == 0:
        return 1
    block = (2.0 * big_g ** 2 / d_cb) ** (1 / 3) * n ** (1 / 3)
    return int(max(1, min(round(block), bmax)))


def block_paths(x, count, length, block, rng):
    """`count` circular moving-block resamples of `x`, each `length` long."""
    n = len(x)
    blocks = int(math.ceil(length / block))
    starts = rng.integers(0, n, size=(count, blocks))
    index = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return x[index.reshape(count, -1)[:, :length]]


def max_drawdown(r):
    """Worst peak-to-trough fall of cumulative R, peak starting at 0, per row."""
    cum = np.cumsum(r, axis=-1)
    peak = np.maximum(np.maximum.accumulate(cum, axis=-1), 0.0)
    return np.max(peak - cum, axis=-1)


def cusum_peak(z, k=CUSUM_K):
    """Peak of the lower CUSUM S_t = max(0, S_t-1 - z_t - k), per row."""
    s = np.zeros(z.shape[0])
    peak = np.zeros(z.shape[0])
    for column in z.T:
        s = np.maximum(0.0, s - column - k)
        peak = np.maximum(peak, s)
    return peak


def worst_window(d, w):
    """Lowest rolling mean over `w` consecutive entries, per row."""
    cum = np.concatenate([np.zeros((d.shape[0], 1)), np.cumsum(d, axis=1)], axis=1)
    return np.min((cum[:, w:] - cum[:, :-w]) / w, axis=1)


def statistics(live, mu, sd, drift, w):
    """The four decay statistics, oriented so that LARGER is WORSE.

    Each is computed on the live stretch's excess over ITS benchmark's mean, so
    a replicate scored against its own drawn benchmark is exactly comparable to
    the real stretch scored against the real one. The drawdown adds `drift`,
    the observed benchmark mean, back: it is the drawdown the stretch would
    have had at the benchmark's own edge, measured the same way on both sides.
    """
    excess = live - mu[:, None]
    return {
        "dd": max_drawdown(excess + drift),
        "cusum": cusum_peak(excess / sd[:, None]),
        "alpha": -worst_window(excess, w),
        "eq": -excess.mean(axis=1),
    }


def null_test(rb, rl, rng, paths=PATHS):
    """p-values for every statistic, the block length and the benchmark t."""
    nb, n = len(rb), len(rl)
    block = optimal_block(rb)
    w = min(WINDOW, n)
    mu, sd = rb.mean(), rb.std(ddof=1)
    observed = statistics(rl[None, :], np.array([mu]), np.array([sd]), mu, w)
    worse = {k: 0 for k in observed}
    means = []
    for start in range(0, paths, CHUNK):
        count = min(CHUNK, paths - start)
        bench = block_paths(rb, count, nb, block, rng)
        run = block_paths(rb, count, n, block, rng)
        mu_b, sd_b = bench.mean(axis=1), bench.std(axis=1, ddof=1)
        sd_b = np.where(sd_b > 0, sd_b, sd)
        null = statistics(run, mu_b, sd_b, mu, w)
        for key in worse:
            worse[key] += int(np.sum(null[key] >= observed[key][0] - 1e-12))
        means.append(mu_b)
    p = {k: (1 + v) / (1 + paths) for k, v in worse.items()}
    # Decay needs BOTH detectors (an intersection-union test, level-preserving
    # as the larger p); the stop is either route, Bonferroni over the two.
    p["stop"] = min(1.0, 2 * min(p["dd"], max(p["cusum"], p["alpha"])))
    se = float(np.std(np.concatenate(means), ddof=1))
    return p, block, (mu / se if se > 0 else float("nan"))


def rate_test(bench_ts, n_live, bench_lo, live_lo, live_hi):
    """Live trade count against the benchmark rate, allowing for clustering.

    Quasi-Poisson: the dispersion is the variance-to-mean ratio of the
    benchmark's own 30-day counts, and the variance also carries the error in
    the benchmark rate itself. Two-sided.
    """
    span_b, span_l = (live_lo - bench_lo) / DAY, (live_hi - live_lo) / DAY
    bins = int(span_b // RATE_BIN_DAYS)
    if bins < MIN_RATE_BINS or span_l <= 0:
        return float("nan"), float("nan")
    edges = bench_lo + np.arange(bins + 1) * RATE_BIN_DAYS * DAY
    counts = np.histogram(bench_ts, bins=edges)[0]
    if counts.mean() <= 0:
        return float("nan"), float("nan")
    phi = max(1.0, counts.var(ddof=1) / counts.mean())
    expected = len(bench_ts) / span_b * span_l
    z = (n_live - expected) / math.sqrt(phi * expected * (1 + span_l / span_b))
    return n_live / expected, float(2 * sps.norm.sf(abs(z)))


def holm(pvalues):
    """Holm step-down adjusted p-values; NaN entries are left out."""
    index = [i for i, p in enumerate(pvalues) if p == p]
    order = sorted(index, key=lambda i: pvalues[i])
    adjusted = [float("nan")] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(order) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


# --------------------------------------------------------------------------
# One row
# --------------------------------------------------------------------------

def monitor(name, trades, window, rng):
    bench_lo, live_lo, live_hi = window
    bench = [t for t in trades if t["entry_ts"] >= bench_lo and t["exit_ts"] < live_lo]
    run = [t for t in trades if live_lo <= t["exit_ts"] < live_hi]
    row = {"name": name, "nb": len(bench), "nl": len(run),
           "bench": bench, "live": run,
           "mb": float(np.mean([t["r"] for t in bench])) if bench else float("nan"),
           "ml": float(np.mean([t["r"] for t in run])) if run else float("nan")}
    if len(bench) < MIN_BENCH or len(run) < MIN_LIVE:
        return row
    rb = np.array([t["r"] for t in bench])
    rl = np.array([t["r"] for t in run])
    row["p"], row["block"], row["tb"] = null_test(rb, rl, rng)
    row["rate"], row["p_rate"] = rate_test(
        np.array([t["exit_ts"] for t in bench]), len(run), bench_lo, live_lo, live_hi)
    return row


def decide(rows):
    tested = [r for r in rows if "p" in r]
    for r, adj in zip(tested, holm([r["p"]["stop"] for r in tested])):
        r["adj_stop"] = adj
    for r, adj in zip(tested, holm([r["p_rate"] for r in tested])):
        r["adj_rate"] = adj
    for r in rows:
        if "p" not in r:
            r["verdict"] = "TOO FEW"
        elif r["adj_stop"] < LEVEL:
            r["verdict"] = "STOP"
        elif r["adj_rate"] == r["adj_rate"] and r["adj_rate"] < LEVEL:
            r["verdict"] = "PAUSE"
        elif not r["tb"] >= EDGE_T:
            r["verdict"] = "UNPROVEN"
        else:
            r["verdict"] = "OK"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def map_span(symbol, maps):
    """[first, last) stamp the live-fill maps price for this symbol."""
    entry = maps.get(symbol, {}).get("entry") or {}
    if not entry:
        return None
    keys = [int(k) for k in entry]
    return min(keys), max(keys) + int(maps[symbol].get("bar_seconds", 1800))


def load(keys, bench_lo):
    """R-multiple trade streams per sleeve on live execution, and each
    symbol's usable span: bars and fill maps both present."""
    from sandbox.research import _mc_live as live
    from sandbox.research import exness_combined_strategies as ecs

    live.arm()
    with open(ecs.MAPS_OVERRIDE, encoding="utf-8") as handle:
        maps = json.load(handle)
    rows = ecs.candidate_rows_exact(keys)
    streams, spans = {}, {}
    for index, key in enumerate(keys, start=1):
        symbol = key.split(":", 1)[0]
        progress(f"replaying {index}/{len(keys)}  {key}")
        if key in ecs.EXTERNAL or key not in rows:
            raise SystemExit(f"{key}: not a sealed in-house sleeve")
        ecs.ef.resolve(symbol, allow_stale=True)
        _res, log, bars, _ctx = ecs.sleeve_trades(
            rows[key], lo=bench_lo, hi=int(time.time()) + DAY,
            shown=ecs.SHOWN_EQUITY.get(key, 1.0))
        span = map_span(symbol, maps)
        if span is None:
            raise SystemExit(f"{symbol}: no live-fill map")
        spans[symbol] = (span[0], min(span[1], bars[-1][0] + 1))
        streams[key] = [{"entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
                         "side": t.get("side", 0), "r": t["points"] / t["distance"]}
                        for t in log if t.get("distance", 0) > 0]
    progress("")
    return streams, spans


# --------------------------------------------------------------------------
# Terminal view
# --------------------------------------------------------------------------

COLOR = {"red": "31", "green": "32", "yellow": "33", "cyan": "36",
         "gray": "90", "bold": "1", "dim": "2"}
_USE_COLOR = [False]
WIDTH = 96


def paint(text, *styles):
    if not _USE_COLOR[0] or not styles:
        return text
    codes = ";".join(COLOR[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def visible(text):
    """Length on screen, colour codes excluded."""
    out, skip = 0, False
    for ch in text:
        if ch == "\033":
            skip = True
        elif skip and ch == "m":
            skip = False
        elif not skip:
            out += 1
    return out


def pad(text, width, right=False):
    gap = " " * max(0, width - visible(text))
    return gap + text if right else text + gap


def progress(text):
    if sys.stdout.isatty():
        sys.stdout.write("\r  " + pad(paint(text, "gray"), WIDTH - 2) + "\r")
        sys.stdout.flush()


def setup_terminal(no_color=False):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    _USE_COLOR[0] = (not no_color and sys.stdout.isatty()
                     and not os.environ.get("NO_COLOR"))
    if _USE_COLOR[0] and os.name == "nt":
        os.system("")           # switches the Windows console into ANSI mode


def box(lines):
    inner = WIDTH - 4
    print("  ╭" + "─" * inner + "╮")
    for line in lines:
        print("  │ " + pad(line, inner - 2) + " │")
    print("  ╰" + "─" * inner + "╯")


def rule(title=""):
    label = f" {title} " if title else ""
    print("  " + paint("──", "gray") + paint(label, "bold")
          + paint("─" * (WIDTH - 4 - visible(label)), "gray"))


VERDICT = {"STOP": ("■ STOP", "red", "bold"),
           "PAUSE": ("■ PAUSE", "yellow", "bold"),
           "OK": ("● OK", "green"),
           "UNPROVEN": ("○ UNPROVEN", "cyan"),
           "TOO FEW": ("· too few", "gray")}


def verdict_text(verdict):
    label, *styles = VERDICT[verdict]
    return paint(label, *styles)


def signal_text(row):
    """How strong the decay evidence is, in words, from the stop p-value."""
    if "p" not in row:
        return paint("—", "gray")
    raw, adj = row["p"]["stop"], row["adj_stop"]
    if adj < LEVEL:
        return paint("▮▮▮▮ confirmed", "red", "bold")
    if raw < LEVEL:
        return paint("▮▮▮▯ borderline", "yellow")
    if raw < 0.20:
        return paint("▮▮▯▯ weak", "yellow", "dim")
    return paint("▯▯▯▯ none", "gray")


def r_text(value):
    if value != value:
        return paint("—", "gray")
    return paint(f"{value:+.2f}", "green" if value > 0 else "red")


def view(rows, case, keys, windows, args):
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {}
    for r in rows:
        if r["name"] != "---":
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lo = min(w[0] for w in windows.values())
    live_lo = next(iter(windows.values()))[1]
    hi = max(w[2] for w in windows.values())

    print()
    box([paint("ALPHA DECAY CHECK", "bold") + paint(f"   {case} · {len(keys)} sleeves", "cyan")
         + " " * 8 + paint(today, "gray"),
         paint("benchmark ", "gray") + f"{day(lo)} → {day(live_lo - 1)}"
         + paint("     live ", "gray") + f"{day(live_lo)} → {day(hi - 1)}",
         paint("R = profit ÷ risk taken, after costs · live stops where the cost "
               "maps stop", "gray")])
    print()
    summary = "   ".join(
        f"{verdict_text(v)} {paint(str(counts[v]), 'bold')}"
        for v in ("STOP", "PAUSE", "OK", "UNPROVEN", "TOO FEW") if v in counts)
    print("  " + summary)
    print()

    head = (pad("", 4) + pad("sleeve", 24) + pad("trades", 13, True)
            + pad("R per trade", 18, True) + "   " + pad("decay signal", 18)
            + "verdict")
    sub = (pad("", 4) + pad("", 24) + pad("bench", 7, True) + pad("live", 6, True)
           + pad("bench", 9, True) + pad("live", 9, True))
    by_symbol = {}
    for r in rows:
        if r["name"] == "---":
            continue
        symbol = r["name"].split(":", 1)[0].split(" ", 1)[0]
        by_symbol.setdefault(symbol, []).append(r)
    print(paint(head, "gray"))
    print(paint(sub, "gray"))
    for symbol, group in by_symbol.items():
        rule(symbol.upper())
        for r in group:
            pooled = " pooled" in r["name"]
            name = (f"all {len(group) - 1} together" if pooled
                    else r["name"].split(":", 1)[1])
            line = ("    " + pad(paint(name, "bold") if pooled else name, 24)
                    + pad(str(r["nb"]), 7, True) + pad(str(r["nl"]), 6, True)
                    + pad(r_text(r["mb"]), 9, True) + pad(r_text(r["ml"]), 9, True)
                    + "   " + pad(signal_text(r), 18) + verdict_text(r["verdict"]))
            print(line)
    print()
    rule("what it means")
    for verdict, meaning in (
            ("STOP", "live is significantly worse than its benchmark: switch it off"),
            ("PAUSE", "trading far more or less often than it should: look for a bug"),
            ("OK", "no sign of decay, and the benchmark proved an edge"),
            ("UNPROVEN", "benchmark never proved an edge (t under 2): nothing to decay from"),
            ("TOO FEW", "under 10 live trades: too early to judge")):
        print("    " + pad(verdict_text(verdict), 13) + paint(meaning, "gray"))
    print("    " + pad(paint("signal", "bold"), 13)
          + paint("none · weak · borderline (one sleeve alone) · confirmed "
                  "(survives all rows)", "gray"))
    print("    " + pad("", 13) + paint("false alarms are held at 5% (see the self-test); the "
                                     "price is power:", "gray"))
    print("    " + pad("", 13) + paint("a sleeve that lost its whole edge is caught only "
                                     "17-30% of the time", "gray"))

    flagged = [r for r in rows if r.get("verdict") in ("STOP", "PAUSE")]
    if flagged:
        print()
        rule("why it fired")
        print(paint(f"    {'':24}{'':>6}{'win%':>7}{'payoff':>8}{'R/trade':>9}"
                    f"{'longs':>8}{'shorts':>8}", "gray"))
        for r in flagged:
            for label, trades in (("bench", r["bench"]), ("live", r["live"])):
                win, payoff, mean = side_stats(trades)
                print(f"    {pad(r['name'] if label == 'bench' else '', 24)}"
                      f"{label:>6}{fmt(win, '.0f'):>7}{fmt(payoff, '.2f'):>8}"
                      f"{fmt(mean, '+.3f'):>9}{fmt(side_stats(trades, 1)[2], '+.3f'):>8}"
                      f"{fmt(side_stats(trades, -1)[2], '+.3f'):>8}")
    if args.detail:
        print()
        rule("all the numbers")
        detail_table(rows)
    print()


def fmt(value, spec, empty="-"):
    return empty if value is None or value != value else format(value, spec)


def side_stats(trades, side=None):
    """Win rate, payoff and mean R, for one side or both."""
    r = np.array([t["r"] for t in trades if side is None or t["side"] == side])
    if len(r) == 0:
        return float("nan"), float("nan"), float("nan")
    wins, losses = r[r > 0], r[r <= 0]
    payoff = (wins.mean() / -losses.mean()
              if len(wins) and len(losses) and losses.mean() < 0 else float("nan"))
    return len(wins) / len(r) * 100, payoff, r.mean()


def detail_table(rows):
    head = (f"{'sleeve':28}{'bench n':>8}{'R/tr':>7}{'t':>6}{'live n':>7}{'R/tr':>7}"
            f"{'p dd':>7}{'p cusum':>8}{'p alpha':>8}{'p eq':>7}{'rate':>6}{'p rate':>7}"
            f"{'adj p':>7}  verdict")
    width = len(head) - len("  verdict")
    print("  " + head)
    for r in rows:
        if r["name"] == "---":
            continue
        line = (f"{r['name']:28}{r['nb']:>8}{fmt(r['mb'], '+.3f'):>7}"
                f"{fmt(r.get('tb'), '+.1f'):>6}{r['nl']:>7}{fmt(r['ml'], '+.3f'):>7}")
        if "p" in r:
            p = r["p"]
            line += (f"{p['dd']:>7.3f}{p['cusum']:>8.3f}{p['alpha']:>8.3f}{p['eq']:>7.3f}"
                     f"{fmt(r['rate'], '.2f'):>6}{fmt(r['p_rate'], '.3f'):>7}"
                     f"{r['adj_stop']:>7.3f}")
        print(f"  {line:<{width}}  {r['verdict']}")
    print(paint("\n  t = benchmark mean / its block-bootstrap error.  p dd = worst drawdown,"
                " p cusum / p alpha = mean-shift detectors,\n  p eq = live mean vs benchmark."
                "  adj p = Holm-adjusted min(dd, max(cusum, alpha)) x 2 over every row.",
                "gray"))


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def synthetic(n, rng, win_lo, win_hi, stay=0.97):
    """A trend-sleeve-like R stream: -1R losers, lognormal winners (median
    2R), and a win rate that switches between two regimes and persists -- the
    serial dependence the block bootstrap has to carry."""
    state, out = int(rng.integers(2)), np.empty(n)
    for i in range(n):
        if rng.random() > stay:
            state = 1 - state
        p = win_hi if state else win_lo
        out[i] = (rng.lognormal(math.log(2.0), 0.6) if rng.random() < p
                  else -1.0 + rng.normal(0, 0.05))
    return out


def selftest(reps=300):
    """How often the STOP fires, unadjusted, on synthetic sleeves.

    Base win rates 32% / 42% give about +0.26R a trade. `cut` is the fraction
    of that edge removed from the live stretch by lowering both win rates.
    """
    rng = np.random.default_rng(SEED)
    paths = PATHS // 4
    base = 0.37 * 2.0 * math.exp(0.18) - 0.63
    per_point = 2.0 * math.exp(0.18) + 1.0     # d(mean R) / d(win rate)
    print()
    box([paint("SELF-TEST", "bold") + paint("   does the stop fire when it should, "
                                          "and only then?", "cyan"),
         paint(f"{reps} fake sleeves per line · edge {base:+.2f}R a trade · "
               f"win rate switches between regimes", "gray")])
    print()
    print(paint(f"    {'case':34}{'bench':>7}{'live':>6}   {'stop fired':<30}", "gray"))
    for label, nb, n, cut in (("no decay", 300, 100, 0.0),
                              ("no decay, short benchmark", 80, 80, 0.0),
                              ("no decay, long live", 300, 300, 0.0),
                              ("edge halved", 300, 100, 0.5),
                              ("edge gone", 300, 100, 1.0),
                              ("edge gone, long live", 300, 300, 1.0)):
        drop = cut * base / per_point
        fired = 0
        for index in range(reps):
            progress(f"{label}  {index + 1}/{reps}")
            rb = synthetic(nb, rng, 0.32, 0.42)
            rl = synthetic(n, rng, 0.32 - drop, 0.42 - drop)
            p, _block, _t = null_test(rb, rl, rng, paths=paths)
            fired += p["stop"] < LEVEL
        rate = fired / reps * 100
        bar = "█" * int(round(rate / 2.5))
        if cut == 0:
            mark = paint("✔ false alarm ≤ 5%", "green") if rate <= 5 else paint("✘ over 5%", "red", "bold")
            bar = paint(bar, "green" if rate <= 5 else "red")
        else:
            mark = paint("caught", "gray")
            bar = paint(bar, "cyan")
        print(f"    {label:34}{nb:>7}{n:>6}   {rate:>5.1f}%  {pad(bar, 14)} {mark}")
    print()
    print("    " + paint("no-decay lines must stay at or under 5%; decay lines show how "
                         "often a real loss is caught", "gray"))
    print()


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def run(args):
    from sandbox.research import exness_combined_strategies as ecs

    bench_lo, live_lo = stamp(args.bench_start), stamp(args.live_start)
    as_of = stamp(args.as_of) + DAY if args.as_of else None
    keys = list(ecs.BOOK) if args.case == "canon" else list(JP225_DROPPED)
    streams, spans = load(keys, bench_lo)

    def window(symbol):
        first, last = spans[symbol]
        return max(bench_lo, first), live_lo, (min(last, as_of) if as_of else last)

    rng = np.random.default_rng(SEED)
    rows = []
    for index, key in enumerate(keys, start=1):
        progress(f"testing {index}/{len(keys)}  {key}")
        rows.append(monitor(key, streams[key], window(key.split(":", 1)[0]), rng))

    # Pooled per symbol: sleeves on one market share its session and its bad
    # fortnight, and the account feels them as one system. Each pooled row
    # follows its own sleeves so the view can group them.
    symbols = {}
    for key in keys:
        symbols.setdefault(key.split(":", 1)[0], []).append(key)
    ordered = []
    for symbol, members in symbols.items():
        ordered += [r for r in rows if r["name"] in members]
        if len(members) > 1:
            progress(f"testing {symbol} pooled")
            merged = sorted((t for k in members for t in streams[k]),
                            key=lambda t: t["exit_ts"])
            ordered.append(monitor(f"{symbol} pooled ({len(members)})",
                                   merged, window(symbol), rng))
    progress("")
    decide(ordered)
    view(ordered, args.case, keys, {s: window(s) for s in symbols}, args)


#: What `--full` shows, in order, before the self-test.
PRESETS = (
    ("Canon since go-live", "benchmark 2022 → go-live, live from 2026-09-07",
     ["--case", "canon"]),
    ("Canon, 2026 vs 2025", "out-of-sample benchmark, 2026 as live",
     ["--case", "canon", "--bench-start", "2025-01-01", "--live-start", "2026-01-01"]),
    ("jp225 case since go-live", "the six sleeves dropped on 2026-09-22",
     ["--case", "jp225"]),
    ("jp225, 2026 vs 2025", "same six, out-of-sample benchmark",
     ["--case", "jp225", "--bench-start", "2025-01-01", "--live-start", "2026-01-01"]),
)


def full(parser):
    """Every preset with all its numbers, then the self-test: what the .bat shows."""
    for title, note, argv in PRESETS:
        print()
        print("  " + paint(f"▶ {title}", "bold", "cyan") + paint(f"   {note}", "gray"))
        run(parser.parse_args(argv + ["--detail"]))
    selftest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", choices=("canon", "jp225"), default="canon")
    parser.add_argument("--bench-start", default="2022-01-01")
    parser.add_argument("--live-start", default=GO_LIVE)
    parser.add_argument("--as-of", default=None,
                        help="last day of data to use (default: all)")
    parser.add_argument("--detail", action="store_true",
                        help="also print every p-value")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--full", action="store_true",
                        help="every preset and the self-test (what the .bat runs)")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    setup_terminal(args.no_color)
    if args.full:
        full(parser)
    elif args.selftest:
        selftest()
    else:
        run(args)


if __name__ == "__main__":
    main()
