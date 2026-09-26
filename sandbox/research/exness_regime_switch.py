"""A dead switch: each sleeve trades only in the regimes where it earned in-sample.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the tables into the reply,
including the per-sleeve assignment table and the per-sleeve contribution table.
Reporting the book line alone is not a result ([[paste-results-into-the-reply]],
[[show-per-sleeve-detail-every-time]]).

WHAT THIS IS.

`exness_combined_strategies` runs every member all the time. This module lets a
member be switched OFF for whole stretches of calendar: one set of strategies
owns regime A, another owns regime B, and the book at any instant is whoever is
alive. The switch is a hard refusal in `replay` -- `regime_gate` is consulted
before sizing, so a dead sleeve consumes no equity and takes no gross-cap budget
from the sleeves that are alive. Scaling to zero would not do this: every
downstream table would still count the trade as filled.

THE PROTOCOL, WHICH IS THE WHOLE POINT.

    fit    2018-01-01 .. 2024-12-31     which regimes each sleeve is ON in
    test   2025-01-01 .. 2026-08-20     the switch scored, once

That is the split the sleeves' own parameters were sealed on, so the regime
assignment is chosen where the cells were already fitted and read where they
were not. It is also roughly 1,760 trading days rather than the 580 the previous
attempt at this had, which is the reason it is worth trying again -- a regime
rule's sample size is the number of regime EPISODES, not trades, and the earlier
study died at 1-10 episodes with every t inside |1.26|
([[regime-switching-has-no-testable-sample]]). The episode count per regime is
printed beside every assignment for exactly that reason: if it is single digits,
the assignment is noise and the table says so rather than hiding it.

TWO THINGS THE FIT WINDOW DOES NOT BUY.

  * 2018-2024 is IN-SAMPLE for each cell's own parameters. So a sleeve's
    per-regime P&L there partly reflects where its parameter search found its
    wins, not where the strategy works. The regime rule inherits that fit. This
    is unavoidable -- the alternative is fitting on the holdout, which is worse
    -- and it is why the OOS column is the only one that counts.
  * Pool MEMBERSHIP was screened on 2025-2026
    ([[exness-survivor-pool-is-oos-conditioned]]). The regime rule is honest;
    the menu it chooses from is not. That biases which sleeves are available,
    not which regimes they are assigned to.

THE REGIME SIGNAL IS EXOGENOUS AND SHARED.

One label per calendar day, from an EWMA of NQ daily returns -- the same
construction as `daily_multipliers`, and causal in the same way: the label
offered for a day is built from days STRICTLY BEFORE it. NQ price volatility is
used rather than each sleeve's own market or the book's equity curve because
that comparison has already been run: exogenous NQ price vol beat the constant
18/18 while an equity-curve signal managed 15/24, and routing each sleeve to its
own market's volatility measured worse
([[price-vol-beats-pnl-vol-for-exposure]]).

Bucket boundaries are quantiles of the FIT window only, then frozen and applied
unchanged to the test window. Re-quantiling on the full sample would let the
2025-2026 distribution decide what counted as calm in 2019.

NQ:OFI CANNOT BE SWITCHED. Its `level_two` feature table begins 2025-02-12, so
it has no fit-window history at all -- there is nothing to assign it on that is
not the holdout. It is held ALWAYS ON and marked as such in the table. Any
sleeve whose fit window is too short is treated the same way.

DEFAULT ON, NOT DEFAULT OFF. A regime in which a sleeve has fewer than
`MIN_REGIME_TRADES` fit-window trades is left ON. No evidence is not evidence
against, and defaulting off would silently shrink the book every time a rare
regime met a low-frequency sleeve.

THE CONTROLS.

  * the always-on book on the same members and the same window, printed beside
    every result -- this is the comparison, not an extra. The last time a
    per-strategy on/off overlay was tried it lost to leaving everything switched
    on ($1,183 against $1,321, monthly drawdown 5.52% against 1.74%), and no
    number here means anything without that line under it.
  * `--null` reassigns the same NUMBER of sleeve-regime cells to OFF at random,
    ten seeds, and reports the band. A switch that lands inside its own random
    band is a duty cycle, not a regime rule
    ([[coin-flip-control-beats-real-signals]]).

    py -m sandbox.research.exness_regime_switch fit
    py -m sandbox.research.exness_regime_switch fit --buckets 3
    py -m sandbox.research.exness_regime_switch fit --source pool
    py -m sandbox.research.exness_regime_switch fit --null
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import cfd_families as ef

OUT_PATH = os.path.join(ecs.RESULTS, "exness_regime_switch.json")

DAY = 86_400
FIT_START = "2018-01-01"
FIT_END = "2025-01-01"            # exclusive; == ef.IS_END
TEST_END = ecs.CANON_DATA_END     # exclusive, 2026-08-21

#: Below this many fit-window trades in a regime, the sleeve is left ON. See
#: DEFAULT ON in the module docstring.
MIN_REGIME_TRADES = 20

#: Below this many fit-window trades in TOTAL, the sleeve is never switched at
#: all -- it is held always-on and marked. nq:ofi lands here with zero.
MIN_FIT_TRADES = 40

#: Runs of consecutive same-label days in the FIT window. Printed beside every
#: assignment; the previous study died at 1-10 of these.
MIN_EPISODES_WARN = 12

NAMES = {2: ("calm", "fast"),
         3: ("calm", "mid", "fast"),
         4: ("calm", "quiet", "active", "fast")}


def _stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def regime_names(buckets):
    return NAMES.get(buckets, tuple(f"r{i}" for i in range(buckets)))


# --------------------------------------------------------------------------- #
# the regime signal
# --------------------------------------------------------------------------- #

def nq_vol_series():
    """`{day: annualised EWMA vol}` from NQ daily closes.

    NO LOOKAHEAD, for the same reason `daily_multipliers` has none: the value
    offered for a day is computed from days strictly before it, and the day's
    own return is folded in only after that value has been recorded. Reading it
    the other way would let the switch turn a sleeve off on the morning of a
    move it has not seen, which is the most flattering bug available here.
    """
    ef.resolve("nq", allow_stale=True)
    bars, _ctx = ecs._context("nq")
    by_day = {}
    for bar in bars:
        day = bar[ef.TS] // DAY
        if day not in by_day or bar[ef.TS] > by_day[day][0]:
            by_day[day] = (bar[ef.TS], bar[ef.C])

    lam = 0.5 ** (1.0 / ecs.VOL_HALFLIFE)
    out, variance, seen, previous = {}, 0.0, 0, None
    for day in sorted(by_day):
        if seen >= ecs.VOL_MIN_DAYS and variance > 0.0:
            out[day] = math.sqrt(variance * 252.0)
        close = by_day[day][1]
        if previous is not None and previous > 0 and close > 0:
            change = close / previous - 1.0
            variance = (change * change if seen == 0
                        else lam * variance + (1.0 - lam) * change * change)
            seen += 1
        previous = close
    return out


def cut_points(vol, buckets):
    """Quantile boundaries taken from the FIT WINDOW ONLY, then frozen.

    Quantiling the whole sample instead would let the test window's own
    distribution decide what counted as a calm day in 2019.
    """
    lo, hi = _stamp(FIT_START) // DAY, _stamp(FIT_END) // DAY
    sample = sorted(value for day, value in vol.items() if lo <= day < hi)
    if len(sample) < buckets * 30:
        raise SystemExit(f"fit window has only {len(sample)} labelled days")
    return [sample[len(sample) * i // buckets] for i in range(1, buckets)]


def labeller(vol, cuts):
    """`f(day) -> regime index`, or None where NQ printed no usable history."""
    def label(day):
        value = vol.get(day)
        if value is None:
            return None
        for index, cut in enumerate(cuts):
            if value < cut:
                return index
        return len(cuts)
    return label


def episodes(label, lo_day, hi_day, buckets):
    """Maximal runs of consecutive labelled days per regime.

    This is the sample size of any assignment, not the trade count. A regime
    with four episodes has been observed four times regardless of how many
    trades fell inside them.
    """
    runs = {index: 0 for index in range(buckets)}
    days = {index: 0 for index in range(buckets)}
    previous = None
    for day in range(lo_day, hi_day):
        current = label(day)
        if current is None:
            # Do NOT reset `previous` here. Weekends and holidays are unlabelled,
            # so resetting would end an episode every Friday and start a new one
            # every Monday -- which reported a sticky hysteresis state as a
            # 4.8-day "day filter" and made every axis look equally twitchy.
            continue
        days[current] += 1
        if current != previous:
            runs[current] += 1
        previous = current
    return runs, days


# --------------------------------------------------------------------------- #
# trades
# --------------------------------------------------------------------------- #

def survivor_rows():
    """Every sealed cell in `results/exness`, ungated, plus the two NQ sleeves.

    `candidates()` is not used here on purpose. Its three size gates are all
    scored on 2025-2026, so filtering with them picks the menu using the window
    the switch is about to be tested on ([[exness-survivor-pool-is-oos-conditioned]]).
    Only cells whose symbol cannot be resolved are dropped, and that is a data
    fact rather than a performance judgement.
    """
    cells = []
    for name in sorted(os.listdir(ecs.SURVIVOR_DIR)):
        if not name.endswith(f"_{ecs.BAR}m.json"):
            continue
        with open(os.path.join(ecs.SURVIVOR_DIR, name), encoding="utf-8") as handle:
            cells.append(json.load(handle))

    # Resolve every symbol BEFORE any context is built, for the reason spelled
    # out in `ecs.candidates`: a benchmark-only spec cached first leaves the
    # real cells dying on KeyError('multiplier') later.
    unresolved = {}
    for cell in cells:
        symbol = cell["symbol"]
        if symbol in ef.INSTRUMENTS or symbol in unresolved:
            continue
        try:
            ef.resolve(symbol, allow_stale=True)
        except SystemExit as exc:
            unresolved[symbol] = str(exc)

    rows, seen = [], set()
    for cell in cells:
        key = f"{cell['symbol']}:{cell['family']}"
        if cell["symbol"] in unresolved or key in seen:
            continue
        seen.add(key)
        rows.append({"symbol": cell["symbol"], "family": cell["family"],
                     "params": ecs._retuple(cell["params"]),
                     "asset_class": cell.get("asset_class", "unknown")})
    for key in ("nq:ofi", "nq:drift_vwap"):
        if key not in seen:
            symbol, family = key.split(":", 1)
            rows.append({"symbol": symbol, "family": family})
    if unresolved:
        print(f"  {len(unresolved)} symbol(s) unresolved, dropped: "
              f"{sorted(unresolved)}")
    return rows


def load_members(source):
    """`(members, logs, bars_by, ctx_by)` over the FULL 2018-2026 span.

    One backtest per sleeve covering both windows, split later by entry stamp.
    Running fit and test as two backtests would restart every indicator warm-up
    at 2025-01-01 and give the test window a colder book than the fit window had
    ([[cold-start-oos-fakes-regime-edges]]).
    """
    if source == "canon":
        by_key = ecs.candidate_rows_exact(
            [k for k in ecs.BOOK if k not in ecs.EXTERNAL])
        members = []
        for key in ecs.BOOK:
            if key in ecs.EXTERNAL:
                symbol, family = key.split(":", 1)
                members.append({"symbol": symbol, "family": family})
            else:
                if key not in by_key:
                    raise SystemExit(f"canon member absent from pool: {key}")
                members.append(by_key[key])
    elif source == "pool":
        # `candidates()` gates on MIN_OOS_RETURN / MIN_OOS_TRADES / MIN_IS_T,
        # all three scored on 2025-2026 -- the test window. The menu is
        # therefore holdout-filtered even though the regime assignment is not.
        members = ecs.candidates()
        keys = {f"{m['symbol']}:{m['family']}" for m in members}
        for key in ("nq:ofi", "nq:drift_vwap"):
            if key not in keys:
                symbol, family = key.split(":", 1)
                members.append({"symbol": symbol, "family": family})
    else:
        members = survivor_rows()

    lo, hi = _stamp(FIT_START), _stamp(TEST_END)
    logs, bars_by, ctx_by = {}, {}, {}
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        print(f"  loading {key}", flush=True)
        if key in ecs.EXTERNAL:
            logs[key] = ecs.external_trades(key, window=(FIT_START, TEST_END))
            continue
        ef.resolve(member["symbol"], allow_stale=True)
        _r, log, bars, ctx = ecs.sleeve_trades(member, lo=lo, hi=hi)
        logs[key] = log
        bars_by[member["symbol"]] = bars
        ctx_by[member["symbol"]] = ctx
    return members, logs, bars_by, ctx_by


# --------------------------------------------------------------------------- #
# the assignment
# --------------------------------------------------------------------------- #

def assign(logs, label, buckets):
    """Which regimes each sleeve is ON in, fitted on 2018-2024 alone.

    The metric is the SUM of size-free per-trade returns (`points / entry`, the
    same quantity `daily_stream` uses), not dollars. Dollars over a 2018-2024
    replay are dominated by compounding -- a sleeve's late trades are many times
    the size of its early ones, so a dollar metric would assign regimes by WHEN
    they happened to occur rather than by how the sleeve performed in them.
    """
    fit_lo, fit_hi = _stamp(FIT_START), _stamp(FIT_END)
    table = {}
    for key, log in logs.items():
        cells = {index: [] for index in range(buckets)}
        total = 0
        for trade in log:
            if not fit_lo <= trade["entry_ts"] < fit_hi:
                continue
            index = label(trade["entry_ts"] // DAY)
            if index is None:
                continue
            cells[index].append(trade["points"] / trade["entry"])
            total += 1

        row = {"fit_trades": total, "regimes": {}, "always_on": False}
        if total < MIN_FIT_TRADES:
            # No fit-window history worth the name. nq:ofi is the reason this
            # branch exists: its level_two features begin 2025-02-12, so every
            # trade it has ever made is inside the holdout and there is nothing
            # to assign it on that would not be circular.
            row["always_on"] = True
            row["reason"] = f"{total} fit-window trades"
            for index in range(buckets):
                row["regimes"][index] = {"on": True, "trades": len(cells[index]),
                                         "sum_ret": 0.0, "per_trade": 0.0,
                                         "why": "always-on"}
            table[key] = row
            continue

        for index in range(buckets):
            values = cells[index]
            total_ret = sum(values)
            if len(values) < MIN_REGIME_TRADES:
                on, why = True, "too few"
            else:
                on = total_ret > 0
                why = "earned" if on else "lost"
            row["regimes"][index] = {
                "on": on, "trades": len(values),
                "sum_ret": round(total_ret, 4),
                "per_trade": round(total_ret / len(values), 5) if values else 0.0,
                "why": why}
        table[key] = row
    return table


def gate_from(table, label):
    """`f(sleeve, entry_ts) -> bool` for `replay`'s `regime_gate`."""
    def gate(sleeve, entry_ts):
        row = table.get(sleeve)
        if row is None:
            return True
        index = label(entry_ts // DAY)
        if index is None:
            return True                     # unlabelled day: never refuse
        return row["regimes"][index]["on"]
    return gate


def randomised(table, buckets, seed):
    """The same COUNT of off-cells, placed at random. `--null`'s comparison.

    Sleeves held always-on are left alone: randomising them would compare
    against a book that switches things the real rule structurally cannot.
    """
    switchable = [(key, index) for key, row in table.items()
                  if not row["always_on"] for index in range(buckets)]
    off_count = sum(1 for key, index in switchable
                    if not table[key]["regimes"][index]["on"])
    rng = random.Random(seed)
    off = set(rng.sample(switchable, off_count)) if off_count else set()
    out = {}
    for key, row in table.items():
        out[key] = {**row, "regimes": {
            index: {**cell, "on": (True if row["always_on"]
                                   else (key, index) not in off)}
            for index, cell in row["regimes"].items()}}
    return out


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

def score(members, logs, bars_by, ctx_by, gate, risk_scale, gross_cap, initial):
    """One test-window replay. `gate=None` is the always-on control."""
    return ecs.replay(members, logs, bars_by, ctx_by,
                      scale=ecs.SLEEVE_SCALE,
                      sizing_cap=ecs.sizing_caps(members),
                      risk_scale=risk_scale, gross_cap=gross_cap,
                      lo=_stamp(FIT_END), hi=_stamp(TEST_END),
                      fair_cap=ecs.FAIR_CAP, initial=initial,
                      regime_gate=gate)


def monthly_stats(book, initial):
    """`ecs.monthly` already annualises the Sharpe; do not recompute it here."""
    series, sharpe, positive, total = ecs.monthly(book["settled"], initial)
    values = [row["return_pct"] for row in series.values()]
    return {"months": total, "positive": positive,
            "monthly_sharpe": sharpe,
            "worst_month_pct": round(min(values), 2) if values else 0.0,
            "median_month_pct": round(statistics.median(values), 2) if values else 0.0}


def report(name, book, initial):
    stats = monthly_stats(book, initial)
    print(f"  {name:<12} {book['return_pct']:>9.2f}%  "
          f"final ${book['final']:>10,.2f}  "
          f"mtm dd {book['mtm_dd_pct']:>6.2f}%  "
          f"closed dd {book['max_dd_pct']:>6.2f}%  "
          f"trades {book['trades']:>5}  "
          f"mSharpe {stats['monthly_sharpe']:>6.3f}  "
          f"{stats['positive']}/{stats['months']} pos")
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Per-sleeve regime dead switch, fitted 2018-2024, "
                    "scored 2025-2026.")
    parser.add_argument("command", choices=["fit", "signal", "dump"])
    parser.add_argument("--buckets", type=int, default=2,
                        help="number of volatility regimes; 2 or 3")
    parser.add_argument("--source", choices=["canon", "pool", "ungated"],
                        default="ungated",
                        help="ungated (default) is EVERY sealed cell in "
                             "results/exness plus the two NQ level-two "
                             "sleeves; pool applies candidates()' three "
                             "holdout-scored size gates first; canon is the "
                             "14-sleeve BOOK, which is a fixed member list and "
                             "so can only ever switch what is already in it")
    parser.add_argument("--risk-scale", type=float,
                        default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--gross-cap", type=float, default=ecs.CANON_GROSS_CAP)
    parser.add_argument("--initial", type=float, default=ecs.CANON_INITIAL)
    parser.add_argument("--null", action="store_true",
                        help="also run ten random-assignment seeds")
    parser.add_argument("--null-seeds", type=int, default=10)
    parser.add_argument("--out", default=OUT_PATH)
    args = parser.parse_args()

    ecs.UNCAPPED = True
    ecs.FORCE_MINIMUM_LOT = True
    gross_cap = None if args.gross_cap == 0 else args.gross_cap
    names = regime_names(args.buckets)

    vol = nq_vol_series()
    cuts = cut_points(vol, args.buckets)
    label = labeller(vol, cuts)
    fit_lo, fit_hi = _stamp(FIT_START) // DAY, _stamp(FIT_END) // DAY
    test_hi = _stamp(TEST_END) // DAY
    fit_runs, fit_days = episodes(label, fit_lo, fit_hi, args.buckets)
    test_runs, test_days = episodes(label, fit_hi, test_hi, args.buckets)

    print(f"\nREGIME SIGNAL  NQ EWMA daily vol, halflife {ecs.VOL_HALFLIFE:g}, "
          f"annualised; cuts frozen on {FIT_START}..{FIT_END}")
    print(f"  {'regime':<8} {'band':>18} {'fit days':>9} {'fit eps':>8} "
          f"{'test days':>10} {'test eps':>9}")
    for index in range(args.buckets):
        low = "0" if index == 0 else f"{cuts[index - 1] * 100:.1f}%"
        high = "inf" if index == len(cuts) else f"{cuts[index] * 100:.1f}%"
        flag = "  <-- thin" if fit_runs[index] < MIN_EPISODES_WARN else ""
        print(f"  {names[index]:<8} {low + '..' + high:>18} "
              f"{fit_days[index]:>9} {fit_runs[index]:>8} "
              f"{test_days[index]:>10} {test_runs[index]:>9}{flag}")

    if args.command == "signal":
        return

    if args.command == "dump":
        # A projection of every sleeve's trades to `(entry_ts, size-free
        # return)`. Loading the 166 sleeves is the only expensive step in this
        # module; once dumped, an axis search can evaluate any number of regime
        # definitions against the same trades for free.
        _members, logs, _bars, _ctx = load_members(args.source)
        payload = {key: [[t["entry_ts"], t["points"] / t["entry"]]
                         for t in log if t["entry"]]
                   for key, log in logs.items()}
        path = os.path.join(ecs.CACHE_DIR, f"regime_trades_{args.source}.json")
        os.makedirs(ecs.CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        print(f"\ndumped {sum(len(v) for v in payload.values())} trades "
              f"across {len(payload)} sleeves to {path}")
        return

    print(f"\nloading {args.source} sleeves over {FIT_START}..{TEST_END}")
    members, logs, bars_by, ctx_by = load_members(args.source)
    table = assign(logs, label, args.buckets)

    print(f"\nASSIGNMENT  fitted on {FIT_START}..{FIT_END} only")
    header = f"  {'sleeve':<28} {'fit n':>6}"
    for index in range(args.buckets):
        header += f" {names[index]:>22}"
    print(header)
    for key in sorted(table, key=lambda k: -table[k]["fit_trades"]):
        row = table[key]
        line = f"  {key:<28} {row['fit_trades']:>6}"
        for index in range(args.buckets):
            cell = row["regimes"][index]
            mark = "ON " if cell["on"] else "OFF"
            line += (f"  {mark} n={cell['trades']:<4} "
                     f"{cell['sum_ret'] * 100:>+6.1f}%")
        if row["always_on"]:
            line += f"   [always-on: {row['reason']}]"
        print(line)

    off_cells = sum(1 for row in table.values() for cell in row["regimes"].values()
                    if not cell["on"])
    switchable = sum(args.buckets for row in table.values()
                     if not row["always_on"])
    print(f"\n  {off_cells} of {switchable} switchable sleeve-regime cells are OFF")

    print(f"\nTEST WINDOW  {FIT_END}..{ecs.CANON_DATA_THROUGH}  "
          f"initial ${args.initial:,.0f}  risk {args.risk_scale}  "
          f"gross cap {gross_cap}")
    control = score(members, logs, bars_by, ctx_by, None,
                    args.risk_scale, gross_cap, args.initial)
    control_stats = report("always-on", control, args.initial)
    switched = score(members, logs, bars_by, ctx_by, gate_from(table, label),
                     args.risk_scale, gross_cap, args.initial)
    switched_stats = report("switched", switched, args.initial)

    nulls = []
    if args.null:
        for seed in range(args.null_seeds):
            book = score(members, logs, bars_by, ctx_by,
                         gate_from(randomised(table, args.buckets, seed), label),
                         args.risk_scale, gross_cap, args.initial)
            nulls.append(book)
            report(f"null s{seed}", book, args.initial)
        returns = sorted(b["return_pct"] for b in nulls)
        draws = sorted(b["mtm_dd_pct"] for b in nulls)
        print(f"\n  null return band  {returns[0]:>9.2f}% .. {returns[-1]:>9.2f}%"
              f"   median {statistics.median(returns):>9.2f}%")
        print(f"  null mtm dd band  {draws[0]:>9.2f}% .. {draws[-1]:>9.2f}%"
              f"   median {statistics.median(draws):>9.2f}%")
        beat = sum(1 for r in returns if switched["return_pct"] > r)
        print(f"  switched beats {beat}/{len(returns)} random assignments "
              f"on return")

    print(f"\nPER-SLEEVE  test window, switched book beside the always-on control")
    print(f"  {'sleeve':<28} {'sw pnl':>10} {'ctl pnl':>10} {'delta':>10} "
          f"{'sw n':>6} {'ctl n':>6} {'gated':>6} {'sw mtm dd':>10} "
          f"{'ctl mtm dd':>11}")
    gated = switched.get("refused_by_regime", {})
    keys = sorted(set(switched["by_sleeve"]) | set(control["by_sleeve"]))
    for key in keys:
        s = switched["by_sleeve"].get(key, {})
        c = control["by_sleeve"].get(key, {})
        s_pnl, c_pnl = s.get("pnl", 0.0), c.get("pnl", 0.0)
        print(f"  {key:<28} {s_pnl:>10.2f} {c_pnl:>10.2f} "
              f"{s_pnl - c_pnl:>+10.2f} "
              f"{s.get('trades', 0):>6} {c.get('trades', 0):>6} "
              f"{gated.get(key, 0):>6} "
              f"{s.get('mtm_dd_usd', 0.0):>10.2f} "
              f"{c.get('mtm_dd_usd', 0.0):>11.2f}")

    payload = {
        "fit_window": f"{FIT_START}..{FIT_END}",
        "test_window": f"{FIT_END}..{ecs.CANON_DATA_THROUGH}",
        "source": args.source, "buckets": args.buckets,
        "regime_names": list(names[:args.buckets]),
        "cuts": cuts, "halflife": ecs.VOL_HALFLIFE,
        "fit_episodes": fit_runs, "fit_days": fit_days,
        "test_episodes": test_runs, "test_days": test_days,
        "assignment": table,
        "off_cells": off_cells, "switchable_cells": switchable,
        "risk_scale": args.risk_scale, "gross_cap": gross_cap,
        "initial": args.initial,
        "control": {k: v for k, v in control.items()
                    if k not in ("settled", "curve", "marked")},
        "control_monthly": control_stats,
        "switched": {k: v for k, v in switched.items()
                     if k not in ("settled", "curve", "marked")},
        "switched_monthly": switched_stats,
        "nulls": [{k: v for k, v in b.items()
                   if k not in ("settled", "curve", "marked")} for b in nulls],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
