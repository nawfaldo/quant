"""Search sleeve COMBINATIONS, risk scale and exposure cap against a target.

WHY THIS EXISTS, AND WHAT `exness_combined_strategies build` DOES NOT DO.

`build --consistency` is a greedy FORWARD selection scored on monthly
consistency. Three things follow from that, and all three bit:

  * It never removes or exchanges a seated sleeve. fr40:swing_ma bore 30-35% of
    the worst drawdown in three consecutive configurations and stayed in every
    one of them, because nothing in the search can drop a member once seated.
  * Its objective is consistency, not the operator's target. Drawdown is
    reported, never optimised, so asking for "400% under 16%" and running that
    search is asking a question it is not answering.
  * Forced members are seated before the search begins and are never questioned,
    so the cost of keeping them is invisible.

This module does a constrained local search instead. It maximises RETURN subject
to hard constraints, over three things at once: which sleeves, `risk_scale`, and
the per-sleeve exposure budget.

THE CONSTRAINTS ARE HARD, NOT PENALTIES.

  mtm_dd_pct   <= `--max-dd`        marked to market, never the closed figure
  ratio        >= `--min-ratio`     every sleeve at least as good in the book as
                                    it was standalone, which is the thing that
                                    kept breaking
  concentration<= `--max-dd-share`  no single sleeve owning the worst fall
  members      >= `--min-sleeves`   six was called unforgivably low

A combination violating any of them is not scored at all. Nothing is traded off
against return, so a reported result satisfies every constraint by construction.

EXPOSURE BUDGET IS PER SLEEVE HERE, NOT DIVIDED.

`replay(fair_cap=True)` splits `gross_cap` by member count, so `--gross-cap 4`
on ten sleeves gives each 0.4x equity and refuses almost everything: measured,
4,415 of 4,700 entries refused, return -5.0%. This module therefore passes
`gross_cap = budget * n` so `--budget 2` means each sleeve gets 2x equity of its
own, independent of how many sleeves the search happens to be holding.

COST. One `replay` dominates, so the search is priced in replays. Contexts and
trade logs are built ONCE for the whole pool and reused, which is what makes
hundreds of evaluations affordable where `build` costs one point per 8 minutes.

    py -m sandbox.research.book_search --max-dd 16 --min-sleeves 8 \
       --target-return 400

READ THIS BEFORE BELIEVING THE OUTPUT.

The search is fitted to the holdout on every axis: membership, risk and cap are
all chosen by reading results on 2025-01-01..2026-08-16. It is a feasibility
study -- "does any combination reach this target" -- and NOT evidence that the
combination it finds will hold. The more of the pool it is allowed to rearrange,
the more of the holdout it is consuming ([[selection-gate-manufactures-drawdown-and-consistency]]).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import statistics
import time

from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cs


def load_pool(min_trades=None, forced=(), quiet=False, only=()):
    """Trade logs, daily streams and standalone results for every candidate.

    Built once. `sleeve_trades` reloads a symbol's whole context on every call,
    so doing this per evaluation is what makes a naive search unaffordable.
    """
    rows = cs.candidates(min_trades=min_trades)
    if only:
        wanted = set(only)
        rows = [row for row in rows
                if f"{row['symbol']}:{row['family']}" in wanted]
    by_key = {f"{r['symbol']}:{r['family']}": r for r in rows}
    for name in forced:
        # AN IMPORTED SLEEVE OVERRIDES A NATIVE CELL OF THE SAME NAME.
        #
        # The guard here used to be `name not in by_key`, which silently did
        # nothing when the pool already carried a cell by that name -- and
        # nq:volatility_breakout is exactly that case: it is a real
        # `exness_families` cell AND the canon runs it through
        # `external_trades`, on fixed `units_per_dollar` sizing rather than the
        # ATR-scaled lots the native path derives. So the canon quietly ran a
        # DIFFERENT sleeve from the one it names: $762 instead of $831, and a
        # book marked on 30m bars instead of the import's minute stamps, which
        # is +627.5%/13.12% where `build --members canon` reports
        # +640.0%/12.87%. The two only diverged once the candidate pool grew
        # wide enough to contain the native cell, so nothing caught it before.
        if name in cs.EXTERNAL:
            symbol, family = name.split(":", 1)
            by_key[name] = {"symbol": symbol, "family": family,
                            "asset_class": "external", "external": True,
                            "oos_return": float("nan"), "oos_trades": 0}
    logs, streams, bars_by, ctx_by, alone = {}, {}, {}, {}, {}
    for key, row in by_key.items():
        if row.get("external"):
            log = cs.external_trades(key, window=("2025-01-01", "2026-08-16"))
            logs[key] = log
            streams[key] = cs.daily_stream(log)
            alone[key] = None                     # no standalone run exists
            continue
        ef.resolve(row["symbol"], allow_stale=True)
        result, log, bars, ctx = cs.sleeve_trades(row)
        logs[key] = log
        streams[key] = cs.daily_stream(log)
        bars_by[row["symbol"]] = bars
        ctx_by[row["symbol"]] = ctx
        alone[key] = result["return_pct"] / 100.0 * ef.INITIAL_BALANCE
        if not quiet:
            print(f"  loaded {key:26} {len(log):>5} trades", flush=True)
    return by_key, logs, streams, bars_by, ctx_by, alone


class Evaluator:
    """One `replay` per (members, risk, budget), with the results memoised."""

    def __init__(self, pool, min_ratio, max_dd, max_share, max_rho, max_lift,
                 min_pnl_dd=1.0, min_share=0.05, ladder=True,
                 strict_actual_minimum=False, initial=ef.INITIAL_BALANCE,
                 min_symbols=1, min_positive_months=0,
                 min_monthly_sharpe=0.0,
                 min_worst_month=float("-inf")):
        (self.by_key, self.logs, self.streams,
         self.bars_by, self.ctx_by, self.alone) = pool
        self.min_ratio = min_ratio
        #: A sleeve must EARN MORE THAN THE DRAWDOWN IT CONTRIBUTES. Distinct
        #: from `min_ratio`, which only asks whether a sleeve did as well in the
        #: book as it did alone: xaueur:orb passed that at exactly 1.00 while
        #: making $32 against $118 of marked drawdown. Paying 3.7x its earnings
        #: in risk is a bad trade for the book however faithfully it reproduces
        #: its standalone self.
        self.min_pnl_dd = min_pnl_dd
        #: Minimum share of book P&L. A sleeve too small to matter still costs a
        #: full risk budget and a slot: fr40:squeeze contributed 3.9% while
        #: carrying $76 of drawdown. Separate from `min_pnl_dd` -- that catches a
        #: sleeve paying too much risk for its earnings (xaueur:orb, $32 against
        #: $118), this catches one that simply is not pulling its weight.
        self.min_share = min_share
        self.max_dd = max_dd
        self.max_share = max_share
        self.initial = initial
        self.min_symbols = min_symbols
        self.min_positive_months = min_positive_months
        self.min_monthly_sharpe = min_monthly_sharpe
        self.min_worst_month = min_worst_month
        self.max_rho, self.max_lift = max_rho, max_lift
        #: OFF means: one replay at shown equity 1.0, and a refused trade is
        #: reported rather than treated as disqualifying.
        #:
        #: The ladder is right when the question is "what could this book do if
        #: the account were big enough to fill it", and wrong when the question
        #: is "is this better than the book I am running". The canon has five
        #: entries under the broker minimum; raising shown equity until they
        #: fill moves its marked drawdown 12.87% -> 15.33% and its return
        #: +640.0% -> +635.0%, so a laddered baseline is not the book
        #: `exness_combined_strategies build --members canon` reports and any
        #: margin measured against it is measured against a different account.
        self.ladder = ladder
        #: Unlike the historical screen mode, a non-laddered strict run still
        #: rejects books that skip even one order at the broker lot floor.
        #: This prices the actual $1,000 account, without pretending a sleeve
        #: sees more equity merely to clear volume_min.
        self.strict_actual_minimum = strict_actual_minimum
        self.cache = {}
        self.calls = 0

    def dependence_violations(self, keys):
        """Pairs failing `down_rho` or `loss_lift`, as `{(a, b): reason}`."""
        out = {}
        for a, b in itertools.combinations(sorted(keys), 2):
            rho = cs.down_rho(self.streams[a], self.streams[b])
            lift = cs.loss_lift(self.streams[a], self.streams[b])
            if rho > self.max_rho:
                out[(a, b)] = f"down_rho {rho:+.2f}"
            elif lift > self.max_lift:
                out[(a, b)] = f"loss_lift {lift:.2f}"
        return out

    def dependence_ok(self, keys, grandfathered=()):
        """Pairwise downside gates, the diversity requirement as a hard filter.

        `grandfathered` exempts pairs an anchor book already runs, so a
        candidate is never refused for a dependence that is in the book with or
        without it. Empty by default, which is the original all-pairs gate.
        """
        return not (set(self.dependence_violations(keys)) - set(grandfathered))

    #: Shown-equity ladder, same idea as `combined_book`'s SHOWN_EQUITY: tell a
    #: sleeve it has a larger balance purely so its order clears `volume_min`.
    #: Climbed one rung at a time and only for sleeves that ACTUALLY refused, so
    #: a sleeve never gets more than it needs -- past the point where it fills,
    #: more multiplier is leverage rather than access, and the margin ceiling in
    #: `size` still reads the real balance either way.
    SHOWN_LADDER = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)

    def member_bars(self, members):
        """`bars_by_symbol` cut down to the symbols this book actually trades.

        `replay` builds its mark-to-market grid from EVERY symbol in the dict it
        is handed, not from the members', so without this a book's reported
        drawdown is a function of which OTHER candidates happen to be loaded
        beside it -- two searches over different pools would not be comparable,
        and neither would be comparable with `build`.

        MEASURED, IT CHANGES NOTHING HERE, and that is worth writing down rather
        than leaving as an assumption: the canon replayed against 8 member
        symbols and against all 27 pool symbols returns the identical
        +627.46% / 13.12% / 4,796 trades. Every symbol in the study prints its
        30m bars on the same timestamp grid, so the extra symbols contribute
        almost no new marking instants. This is kept for the invariant, not for
        a number it moves. (The real cause of the 12.87 vs 13.12 gap was an
        imported sleeve being shadowed by a native cell -- see `load_pool`.)
        """
        want = {m["symbol"] for m in members}
        return {s: b for s, b in self.bars_by.items() if s in want}

    def calibrate(self, keys, risk, budget):
        """Smallest per-sleeve shown equity that leaves nothing refused."""
        members = [self.by_key[k] for k in keys]
        gross = budget * len(members) if budget else None
        shown = {k: 1.0 for k in keys}
        bars = self.member_bars(members)
        book = None
        if not self.ladder:
            self.calls += 1
            return cs.replay(members, self.logs, bars, self.ctx_by,
                             scale=cs.SLEEVE_SCALE,
                             sizing_cap=cs.sizing_caps(members),
                             risk_scale=risk, gross_cap=gross,
                             fair_cap=bool(budget), initial=self.initial), shown
        for _ in range(len(self.SHOWN_LADDER)):
            book = cs.replay(members, self.logs, bars, self.ctx_by,
                             scale=cs.SLEEVE_SCALE,
                             sizing_cap=cs.sizing_caps(members),
                             risk_scale=risk, gross_cap=gross,
                             fair_cap=bool(budget), shown=dict(shown),
                             initial=self.initial)
            self.calls += 1
            below = book.get("below_broker_minimum") or {}
            if not below:
                break
            raised = False
            for k in below:
                nxt = next((v for v in self.SHOWN_LADDER if v > shown.get(k, 1.0)),
                           None)
                if nxt is not None:
                    shown[k], raised = nxt, True
            if not raised:
                break                       # already at MAX_SHOWN and still short
        return book, shown

    def evaluate(self, keys, risk, budget):
        key = (tuple(sorted(keys)), risk, budget)
        if key in self.cache:
            return self.cache[key]
        book, shown = self.calibrate(keys, risk, budget)
        ratios = {}
        for k in keys:
            base = self.alone.get(k)
            if base:
                ratios[k] = book["by_sleeve"][k]["pnl"] / base
        # NO REFUSED TRADE IS ACCEPTABLE. A refusal means the book did not take
        # a trade the strategy generated, so the backtest and a live account are
        # running different strategies -- and which trades get dropped is decided
        # by the account balance and the lot floor, not by the rule
        # ([[small-balance-hides-drawdown-by-dropping-trades]]). Counted three
        # ways because there are three ways to lose a trade: the exposure cap,
        # the broker minimum, and being crowded out of a shared budget.
        refused = (book.get("refused_by_gross_cap", 0)
                   + sum((book.get("below_broker_minimum") or {}).values())
                   + sum((book.get("refused_by_sleeve") or {}).values()))
        # P&L against own drawdown contribution, and share of book P&L.
        pays, shares = {}, {}
        earned = sum(v["pnl"] for v in book["by_sleeve"].values()) or 1.0
        for k in keys:
            row = book["by_sleeve"][k]
            dd = row["mtm_dd_usd"]
            pays[k] = (row["pnl"] / dd) if dd > 0 else float("inf")
            shares[k] = row["pnl"] / earned
        month_rows, month_sharpe, positive_months, month_count = cs.monthly(
            book["settled"], self.initial)
        worst_month = min((row["return_pct"] for row in month_rows.values()),
                          default=0.0)
        symbol_count = len({key.split(":", 1)[0] for key in keys})
        feasible = ((refused == 0 or
                     (not self.ladder and not self.strict_actual_minimum))
                    and book["mtm_dd_pct"] <= self.max_dd
                    and book.get("dd_concentration", 0.0) <= self.max_share
                    and all(v >= self.min_ratio for v in ratios.values())
                    and all(v >= self.min_pnl_dd for v in pays.values())
                    and all(v >= self.min_share for v in shares.values())
                    and symbol_count >= self.min_symbols
                    and positive_months >= self.min_positive_months
                    and month_sharpe >= self.min_monthly_sharpe
                    and worst_month >= self.min_worst_month)
        # A hill climb can replay thousands of memberships.  Keeping every
        # membership's full settled-trade/equity history in ``self.cache``
        # exhausts RAM on the small-account searches even though reporting only
        # needs the scalar summary and per-sleeve attribution.  Monthly quality
        # has already been calculated above, so retain only the fields consumed
        # by ``report`` and discard the large event arrays.
        lean_book = {
            "by_sleeve": book["by_sleeve"],
            "return_pct": book["return_pct"],
            "mtm_dd_pct": book["mtm_dd_pct"],
            "max_dd_pct": book["max_dd_pct"],
            "dd_concentration": book.get("dd_concentration", 0.0),
        }
        out = {"return_pct": book["return_pct"],
               "mtm_dd_pct": book["mtm_dd_pct"],
               "closed_dd_pct": book["max_dd_pct"],
               "concentration": book.get("dd_concentration", 0.0),
               "worst_ratio": min(ratios.values()) if ratios else float("nan"),
               "worst_pays": min(pays.values()) if pays else float("nan"),
               "worst_share": min(shares.values()) if shares else float("nan"),
               "pays": pays, "shares": shares, "ratios": ratios,
               "symbols": symbol_count, "positive_months": positive_months,
               "month_count": month_count, "monthly_sharpe": month_sharpe,
               "worst_month_pct": worst_month,
               "refused": refused, "shown": shown,
               # Total constraint violation. Every blocking condition
               # contributes, because a climb that only measures drawdown walks
               # the same path whatever else is broken -- which is exactly what
               # happened: two runs with different constraints produced byte
               # identical trajectories because the ratios were blocking and
               # nothing in the ranking could see them.
               "violation": (refused * (1000.0 if (self.ladder or
                                                    self.strict_actual_minimum)
                                          else 0.0)
                             + max(0.0, book["mtm_dd_pct"] - self.max_dd) * 10.0
                             + max(0.0, book.get("dd_concentration", 0.0)
                                   - self.max_share) * 100.0
                             + sum(max(0.0, self.min_ratio - v)
                                   for v in ratios.values()) * 100.0
                             + sum(max(0.0, self.min_pnl_dd - v)
                                   for v in pays.values()) * 100.0
                             + sum(max(0.0, self.min_share - v)
                                   for v in shares.values()) * 1000.0
                             + max(0, self.min_symbols - symbol_count) * 1000.0
                             + max(0, self.min_positive_months
                                   - positive_months) * 100.0
                             + max(0.0, self.min_monthly_sharpe
                                   - month_sharpe) * 100.0
                             + max(0.0, self.min_worst_month
                                   - worst_month) * 100.0),
               "feasible": feasible, "book": lean_book,
               "keys": tuple(sorted(keys)),
               "risk": risk, "budget": budget}
        self.cache[key] = out
        return out


#: Risk grid the drawdown tuner walks. Fine near the canon's 0.7 because that
#: is where the answer lands; coarse at the ends, which only matter for books
#: far smaller or larger than the one being compared against.
DD_RISK_GRID = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70,
                0.80, 0.90, 1.00, 1.15, 1.30)


def at_drawdown(evaluator, keys, ceiling, budget=None, grid=DD_RISK_GRID):
    """Highest return `keys` reaches without exceeding `ceiling` marked drawdown.

    WHY EVERY COMPARISON HAS TO GO THROUGH THIS. Each sleeve carries a whole
    risk budget ([[combined-book-stacks-risk-budgets]]), so at a fixed
    `risk_scale` a twelve-sleeve book earns more than an eleven-sleeve one AND
    falls further. Ranking those two on return alone ranks them by how much was
    bet, not by which is the better book. Re-tuning both to land at the same
    marked drawdown is what makes the return figures mean the same thing.

    The grid is walked in full rather than bisected: drawdown is NOT monotone in
    `risk_scale`, because the risk unit decides which orders clear the broker's
    lot floor and therefore which trades the book takes at all
    ([[small-balance-hides-drawdown-by-dropping-trades]]).
    """
    best = None
    for risk in grid:
        got = evaluator.evaluate(keys, risk, budget)
        if got["mtm_dd_pct"] > ceiling:
            continue
        if best is None or got["return_pct"] > best["return_pct"]:
            best = got
    return best


def screen(evaluator, anchor, budget=None, top_swaps=12, quiet=False,
           screen_risk=0.70):
    """Marginal value of every add, every drop and the promising swaps.

    Ranked at ONE common `risk_scale` because that costs a single replay per
    move; the shortlist is then re-priced through `at_drawdown`, which costs a
    whole grid. Nothing is reported from the cheap pass without re-pricing.

    The ranking statistic is log growth per point of marked drawdown. Return
    compounds in `risk_scale` while drawdown is roughly linear in it, so raw
    return puts the riskiest book first and even `return / dd` still leans that
    way; taking the log makes the numerator the part that scales linearly with
    risk, which is what makes two different memberships comparable before
    either has been tuned.

    Pairs that the ANCHOR ITSELF already runs outside the downside gates are
    grandfathered. The canon was assembled at nine sleeves and had members
    added by hand afterwards, so it violates its own gate in places -- without
    this every candidate is refused for a pair that is already in the book, and
    the screen reports that nothing may be added.
    """
    def score(row):
        dd = row["mtm_dd_pct"]
        return (math.log(max(1e-9, 1.0 + row["return_pct"] / 100.0)) / dd
                if dd > 0 else float("-inf"))

    grandfathered = evaluator.dependence_violations(anchor)
    base = evaluator.evaluate(anchor, screen_risk, budget)
    base_score = score(base)
    if not quiet:
        print(f"anchor at risk {screen_risk}: {base['return_pct']:+.1f}% "
              f"MTM {base['mtm_dd_pct']:.2f}%  score {base_score:.4f}",
              flush=True)

    pool = [k for k in evaluator.by_key if k not in anchor]
    adds = []
    for i, key in enumerate(pool, 1):
        trial = list(anchor) + [key]
        got = evaluator.evaluate(trial, screen_risk, budget)
        adds.append({"move": "add", "key": key,
                     "dep_ok": evaluator.dependence_ok(trial, grandfathered),
                     "score": score(got), **got})
        if not quiet and i % 20 == 0:
            print(f"  add screen {i}/{len(pool)}", flush=True)
    adds.sort(key=lambda r: -r["score"])

    drops = []
    for key in anchor:
        trial = [k for k in anchor if k != key]
        got = evaluator.evaluate(trial, screen_risk, budget)
        drops.append({"move": "drop", "key": key, "dep_ok": True,
                      "score": score(got), **got})
    drops.sort(key=lambda r: -r["score"])

    # Swaps are the expensive move and most are pointless, so only the adds
    # that beat the anchor are paired against the members whose removal helped
    # -- the two ends of the two screens above.
    good = [r["key"] for r in adds if r["dep_ok"] and r["score"] > base_score]
    good = good[:top_swaps]
    weak = [r["key"] for r in drops[:max(1, len(anchor) // 2)]]
    swaps = []
    for out_key in weak:
        for in_key in good:
            trial = [k for k in anchor if k != out_key] + [in_key]
            if not evaluator.dependence_ok(trial, grandfathered):
                continue
            got = evaluator.evaluate(trial, screen_risk, budget)
            swaps.append({"move": "swap", "out": out_key, "in": in_key,
                          "score": score(got), **got})
    swaps.sort(key=lambda r: -r["score"])
    return base, base_score, adds, drops, swaps, grandfathered


def search(pool, forced, min_sleeves, max_sleeves, risks, budgets, evaluator,
           target, seconds, seed=0):
    """Hill-climb on membership, re-tuning risk and budget at every step.

    Infeasible points are still climbed THROUGH: a book over the drawdown limit
    is ranked by how far over it is, so the search can walk downhill into the
    feasible region instead of stalling outside it. Once inside, the objective
    switches to return. Without that the search never starts -- every seed book
    exceeds 16%.
    """
    rng = random.Random(seed)
    all_keys = [k for k in evaluator.by_key if k not in forced]
    started = time.time()
    # Operator-forced sleeves are the immutable source core. Their existing
    # pairwise violations must not prevent every possible add; new candidate
    # pairs still have to clear the normal downside gates.
    grandfathered = set(evaluator.dependence_violations(forced))

    def tune(keys):
        """Best (feasible-first, then highest return) setting for this set."""
        best = None
        for risk in risks:
            for budget in budgets:
                got = evaluator.evaluate(keys, risk, budget)
                rank = ((1 if got["feasible"] else 0),
                        got["return_pct"] if got["feasible"]
                        else -got["violation"])
                if best is None or rank > best[0]:
                    best = (rank, got)
        return best[1]

    current = list(forced)
    while len(current) < min_sleeves:
        pick = [k for k in all_keys if k not in current]
        if not pick:
            break
        current.append(rng.choice(pick))
    if not evaluator.dependence_ok(current, grandfathered):
        # Seed with the pairwise-cleanest set we can assemble greedily instead.
        current = list(forced)
        for k in all_keys:
            if len(current) >= min_sleeves:
                break
            if evaluator.dependence_ok(current + [k], grandfathered):
                current.append(k)

    best = tune(current)
    feasible_seen = [best] if best["feasible"] else []
    print(f"\nseed {len(current)} sleeves: return {best['return_pct']:+.1f}% "
          f"dd {best['mtm_dd_pct']:.1f}% refused {best['refused']} "
          f"{'FEASIBLE' if best['feasible'] else 'infeasible'}", flush=True)

    improved = True
    while improved and time.time() - started < seconds:
        improved = False
        moves = []
        for k in all_keys:
            if k not in current and len(current) < max_sleeves:
                moves.append(("add", k, None))
        for k in current:
            if k not in forced and len(current) > min_sleeves:
                moves.append(("drop", k, None))
        for out in current:
            if out in forced:
                continue
            for into in all_keys:
                if into not in current:
                    moves.append(("swap", out, into))
        rng.shuffle(moves)

        for kind, a, b in moves:
            if time.time() - started > seconds:
                break
            trial = list(current)
            if kind == "add":
                trial.append(a)
            elif kind == "drop":
                trial.remove(a)
            else:
                trial.remove(a)
                trial.append(b)
            if (len(set(trial)) != len(trial)
                    or not evaluator.dependence_ok(trial, grandfathered)):
                continue
            got = tune(trial)
            if got["feasible"]:
                feasible_seen.append(got)
            if got["feasible"] or best["feasible"]:
                better = ((got["feasible"], got["return_pct"]) >
                          (best["feasible"], best["return_pct"]))
            else:
                better = got["violation"] < best["violation"]
            if better:
                current, best, improved = trial, got, True
                flag = "FEASIBLE" if got["feasible"] else "infeasible"
                print(f"  {kind:4} -> {len(trial)} sleeves  "
                      f"return {got['return_pct']:+7.1f}%  dd {got['mtm_dd_pct']:5.1f}%  "
                      f"risk {got['risk']}  budget {got['budget']}  "
                      f"refused {got['refused']}  {flag}  "
                      + (f"+{a}" if kind == "add" else
                         f"-{a}" if kind == "drop" else f"-{a} +{b}"),
                      flush=True)
                break
        if best["feasible"] and best["return_pct"] >= target:
            print(f"\nreached the target: {best['return_pct']:+.1f}% "
                  f"at {best['mtm_dd_pct']:.1f}% drawdown", flush=True)
            break
    return best, feasible_seen


def report(result, evaluator, label=""):
    book = result["book"]
    print(f"\n{'=' * 78}\n{label}{len(result['keys'])} sleeves   "
          f"risk_scale {result['risk']}   per-sleeve budget "
          f"{result['budget'] or 'off'}\n{'=' * 78}")
    print(f"  return          {result['return_pct']:+.1f}%")
    print(f"  MTM dd          {result['mtm_dd_pct']:.1f}%   "
          f"(closed {result['closed_dd_pct']:.1f}%)")
    print(f"  concentration   {100 * result['concentration']:.1f}%")
    print(f"  refused trades  {result['refused']}   (must be 0)")
    lifted = {k: v for k, v in (result.get("shown") or {}).items() if v != 1.0}
    print(f"  shown equity    {lifted or 'all sleeves at 1.0x'}")
    print(f"  feasible        {result['feasible']}")
    total = sum(v["pnl"] for v in book["by_sleeve"].values()) or 1.0
    print(f"\n{'sleeve':26}{'P&L $':>9}{'share':>7}{'MTM dd$':>9}"
          f"{'clsd dd$':>9}{'P&L/dd':>8}{'ddEvt%':>8}{'n':>6}{'alone$':>9}"
          f"{'ratio':>7}")
    for k, row in sorted(book["by_sleeve"].items(), key=lambda kv: -kv[1]["pnl"]):
        base = evaluator.alone.get(k)
        tail = (f"{base:>9,.0f}{row['pnl'] / base:>7.2f}" if base
                else f"{'imported':>9}{'-':>7}")
        pays = (result.get("pays") or {}).get(k, float("nan"))
        print(f"{k:26}{row['pnl']:>9,.0f}{100 * row['pnl'] / total:>6.1f}%"
              f"{row['mtm_dd_usd']:>9,.0f}{row['closed_dd_usd']:>9,.0f}"
              f"{pays:>8.2f}"
              f"{100 * row.get('dd_event_share', 0.0):>7.1f}%{row['trades']:>6}{tail}")


def detail(result, evaluator, label):
    """The mandatory per-sleeve table ([[show-per-sleeve-detail-every-time]])."""
    book = result["book"]
    series, sharpe, positive, months = cs.monthly(book["settled"],
                                                  evaluator.initial)
    values = [row["return_pct"] for row in series.values()]
    print(f"\n{'=' * 100}\n{label}   {len(result['keys'])} sleeves   "
          f"risk_scale {result['risk']}\n{'=' * 100}")
    print(f"  return          {result['return_pct']:+.1f}%   "
          f"(${evaluator.initial:,.0f} -> ${book['final']:,.2f})")
    print(f"  MTM dd          {result['mtm_dd_pct']:.2f}%   "
          f"closed dd {result['closed_dd_pct']:.2f}%   "
          f"trough {book['mtm_dd_trough']}")
    print(f"  concentration   {100 * result['concentration']:.1f}%   "
          f"trades {book['trades']}   refused {result['refused']}")
    print(f"  monthly Sharpe  {sharpe}   ({positive}/{months} positive, "
          f"median {statistics.median(values):+.2f}%, "
          f"worst {min(values):+.2f}%, best {max(values):+.2f}%)")
    total = sum(v["pnl"] for v in book["by_sleeve"].values()) or 1.0
    print(f"\n{'sleeve':28}{'P&L $':>9}{'share':>7}{'MTM dd$':>9}"
          f"{'clsd dd$':>9}{'P&L/dd':>8}{'ddEvt%':>8}{'n':>6}"
          f"{'alone$':>9}{'ratio':>7}")
    for k, row in sorted(book["by_sleeve"].items(), key=lambda kv: -kv[1]["pnl"]):
        base = evaluator.alone.get(k)
        tail = (f"{base:>9,.0f}{row['pnl'] / base:>7.2f}" if base
                else f"{'imported':>9}{'-':>7}")
        dd = row["mtm_dd_usd"]
        print(f"{k:28}{row['pnl']:>9,.0f}{100 * row['pnl'] / total:>6.1f}%"
              f"{dd:>9,.0f}{row['closed_dd_usd']:>9,.0f}"
              f"{(row['pnl'] / dd if dd else float('inf')):>8.2f}"
              f"{100 * row.get('dd_event_share', 0.0):>7.1f}%"
              f"{row['trades']:>6}{tail}")
    weak = [k for k, v in (result.get("ratios") or {}).items() if v < 1.0]
    pays = [(k, v) for k, v in (result.get("pays") or {}).items() if v < 1.0]
    print(f"  ratio < 1.00 (worse in the book than alone): "
          f"{', '.join(weak) if weak else 'none'}")
    print(f"  P&L/dd < 1.00 (pays more drawdown than it earns): "
          f"{', '.join(f'{k} {v:.2f}' for k, v in pays) if pays else 'none'}")
    print("\n  month-by-month")
    for key in sorted(series):
        row = series[key]
        bar = ("+" if row["return_pct"] >= 0 else "-") * min(
            40, int(abs(row["return_pct"])))
        print(f"    {key}  {row['return_pct']:+7.2f}%  "
              f"${row['pnl']:>10,.2f}  {bar}")


def run_screen(args, pool, evaluator, anchor, budget):
    """Add/drop/swap against `anchor`, everything re-priced at equal drawdown."""
    fixed_risk = args.screen_risk
    base = (evaluator.evaluate(anchor, fixed_risk, budget)
            if fixed_risk is not None
            else at_drawdown(evaluator, anchor, args.max_dd, budget))
    if base is None:
        raise SystemExit(f"the anchor book cannot be tuned under "
                         f"{args.max_dd}% marked drawdown on this risk grid")
    ceiling = min(args.max_dd, base["mtm_dd_pct"])
    print(f"\nANCHOR {'at fixed canon risk' if fixed_risk is not None else 're-tuned'} "
          f"to <= {ceiling}% MTM: "
          f"{base['return_pct']:+.1f}% at {base['mtm_dd_pct']:.2f}% "
          f"(risk_scale {base['risk']})", flush=True)

    flat, flat_score, adds, drops, swaps, grandfathered = screen(
        evaluator, anchor, budget, top_swaps=args.top_swaps,
        screen_risk=(fixed_risk if fixed_risk is not None else 0.70))
    if grandfathered:
        print(f"\nanchor pairs already outside the downside gates "
              f"(grandfathered):")
        for (a, b), why in grandfathered.items():
            print(f"  {a:28} {b:28} {why}")

    print(f"\nADDS -- anchor + one cell, all at risk "
          f"{fixed_risk if fixed_risk is not None else 0.70} "
          f"(anchor score {flat_score:.4f})")
    print(f"  {'cell':28}{'return':>9}{'MTM dd':>9}{'score':>8}"
          f"{'its P&L':>9}{'share':>7}{'P&L/dd':>8}{'dep':>6}")
    for r in adds[:args.show]:
        row = r["book"]["by_sleeve"][r["key"]]
        dd = row["mtm_dd_usd"]
        print(f"  {r['key']:28}{r['return_pct']:>8.1f}%{r['mtm_dd_pct']:>8.2f}%"
              f"{r['score']:>8.4f}{row['pnl']:>9,.0f}"
              f"{100 * r['shares'][r['key']]:>6.1f}%"
              f"{(row['pnl'] / dd if dd else float('inf')):>8.2f}"
              f"{'ok' if r['dep_ok'] else 'REFUSE':>6}")

    print(f"\nDROPS -- anchor minus one member, all at risk "
          f"{fixed_risk if fixed_risk is not None else 0.70}")
    print(f"  {'cell':28}{'return':>9}{'MTM dd':>9}{'score':>8}{'vs anchor':>11}")
    for r in drops:
        print(f"  {r['key']:28}{r['return_pct']:>8.1f}%{r['mtm_dd_pct']:>8.2f}%"
              f"{r['score']:>8.4f}{r['score'] - flat_score:>+11.4f}")

    print(f"\nSWAPS -- best {min(args.show, len(swaps))} of {len(swaps)} tried")
    print(f"  {'out':28}{'in':28}{'return':>9}{'MTM dd':>9}{'score':>8}")
    for r in swaps[:args.show]:
        print(f"  {r['out']:28}{r['in']:28}{r['return_pct']:>8.1f}%"
              f"{r['mtm_dd_pct']:>8.2f}%{r['score']:>8.4f}")

    # Re-price the shortlist properly. The screen ranked at one risk; these are
    # the only numbers that may be compared with the anchor.
    seeds = {tuple(sorted(anchor))}
    for r in adds[:args.seeds]:
        if r["dep_ok"]:
            seeds.add(tuple(sorted(set(anchor) | {r["key"]})))
    for r in drops[:args.seeds]:
        seeds.add(tuple(sorted(set(anchor) - {r["key"]})))
    for r in swaps[:args.seeds]:
        seeds.add(tuple(sorted((set(anchor) - {r["out"]}) | {r["in"]})))
    print(f"\nre-pricing {len(seeds)} shortlisted books at the "
          f"{ceiling}% ceiling ...", flush=True)
    priced = []
    for keys in seeds:
        got = (evaluator.evaluate(list(keys), fixed_risk, budget)
               if fixed_risk is not None
               else at_drawdown(evaluator, list(keys), ceiling, budget))
        if got is not None and got["mtm_dd_pct"] <= ceiling:
            priced.append(got)
    priced.sort(key=lambda r: -r["return_pct"])
    print(f"  {'n':>3}{'risk':>6}{'return':>10}{'MTM dd':>9}{'closed':>8}"
          f"{'conc':>7}   change vs anchor")
    for r in priced[:args.show]:
        added = sorted(set(r["keys"]) - set(anchor))
        gone = sorted(set(anchor) - set(r["keys"]))
        change = ("anchor" if not added and not gone
                  else " ".join([f"+{k}" for k in added]
                                + [f"-{k}" for k in gone]))
        print(f"  {len(r['keys']):>3}{r['risk']:>6}{r['return_pct']:>9.1f}%"
              f"{r['mtm_dd_pct']:>8.2f}%{r['closed_dd_pct']:>7.2f}%"
              f"{100 * r['concentration']:>6.1f}%   {change}")

    best = priced[0]
    detail(base, evaluator, "ANCHOR (canon, re-tuned)")
    if best["keys"] != tuple(sorted(anchor)):
        detail(best, evaluator, "BEST FOUND")
    print(f"\n{evaluator.calls} replays")
    print(f"\nchange vs anchor: "
          f"added {sorted(set(best['keys']) - set(anchor)) or 'none'}, "
          f"dropped {sorted(set(anchor) - set(best['keys'])) or 'none'}")
    print(f"  return {base['return_pct']:+.1f}% -> {best['return_pct']:+.1f}%"
          f"   MTM dd {base['mtm_dd_pct']:.2f}% -> {best['mtm_dd_pct']:.2f}%")

    strip = ("book", "shown", "pays", "shares", "ratios")
    payload = {
        "window": "2025-01-01..2026-08-16",
        "pool_size": len(evaluator.by_key), "ceiling_mtm_dd": args.max_dd,
        "anchor": [k for k in anchor],
        "anchor_priced": {k: v for k, v in base.items() if k not in strip},
        "best_priced": {k: v for k, v in best.items() if k not in strip},
        "priced": [{k: v for k, v in r.items() if k not in strip}
                   for r in priced],
        "adds": [{k: v for k, v in r.items() if k not in strip}
                 for r in adds],
        "drops": [{k: v for k, v in r.items() if k not in strip}
                  for r in drops],
        "swaps": [{k: v for k, v in r.items() if k not in strip}
                  for r in swaps[:80]],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {args.out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-dd", type=float, default=16.0)
    parser.add_argument("--target-return", type=float, default=400.0)
    parser.add_argument("--min-ratio", type=float, default=1.0)
    parser.add_argument("--min-share", type=float, default=0.05,
                        help="minimum share of book P&L per sleeve; below this a "
                             "sleeve costs a risk budget without pulling weight")
    parser.add_argument("--min-pnl-dd", type=float, default=1.0,
                        help="each sleeve's P&L divided by its own marked "
                             "drawdown contribution must be at least this; "
                             "below 1.0 a sleeve costs more risk than it earns")
    parser.add_argument("--max-dd-share", type=float, default=0.30)
    parser.add_argument("--min-sleeves", type=int, default=8)
    parser.add_argument("--max-sleeves", type=int, default=14)
    parser.add_argument("--min-symbols", type=int, default=1)
    parser.add_argument("--min-positive-months", type=int, default=0)
    parser.add_argument("--min-monthly-sharpe", type=float, default=0.0)
    parser.add_argument("--min-worst-month", type=float,
                        default=float("-inf"))
    parser.add_argument("--initial", type=float, default=ef.INITIAL_BALANCE)
    parser.add_argument("--max-down-rho", type=float, default=cs.MAX_DOWN_RHO)
    parser.add_argument("--max-loss-lift", type=float, default=cs.MAX_LOSS_LIFT)
    parser.add_argument("--min-trades", type=int, default=None)
    parser.add_argument("--sizing-cap", type=float, default=1500.0)
    parser.add_argument("--uncapped", action="store_true",
                        help="disable every per-sleeve sizing-equity cap")
    parser.add_argument("--strict-actual-minimum", action="store_true",
                        help="require zero refused/under-minimum trades at the "
                             "actual account equity; disables the shown-equity "
                             "ladder")
    parser.add_argument("--force-minimum-lot", action="store_true",
                        help="round positive under-minimum requests up to the "
                             "broker minimum; excess risk remains in MTM DD")
    parser.add_argument("--require-positive-standalone", action="store_true",
                        help="remove every freshly replayed native candidate "
                             "whose current holdout standalone P&L is not "
                             "strictly positive")
    parser.add_argument("--risks", default="0.6,0.9,1.2,1.6")
    parser.add_argument("--budgets", default="0,2,3,4",
                        help="per-sleeve exposure budget in multiples of "
                             "equity; 0 means no cap")
    parser.add_argument("--include", default="usdjpy:range_expansion,nq:ofi,"
                                             "nq:drift_vwap")
    parser.add_argument("--pool", default=None,
                        help="load only these comma-separated candidates plus "
                             "the screen anchor/imported includes")
    parser.add_argument("--free-forced", action="store_true",
                        help="let the search drop the --include sleeves too, so "
                             "the cost of keeping them is measurable")
    parser.add_argument("--seconds", type=float, default=1500.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--restarts", type=int, default=3,
                        help="independent hill climbs from different random "
                             "seed books; one climb finds one local optimum")
    parser.add_argument("--screen", metavar="ANCHOR", default=None,
                        help="instead of the hill climb, measure the marginal "
                             "value of every add, drop and promising swap "
                             "against ANCHOR -- a comma-separated sleeve list, "
                             "or 'canon' for the recorded BOOK. Runs without "
                             "the shown-equity ladder and prices every "
                             "membership at --max-dd, so the return figures "
                             "are comparable with `build --members canon`.")
    parser.add_argument("--screen-risk", type=float, default=None,
                        help="screen every membership at this one fixed risk; "
                             "also cap candidates at the anchor's measured "
                             "MTM drawdown, so additions cannot buy return by "
                             "adding risk")
    parser.add_argument("--show", type=int, default=25,
                        help="rows printed per screen table")
    parser.add_argument("--seeds", type=int, default=8,
                        help="best moves per table that get re-priced at the "
                             "drawdown ceiling")
    parser.add_argument("--top-swaps", type=int, default=12,
                        help="how many of the best adds to try as swaps")
    parser.add_argument("--out", default=None,
                        help="where --screen writes its JSON")
    args = parser.parse_args()

    cs.GLOBAL_SIZING_CAP = args.sizing_cap
    cs.UNCAPPED = args.uncapped
    cs.FORCE_MINIMUM_LOT = args.force_minimum_lot
    forced = tuple(s for s in args.include.replace(" ", "").split(",") if s)
    risks = [float(v) for v in args.risks.split(",") if v]
    budgets = [float(v) or None for v in args.budgets.split(",") if v != ""]

    anchor = None
    if args.screen:
        anchor = (list(cs.BOOK) if args.screen.strip().lower() == "canon"
                  else [s for s in args.screen.replace(" ", "").split(",") if s])
        # The anchor decides membership, so `--include` is not what has to be
        # loaded -- the anchor's own imported sleeves are. Without this the
        # canon anchor fails on nq:volatility_breakout, which is in `EXTERNAL`
        # but not in `--include`'s default.
        forced = tuple(k for k in anchor if k in cs.EXTERNAL)

    print("loading pool ...", flush=True)
    only = tuple(s for s in (args.pool or "").replace(" ", "").split(",")
                 if s)
    if anchor is not None and only:
        only = tuple(dict.fromkeys((*only, *anchor, *forced)))
    pool = load_pool(min_trades=args.min_trades, forced=forced, only=only)
    if args.require_positive_standalone:
        by_key, logs, streams, bars_by, ctx_by, alone = pool
        rejected = {key for key, pnl in alone.items()
                    if pnl is not None and pnl <= 0}
        bad_forced = sorted(rejected.intersection(forced))
        if bad_forced:
            raise SystemExit("forced sleeves fail fresh positive-standalone "
                             f"gate: {bad_forced}")
        for key in rejected:
            by_key.pop(key, None)
            logs.pop(key, None)
            streams.pop(key, None)
            alone.pop(key, None)
        pool = by_key, logs, streams, bars_by, ctx_by, alone
        print(f"fresh positive-standalone gate removed {len(rejected)} "
              "stale/losing candidates", flush=True)
    print(f"pool: {len(pool[0])} candidates", flush=True)

    ev = Evaluator(pool, args.min_ratio, args.max_dd, args.max_dd_share,
                   args.max_down_rho, args.max_loss_lift, args.min_pnl_dd,
                   args.min_share,
                   ladder=(anchor is None and
                           not args.strict_actual_minimum),
                   strict_actual_minimum=args.strict_actual_minimum,
                   initial=args.initial, min_symbols=args.min_symbols,
                   min_positive_months=args.min_positive_months,
                   min_monthly_sharpe=args.min_monthly_sharpe,
                   min_worst_month=args.min_worst_month)
    if anchor is not None:
        absent = [k for k in anchor if k not in ev.by_key]
        if absent:
            raise SystemExit(f"anchor sleeves absent from the pool: {absent}")
        args.out = args.out or os.path.join(cs.RESULTS, "book_search_screen.json")
        run_screen(args, pool, ev, anchor, budgets[0] if budgets else None)
        return

    best, feasible = None, []
    slice_seconds = args.seconds / max(1, args.restarts)
    for attempt in range(max(1, args.restarts)):
        print(f"\n--- restart {attempt + 1}/{args.restarts}", flush=True)
        got, seen = search(pool, () if args.free_forced else forced,
                           args.min_sleeves, args.max_sleeves, risks, budgets,
                           ev, args.target_return, slice_seconds,
                           args.seed + attempt)
        feasible.extend(seen)
        if (best is None
                or (got["feasible"] and not best["feasible"])
                or (got["feasible"] and best["feasible"]
                    and got["return_pct"] > best["return_pct"])
                or (not got["feasible"] and not best["feasible"]
                    and got["violation"] < best["violation"])):
            best = got
        if best["feasible"] and best["return_pct"] >= args.target_return:
            break

    print(f"\n{ev.calls} replays, {len(feasible)} feasible points found")
    if feasible:
        top = sorted(feasible, key=lambda r: -r["return_pct"])[:5]
        print(f"\ntop feasible by return:")
        print(f"  {'n':>3}{'risk':>6}{'budget':>8}{'return':>10}{'MTM dd':>9}"
              f"{'closed':>8}{'conc':>7}{'refused':>9}{'wst ratio':>11}"
              f"{'wst P&L/dd':>12}{'wst share':>11}")
        for r in top:
            print(f"  {len(r['keys']):>3}{r['risk']:>6}"
                  f"{(r['budget'] or 0):>8}{r['return_pct']:>9.1f}%"
                  f"{r['mtm_dd_pct']:>8.1f}%{r['closed_dd_pct']:>7.1f}%"
                  f"{100 * r['concentration']:>6.1f}%{r['refused']:>9}"
                  f"{r['worst_ratio']:>11.2f}{r['worst_pays']:>12.2f}"
                  f"{100 * r['worst_share']:>11.1f}%")
        report(top[0], ev, label="BEST FEASIBLE   ")
    else:
        print("\nNO combination satisfied every constraint. Closest reached:")
        report(best, ev, label="CLOSEST (infeasible)   ")

    strip = ("book", "shown", "pays", "shares", "ratios")
    saved = {k: v for k, v in best.items() if k not in strip}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(saved, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
