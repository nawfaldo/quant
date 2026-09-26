"""One $1,000 account running several `cfd_families` cells that do not
overlap.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the tables into the reply.
See the same warning at the top of `cfd_families` -- it cost a session once
([[paste-results-into-the-reply]]).

**EVERY REPORTED RESULT MUST INCLUDE THE PER-SLEEVE TABLE.** Not the book
summary alone. For each sleeve: its P&L contribution, its share, its drawdown
BOTH marked-to-market AND closed-trade, its trade count, and its standalone
return, standalone drawdown and standalone dollar equivalent beside them, plus
the ratio of the two. `build` prints exactly this and flags any sleeve with a
ratio under 1.00.

The book total hides the two failures that actually matter: a book that merely
tracks its best member, and a sleeve that earns standalone and INVERTS inside
the combination. ethusd:swing_ma was the best standalone cell in the pool at
+44.0% and contributed -$93 to the book; nothing in the headline number showed
it. Reporting "+910%, drawdown 18.9%" and stopping there is not a result, it is
a summary of one number the operator cannot act on.

WHAT THIS IS FOR.

`cfd_families` produced 50 cells that beat their own coin-flip null at 30m,
and twelve of them are ETHUSD. Twelve views of one instrument in one session is
one hypothesis, not twelve, and stacking them would buy no diversification while
paying twelve risk budgets. This module picks a set whose P&L streams are
genuinely close to independent and then runs them together properly.

WHAT "UNCORRELATED" MEANS HERE: NOT LOSING TOGETHER.

Plain Pearson correlation is the wrong gate for this book and was the first
thing tried. It punishes two sleeves for winning on the same day, which costs
nothing, and it averages the good days and the bad days into one number that
hides the only behaviour that actually hurts -- every sleeve red in the same
week. Trades overlapping in time is likewise not a problem and is not tested.

So admission is on DOWNSIDE dependence, measured three ways:

  `down_rho`    Pearson over only the days where at least one of the pair lost.
                Co-movement while the account is going backwards.
  `loss_lift`   P(both lose on a day both traded) divided by what independence
                would predict, P(a loses) x P(b loses). 1.0 is independent;
                2.0 means they lose together twice as often as chance.
  `bad_overlap` of one sleeve's five worst months, the share that were also
                losing months for the other. The tail question stated directly.

A pair is refused if `down_rho` or `loss_lift` exceeds its threshold. Upside
correlation is not gated at all.

Members are ranked by holdout return and taken greedily in that order. The
ranking is a number selection already maximised, so it is a convenience, not
evidence -- what matters is the dependence matrix this prints, which is a
property of the trade streams rather than of the search.

WHY THE BOOK IS NOT THE SUM OF ITS SLEEVES.

Each member is re-sized against the LIVE shared balance, in entry order, using
its own symbol's contract spec and `cfd_families.quantity`. So a gold trade
taken after ETHUSD has drawn the account down is a smaller trade than it was
standalone. Summing standalone daily return streams instead hides about half the
drawdown ([[blend-model-understates-portfolio-drawdown]]), and a third sleeve
adds a whole risk budget rather than diversifying one away
([[combined-book-stacks-risk-budgets]]).

DRAWDOWN IS MARKED TO MARKET, NOT TAKEN ON CLOSED TRADES.

Closed-trade replay once read 21% where the engine read 37%: the difference is
open risk, and on an intraday book the worst moment is usually inside a position
rather than after it ([[engine-drawdown-is-mark-to-market]],
[[book-drawdown-is-one-intraday-position]]). So every open position is revalued
on every 30-minute bar its symbol prints, and the reported drawdown is taken on
that path.

THE CONTROLS.

Two, because a portfolio result has two distinct ways of being an illusion:

  * `--null` rebuilds the book from coin-flip sleeves -- the same members, the
    same count, directions randomised -- so the diversification benefit is
    measured against what merging noise streams produces
    ([[coin-flip-control-beats-real-signals]]).
  * the per-sleeve table prints each member standalone on the same window, so a
    book that merely tracks its best member is visible as such.

WINDOW. The holdout only: 2025-01-01 to 2026-08-20. Every member's parameters
were sealed on data ending 2024-12-31, so the combination is measured where none
of its parts were fitted. The correlations, however, are estimated on this same
window -- with the sample this short that is a real limitation and it is printed
rather than hidden.

    python -m sandbox.research.exness_combined_strategies build
    python -m sandbox.research.exness_combined_strategies build --null
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone

from sandbox.research import cfd_families as ef

RESULTS = ef.RESULTS
OUT_PATH = os.path.join(RESULTS, "exness_combined_strategies.json")

#: The source refresh performed on 2026-08-21 is complete through the prior
#: local market day. This is an EXCLUSIVE boundary: every query/replay below
#: may consume 2026-08-20, but never a partial 2026-08-21 session.
CANON_DATA_THROUGH = "2026-08-20"
CANON_DATA_END = "2026-08-21"
ef.OOS_END = int(datetime.fromisoformat(CANON_DATA_END)
                 .replace(tzinfo=timezone.utc).timestamp())

#: ES changed feeds at a day boundary. `es_30m` contains the historical 30m
#: aggregation of `es_1m` before 2026-06-24; the Yahoo importer replaced that
#: entire DAY partition and tagged every Yahoo row from 2026-06-24 onward.
#: Preferring the native table therefore gives one source per calendar day and
#: cannot double-count the overlapping final `es_1m` day.
ef.PREFER_30M.add("es")
# `es_1m` and Yahoo `es_30m` are both Chicago wall clock. This is the session
# already established by the ES research modules, stated here so `resolve`
# does not try to infer it using Exness-native `tick_volume` metadata.
ef.SESSION.setdefault("es", (8 * 60 + 30, 15 * 60))

#: Downside gates. A pair is refused if EITHER is exceeded.
#:
#: `down_rho` is correlation on losing days only, so 0.40 still allows a fair
#: amount of shared bad weather -- the aim is to exclude sleeves that are the
#: same bet, not to demand independence that does not exist inside one broker's
#: symbol list.
#:
#: `loss_lift` is the ratio of observed joint-loss days to what independence
#: predicts. 1.40 means: they may lose together up to 40% more often than
#: chance, and no more.
MAX_DOWN_RHO = 0.40
MAX_LOSS_LIFT = 1.40

#: Days in each sleeve's tail used by `bad_overlap`. Reported, not gated --
#: with a 20-month holdout, five months is already a thin tail and gating on it
#: would be gating on four or five observations.
TAIL_MONTHS = 5

#: Economic-significance gates, applied BEFORE the null. `cfd_families`'s
#: verdict is purely statistical, and on jp225 that let eight families through
#: at +0.2% on a quantised lot floor -- statistically fine, economically
#: nothing. A sleeve has to clear all three to be considered.
MIN_OOS_RETURN = 3.0        # per cent over the holdout
MIN_OOS_TRADES = 40         # below this a correlation estimate is noise anyway
MIN_IS_T = 1.50             # in-sample edge-vs-drift t-stat

#: How much better than buy-and-hold a DRIFT-EXPLOITING sleeve must be, on
#: return per unit of drawdown, to be admitted without a per-trade edge.
#:
#: 1.5x rather than 1.0x because beating the benchmark by a hair on one window
#: is noise. xauaud:high_52w clears it easily -- 9.86 against buy-and-hold's
#: 1.60 on the holdout, i.e. 3.3x the return at half the drawdown -- which is
#: what a real drift-exploiting sleeve looks like: it does not predict the
#: market, it holds the same exposure through far less of the pain.
DRIFT_MARGIN = 1.5

#: Sleeves excluded by operator decision, with the reason, because a missing
#: member always looks like an oversight to the next reader.
#: Whole instruments barred from the pool, so a dropped cell cannot be replaced
#: by another cell on the same symbol at the next rebuild. BTC is out at the
#: operator's instruction (2026-08-17): btc:momentum and btc:vwap were dropped
#: for risk-per-dollar, and btc:orb then took their place -- which is the thing
#: the per-cell list cannot prevent.
#: TSLA and FR40 are out at the operator's instruction (2026-08-17), as whole
#: instruments for the same reason: tsla:failed_break and fr40:squeeze both ran
#: a book/standalone ratio under 1.00 (0.95 and 0.92), and dropping only those
#: cells would let tsla:ib or another cell on the same symbol walk back in.
#: STOCKS ARE OUT AS A WHOLE ASSET CLASS at the operator's instruction
#: (2026-08-24): the Exness account being funded cannot trade individual
#: equity CFDs at all, so a stock cell is not a strategy decision but an
#: unavailable instrument. Barring the class rather than the two cells stops
#: another aapl/nvda/googl cell walking into the slot at the next rebuild --
#: the same failure that put BTC in EXCLUDE_SYMBOLS.
#: FR40 IS NOW ACTUALLY ENFORCED (2026-08-25). It was declared barred as a
#: whole instrument on 2026-08-17 in the comment above, but never added here,
#: so `candidates()` kept offering fr40 cells and a search duly found
#: fr40:swing_break -- exactly the substitution the symbol-level bar existed to
#: prevent. A policy that lives only in a docstring is not a policy.
#: NQ IS BARRED AS A SYMBOL (operator decision, 2026-09-03), and USTEC is the
#: replacement rather than a rename. `nq`'s table is the back-adjusted futures
#: continuum -- a ratio-adjusted level series, not a price the account can be
#: filled at -- and its five canon sleeves reached level two only through the
#: EXTERNAL import path, which is a second data dependency (Databento and
#: Bookmap tick archives) that the live runtime does not need for anything else.
#: `ustec` is the Nasdaq 100 CFD Exness actually quotes, with a broker 1m series
#: behind it, so a ustec sleeve is priced by the same feed it would be filled on
#: ([[nq-has-two-incompatible-price-series]]).
#: RE-TESTED AND THE BAR STANDS, 2026-09-04. The five sleeves were run against
#: the decay rule that produced the current book and only `nq:drift_vwap`
#: cleared it -- positive every year 2022-2026 at 1.62x its prior edge, worth
#: +2 positive months and shorter time underwater in the book. It was still
#: refused, on COST rather than on edge: one sleeve does not pay for the
#: Databento and Bookmap subscriptions the EXTERNAL level-two path needs, and
#: nothing else in the book reads them. Operator decision.
#:
#: The other four fail the rule outright: `nq:level_confluence` is negative in
#: 2026 (-0.19x), `nq:sar` was negative in 2025, `nq:volatility_breakout` is
#: 0.52x, and `nq:ofi` has no history before 2025-02 so it cannot be decay
#: tested at all.
#:
#: ONE FINDING FROM THAT TEST IS ABOUT CANON, NOT ABOUT NQ, AND IT SURVIVES THE
#: REFUSAL. Adding NQ took realised MTM drawdown 12.0% -> 20.0% while
#: `nq:drift_vwap` itself bore NONE of the worst fall. The extra P&L lifted the
#: shared balance, every other sleeve then sized larger, and a joint
#: usdjpy+eurjpy+gbpjpy move that is minor at today's equity became 97% of an
#: eight-point event. That FX cluster is latent in the current book; it is
#: simply not funded to that size yet ([[book-drawdown-is-one-intraday-position]]).
EXCLUDE_SYMBOLS = {"nq", "fr40", "aapl", "amzn", "avgo", "googl", "msft",
                   "nvda", "tsla", "tsm"}

EXCLUDE = {
    "xaugbp:key_reversal":
        "unaffordable on $1,000. Gold-in-GBP went 1487 -> 3516, widening the "
        "ATR-scaled stop from 2.91 to ~7 points, so at 0.6% risk a trade needs "
        "~0.005 lots against a 0.01 minimum. Fill rate by year: 100% (2022), "
        "100% (2024), 70% (2025), 0% (2026) -- 66 signals and zero fills in "
        "2026. It only looked healthy inside the book because the shared "
        "balance was $33k by then. Dropped 2026-08-16.",
    #: USDJPY was pinned to range_expansion at the operator's instruction
    #: (2026-08-17). The other five cells cleared the null but are five more
    #: views of the same session of the same symbol: their worst-5-month overlap
    #: with each other runs 60-100% (overnight vs ma_cross 100%, volume_thrust
    #: vs range_expansion 80%), so they pass a pairwise loss_lift gate while
    #: still failing in the same months. range_expansion is kept because it is
    #: the only one of the six whose coin-flip null found no in-sample cell in
    #: any seed AND which beat buy-and-hold by a real margin (+22.8 vs +1.5).
    "usdjpy:overnight": "pinned to range_expansion 2026-08-17 (same symbol, "
        "same session; worst-month overlap 80-100% with the others)",
    "usdjpy:pdr": "pinned to range_expansion 2026-08-17 (same symbol, same "
        "session; standalone ret/dd 0.38)",
    "usdjpy:ma_cross": "pinned to range_expansion 2026-08-17 (same symbol, "
        "same session; 100% worst-month overlap with usdjpy:overnight)",
    "usdjpy:key_reversal": "pinned to range_expansion 2026-08-17; already "
        "refused by the downside gate on loss_lift 1.56 vs usdjpy:ma_cross",
    #: Dropped 2026-08-17 on risk-per-dollar inside the book, not on standalone
    #: return. Figures are from the 12-sleeve replay at risk_scale 0.9 over
    #: 2025-01-01..2026-08-16.
    "btc:momentum": "the only losing member: -$409 P&L while carrying $1,125 "
        "of mark-to-market drawdown. Dropped 2026-08-17.",
    "btc:vwap": "worst risk-per-dollar in the book: +$439 P&L against $1,536 "
        "of MTM drawdown, the single largest drawdown contributor while "
        "supplying 4.8% of P&L. Dropped 2026-08-17.",
    "fr40:swing_ma": "the book's largest drawdown contributor in every "
        "configuration tried -- 30.5%, 35.4% and 35.0% of the worst marked "
        "fall across three risk settings -- on 75 trades supplying 13.6% of "
        "P&L. Dropped 2026-08-17 while pulling MTM drawdown under 18%.",
    "xznusd:volume_thrust": "+$11 P&L against $824 of MTM drawdown at a $2,000 "
        "sizing cap -- ratio 0.18 against its own standalone, the only member "
        "still below it, and the largest single drawdown contributor in the "
        "book. Standalone it is +6.0% on a 25.9% drawdown, so it was never "
        "carrying its risk budget. Dropped 2026-08-17.",
    "de40:donchian": "swapped to de40:ib by operator decision 2026-08-17.",
    "ethusd:drift_vwap": "negative P&L (-$54) in combined book across 1,573 trades. Dropped 2026-08-17.",
    "fr40:squeeze": "dropped by operator decision 2026-08-17.",
    #: Tested and REJECTED 2026-08-25. It looked like the best find of the whole
    #: drawdown search -- long-window MTM 14.2% -> 11.2% and +6229% vs +5268% --
    #: and the per-year split shows the entire benefit sits in the FITTED years:
    #:
    #:            2020  2021  2022  2023  2024 | 2025  2026   aggregate
    #:   without   9.5   6.1  11.9   7.5  10.2 |  4.9  11.2       14.2
    #:   with      9.4   5.3   9.8   7.5   9.1 |  5.6  11.2       11.2
    #:
    #: Out of sample it makes 2025 WORSE (4.9 -> 5.6) and leaves 2026 identical;
    #: the cold-start trough does not move (2025-09-10, 10.39% -> 10.37%). The
    #: aggregate falls only because shaving 2022 and 2024 breaks a drawdown that
    #: spanned a year boundary -- real arithmetic, in fitted years only.
    #: Corroborated by its own stats: loss_lift 0.997 against the book (i.e.
    #: independent, not damping) and +0.08% on the book's worst days. Standalone
    #: it loses to buy-and-hold on return (11.4 vs 17.9) AND on return/drawdown
    #: (0.75 vs 0.98). Admitted to the pool on t=2.60 alone.
    "fr40:swing_break": "tested 2026-08-25: its whole drawdown benefit is in "
        "the fitted years, OOS it is neutral-to-worse, and fr40 is barred as a "
        "symbol anyway. See EXCLUDE_SYMBOLS.",
    "xaueur:orb": "dropped by operator decision 2026-08-17.",
    "usdjpy:range_expansion": "swapped to usdjpy:volume_thrust by operator decision 2026-08-17.",
    "jp225:pdr": "swapped to nq:volume_thrust by operator decision 2026-08-17 (bore 24.6% of July 2025 event).",
    "tsla:ib": "dropped by operator decision 2026-08-17.",
    "tsla:failed_break": "swapped to eurjpy:vwap by operator decision 2026-08-17.",
    "ethusd:failed_break": "swapped to ethusd:swing_ma by operator decision 2026-08-17.",
    "xaueur:key_reversal": "swapped to ethusd:range_expansion by operator decision 2026-08-17 (unaffordable on $400).",
    "nq:volume_thrust": "dropped by operator decision 2026-08-18 (OOS volume baseline distortion on L2 ticks).",
    "xagaud:failed_break": "dropped 2026-08-20: 50% missed fills on $1,000 and 0 fills in 2026; swapped to es:volatility_breakout.",
    "ethusd:range_expansion": "dropped 2026-08-20: negative 2026 return (-$63) and 32% drawdown concentration; swapped to gbpjpy:trap.",
    "ethusd:swing_ma": "dropped 2026-08-20: wide 23.4% ATR stop caused 88.5% drawdown concentration on 2025-05-13 and below broker min skips on small balance.",
    "es:volatility_breakout": "dropped 2026-08-21: its sealed +19.93% / 188-trade OOS record used data only through 2026-06-24 despite being labeled through 2026-08-16; a fresh complete replay was -7.07% / 220 trades and lost $39.28 in the shared book.",
    #: Dropped 2026-08-23 when the account was fixed at $400. This is a MARGIN
    #: refusal, not a risk or edge judgement, and it cannot be tuned away:
    #: DE40's `volume_min` is 0.07 lots (not 0.01), so one minimum lot needs
    #: entry x 1.157 x MARGIN_FRACTION x 0.07 of margin -- $536 at the most
    #: recent entry (26,473). `size` compares that ceiling against REAL equity,
    #: so SHOWN_EQUITY cannot lift it
    #: ([[shown-equity-cannot-fix-a-margin-refusal]]).
    #:
    #: It became unaffordable only recently -- median margin by year: 2020 $261,
    #: 2022 $281, 2024 $373, 2025 $480, 2026 $503 -- which is why a 2020-start
    #: replay shows zero skipped trades and a cold $400 start in 2025 skips 104.
    #:
    #: It cost real money to lose: sized to a 14% long-window ceiling, no
    #: replacement recovered more than 30% of it, because its worst loss_lift
    #: against the rest of the book is 1.09 and it was damping drawdown rather
    #: than earning return ([[low-loss-lift-sleeves-are-drawdown-dampers]]).
    #: Put it back the day the account can margin it.
    #: DROPPED TWICE. First on 2026-08-23 as unaffordable on $400, then
    #: restored 2026-08-24 on a $537 MARGIN_EQUITY_FLOOR, then dropped again
    #: the same day on what that restoration actually cost.
    #:
    #: The floor worked -- zero margin refusals, +398.2% cold start against the
    #: previous book's +287.8%. But the MTM drawdown went 10.6% -> 14.4% and
    #: ALL of the increase was one position. On 2025-04-04 the book fell $90.27
    #: in 31 minutes, $86.10 of it de40: 95.4% concentration.
    #:
    #: The cause is lot granularity, NOT risk_scale and NOT the gross cap, and
    #: this is why no setting fixes it. At the $627 peak de40 wanted 0.0178
    #: lots and the broker minimum is 0.07, so `--force-minimum-lot` placed 4x
    #: the intended risk; risk_scale cannot shrink it further (it still places
    #: 0.07 at 0.01) and cutting risk only shrinks the OTHER sleeves, raising
    #: de40's share. One minimum lot is $1,757 notional = 2.80x equity, against
    #: an 8x cap with $3,260 of unused headroom, so the cap never came near
    #: binding. Then a 4.90% index move blew through the 60-point stop by ~18x
    #: and turned a 0.20%-of-equity intended risk into a 13.73% loss
    #: ([[book-drawdown-is-one-intraday-position]]).
    #:
    #: PUT IT BACK when the account can size it properly, which is a balance
    #: question, not a settings one: at $2,000 the same lot is 0.88x equity and
    #: the same move costs ~4.3%.
    "hk50:supertrend": "dropped 2026-08-26 by operator decision.",
    "stoxx50:climax": "dropped 2026-08-26. Also the only canon sleeve whose "
        "edge had DECAYED: OOS 2.9 bp/trade against 10.8 in-sample (ratio "
        "0.26), 0.0 bp in 2026, OOS t 0.38.",
    "audjpy:gated_donchian": "dropped 2026-08-26 by operator decision.",
    "jp225:xma_ribbon": "dropped 2026-08-26 by operator decision.",
    "jp225:swing_break": "swapped for ethusd:macd_hist 2026-08-25: the swap "
        "took the book to 20/20 positive cold months (+541.1% vs +462.1%) and "
        "raised monthly Sharpe 4.25 -> 5.11.",
    "audusd:trap": "tested 2026-08-25 and rejected: the ONLY unprofitable "
        "sleeve in the 19-sleeve book on BOTH windows (-$52.66 cold, -$1,133.33 "
        "long) while carrying the largest single long-window drawdown of any "
        "member ($3,309.73). Standalone +4.7% on 10.08% dd, ratio 0.47. Its "
        "apparent drawdown benefit at gross_cap 6.0x came from CROWDING the "
        "cap -- removing it cut refusals 413 -> 314 and ADDED 30pp of cold "
        "return -- so it disappears once the cap is off.",
    "de40:floor_pivot": "dropped 2026-08-24 (again): one forced minimum lot is "
        "2.80x equity on this balance and bore 95.4% of the worst drawdown in "
        "31 minutes on 2025-04-04. Lot granularity, not risk or the gross cap.",
    "msft:xma_cross": "dropped 2026-08-24: the funded Exness account cannot "
        "trade individual stock CFDs. See EXCLUDE_SYMBOLS -- the whole class "
        "is barred, so this entry is belt-and-braces.",
    "tsla:floor_pivot": "dropped 2026-08-24: the funded Exness account cannot "
        "trade individual stock CFDs. See EXCLUDE_SYMBOLS.",
    "gbpjpy:xma_cross": "tested 2026-08-28 and rejected by operator: contributed "
        "only +$4 P&L against $80 MTM drawdown (ratio 0.14) in the holdout.",
    #: DROPPED 2026-09-07 ON DATA QUALITY, NOT ON PERFORMANCE, and the two are
    #: worth keeping apart. Removing it changed nothing measurable: full p95
    #: 21.30 -> 21.46, OOS p95 19.69 -> 19.64, full p99 27.94 -> 27.57, returns
    #: -1.4% full and -2.8% OOS, months and underwater days identical -- every
    #: move inside the Monte Carlo's own sampling error.
    #:
    #: What it removes is an UNMEASURED sleeve. `hk50` is the gappiest symbol
    #: the book has held: 74 of 1,215 weekdays (6%) carry no 30m bar at all
    #: against 0-1% for every other canon symbol, and its median day is 13 bars
    #: against an expected 14. Worse for a live-execution study, only 70% of its
    #: HOLDOUT entries fall on a bar the broker maps can reprice, against
    #: 99-100% for everything else -- so a third of its trades were being scored
    #: at the constant spread while the rest of the book paid measured costs.
    #:
    #: It was also the smallest contributor in the book: $14 of P&L on 122
    #: holdout trades, 0.4% of the total, and 0.0% of the worst marked fall.
    "hk50:level_confluence": "dropped 2026-09-07: 6% of decision days missing "
        "and only 70% of holdout entries repriceable by the live maps, for "
        "0.4% of book P&L. Removed to make every remaining sleeve measurable, "
        "not for its record.",
    #: DROPPED 2026-09-03 BY THE DECAY SCREEN, at the operator's instruction
    #: that no sleeve doing badly in the latter year may stay. The measure is
    #: R-multiples per trade (`gross / distance`) after live-execution cost, by
    #: calendar year -- sizing-independent, so a sleeve cannot look worse merely
    #: for having been sized smaller late in a compounding run. Two were
    #: outright negative in 2026; five had lost more than half their 2022-2025
    #: mean edge. `_seventh_decay.py` reproduces the table.
    "de40:consecutive": "decay screen 2026-09-03: -13.8R over 125 trades in "
        "2026 (mean -0.110 vs +0.099 prior) while bearing 25.8% of the holdout "
        "drawdown event. The worst sleeve in the book on both legs.",
    "ethusd:idio_break": "decay screen 2026-09-03: -4.1R over 49 trades in "
        "2026 (mean -0.083 vs +0.125 prior).",
    "gbpusd:obv_break": "decay screen 2026-09-03: 2026 mean R +0.006 against "
        "+0.312 for 2022-2025, a ratio of 0.02x. The edge is gone, not smaller.",
    "ustec:key_reversal": "decay screen 2026-09-03: ratio 0.02x (2026 mean R "
        "+0.006 vs +0.245 prior).",
    "ustec:kendall": "decay screen 2026-09-03: ratio 0.09x (+0.027 vs +0.320) "
        "and the largest single contributor to the 2026 drawdown event at "
        "26.9%. Seated by operator request 2026-09-03 and removed the same day "
        "when the decay rule was set -- the rule is the later instruction.",
    "ustec:volatility_breakout": "decay screen 2026-09-03: ratio 0.39x (+0.084 "
        "vs +0.215). Its 2025 was already flat at +2.2R.",
    "gbpjpy:fracdiff": "decay screen 2026-09-03: ratio 0.23x (+0.071 vs "
        "+0.313) on 18 trades in 2026.",
}

#: Below this share of signals actually filled, a sleeve's results are decided
#: by which trades the account could afford rather than by the rule. NOT a hard
#: gate -- xauaud:high_52w fills 15.7% and is in the book by operator choice --
#: but it is printed loudly, because a low fill rate means the backtest and a
#: real account are trading different strategies
#: ([[small-balance-hides-drawdown-by-dropping-trades]]).
WARN_FILL_RATE = 60.0

#: `MIN_IS_T` EXCLUDES xauaud:high_52w, AND THAT IS THE OPERATOR'S DECISION.
#: Recorded because the evidence is genuinely split and this WILL look like an
#: oversight to whoever reads the member list next.
#:
#: It is the largest single holdout return in the study, +144.2%, and admitting
#: it takes the book from +258.6% to +626.3% (cap off) or +192.0% to +283.1%
#: (3x cap). The BOOK-level null even improves: margin over the best coin-flip
#: seed goes 1.47x -> 4.24x and 1.98x -> 5.12x.
#:
#: It is out anyway, on the per-trade evidence:
#:
#:     gross 24.94 bp/trade, of which 19.26 bp is DRIFT -- a long-biased rule on
#:     gold in AUD across a gold bull market. Real edge 5.68 bp against a 136.8
#:     bp per-trade sd, so t = 0.50 on 145 in-sample trades. Its family-level
#:     coin flip -- which re-runs the parameter search, unlike the book-level
#:     null -- reached +101.9% against its +144.2%, collecting the same drift.
#:
#: The two controls disagree because they are not the same control: the
#: book-level null flips directions on ALREADY-FITTED parameters and never
#: re-searches, so it systematically under-prices a cell whose edge came from
#: the search. For this sleeve the family-level number is the honest one.
#:
#: Do not "fix" this by lowering the constant. Lowering it to 0 is a one-line
#: change and the counterfactual above is what it buys.

#: Cap on how many members the book takes. Each one adds a full risk budget, so
#: this is a risk decision rather than a search parameter. Confirmed empirically
#: on 2026-08-23: a greedy search over eleven gate-clean, $400-affordable
#: candidates found NO addition to the 14-sleeve book worth more than 5pp, and
#: the ones that helped the 2020-2026 window blew the cold-start drawdown out to
#: 20.8% ([[cold-start-at-400-is-the-binding-test]]).
MAX_MEMBERS = 26

#: The balance the account is actually funded with. Canon is sized and reported
#: on this, NOT on `ef.INITIAL_BALANCE` -- on a small balance the broker's lot
#: floor makes each fill a large share of equity, so a book validated at $1,000
#: is a different strategy from the same book at $500
#: ([[minimum-lot-de-diversifies-pro-cyclically]]).
#: RAISED $400 -> $500 by operator decision 2026-09-03. It is not a cosmetic
#: change: every affordability result on this book was measured against the lot
#: floor at $400, so a sleeve refused there may fill here and the membership
#: search has to be re-run rather than re-scaled
#: ([[cold-start-at-400-is-the-binding-test]]).
CANON_INITIAL = 500.0

#: The drawdown the book is being sized to, in per cent, marked to market.
TARGET_MTM_DD = 15.0

#: Chosen uncapped, minimum-lot risk multiplier below the 15% continuous MTM
#: budget. With the 14-sleeve $400 book and CANON_GROSS_CAP, 0.131 produces
#: +5,087.3% / 14.7% MTM DD with 66/80 positive months over 2020-2026, and
#: +287.8% / 10.6% with 16/20 positive months cold-started on the 2025-2026
#: holdout. Operator decision 2026-08-23.
#:
#: SET TO 0.130 ON THE TWENTY-SLEEVE DECAY-SCREENED BOOK, 2026-09-03, and it is
#: chosen on the DISTRIBUTION rather than on a path. 1,000 block-bootstrap
#: paths on live execution (`_seventh_clean_mc.py`):
#:
#:                    full 2022-2026        OOS 2025-2026
#:                    p95     ret p50       p95     ret p50
#:   0.130           21.66      6,250%     19.81        708%
#:   0.190           25.22     25,981%     20.50      1,219%
#:
#: 0.130 is the operator's choice for the lowest tail available: it is the only
#: setting in this study that puts BOTH windows near 20%, and the OOS window --
#: the one the account is about to trade -- under it.
#:
#: THE DIAL WORKS AGAIN AND THAT IS AN EFFECT OF THE SCREEN, NOT OF THE DIAL.
#: On the 26-sleeve book p95 moved 23.63 -> 24.02 across risk 0.080 to 0.150,
#: because minimum-lot-pinned sleeves place the broker minimum whatever the risk
#: request says ([[oos-drawdown-ignores-the-risk-dial]]). Several of the seven
#: sleeves the decay screen removed were exactly those, so sizing regained its
#: grip: here 0.130 -> 0.190 moves p95 3.6 points.
#: RAISED 0.135 -> 0.190 on 2026-09-03 with the NQ-free book, and the reason is
#: that MEMBERSHIP had already bought most of the drawdown budget back.
#:
#: THE 26th SLEEVE IS PAID FOR IN CEILING, NOT IN SIZE, AND THAT IS AN OPERATOR
#: DECISION (2026-09-03). `ustec:kendall` spends about 1.1 points of p95, which
#: at this risk puts the full-window p95 at ~25.5% against a 25% target. The
#: alternative was to seat it and size down to 0.170, and that is WORSE on the
#: only comparison that matters -- median return 50,788% at 0.170 against
#: 65,525% for the 25-sleeve book at 0.190 -- so sizing down costs more than the
#: sleeve adds. The operator chose the sleeve and the overshoot. On
#: `_mc_live_frontier risk`, live execution, 600 paths per level:
#:
#:            full 2022-2026             OOS 2025-2026
#:            p10    p95    ret p50      p95    ret p50
#:   0.170   14.30  24.91    50788%    23.67     1413%
#:   0.180   14.86  25.40    66829%    23.68     1571%
#:   0.190   15.2x  ~25.5x   ~87612%   ~23.6x    ~1723%
#:
#: The OOS window stays inside 25% at every level; the full window is the one
#: that overshoots, and it overshoots at p95 only -- p10 through p90 are inside.
#:
#: CALIBRATED AFTER MEMBERSHIP WAS FIXED, NEVER JOINTLY WITH IT. A sizing knob
#: inside a selection grid scores every cell at a size the search itself chose
#: and manufactures its own winner ([[usoil-intraday-fails-twice]]). Members
#: here were picked at 0.135 throughout and the dial was moved once, afterwards.
CANON_RISK_SCALE = 0.130

#: Max simultaneous notional, in multiples of equity. OFF since 2026-08-25.
#:
#: The cap beat a random-refusal control on both windows and was canon at 8x
#: then 6x ([[gross-exposure-cap-beats-its-null]]) -- but at 18 sleeves it stops
#: being a risk control and becomes a queue. Every refusal is FIRST-COME-FIRST-
#: SERVED, so it allocates the book by trade frequency rather than by quality:
#: at 6.0x this book refused 413 entries. Switching it off removes all 413 and
#: the drawdown is held by `risk_scale` instead. `off` and 20x are identical
#: here, which is the evidence that nothing above ~12x ever binds.
CANON_GROSS_CAP = None

#: Per-sleeve MINIMUM equity, in dollars, that `size` sizes and margins
#: against however small the real balance is. Operator decision 2026-08-24.
#:
#: IT APPLIES TO THE MARGIN CEILING ONLY, NEVER TO THE RISK REQUEST. Below its
#: floor a sleeve places one broker-minimum lot and nothing more; `sizing_equity`
#: still reads the real balance, so the floor can unlock a trade but can never
#: make a sleeve bet money the account does not hold.
#:
#: THIS IS STILL THE ONE PLACE IN THE MODULE WHERE THE BACKTEST ASSUMES MARGIN
#: THE ACCOUNT DOES NOT HAVE, AND EVERY NUMBER A FLOORED SLEEVE PRODUCES
#: INHERITS THAT ASSUMPTION. It exists because `SHOWN_EQUITY` provably cannot
#: do this job: it multiplies the risk REQUEST, while the refusal happens at
#: the margin CEILING, which reads real equity. Measured at $400 on
#: de40:floor_pivot, shown multipliers of 1.5, 2, 3, 6, 20 and 100 all return
#: 0.00 lots, with and without `--force-minimum-lot`
#: ([[shown-equity-cannot-fix-a-margin-refusal]]).
#:
#: EACH VALUE IS THAT SLEEVE'S OWN ABSOLUTE MINIMUM, not a round number: the
#: largest margin any of its trades needs for ONE broker-minimum lot, over
#: 2018-01-01..CANON_DATA_END, rounded up to the dollar --
#:
#:     max over trades of  entry x contract_size x fx_to_usd
#:                         x MARGIN_FRACTION x volume_min
#:
#: so it is the smallest floor at which that sleeve refuses NO trade for
#: margin, and a dollar less would refuse at least one. Recompute it when the
#: bars are extended: a new high in any symbol raises its own floor, and DE40's
#: requirement alone has risen about 5 per cent a year (2020 $261, 2022 $281,
#: 2024 $373, 2025 $480, 2026 $503).
#:
#: WHAT IT DOES NOT DO. Exness sizes margin off the REAL balance. A $400
#: account still cannot place 0.07 DE40 lots, so a floored sleeve is a
#: statement about an account holding its floor, not about $400. It also does
#: NOTHING for gross-cap refusals, which are a different mechanism and are made
#: WORSE by flooring, because a floored sleeve carries minimum-lot notional
#: while contributing nothing extra to the shared equity the cap is a multiple
#: of ([[gross-exposure-cap-beats-its-null]]).
#:
#: The three EXTERNAL sleeves -- nq:ofi, nq:drift_vwap, nq:volatility_breakout
#: -- are absent on purpose. They take the `units_per_dollar` path in `replay`,
#: which has no margin ceiling at all and already floors to one step under
#: `--force-minimum-lot`, so a value here would be inert.
#: ONLY SLEEVES WHOSE MINIMUM EXCEEDS THE BALANCE APPEAR HERE. Every canon
#: member's minimum was measured; nine came out BELOW the $400 the account is
#: funded with, and a floor under the balance can never fire -- the marked
#: equity path on the 2025-2026 cold start bottoms at $384.71, below none of
#: them. Listing them anyway would be nine standing licences to assume margin,
#: dormant until some future run started smaller. They are recorded here
#: instead, so the measurement is not lost:
#:
#:     gbpusd:obv_break               $ 360   (max entry 1.44, 493 trades)
#:     gbpjpy:trap                    $ 342   (max entry 217.94, 563 trades)
#:     jp225:swing_donchian           $ 341   (max entry 72325.52, 347 trades)
#:     jp225:swing_break              $ 326   (max entry 69245.47, 726 trades)
#:     ukoil:xma_cross                $ 308   (max entry 123.11, 544 trades)
#:     usdjpy:volume_thrust           $ 257   (max entry 163.49, 1022 trades)
#:     audusd:zscore                  $ 202   (max entry 0.81, 279 trades)
#:     ethusd:confluence              $ 122   (max entry 4859.17, 443 trades)
#:     ethusd:volatility_breakout     $ 121   (max entry 4816.5, 560 trades)
#:
#: Re-measure and promote one into the dict only if a run's equity path
#: actually falls below it.
#: EMPTY SINCE 2026-08-24, and that is the healthy state. de40:floor_pivot was
#: its only entry and is out of the book again -- one forced minimum lot on a
#: $627 balance is 2.80x equity, and on 2025-04-04 it lost $86.10 of a $90.27
#: book drawdown in 31 minutes, 95.4% of the worst fall. Every remaining member
#: needs less margin than the funded $400, so nothing needs a floor. The
#: mechanism stays because the next unaffordable-but-wanted sleeve will need it.
MARGIN_EQUITY_FLOOR = {}


SLEEVE_SCALE = {
    "nq:ofi": 1.0,
    "usdjpy:volume_thrust": 1.0,
    "audusd:zscore": 1.0,
    "nq:drift_vwap": 1.0,
    "jp225:swing_donchian": 1.6,
    "nq:volatility_breakout": 1.0,
    "ethusd:confluence": 1.0,
    "ethusd:volatility_breakout": 1.0,
    "gbpjpy:trap": 1.0,
    "gbpusd:obv_break": 1.0,
    "ukoil:xma_cross": 1.0,
    "jp225:swing_break": 1.0,
    #: Added 2026-08-23 with the de40 replacement. Both sit at 1.0: the pair was
    #: selected on its contribution at unit scale and neither was boosted.
    "msft:xma_cross": 1.0,
    "tsla:floor_pivot": 1.0,
}

#: Per-sleeve ceiling on the equity a sleeve may SIZE against,
#: `combined_book`'s `SIZING_EQUITY_CAP`. Absent means uncapped. This is the
#: knob that stops a sleeve riding 20 months of compounding into a position
#: size the account was never meant to carry.
#: $1,500 per canon member. The book compounds $1,000 into four figures, and
#: without a ceiling a late trade is sized many times an early one, which turns
#: the ORDER of a sleeve's returns into its contribution. That is what made
#: ethusd:swing_ma -- the best standalone cell in the pool -- post a negative
#: contribution before this was applied.
SIZING_EQUITY_CAP = {
    "nq:ofi": 1500.0,
    "usdjpy:range_expansion": 1500.0,
    "audusd:zscore": 1500.0,
    "nq:drift_vwap": 1500.0,
    "tsla:failed_break": 1500.0,
    "xaueur:key_reversal": 1500.0,
    "tsla:ib": 1500.0,
    "fr40:squeeze": 1500.0,
    "xaueur:orb": 1500.0,
    "de40:donchian": 1500.0,
    "de40:consecutive": 1500.0,
    "de40:ib": 1500.0,
    "nq:volume_thrust": 1500.0,
    "ethusd:range_expansion": 1500.0,
    "ethusd:failed_break": 1500.0,
    "ethusd:drift_vwap": 1500.0,
    "jp225:pdr": 1500.0,
    "usdjpy:volume_thrust": 1500.0,
    "ethusd:swing_ma": 1500.0,
    "eurjpy:vwap": 1500.0,
    "xauaud:momentum": 1500.0,
    "jp225:swing_donchian": 1500.0,
    "xagaud:failed_break": 1500.0,
    "stoxx50:climax": 1500.0,
    "de40:floor_pivot": 1500.0,
    "nq:volatility_breakout": 1500.0,
    "ethusd:confluence": 1500.0,
    "ethusd:volatility_breakout": 1500.0,
    "gbpjpy:trap": 1500.0,
    "es:volatility_breakout": 1500.0,
    "gbpusd:obv_break": 1500.0,
    "ukoil:xma_cross": 1500.0,
    "jp225:swing_break": 1500.0,
}

#: A book-wide sizing ceiling in dollars, applied to every sleeve that has no
#: entry of its own in `SIZING_EQUITY_CAP`. Set from `--sizing-cap`. `None`
#: leaves the book uncapped, which is the historical behaviour.
GLOBAL_SIZING_CAP = None

#: Explicitly bypass every entry in `SIZING_EQUITY_CAP`. Historically `None`
#: meant "use the per-sleeve map", so there was no CLI spelling for a genuinely
#: uncapped book and old uncapped research could not be reproduced by `main`.
UNCAPPED = True
FORCE_MINIMUM_LOT = True

#: Largest share of the book's worst marked fall any ONE sleeve may bear, set
#: from `--max-dd-share`. `None` disables the gate. This exists because the
#: 10-sleeve book's 21.4% drawdown was 85.5% a single ethusd:swing_ma position
#: marked across one three-hour window: adding sleeves cannot dilute that, and a
#: minimum trade count does not detect it.
MAX_DD_CONCENTRATION = None

#: Set from `--fair-cap`. Splits the `gross_cap` budget per sleeve instead of
#: running one shared pool. The shared pool is first-come-first-served, so the
#: highest-frequency sleeve holds the exposure when the others want it: a 6x
#: shared cap refused 1,544 entries, RAISED drawdown 22.3% -> 23.0% and pushed
#: four sleeves below their standalone. `replay` has always supported this; it
#: was simply never reachable from the command line.
FAIR_CAP = False



def sizing_caps(members):
    """`SIZING_EQUITY_CAP`, with `GLOBAL_SIZING_CAP` filled in for the rest."""
    if UNCAPPED:
        return {}
    if GLOBAL_SIZING_CAP is None:
        return SIZING_EQUITY_CAP
    caps = dict(SIZING_EQUITY_CAP)
    for member in members:
        caps.setdefault(f"{member['symbol']}:{member['family']}",
                        GLOBAL_SIZING_CAP)
    return caps


BAR = 30
DAY = 86_400


# --------------------------------------------------------------------------- #
# candidates
# --------------------------------------------------------------------------- #

_BUY_HOLD = {}


def buy_hold(symbol, lo=ef.IS_END, hi=ef.OOS_END):
    """`(return_pct, max_dd_pct)` for simply holding the instrument.

    The mandatory second control ([[buy-and-hold-is-the-second-mandatory-control]]),
    and the ONLY meaningful benchmark for a sleeve whose return is mostly drift.
    A coin-flip null does not price drift away -- randomising direction still
    leaves a rule that is often long in a rising market -- so a drift-exploiting
    cell can clear its null and still be worse than owning the thing.

    Drawdown is taken high-to-low on the bars, not close-to-close, so it is
    comparable with the mark-to-market figure the book reports rather than with
    the optimistic closed-trade one.

    Memoised per symbol: it is called once per candidate during screening and
    every call would otherwise rebuild that symbol's whole context, which is by
    far the most expensive thing in the screen.
    """
    cached = _BUY_HOLD.get((symbol, lo, hi))
    if cached is not None:
        return cached
    if symbol not in ef.INSTRUMENTS:
        # `candidates` screens before `build` resolves, so resolve on demand.
        # Stale spreads are accepted here because the caller decides that
        # policy; by this point every symbol has a tick-sampled spread anyway.
        ef.resolve(symbol, allow_stale=True)
    # Shares `_context`'s per-symbol cache with `sleeve_trades`. Its own
    # `_BUY_HOLD` memo only stops repeat calls for the SAME symbol; without
    # this, screening 33 candidates still rebuilt one context per distinct
    # symbol here and a second one there.
    bars, _ctx = _context(symbol)
    window = [b for b in bars if lo <= b[ef.TS] < hi]
    if len(window) < 2:
        _BUY_HOLD[(symbol, lo, hi)] = (float("nan"), float("nan"))
        return _BUY_HOLD[(symbol, lo, hi)]
    entry = window[0][ef.O]
    peak, worst = entry, 0.0
    for bar in window:
        peak = max(peak, bar[ef.H])
        worst = max(worst, (peak - bar[ef.L]) / peak if peak > 0 else 0.0)
    out = (100.0 * (window[-1][ef.C] / entry - 1.0), 100.0 * worst)
    _BUY_HOLD[(symbol, lo, hi)] = out
    return out


def null_best(symbol, family):
    """`(best coin-flip holdout return, seeds that found a cell)`."""
    path = os.path.join(RESULTS, f"exness_families_null_{symbol}_{BAR}m.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as handle:
        control = json.load(handle)["null_control"]
    if family not in control:
        return None, None
    rows = [r for r in (control.get(family) or []) if r]
    if not rows:
        return None, 0
    return max(r["out_of_sample"]["return_pct"] for r in rows), len(rows)


#: Where the eligible cells come from: `results/exness/`, one JSON per
#: survivor, plus `SURVIVORS.json` as its index.
#:
#: THIS USED TO BE A GLOB OVER `RESULTS` AND THAT WAS A BUG. The old test was
#: `name.startswith("exness_families_") and name.endswith(f"_{BAR}m.json")`,
#: which matches the ORIGINAL study wave and nothing else -- the two later
#: waves were sealed as `*_30m_new.json` and `*_30m_combined.json` and were
#: silently rejected by the `endswith`. So every book this module ever built
#: chose from 20 eligible cells while 164 survivors existed, and no research
#: done after the first wave could reach the book however good it was.
#:
#: The survivor folder is the right source rather than a wider glob because it
#: is already the null-tested set: a file is only in it if it won its in-sample
#: search, cleared the 2025-01-01..2026-08-16 holdout, AND beat its own
#: coin-flip null. That is exactly the three-part test `candidates` was
#: applying by hand, with `null_best` no longer needing to guess at a null
#: file name that changed format between waves.
SURVIVOR_DIR = os.path.join(RESULTS, "exness")


def _retuple(value):
    """JSON turns every tuple into a list; several families index on theirs.

    `macd_hist` reads its indicator as `ctx["macd_hist"][params["macd_set"]]`
    and a list is not hashable, so a param round-tripped through JSON raises
    where the sealed in-memory param works. The original wave never hit this
    because none of its families carried a tuple parameter -- which is another
    way of saying it only shows up now that the later waves are visible.
    """
    if isinstance(value, list):
        return tuple(_retuple(v) for v in value)
    if isinstance(value, dict):
        return {k: _retuple(v) for k, v in value.items()}
    return value


def candidates(min_return=None, min_trades=None, min_is_t=None):
    """Every sealed cell that beat its null AND is economically worth trading.

    The three size gates are arguments rather than constants so the effect of
    each can be shown instead of asserted. They matter more than they look:
    `MIN_IS_T` alone is what excludes xauaud:high_52w, the largest single
    holdout return in the whole study (+144.2%) and also the one whose coin flip
    reached +101.9% on an in-sample t-stat of 0.50.
    """
    min_return = MIN_OOS_RETURN if min_return is None else min_return
    min_trades = MIN_OOS_TRADES if min_trades is None else min_trades
    min_is_t = MIN_IS_T if min_is_t is None else min_is_t

    cells = []
    for name in sorted(os.listdir(SURVIVOR_DIR)):
        if not name.endswith(f"_{BAR}m.json"):
            continue
        with open(os.path.join(SURVIVOR_DIR, name), encoding="utf-8") as handle:
            cells.append(json.load(handle))

    # RESOLVE EVERY SYMBOL BEFORE BUILDING ANY CONTEXT.
    #
    # `benchmark_closes` calls `ef.register_series`, which installs a DATA-ONLY
    # spec carrying no `multiplier`, `tick_value` or `volume_min`. The stock
    # cells sort first and every one of them benchmarks against `nq`, so by the
    # time an NQ cell is reached `nq` is already in `INSTRUMENTS` -- and
    # `buy_hold` only resolves a symbol that is ABSENT. The reference-only spec
    # therefore went into `_CTX_CACHE["nq"]` and stayed there, so every NQ cell
    # died on `KeyError: 'multiplier'` inside `ef.backtest`, and a later
    # `resolve` could not undo it because the context was already cached.
    # A REFERENCE-ONLY ENTRY IS NOT "ALREADY RESOLVED", and skipping it on the
    # strength of the key being present is how the KeyError came back. Anything
    # that builds an ETHUSD context BEFORE this loop -- `_mc_live.members()`
    # does, and so does any earlier `build` in the same process -- registers
    # `btc` as ETHUSD's benchmark first, and the loop then walked past it and
    # left the data-only spec standing for every btc cell that followed.
    unresolved = {}
    for cell in cells:
        symbol = cell["symbol"]
        seen = ef.INSTRUMENTS.get(symbol)
        if (seen is not None and not seen.get("reference_only"))                 or symbol in unresolved:
            continue
        try:
            ef.resolve(symbol, allow_stale=True)
            # A context built against the data-only spec has already copied it
            # into `ctx["cfg"]`, and `backtest` reads the copy, so fixing
            # `INSTRUMENTS` alone would leave the poisoned context serving.
            _CTX_CACHE.pop((symbol, BAR, False), None)
            _CTX_CACHE.pop((symbol, BAR, True), None)
        except SystemExit as exc:
            unresolved[symbol] = str(exc)

    out = []
    for cell in cells:
        symbol, family = cell["symbol"], cell["family"]
        if f"{symbol}:{family}" in EXCLUDE or symbol in EXCLUDE_SYMBOLS:
            continue
        if symbol in unresolved:
            continue
        oos, stat = cell["out_of_sample"], cell["in_sample"]
        if (oos["return_pct"] < min_return
                or oos["trades"] < min_trades):
            continue

        # TWO WAYS IN, because `MIN_IS_T` alone measured the wrong thing.
        #
        # A two-sided rule tested in a one-directional market can only ever
        # fire one of its sides, so its in-sample t-stat describes the
        # market, not the rule. xauaud:high_52w was 100% long in-sample
        # (2022-2024 gold-AUD made nothing but new highs) and scored t=0.50;
        # on the holdout, which contains a 27.5% slide, it went 29% short
        # and scored t=2.00 with 79% of gross as edge rather than 21%.
        #
        # So a sleeve is admitted if it shows a per-trade edge OR if it
        # beats simply owning the instrument on return per unit of
        # drawdown. The second is "drift exploitation": the return may be
        # mostly the market's own move, but capturing it at a fraction of
        # the market's drawdown is a real and tradeable thing, and
        # buy-and-hold is the honest benchmark for it.
        edge_t = stat.get("edge_vs_drift_t_stat") or 0.0
        bh_return, bh_dd = buy_hold(symbol)
        bh_ratio = (bh_return / bh_dd if bh_dd and bh_dd == bh_dd
                    and bh_dd > 0 else float("nan"))
        own_ratio = (oos["return_pct"] / oos["max_dd_pct"]
                     if oos["max_dd_pct"] > 0 else float("inf"))
        # The drift route requires there to BE drift. Where buy-and-hold
        # lost money the ratio is negative and `> bh_ratio * 1.5` is
        # satisfied by any positive return at all -- which let
        # ethusd:key_reversal (t=1.33, +6.0%) in against ETH's -43.4% hold.
        # Beating a falling market is not drift exploitation, it is just
        # having an edge, so those sleeves must qualify on `edge_t`.
        beats_holding = (bh_ratio == bh_ratio and bh_ratio > 0
                         and own_ratio > bh_ratio * DRIFT_MARGIN)
        if edge_t < min_is_t and not beats_holding:
            continue
        null = cell.get("null_control") or {}
        out.append({
            "symbol": symbol, "family": family,
            "params": _retuple(cell["params"]),
            "asset_class": cell["asset_class"],
            "oos_return": oos["return_pct"], "oos_dd": oos["max_dd_pct"],
            "oos_trades": oos["trades"], "oos_pf": oos["pf"],
            "is_t": edge_t, "wave": cell.get("study_wave"),
            "flip": null.get("best_null_oos_return_pct"),
            "null_seeds": null.get("null_seeds_run"),
            "buy_hold_return": round(bh_return, 1),
            "buy_hold_dd": round(bh_dd, 1),
            "buy_hold_ratio": round(bh_ratio, 2) if bh_ratio == bh_ratio else None,
            "own_ratio": round(own_ratio, 2),
            "admitted_as": "edge" if edge_t >= min_is_t else "drift",
            "fill_rate": oos.get("fill_rate", 0.0),
            "signals": oos.get("signals", 0),
        })
    out.sort(key=lambda row: -row["oos_return"])
    return out


def candidate_rows_exact(keys):
    """Load named sealed rows without resolving the entire research pool.

    Exact-book validation already knows membership. Running `candidates()`
    here needlessly resolves every symbol just to reconstruct a handful of
    parameter dictionaries, and one unavailable unrelated source can block the
    whole report.
    """
    wanted = set(keys)
    found = {}
    for name in sorted(os.listdir(SURVIVOR_DIR)):
        if not name.endswith(f"_{BAR}m.json"):
            continue
        with open(os.path.join(SURVIVOR_DIR, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        key = f"{cell['symbol']}:{cell['family']}"
        if key not in wanted:
            continue
        oos = cell.get("out_of_sample") or {}
        found[key] = {
            "symbol": cell["symbol"], "family": cell["family"],
            "params": _retuple(cell["params"]),
            "asset_class": cell.get("asset_class", "unknown"),
            "oos_return": oos.get("return_pct", float("nan")),
            "oos_dd": oos.get("max_dd_pct", float("nan")),
            "oos_trades": oos.get("trades", 0),
            "oos_pf": oos.get("pf", float("nan")),
        }
        if len(found) == len(wanted):
            break
    return found


# --------------------------------------------------------------------------- #
# per-sleeve trades
# --------------------------------------------------------------------------- #

#: One `(bars, ctx)` per symbol for the life of the process. `ef.context`
#: reloads the bars and rebuilds every indicator array on each call, and a
#: 33-candidate pool covers only ~15 distinct symbols, so better than half those
#: builds were rebuilding something identical. Safe to share: `ef.backtest`
#: reads the context and keeps its own per-run state, and the key carries the
#: bar size so a different timeframe cannot collide.
_CTX_CACHE = {}

#: Fill every sleeve at the BROKER's prices instead of the vendor's. Off by
#: default, so every sealed result in this module reproduces unchanged.
#:
#: WHAT IT DOES NOT COVER, and this has to be read with any number it produces.
#: `nq:ofi` and `nq:drift_vwap` do not come through `sleeve_trades` at all --
#: they are imported from `combined_book`'s order lists -- so they stay on
#: vendor prices whatever this is set to. A book run with it on is therefore
#: eighteen sleeves on broker fills and two on vendor fills, not twenty.
#:
#: And `exness_broker_fills` flags three more sleeves whose broker series is a
#: different INSTRUMENT rather than a different quote (both NQ cells against
#: USTEC, ukoil against Exness UKOIL). Their fills are swapped here anyway,
#: because refusing would silently change the book's membership; the flag lives
#: in the report, not in the replay.
FILL_FEED = False
_FILL_CACHE = {}


#: RESCALE a symbol onto the vendor's price level when its broker series is a
#: different instrument rather than a different quote of the same one, instead
#: of filling it at a level the strategy's point-denominated stop does not fit.
#:
#: THIS DEFAULT IS LOAD-BEARING AND WAS MEASURED, NOT ASSUMED. Filling the
#: mismatched symbols raw took the book's marked drawdown from 12.8% to 30.9%,
#: with `nq:level_confluence` bearing 75.9% of the fall in a single day --
#: because its stop is a point distance taken off `nq_1m` and spent at a
#: `USTEC` price 2.6% away, which is a different fraction of the instrument and
#: therefore a different strategy. That number measures the mismatch, not the
#: broker's execution. `--force-mismatched-fills` reproduces it.
FILL_FEED_SKIP_MISMATCH = True


def _fill_bars(symbol, bars):
    """Broker-priced counterpart of `bars`, or `None` when the switch is off."""
    if not FILL_FEED:
        return None
    if broker_signals_for(symbol):
        # Already deciding on the broker's bars, so they are also the fill
        # bars. Swapping again would align the series against itself.
        return None
    key = (symbol, BAR)
    if key not in _FILL_CACHE:
        from sandbox.research import exness_broker_fills as bf

        if not bf.has_fills(symbol):
            _FILL_CACHE[key] = None
        else:
            window = (ef.IS_END, ef.OOS_END)
            if FILL_FEED_SKIP_MISMATCH:
                fills, stats = bf.auto_fills(symbol, bars, BAR, window)
                if stats["rescaled"]:
                    print(f"  {symbol}: broker series rescaled onto the vendor "
                          f"level ({stats['level_shift_before_bp']:+.0f}bp -> "
                          f"{stats['level_shift_bp']:+.0f}bp) so the point "
                          "stop is the same fraction of both")
            else:
                fills, _ = bf.aligned_fills(symbol, bars, BAR, window)
            _FILL_CACHE[key] = fills
    return _FILL_CACHE[key]
_ES_TRANSITION = None


def _validate_es_transition():
    """Return and validate the calendar-day ES source handoff.

    The final legacy minute and first Yahoo 30m row must share a calendar day.
    The Yahoo segment must also have one row per timestamp. The importer already
    replaces the whole transition DAY partition; these checks keep a later
    manual append from silently reintroducing an overlap.
    """
    global _ES_TRANSITION
    if _ES_TRANSITION is not None:
        return _ES_TRANSITION

    import numpy

    # `parquet_store` before `pyarrow.compute`: it disarms the WMI lookup that
    # otherwise hangs the first pandas import on this machine.
    from sandbox import parquet_store as store
    import pyarrow.compute as pc

    legacy_span = store.bounds("es_1m")
    if legacy_span is None:
        raise RuntimeError("ES transition requires es_1m rows")

    # The Yahoo segment is the part of `es_30m` tagged `ES=F`; the rest of the
    # table is the legacy vendor series and its timestamps are not part of the
    # handoff. Only the tag and the timestamp are read, so this stays a column
    # scan rather than a table load.
    stamps, chunk = store.scan("es_30m", columns=["underlying"])
    yahoo = stamps[
        pc.equal(chunk["underlying"], "ES=F").to_numpy(zero_copy_only=False)
    ]
    if not len(yahoo):
        raise RuntimeError("ES transition requires Yahoo-tagged es_30m rows")

    legacy_last = legacy_span[1]
    yahoo_first = store.iso_text(int(yahoo.min()))
    yahoo_last = store.iso_text(int(yahoo.max()))
    yahoo_day = yahoo_first[:10]
    if legacy_last != yahoo_day:
        raise RuntimeError(
            f"ES source gap: es_1m ends {legacy_last}, Yahoo es_30m begins {yahoo_day}"
        )

    # One row per timestamp on or after the handoff. The importer replaces the
    # whole transition DAY partition; this catches a later manual append
    # reintroducing an overlap.
    after = numpy.sort(yahoo[yahoo >= store.to_nanoseconds(yahoo_day)])
    repeated = after[:-1][after[1:] == after[:-1]]
    if len(repeated):
        raise RuntimeError(
            f"es_30m has a duplicated timestamp on or after the Yahoo "
            f"transition {yahoo_day}: {store.iso_text(int(repeated[0]))}"
        )

    _ES_TRANSITION = {
        "legacy_last": legacy_last,
        "yahoo_first": yahoo_first,
        "yahoo_last": yahoo_last,
        "transition_day": yahoo_day,
    }
    return _ES_TRANSITION


#: DECIDE on the broker's bars too, not only fill on them. `FILL_FEED` answers
#: "what does the vendor price cost me at execution"; this answers the harder
#: question "does the edge exist in the broker's own series at all", which is
#: the one that matters for a book that will only ever see Exness prices.
#:
#: It is a STRICTLY stronger test and a strictly worse-conditioned one. Every
#: sleeve was selected on the vendor series, so re-deciding on the broker's is
#: out-of-sample for the signal in a way nothing else here is -- and the broker
#: history is shorter, so warm-up windows and rolling statistics start later.
#: Read a fall in return here as "the cell was partly fitted to vendor noise",
#: not as an execution cost.
SIGNAL_FEED = False

#: Retire the sleeves that go non-positive under `SIGNAL_FEED` instead of
#: carrying them. OFF by default: keeping a losing member is what holds the
#: membership fixed, and only a fixed membership isolates the signal feed.
DROP_SIGNAL_CASUALTIES = False

#: Carry the VENDOR's volume into the broker-signal bars instead of the
#: broker's own.
#:
#: THE TWO FEEDS AGREE ON PRICE AND DO NOT AGREE ON VOLUME AT ALL. Measured
#: over the holdout, the close differs by 0.13-0.30bp on the FX pairs -- the
#: same series to within a fifth of a pip -- while the volume differs by a
#: factor of 0.21x to 7606x with correlation as low as 0.33. Dukascopy
#: publishes a tick COUNT and MetaTrader publishes `tick_volume`; they are not
#: the same measurement and no rescale reconciles them, because the
#: disagreement is in the shape, not the level. `xalusd` is the control: its
#: vendor table is already an Exness feed and it reads 1.0016x at correlation
#: 1.000.
#:
#: So a volume-reading family re-decided on broker bars is not being tested on
#: a different venue's prices, it is being fed a different statistic. That is
#: an apples-to-oranges comparison, not evidence about the edge, and it is what
#: killed `gbpusd:obv_break` and cost `usdjpy:volume_thrust` a fifth of its
#: P&L. Keeping the vendor's volume leaves the test measuring what it is meant
#: to measure: whether the PRICE series supports the signal.
BROKER_SIGNAL_KEEP_VENDOR_VOLUME = True

#: Symbols that keep VENDOR signals even in `SIGNAL_FEED` mode.
#:
#: `nq` because the broker does not quote the instrument at all: `USTEC` is a
#: cash CFD and every NQ sleeve reads a futures series, two of them through the
#: level-two feature table, which has no Exness counterpart. Deciding on USTEC
#: would not be the same strategy tested on a different feed, it would be a
#: different strategy.
#:
#: `ethusd` because the vendor series is Binance -- the venue with the volume,
#: the depth and the 24/7 print history the signals are built out of. Exness
#: quotes ETHUSD as a CFD derived from it, so its bars carry the broker's
#: quoting behaviour but not the market's own microstructure.
#:
#: Their FILLS still follow `FILL_FEED`, so the two switches compose: with both
#: on, the book is broker-decided and broker-filled everywhere except these
#: two, which stay vendor-decided and broker-filled.
BROKER_SIGNAL_EXCLUDE = ("nq", "ethusd")


def broker_signals_for(symbol):
    return SIGNAL_FEED and symbol not in BROKER_SIGNAL_EXCLUDE


def _context(symbol):
    key = (symbol, BAR, broker_signals_for(symbol))
    if key not in _CTX_CACHE:
        if symbol == "es":
            _validate_es_transition()
            # Yahoo's native 30m schema calls the activity column `volume`;
            # Exness-native M30 tables call it `tick_volume`.
            ef.INSTRUMENTS[symbol]["volume_column"] = "volume"
        ef.BAR_MINUTES = BAR
        full = None
        if broker_signals_for(symbol):
            from sandbox.research import exness_broker_fills as bf

            if bf.has_fills(symbol):
                # UNFILTERED bars, exactly what `all_bars` hands `context`:
                # the overnight and gap anchors are built from the
                # out-of-session buckets, and `context` does the session
                # filtering itself.
                full = bf.broker_bars(symbol, BAR)
                if BROKER_SIGNAL_KEEP_VENDOR_VOLUME:
                    vendor_volume = {row[ef.TS]: row[ef.V]
                                     for row in ef.all_bars(symbol, "validate", BAR)}
                    full = [row[:5] + (vendor_volume.get(row[ef.TS], row[5]),)
                            for row in full]
                first = time.strftime("%Y-%m-%d",
                                      time.gmtime(full[0][0])) if full else "-"
                print(f"  {symbol}: signals AND fills on "
                      f"{bf.fill_table(symbol)} from {first}")
        _CTX_CACHE[key] = ef.context(symbol, "validate", BAR, full_bars=full)
    return _CTX_CACHE[key]


_SLEEVE_MEMO = {}

#: Charge every sleeve the spread that actually prevailed at its own entry, and
#: fill it at the price the market had reached after its own feed's publish lag.
#:
#: OFF BY DEFAULT AND SET FROM THE ENVIRONMENT, like `EXNESS_DECISION_LAG_BARS`
#: and for the same reason: the book is re-run one SUBPROCESS per configuration,
#: because `_SLEEVE_MEMO` does not key on any of this.
#:
#: WHAT IT REPLACES. The old broker-fill swap read `exness_<broker>_1m`, and
#: those tables were deleted on 2026-08-30. This is the tick-era successor and
#: it is NOT the same measurement: it covers 2026-01-01 onward instead of 2020,
#: and it moves the entry by what the broker's quotes DID over the lag rather
#: than replacing the whole bar. Bars the ticks do not reach keep the vendor
#: open and the constant spread, so the pre-2026 years are untouched.
TICK_COSTS = os.environ.get("EXNESS_TICK_COSTS", "") == "1"

#: Simulate a broker-side stop-loss and take-profit on the MT5 order.
#:
#: False is the account as it is wired TODAY: no `sl`/`tp` in the payload, so
#: every exit is a market order the runtime sends after its candle rolls. True
#: is the counterfactual the bridge change would buy. Set by
#: `fill_models.exness.book`; it does nothing unless TICK_COSTS is on,
#: because with no exit map there is no late fill to exempt anything from.
BROKER_STOPS = False

#: Which `fill_models.exness` maps file to read, or None for that module's
#: default feed. Set by `fill_models.exness.book` so a run cannot silently
#: replay the wrong series.
MAPS_OVERRIDE = None

_TICK_COST_CACHE = {}


def _tick_costs(symbol, bars):
    """`(spread_by_ts, entry_by_ts, exit_by_ts)`, or `(None, None, None)`.

    READ OFF DISK, NEVER COMPUTED HERE. `fill_models.exness precompute`
    reduces each tick table to a few thousand floats and writes them out; this
    process only loads that, so a book run never opens a tick table.

    THREE MAPS, NOT TWO. The third is the EXIT price, and it is the one that
    changes a result rather than trimming it: this account carries no
    broker-side stop, so every exit is a market order the runtime sends once the
    bar it was watching has closed. The map says where the market was when that
    order landed, and `ef.backtest` spends it instead of the idealised
    `min(open, stop)`.

    The reduction is cheap -- five seconds a symbol measured standalone. Book
    runs that embedded it were nevertheless unusably slow on 2026-08-31 and the
    reason was NOT established; an earlier comment here blamed the external NQ
    sleeves for holding `dbento_nq_ticks` in memory, which is false (`data.py`
    streams it and caches the reduction). Splitting the work out is what made
    the run finish, not any confirmed diagnosis.

    The maps cover EVERY bar, not only the ones that turned out to be entries.
    The trade list changes as soon as the fills do -- a different entry moves
    the stop, which moves the exit, which can move the next entry -- so a map
    built from the unperturbed run's entries would be missing exactly the bars
    the perturbed run needs.
    """
    if not TICK_COSTS:
        return None, None, None
    if not _TICK_COST_CACHE:
        tc = ef.fill_model()

        # `MAPS_OVERRIDE` names WHICH maps file, so a book run states its feed
        # instead of inheriting whatever `precompute` wrote last. None means the
        # default feed, which is the broker's own minute bars.
        loaded = (tc.load_maps(MAPS_OVERRIDE) if MAPS_OVERRIDE
                  else tc.load_maps())
        if not loaded:
            raise SystemExit(
                "EXNESS_TICK_COSTS=1 but no maps on disk -- run "
                "`py -m sandbox.research.fill_models.exness precompute` first")
        _TICK_COST_CACHE.update(loaded)
        print(f"  tick cost maps for {len(loaded)} symbols loaded from disk",
              flush=True)
    spreads, entries, exits = _TICK_COST_CACHE.get(symbol,
                                                   (None, None, None))
    if spreads is None:
        print(f"  {symbol}: no tick table -- left on the constant spread "
              f"and the bar open", flush=True)
    return spreads, entries, exits


def sleeve_trades(row, null_seed=None, lo=None, hi=None, shown=1.0):
    """This cell's holdout trades, carrying everything needed to re-size them.

    Run at unit scale is not possible here -- `quantity` can refuse a trade
    outright below `volume_min`, and that refusal is part of the strategy on a
    small account ([[four-hundred-dollars-selects-the-sleeves-for-you]]). So the
    trade list is taken as the strategy generated it, and the portfolio replay
    re-derives lots from the shared equity using the recorded stop distance.
    """
    params_key = tuple(sorted(row["params"].items())) if isinstance(row.get("params"), dict) else (tuple(row["params"]) if isinstance(row.get("params"), (list, tuple)) else row.get("params"))
    key = (row["symbol"], row["family"], params_key, null_seed, lo, hi, shown, FILL_FEED, SIGNAL_FEED, TICK_COSTS, BROKER_STOPS, ef.DECISION_LAG_BARS)
    if key in _SLEEVE_MEMO:
        res, log, bars, ctx = _SLEEVE_MEMO[key]
        return res, [dict(t) for t in log], bars, ctx

    symbol = row["symbol"]
    bars, ctx = _context(symbol)
    spreads, entries, exits = _tick_costs(symbol, bars)
    # `WEEKEND_ONLY` gates this cell's entries alone; the module global is put
    # back so no other sleeve inherits it.
    saved_days = ef.ENTRY_DAYS
    if f"{symbol}:{row['family']}" in WEEKEND_ONLY:
        ef.ENTRY_DAYS = frozenset({5, 6})
    try:
        result = ef.backtest(row["family"], bars, ctx, row["params"],
                             lo=ef.IS_END if lo is None else lo,
                             hi=ef.OOS_END if hi is None else hi,
                             initial=ef.INITIAL_BALANCE * shown,
                             null_seed=null_seed, include_trades=True,
                             fill_bars=_fill_bars(symbol, bars),
                             tick_spreads=spreads, entry_prices=entries,
                             exit_prices=exits, broker_stops=BROKER_STOPS)
    finally:
        ef.ENTRY_DAYS = saved_days
    for trade in result["trade_log"]:
        trade["symbol"] = symbol
        trade["sleeve"] = f"{symbol}:{row['family']}"
    _SLEEVE_MEMO[key] = (result, result["trade_log"], bars, ctx)
    return result, result["trade_log"], bars, ctx


def daily_stream(trades):
    """Realised P&L per calendar day, keyed by day index.

    Attributed to the EXIT day, which is when the money actually moved. Using
    the entry day would smear a swing sleeve's result backwards and correlate it
    with whatever else opened that morning rather than with what it earned.
    """
    stream = {}
    for trade in trades:
        day = trade["exit_ts"] // DAY
        stream[day] = stream.get(day, 0.0) + trade["points"] / trade["entry"]
    return stream


def _pearson(a, b):
    if len(a) < 3:
        return 0.0
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    va = sum((x - mean_a) ** 2 for x in a)
    vb = sum((x - mean_b) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def correlation(left, right):
    """Plain Pearson over the days EITHER traded. Reported for reference only.

    Not used for admission: it scores winning together as a defect, which is not
    what the book is trying to avoid.
    """
    days = sorted(set(left) | set(right))
    return _pearson([left.get(day, 0.0) for day in days],
                    [right.get(day, 0.0) for day in days])


def down_rho(left, right):
    """Pearson restricted to days on which at least one of the pair LOST.

    A day where both sat flat carries no information about shared risk, and a
    day where both won is the outcome the book wants; including either only
    dilutes the statistic toward zero and makes two genuinely coupled sleeves
    look safe.
    """
    days = [day for day in set(left) | set(right)
            if left.get(day, 0.0) < 0 or right.get(day, 0.0) < 0]
    days.sort()
    return _pearson([left.get(day, 0.0) for day in days],
                    [right.get(day, 0.0) for day in days])


def loss_lift(left, right):
    """How much more often the pair loses together than independence predicts.

    Computed only over days BOTH sleeves traded, because a day one of them sat
    out is not evidence either way about whether they fail together. Returns
    1.0 when there is no overlap to judge, which admits the pair -- two sleeves
    that never trade on the same day cannot sink the account on the same day.
    """
    days = sorted(set(left) & set(right))
    if len(days) < 10:
        return 1.0
    a = [left[day] < 0 for day in days]
    b = [right[day] < 0 for day in days]
    pa, pb = sum(a) / len(a), sum(b) / len(b)
    if pa <= 0 or pb <= 0:
        return 0.0
    joint = sum(x and y for x, y in zip(a, b)) / len(days)
    return joint / (pa * pb)


def bad_overlap(left, right, months=TAIL_MONTHS):
    """Of `left`'s worst months, the share that were also losing for `right`.

    The plain-language version of the question: when this one has its worst
    stretches, is the other one bleeding too?
    """
    def by_month(stream):
        out = {}
        for day, value in stream.items():
            key = datetime.fromtimestamp(day * DAY, tz=timezone.utc)
            out[(key.year, key.month)] = out.get((key.year, key.month), 0.0) + value
        return out

    lm, rm = by_month(left), by_month(right)
    worst = sorted(lm, key=lambda k: lm[k])[:months]
    if not worst:
        return 0.0
    return sum(1 for key in worst if rm.get(key, 0.0) < 0) / len(worst)


def choose_members(rows, streams, limit=MAX_MEMBERS,
                   max_rho=MAX_DOWN_RHO, max_lift=MAX_LOSS_LIFT):
    """Greedy: best first, admitted only if it does not lose WITH the book.

    Both gates are pairwise against every sitting member. A candidate that is
    fine against four members and coupled to the fifth is refused -- the point
    is that no two sleeves go down together, not that the average is acceptable.
    """
    chosen, rejected = [], []
    for row in rows:
        key = f"{row['symbol']}:{row['family']}"
        # SIGNED, not absolute. A negative downside correlation means this
        # sleeve tends to make money exactly when the other is bleeding, which
        # is the single most valuable thing a new member can do. An earlier
        # version gated on |rho| and threw those away as if they were the same
        # defect as moving together -- it refused de40:consecutive at -0.518,
        # the best hedge on the list.
        worst_rho = -1.0
        worst_lift = 0.0
        rho_against = lift_against = None
        for member in chosen:
            other = f"{member['symbol']}:{member['family']}"
            rho = down_rho(streams[key], streams[other])
            lift = loss_lift(streams[key], streams[other])
            if rho > worst_rho:
                worst_rho, rho_against = rho, other
            if lift > worst_lift:
                worst_lift, lift_against = lift, other
        if not chosen:
            worst_rho, worst_lift = 0.0, 1.0
        if chosen and worst_rho > max_rho:
            rejected.append({**row, "blocked_by": rho_against,
                             "reason": "down_rho",
                             "down_rho": round(worst_rho, 3),
                             "loss_lift": round(worst_lift, 3)})
            continue
        if chosen and worst_lift > max_lift:
            rejected.append({**row, "blocked_by": lift_against,
                             "reason": "loss_lift",
                             "down_rho": round(worst_rho, 3),
                             "loss_lift": round(worst_lift, 3)})
            continue
        chosen.append({**row, "max_down_rho": round(worst_rho, 3),
                       "max_loss_lift": round(worst_lift, 3)})
        if len(chosen) >= limit:
            break
    return chosen, rejected


# --------------------------------------------------------------------------- #
# the joint replay
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# sleeves that are not cfd_families cells
# --------------------------------------------------------------------------- #

#: Sleeves imported from `combined_book`. They are not `cfd_families` cells
#: and have no sealed JSON here, so they carry their own sizing rule:
#: `units_per_dollar` already folds in the strategy's risk fraction, stop
#: distance and margin ceiling, so the replay multiplies it by live equity
#: instead of calling `size`.
EXTERNAL = ("nq:ofi", "nq:drift_vwap", "ethusd:drift_vwap",
            "nq:volatility_breakout", "nq:level_confluence", "nq:sar")

#: EVERY NQ SLEEVE IS IMPORTED, AND THAT IS WHAT PUTS IT ON LEVEL TWO. The
#: `nq:` branch of `external_trades` replays a cell as TWO causal segments --
#: nq_1m up to the first timestamp that exists in the L2 feed, the L2 bars
#: after it -- and marks each against its own market. A cell left on the
#: native families path instead reads nq_1m for the whole window and never
#: sees level two at all, which is how `nq:sar` entered the book on the wrong
#: series. The two price levels are NOT concatenated: joining them invents a
#: gap that contaminates VWAP, momentum and marked drawdown
#: ([[nq-has-two-incompatible-price-series]]).


#: Source tables behind the imported sleeves. Their fingerprint is part of every
#: cache key, so re-importing NQ bars or rebuilding the L2 features invalidates
#: the cache by itself rather than serving a stale replay.
EXTERNAL_TABLES = ("nq_1m", "nq_l2_features_1s", "dbento_nq_ticks", "bm_nq_ticks", "ethusd_1m")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", ".cache")
_EXTERNAL_MEMO = {}
EXTERNAL_CACHE_VERSION = "nq-hybrid-at-first-l2-v3-session-only"


def _external_cache(kind, key, build):
    """Memoise one external extraction on disk.

    `combined_book` recomputes `drift_vwap_orders` and `nq_orders` from the store
    on every call, which is most of the wall time of a `build` and is pure
    repetition: the trades are a pure function of the source tables and the
    window. Keyed on the table fingerprint so a data refresh busts it.
    """
    from sandbox import data

    digest = hashlib.sha256(
        f"{EXTERNAL_CACHE_VERSION}:{kind}:{key}:"
        f"{data._table_fingerprint(list(EXTERNAL_TABLES))}"
        .encode()).hexdigest()[:16]
    # In-process memo in front of the disk cache. The greedy search calls
    # `external_prices` once per replay, so the on-disk form alone still cost
    # 569 `json.load`s -- 8.5s of a 166s run -- decoding the same grids over and
    # over.
    if digest in _EXTERNAL_MEMO:
        return _EXTERNAL_MEMO[digest]
    path = os.path.join(CACHE_DIR, f"external_{kind}_{digest}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            _EXTERNAL_MEMO[digest] = json.load(handle)
        return _EXTERNAL_MEMO[digest]
    value = build()
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    os.replace(tmp, path)                 # atomic, so a killed run leaves no half file
    _EXTERNAL_MEMO[digest] = value
    return value


def external_trades(sleeve, window=None):
    """One `combined_book` sleeve, converted to this module's trade dicts.

    `combined_book` emits `(sleeve, entry_ts, exit_ts, points_usd,
    units_per_dollar, step, mark)`. `points_usd` is already money per unit, so
    the replay's `points * lots * money_per_point` becomes `points * lots * 1`.

    THE WINDOW MUST BE PASSED. `drift_vwap_orders()` defaults to
    `combined_book.FULL`, which is ("2025-01-01", "2026-08-05") -- so calling it
    bare silently returns two years of trades no matter what period was asked
    for, and a 2018-2026 table then shows the sleeve starting in 2025 as though
    its data began there. `nq_1m` actually reaches back to 2008.

    `nq:ofi` is different and genuinely cannot be extended: it reads the
    `level_two` feature table, which begins 2025-02-12. Its short history is a
    fact about the data, not a default.
    """
    def build():
        from sandbox.research import combined_book as cb

        if sleeve == "nq:drift_vwap":
            rows = cb.drift_vwap_orders(window=window)
        elif sleeve == "ethusd:drift_vwap":
            rows = cb.eth_drift_vwap_orders(window=window)
        elif sleeve.startswith("nq:") and sleeve not in ("nq:ofi", "nq:drift_vwap"):
            sym, fam = sleeve.split(":", 1)
            ef.resolve(sym, allow_stale=True)
            p_surv = os.path.join(SURVIVOR_DIR, f"{sym}_{fam}_30m.json")
            if os.path.exists(p_surv):
                with open(p_surv, encoding="utf-8") as h:
                    payload = json.load(h)
                params = _retuple(payload["params"])
            else:
                p = os.path.join(RESULTS, f"exness_families_{sym}_30m_new.json")
                if not os.path.exists(p):
                    p = os.path.join(RESULTS, f"exness_families_{sym}_30m_combined.json")
                with open(p, encoding="utf-8") as h:
                    payload = json.load(h)
                params = _retuple(payload["families"][fam]["params"])
            row = {"symbol": sym, "family": fam, "params": params}
            from sandbox.research import drift_vwap_pullback as dv

            lower = int(datetime.fromisoformat(window[0]).replace(
                tzinfo=timezone.utc).timestamp())
            upper = int(datetime.fromisoformat(window[1]).replace(
                tzinfo=timezone.utc).timestamp())
            native_bars, native_ctx = _context(sym)
            l2_minutes = dv.load_minutes(sym, window[0], window[1],
                                         source="level_two")
            boundary = l2_minutes[0].ts if l2_minutes else upper

            def segment(bars, ctx, lo, hi, market):
                if not bars or lo >= hi:
                    return []
                result = ef.backtest(fam, bars, ctx, row["params"],
                                     lo=lo, hi=hi,
                                     initial=ef.INITIAL_BALANCE,
                                     include_trades=True)
                spec = ctx["cfg"]
                money = spec["multiplier"]
                converted = []
                for t in result["trade_log"]:
                    dist = t["distance"]
                    units = (0.015 / dist) if dist > 0 else 0.0
                    converted.append({
                        "sleeve": sleeve,
                        "symbol": market,
                        "entry_ts": t["entry_ts"],
                        "exit_ts": t["exit_ts"],
                        "side": t["side"],
                        "points": t["points"] * money,
                        "entry": t["entry"],
                        "units_per_dollar": units,
                        "step": spec.get("volume_step", 0.01),
                        "point_value": 1.0,
                        "external": True,
                    })
                return converted

            out = segment(native_bars, native_ctx, lower,
                          min(boundary, upper), "nq")
            if l2_minutes and boundary < upper:
                l2_full = [tuple((bar.ts, bar.open, bar.high, bar.low,
                                  bar.close, bar.volume))
                           for bar in dv.aggregate(l2_minutes, BAR,
                                                  rth_only=False)]
                l2_bars, l2_ctx = ef.context(sym, "validate", BAR,
                                              full_bars=l2_full)
                out.extend(segment(l2_bars, l2_ctx, max(boundary, lower),
                                   upper, cb.LEVEL_TWO_MARKET))
            out.sort(key=lambda trade: (trade["entry_ts"], trade["exit_ts"]))
            return out
        else:
            short = sleeve.split(":", 1)[1]
            rows = cb.nq_orders(short, cb.NQ_SLEEVES[short])
        out = []
        for name, entry_ts, exit_ts, points, units, step, mark in rows:
            market, side, price, point_value = mark
            out.append({
                "sleeve": name, "symbol": market, "entry_ts": entry_ts,
                "exit_ts": exit_ts, "side": side, "points": points,
                "entry": price, "units_per_dollar": units, "step": step,
                "point_value": point_value, "external": True,
            })
        return out

    # THE FILL FEED IS PART OF THE KEY. Without it a `--broker-fills` run is
    # served the vendor-priced replay straight off disk and reports it as a
    # broker result -- silently, because the trade count is identical.
    return _external_cache("trades", f"{sleeve}:{window}:fills={FILL_FEED}",
                           build)


def warm_external_cache(windows=None):
    """Materialise every imported sleeve so later runs never rescan the store.

    The windows are the ones the commands actually ask for: `build` uses the
    holdout, `yearly` and `standalone` use `{start_year}-01-01` onwards. Each is
    cached separately because the trades genuinely differ -- the window is an
    argument to the extraction, not a filter applied afterwards.
    """
    windows = windows or [("2025-01-01", CANON_DATA_END),
                          ("2018-01-01", CANON_DATA_END)]
    for window in windows:
        markets = set()
        for sleeve in EXTERNAL:
            rows = external_trades(sleeve, window=window)
            markets.update(row["symbol"] for row in rows)
            print(f"  {sleeve:18} {window[0]}..{window[1]}  {len(rows):,} trades")
        external_prices(sorted(markets), window=window)
        print(f"  price grids cached for {sorted(markets)}")
    print(f"\ncache dir {os.path.normpath(CACHE_DIR)}")


def external_prices(markets, window=None):
    """`{market: {ts: close}}` for the markets the external sleeves trade.

    THE WINDOW MUST BE PASSED HERE TOO. `cb.price_series` defaults to
    `combined_book.FULL` exactly as `drift_vwap_orders` does, and the failure it
    causes is silent and catastrophic rather than merely wrong: the revaluation
    grid ends up covering only 2025-2026, so every trade entered before the
    first grid stamp is opened AT that stamp, all at once. 4,600 NQ positions
    opened on one bar read as a 78.8% mark-to-market drawdown against a 6.8%
    closed one -- the tell that it is a marking artefact and not a result.
    """
    out = {}
    for market in markets:

        def build(market=market):
            from sandbox.research import combined_book as cb

            stamps, _opens, closes = cb.price_series(market, window=window)
            # Stamps are the dict KEYS, and JSON has no integer keys, so the
            # grid is stored as two parallel lists and zipped back on load. A
            # plain dict round-trips as {"1735689600": ...} and every lookup by
            # int then misses silently, which reads as a sleeve with no marks
            # rather than as an error.
            return {"stamps": list(stamps), "closes": list(closes)}

        grid = _external_cache("prices", f"{market}:{window}", build)
        out[market] = dict(zip(grid["stamps"], grid["closes"]))
    return out


#: Per-sleeve SHOWN equity multiplier -- the account balance a sleeve is told it
#: has when sizing, which is not the balance it actually has.
#:
#: This is an ACCESS lever, not a risk lever, and the distinction is the whole
#: point. On $1,000 the broker's minimum lot refuses most DE40 and XAUAUD
#: orders outright: de40:momentum fills 12 of 177 signals, xauaud:high_52w 69 of
#: 439. Those sleeves are not losing, they are not trading. Showing them a
#: larger balance lets the order clear `volume_min`
#: ([[virtual-equity-unlocks-every-sleeve-at-1point5x]]).
#:
#: Past the point where a sleeve fills, more multiplier is straightforwardly
#: leverage and should be called that. `calibrate_shown` therefore solves for
#: the SMALLEST multiplier reaching a target fill rate and refuses to go past
#: `MAX_SHOWN`, so access and leverage stay separable.
#:
#: The margin ceiling in `size` deliberately still reads REAL equity -- a
#: broker does not extend margin because the book pretended.
#: Calibrated 2026-08-16 as the smallest multiplier reaching TARGET_FILL, per
#: sleeve. ETHUSD needs nothing; DE40 and XAUAUD are the expensive instruments
#: on a $1,000 account. xagaud:failed_break is absent because it never reaches
#: 90% even at 6x -- genuinely unaffordable, not merely constrained.
SHOWN_EQUITY = {
    "de40:consecutive": 1.5,     # 80.0% -> 92.6%
    "de40:donchian": 1.5,        # 79.5% -> 91.4%
    "de40:momentum": 2.0,        # 78.7% -> 90.2%
    "de40:ib": 3.0,              # 59.5% -> 99.2%
    "de40:overnight": 3.0,       # 56.2% -> 100.0%
    "de40:orb": 3.0,             # 56.3% -> 100.0%
    "btc:vwap": 1.5,             # 87.5% -> 99.5%
    "btc:orb": 1.5,              # 79.5% -> 97.4%
    "btc:overnight": 2.0,        # 66.4% -> 100.0%
    "xauaud:high_52w": 2.0,      # 15.7% -> 97.6%
    #: Added 2026-08-17, when EWMA vol targeting arrived. ETHUSD realises ~60%
    #: annualised against the 20% target, so its multiplier sits at 0.33 median
    #: (0.24-0.61) and every order is sized to a third. On $1,000 that lands
    #: under the 0.01 lot floor: ethusd:swing_ma fell from 63 fills to 16. 3.0
    #: is 1/0.33 -- it restores the size the sleeve had BEFORE vol targeting,
    #: it does not add risk on top of it. Neither sleeve needed an entry when
    #: the throttle was one-sided and never cut below 1.0.
    "ethusd:swing_ma": 3.0,
    "ethusd:failed_break": 3.0,
    "ethusd:range_expansion": 3.0,
    "ethusd:confluence": 3.0,
    "ethusd:volatility_breakout": 3.0,
    #: Canon book, calibrated 2026-08-17 by `book_search`: the smallest rung on
    #: (1.0, 1.5, 2.0, 3.0, 4.0, 6.0) that leaves the sleeve with ZERO refused
    #: orders at risk_scale 0.6. Without these four the book drops trades to the
    #: 0.01 lot floor, and a dropped trade means the backtest and a live account
    #: are running different strategies. tsla:ib at 6.0 is at the ceiling and is
    #: leverage rather than access at that point -- its 3.14 ratio should be read
    #: as a sizing artefact, not as the sleeve outperforming.
    "tsla:ib": 6.0,
    "tsla:failed_break": 3.0,
    "jp225:swing_donchian": 2.5,     # 76.8% -> 100.0%
    "xauaud:momentum": 4.0,          # 37.4% -> 95.7%
    "stoxx50:climax": 1.0,           # 100.0% from day one
    "jp225:volume_thrust": 2.0,      # 90.1% -> 100.0%
    "jp225:climax": 3.0,             # 84.1% -> 100.0%
    "de40:floor_pivot": 1.0,         # 100.0% fills
    "nq:volatility_breakout": 1.0,   # 100.0% fills
    "xaueur:orb": 1.5,
    "xaueur:key_reversal": 1.5,
}
MAX_SHOWN = 6.0
TARGET_FILL = 90.0

#: The book the operator selected on 2026-08-16: the 8-sleeve point on the
#: forward-selection frontier, at risk_scale 1.0 and a 3x gross cap.
#: +397.9%, 19.6% mark-to-market drawdown, monthly Sharpe 2.93, 17/20 months.
#: Chosen over the 12-sleeve version (+630.4% at 25.9%) because the last four
#: sleeves bought 232 points of return for 6.3 of drawdown and two of them
#: contributed nothing at all.
#: btc:momentum removed 2026-08-16 -- it was the only LOSING member (-$255,
#: P&L/dd -0.43) and cost $588 of drawdown to supply consistency it did not
#: earn.
#: de40:ib removed 2026-08-16. NOT for decay -- its per-trade edge is flat
#: noise across nine years (5.15, 3.33, -1.68, 4.29, 8.09, 2.29, 1.26, 3.59,
#: 0.67 bp) with no trend. It goes because it never cleared a per-year t-stat
#: of 1.5 in ANY of those nine years (best 1.23), and its whole-period
#: significance comes from accumulating 1,269 trades rather than from an edge
#: worth having. Its $511 in 2025 was volume, not skill.
#: 2026-08-17, intermediate: the book was briefly reduced to the single cell
#: usdjpy:range_expansion, then rebuilt. Kept in the record because with one
#: member there is nothing to combine -- the dependence gate, the
#: diversification benefit and the shared-balance re-sizing all have nothing to
#: act on, and the file's numbers are then that strategy standalone.
#:
#: CURRENT BOOK, 2026-08-17. Six sleeves, reproduce with:
#:
#:     py -m sandbox.research.exness_combined_strategies build --consistency \
#:        --include usdjpy:range_expansion,nq:ofi,nq:drift_vwap,ethusd:ib \
#:        --max-members 10 --max-down-rho 0.60 --max-loss-lift 1.70 \
#:        --risk-scale 0.9 --sizing-cap 1500 --min-trades 100
#:
#: +245.3% ($1,000 -> $3,453), MTM dd 21.2% (trough 2025-07-22), closed 20.4%,
#: monthly Sharpe 2.76, 16/20 positive, worst month -8.47%, 4,379 trades.
#:
#: THE 100-TRADE FLOOR IS WHY THIS BOOK IS SMALL, AND IT COST A LOT. Requiring
#: `--min-trades 100` cut the pool from 27 candidates to 13 and the book from
#: ten sleeves to six. Against the ten-sleeve version at the same risk and cap:
#: return +504.8% -> +245.3%, Sharpe 4.74 -> 2.76, positive months 18/20 ->
#: 16/20, worst month -2.45% -> -8.47%. Drawdown did NOT improve (21.4% ->
#: 21.2%), because drawdown here was never a diversification problem -- the
#: ten-sleeve trough was 85.5% one ethusd:swing_ma position marked through a
#: single three-hour window ([[book-drawdown-is-one-intraday-position]]).
#:
#: So the floor bought trustworthiness per sleeve and paid for it in
#: diversification. That is a real trade, not a free improvement, and anyone
#: lowering it back to 40 should expect the return and consistency to come back
#: with it.
#:
#: ethusd:ib is the one member still worse in the book than standalone (ratio
#: 0.55, +$80 against a $146 standalone equivalent). It is held because it is
#: the only ETHUSD cell clearing 100 trades and the operator wants ETHUSD
#: represented.
#: CANON BOOK, set 2026-08-17. Nine sleeves, found by `book_search` rather than
#: by `build`'s greedy forward pass -- it can drop and exchange members, which is
#: how it reached a book no forward-only search could assemble.
#:
#:     py -m sandbox.research.exness_combined_strategies build --consistency \
#:        --include nq:ofi,nq:drift_vwap,usdjpy:range_expansion \
#:        --max-members 9 --max-down-rho 0.40 --max-loss-lift 1.40 \
#:        --risk-scale 0.6 --sizing-cap 1500
#:
#: CANON BOOK, set 2026-08-19. Twelve sleeves across US tech (NQ), FX (USDJPY,
#: AUDUSD), metals (XAGAUD), European indices (DE40), Asian indices (JP225), and crypto (ETHUSD).
#: All NQ strategies (nq:ofi and nq:drift_vwap) execute on Level 2 orderbook feeds,
#: plus nq:volatility_breakout on 30m ATR geometry.
#: Dropped eurjpy:vwap, added ethusd:confluence and ethusd:volatility_breakout.
#:
#: +754.0% ($1,000 -> $8,540.46), closed dd 9.6%, monthly Sharpe 4.91.
#: 19/20 positive months (95% win rate, median +9.31%), worst month -0.31%.
#: CANON BOOK, set 2026-08-20 after an explicit UNCAPPED 2020-2026 risk sweep.
#:
#:     py -m sandbox.research.exness_combined_strategies build \
#:        --members canon --uncapped --risk-scale 0.131 --force-minimum-lot
#:     py -m sandbox.research.exness_combined_strategies yearly \
#:        --start-year 2020 --uncapped --risk-scale 0.131 --force-minimum-lot
#:
#: No sizing-equity cap is applied: every sleeve compounds against the full
#: shared account. Every positive under-minimum request is rounded UP to the
#: broker's minimum and that excess risk is marked. The book retains the 13
#: members that pass a fresh complete-window standalone replay; the stale,
#: truncated es:volatility_breakout survivor was removed on 2026-08-21.
#: Revised 2026-08-23 for a $400 account. `de40:floor_pivot` left the book
#: because a $400 balance cannot margin one 0.07 DE40 lot at today's index level
#: (see EXCLUDE), and `msft:xma_cross` + `tsla:floor_pivot` replaced it -- the
#: pair that priced best of every $400-affordable, gate-clean combination tried.
#: CANON BOOK, set 2026-08-25. TWENTY sleeves at risk 0.135 with the gross cap
#: OFF. Every member PROFITABLE on both windows, ZERO refusals of either kind
#: (no gross-cap refusal, no below-broker-minimum skip), on a $400 account.
#:
#:   cold 2025-2026   +634.3%  MTM 12.83%  19/20 months  Sharpe 5.50
#:                    worst month -0.25%
#:   long 2020-2026  +21261.5%  MTM 14.76%  71/80 months
#:                    worst month -9.80%
#:
#: LONG-WINDOW MARGIN IS 0.24pp under the 15% limit and the drawdown surface is
#: NOT monotone in risk (0.150 -> 14.00%, 0.145 -> 13.29%, 0.140 -> 13.66% on
#: the 17-sleeve predecessor). Treat that margin as noise, not as headroom.
#: risk 0.130 gives +619.9% at 14.37% (0.63pp) if more room is wanted.
#:
#: HOW IT WAS BUILT, in order, because the order is the method:
#:   1. de40 and the two stock cells left (unaffordable / untradeable).
#:   2. Six sleeves added on DRAWDOWN contribution, not return: eurjpy:two_stage,
#:      hk50:supertrend, xalusd:gated_fade, stoxx50:climax, uk100:gated_fade,
#:      audjpy:gated_donchian. Most return under +18% standalone; a return
#:      screen would have discarded every one of them.
#:   3. The gross cap switched off -- at this size it is a first-come-first-
#:      served QUEUE, not a risk control (413 refusals at 6.0x).
#:   4. ethusd:idio_break and jp225:xma_ribbon added, chosen by scoring
#:      candidates on P&L INSIDE THE BOOK'S OWN LOSING MONTHS.
#:   5. jp225:swing_break swapped for ethusd:macd_hist (2026-08-25), found by a
#:      drop-and-swap search rather than the forward pass.
#:   6. Rebuilt 2026-08-26. hk50:supertrend, stoxx50:climax,
#:      audjpy:gated_donchian and jp225:xma_ribbon dropped at operator
#:      instruction; usdjpy:pullback, jp225:break_retest, ethusd:obv_break and
#:      nq:level_confluence took the slots by greedy search over the pool.
#:      Worst-drawdown concentration 52.4% -> 27.1%.
#:
#: TWO THINGS THIS BOOK IS NOT.
#:
#: The long-window return is NOT 17,000% of evidence. 2020-2024 is FITTED for
#: every member; the same six additions moved the out-of-sample cold start by
#: +2.6% while multiplying the long window 1.8x. Read drawdown, month count and
#: Sharpe out of sample -- not return.
#:
#: The +0.13% worst month is FITTED. ethusd:idio_break and ethusd:macd_hist
#: were both selected BECAUSE they earned in the book's own losing months, from
#: a pool already screened on 2025-2026. The 80-month figures moved far less,
#: and that is the honest measure of what they bought.
#:
#: SIZING IS ON A NOISY SHELF. 0.135 -> 14.54% long, 0.130 -> 14.84%,
#: 0.125 -> 14.71%: less risk, more drawdown, twice. The 0.46pp of margin under
#: a 15% limit is not reliable. risk 0.120 (13.44% long, 71/80 months, same
#: -1.40% worst month) is the cell with real separation, at -48pp of cold
#: return. Operator chose 0.135 knowing this.
#:
#: ETHUSD is 5 of 20 sleeves. The cap tested at 4 was lifted by the operator
#: 2026-08-26; the uncapped search chose only one more ETHUSD cell than the
#: capped one, and took two non-crypto sleeves for the other slots.
#: CANON BOOK, set 2026-08-29. TWENTY-FIVE sleeves, ONE RTH EACH: every member
#: enters and exits inside a single session and holds nothing overnight.
#:
#: WHY IT CHANGED. Two bugs, found in that order.
#:
#:   1. `gated_fade` and `cci` are `session` families and were NOT being
#:      flattened. The old test was `minute >= closed`, which needs a bar to
#:      print at or after the configured close; on xalusd and xniusd the 30m
#:      series stops at 13:30 while the session closes at 14:00, so the bar
#:      existed on 34 of 411 days and the flatten never fired on the other 92%.
#:      xalusd:gated_fade then ran on its trailing stop for a median of 20 hours
#:      and a maximum of 35 days -- 104 of its 120 exits were `stop`, 16 were
#:      `session` -- against a bar series with no rows for 13.5 hours a day and
#:      61.5 hours a weekend. Its stop was inert for most of every hold and its
#:      open risk was invisible to the mark-to-market.
#:
#:   2. `nq:sar` was reading nq_1m for the whole window because it sat on the
#:      native families path instead of the `nq:` import, so it never saw level
#:      two at all. Adding it to EXTERNAL costs 28pp of book return, and the
#:      earlier figure was measured on the wrong series.
#:
#: `ef.SESSION_ONLY` now collapses swing and overnight to session everywhere.
#: jp225:swing_donchian and xalusd:gated_fade both go NON-POSITIVE under it --
#: their whole edge was the hold -- and the stale-survivor gate refused to build
#: until they were removed. nq:sar and eurjpy:volatility_breakout replaced them,
#: chosen by scoring every gate-clean candidate INSIDE the book rather than
#: standalone ([[drop-test-not-standalone-return-values-a-sleeve]]).
#:
#:   cold 2025-2026  +804.8%  MTM dd 12.71%  closed 11.4%  6,719 trades
#:                   Sharpe 5.42  18/20 months  worst -2.41%
#:   long 2020-2026  +24,517%  MTM dd 14.70%  closed 12.7%  19,774 trades
#:                   Sharpe 3.81  73/80 months
#:
#: 25 IS THE CEILING UNDER A 13% LIMIT, and that was measured, not assumed. With
#: the standalone gate on, the best remaining candidate takes drawdown to
#: 13.13%; growing to 30 by chasing return reached +1,842% at 23.16% dd. All
#: three NQ sleeves also survived a one-by-one drop-and-replace: removing ANY of
#: them loses return AND raises drawdown, and no gate-clean replacement beat the
#: incumbent inside the cap.
#:
#: TWO THINGS TO WATCH. `eurjpy:volatility_breakout` scores P&L/dd 0.46 on
#: 2020-2026, the only member under 1.0 -- it was picked on 2025-2026 and looks
#: much better there (1.94), which is what selection on a window does. And
#: `usdjpy:volume_thrust` carries $6,550 of long-window drawdown, 2.4x the next
#: worst, plus 45% of the cold window's worst fall.
#: TWO SLEEVES REPLACED 2026-09-01, ON MEASURED EXECUTION RATHER THAN RETURN.
#:
#: OUT: `xniusd:cci` and `uk100:gated_fade`. Both are profitable on the sealed
#: fill and lose money on the one this account actually gets. Priced through
#: `fill_models.exness` -- entry at the broker quote a feed lag after the
#: bar's open, exit as the late market order the runtime really sends, because
#: the MT5 payload carries no stop ([[no-broker-side-stops-exits-are-late-market-orders]]):
#:
#:     xniusd:cci        +27.8% -> -28.5%   cost -22.44 bp/trade, t -6.11
#:     uk100:gated_fade  +17.6% -> -10.5%   cost  -3.72 bp/trade, t -3.25
#:
#: THREE INDEPENDENT TESTS AGREED, which is why this is a removal and not a
#: re-weighting. A paired per-trade cost (same trade, two fills) put both beyond
#: t = -3 when no other member cleared -1.5. A leave-one-out on the live book
#: gave them the only NEGATIVE drop costs of the 25 (-20.9pp and -16.0pp): the
#: book is better without them. And they fail `_live_screen`'s pass/fail, which
#: is mechanical and compares a cell only with itself.
#:
#: They are not weak strategies. On sealed fills dropping the pair COSTS 57.7pp
#: -- they look like good members right up until the fill is measured. Their
#: damage is the ENTRY leg (-56.3pp and -29.6pp; their exits are ~0), and both
#: are fade/reversion entries: they buy a dip that has partly reverted by the
#: time the order lands, so a broker-side stop would not save them either.
#:
#: IN: `de40:consecutive` and `jp225:vol_regime`, chosen as a PAIR over 210
#: pair-builds, not as two independent additions -- members compete for one
#: balance, so seating two cells that each look good alone is not the same test
#: ([[drop-test-not-standalone-return-values-a-sleeve]]).
#:
#: Higher-scoring pairs exist and were declined. The best three all contain
#: `jp225:volatility_breakout`, `jp225:obv_divergence` or `jp225:climax`, and
#: every one of those has a SIGNIFICANTLY POSITIVE execution cost (t +2.57,
#: +2.68, +2.37) -- the late fill makes them measurably better. No mechanism
#: makes a 60-second delay worth 5-11 bp, so that is the same fill-dependency
#: that killed `xniusd:cci` pointing the other way, and banking it would seat a
#: sleeve whose edge partly IS this window's execution noise. The pair below is
#: the best one whose BOTH members are fill-independent at |t| < 2.
#:
#: `de40` WAS DROPPED TWICE ON A FALSE PREMISE, AND THIS SEATS IT BACK.
#:
#: The record said DE40 was unaffordable on $400 because one minimum lot needs
#: "$536 of margin", and that `size` compares it against real equity so nothing
#: could lift it. The first half is a self-imposed number and the second half is
#: about the wrong constraint.
#:
#: MEASURED FROM THE TERMINAL, 2026-09-01, on the funded account (leverage
#: 1:2000000000, balance $392.25): one 0.07 DE30 lot needs **$10.66** of broker
#: margin. jp225 $6.18, ukoil $4.51, nq $3.69, uk100 $3.66, xniusd $3.36,
#: es $2.69, btc $1.98, hk50 $1.14, ethusd $0.62, and the five FX pairs $0.00.
#: The account has never been unable to place any of them.
#:
#: The $536 was `MARGIN_FRACTION = 0.25` -- a 4x notional ceiling this repository
#: chose. Every refusal this book has ever booked was that policy, never the
#: broker. `MIN_LOT_ALWAYS` now makes the policy SHRINK a position rather than
#: refuse one, so the ceiling still bounds discretionary size and a minimum lot
#: goes through whenever the broker can margin it. Refusals: 1 -> 0.
#:
#: WHAT THIS ACCEPTS, AND IT IS NOT NOTHING. A minimum lot can exceed 4x equity:
#: de40 5.41x at $392, nq 3.76x, gbpjpy 3.46x. Per trade the loss is still
#: bounded by the STOP and not by notional, but gap risk scales with notional,
#: and if many sleeves are open at once the simultaneous total can reach ~37x.
#: The lever that bounds simultaneity is `gross_cap`, which canon has OFF
#: ([[gross-exposure-cap-beats-its-null]]). This trades a refusal problem for a
#: simultaneity one, and the gross cap has NOT been re-examined since.
#:
#: It also removes a pro-cyclical de-diversification: refusals bit hardest when
#: equity was lowest, so a drawdown used to switch sleeves off and concentrate
#: the book exactly when that hurt ([[minimum-lot-de-diversifies-pro-cyclically]]).
#:
#: On live fills over 2025-01-02..2026-08-21, against canon as it stood:
#:
#:     canon as-is (25)          +584.6%   MTM dd 17.5%   1 refused
#:     minus the two (23)        +657.8%   MTM dd 15.4%
#:     this book (25)            +852.7%   MTM dd 14.6%   0 refused
#:
#: THE WINDOW MOVED WITH THE MEMBERSHIP and that is a trap worth naming.
#: `fill_models.exness.tick_window_start` opens the window where the LAST
#: member symbol's history begins, so swapping uk100 for de40 moved it from
#: 2025-01-03 03:00 to 2025-01-02 09:00. A book measured against a window
#: derived from DIFFERENT members skips the very trades that distinguish them --
#: it is what hid this refusal for two runs.
#:
#: THE WINDOW IS INSIDE THE SURVIVOR POOL'S OWN SCREENING PERIOD
#: ([[exness-survivor-pool-is-oos-conditioned]]). The pass/fail that removed two
#: sleeves is mechanical and survives that; the ORDERING that chose these two
#: does not. Treat the seats as provisional until they clear a window the pool
#: was not screened on.
#:
#: NOT YET LIVE. `live_trade/src/live/portfolio/routing.rs` and the Rust sleeve files
#: still carry the old 25, and there is no `de40_consecutive.rs`,
#: no `jp225_vol_regime.rs`, and no `de40` market or vendor feed. Until those
#: exist this is the RESEARCH canon only and the account runs the previous book.
#: TWO MORE DROPPED 2026-09-01 TO REACH A DRAWDOWN TARGET, AND THIS ONE IS A
#: WEAKER BASIS THAN THE SWAP ABOVE. Read both notes before trusting either.
#:
#: `btc:xma_ribbon` and `usdjpy:aroon` are gone. They were not failing anything
#: mechanical -- both clear `_live_screen` and both have insignificant execution
#: cost. They were carrying drawdown out of proportion to their return: btc held
#: the largest drawdown-event share in the uncapped book (24%) and usdjpy:aroon
#: 41% at risk 0.16. A greedy leave-one-out at canon sizing, ranking each
#: removal by RETURN SURRENDERED PER POINT OF DRAWDOWN BOUGHT, picked them
#: first and second:
#:
#:     25 sleeves   +852.7%   MTM dd 14.6%
#:     24 sleeves   +803.3%   MTM dd 13.3%   -btc:xma_ribbon   35pp per dd point
#:     23 sleeves   +735.8%   MTM dd 11.3%   -usdjpy:aroon     36pp per dd point
#:
#: MEMBERSHIP IS ~5x MORE EFFICIENT THAN EITHER GLOBAL DIAL and that is the
#: finding. A 49-cell sweep of `risk_scale` x `gross_cap` reached 12% only at
#: +248.8%, about 170pp per drawdown point. It also showed why: uncapped
#: drawdown is 14.6% at EVERY risk from 0.05 to 0.135, because on $400 the
#: sleeves are pinned at the broker minimum and cutting risk cannot shrink a
#: position that is already there ([[min-lot-pinned-sleeves-do-not-compound]]).
#: And a gross cap of 8, 6 or 4 makes drawdown WORSE than no cap (19-20% against
#: 14.6%), because refusing first-come-first-served culls by trade frequency
#: rather than by risk contribution.
#:
#: WHY THIS IS SOFTER EVIDENCE THAN THE SWAP. The two sleeves removed above
#: failed a MECHANICAL test -- a paired per-trade execution cost with a t-stat,
#: agreed by three independent methods. These two failed a drawdown RANKING on a
#: single path. Choosing members to minimise drawdown on one window partly
#: manufactures the number: the survivors are the sleeves that happened not to be
#: open at this window's trough
#: ([[selection-gate-manufactures-drawdown-and-consistency]]), and the window
#: sits inside the pool's own screening period
#: ([[exness-survivor-pool-is-oos-conditioned]]). Neither drop has been
#: reproduced anywhere this search did not touch.
#:
#: The greedy loop STOPPED ON THE TARGET, not on exhaustion, so 11.3% is where
#: it crossed 12% and not a floor. `de40:consecutive` is now the largest single
#: drawdown contributor at 23%.
#:
#: BTC LEAVES THE BOOK AS A SYMBOL. `btc:xma_ribbon` was its only sleeve. The
#: btc FEED must stay regardless: `needs_market` in the live runtime requires
#: BTCUSDT whenever any ETH sleeve runs, because BTC drives the crypto exposure
#: overlay and supplies the quote rate that converts BTC-denominated P&L to USD.
#: TWO MORE DROPPED 2026-09-02, ON A RESAMPLED DRAWDOWN BAND RATHER THAN ONE
#: PATH. This is the first membership change here decided on a DISTRIBUTION.
#:
#: OUT: `de40:consecutive` and `eurjpy:volatility_breakout`. The target was a
#: Monte Carlo band -- 5th to 95th percentile of mark-to-market drawdown inside
#: 10-25% -- measured by `exness_combined_montecarlo` running LIVE execution
#: (`_mc_live`) over the full 1m window, 2025-01-01..2026-08-21, 1,000 block
#: bootstrap paths:
#:
#:     23 sleeves   dd p5 11.55  p50 19.01  p95 36.30   return p50 563%
#:     21 sleeves   dd p5 10.16  p50 13.75  p95 21.65   return p50 509%
#:
#: WHY THE RISK DIAL COULD NOT DO IT. A uniform cut moves both edges of the band
#: together, so it buys the 25% ceiling by breaking the 10% floor. Only
#: membership changes the SHAPE ([[membership-beats-sizing-for-drawdown]]).
#:
#: `de40:consecutive` IS THE ONE DROP THAT COSTS NOTHING. A paired leave-one-out
#: over the 30 worst block orders -- same orders, one sleeve removed -- put it
#: at -14.66 drawdown points AND +30pp of return, the only member with no
#: trade-off; the note above had already measured it as the largest single
#: drawdown contributor at 23%. `eurjpy:volatility_breakout` is second at -5.69
#: for -9pp, and it was the member this file already flagged as the only one
#: scoring under 1.0 on P&L/dd over 2020-2026. Three neighbours were measured
#: and rejected: dropping de40 alone leaves p95 at 26.65, swapping in
#: `ukoil:xma_cross` gives 25.34, and taking all three gives 21.50 with LOWER
#: return and a lower p5 than this pair.
#:
#: THE DAMPERS WERE MEASURED TOO, and they are not the sleeves a return screen
#: would keep. Removing `ukoil:level_confluence`, `nq:drift_vwap` or
#: `gbpjpy:trap` makes tail drawdown WORSE (+2.18, +1.97, +1.77 points)
#: ([[low-loss-lift-sleeves-are-drawdown-dampers]]).
#:
#: WHAT THIS COSTS ON THE REALISED PATH, AND IT IS A REAL COST. On the actual
#: calendar the change is worse on both axes: +713.0% at 11.59% MTM dd becomes
#: +608.4% at 15.23%. That is not a contradiction -- the realised ordering was a
#: LUCKY draw for the 23-sleeve book, sitting at the 5th percentile of its own
#: resampling distribution, while 15.23% is about a median draw for the 21. The
#: band is a statement about what the process can do, not about this path.
#:
#: WHY THIS IS SOFTER EVIDENCE THAN THE EXECUTION SWAP ABOVE. The drops were
#: chosen on orders drawn from the same generator the band was then measured on,
#: so 21.65 is optimistic as a forecast. `de40:consecutive` is the exception: it
#: improved drawdown and return at once, which a search cannot manufacture. And
#: a Monte Carlo over selected history measures the dispersion of a fitted
#: object ([[selection-gate-manufactures-drawdown-and-consistency]]).
#:
#: NOTHING WAS ADDED. An extra sleeve brings a whole risk budget and drawdown is
#: superadditive even at zero correlation ([[combined-book-stacks-risk-budgets]]),
#: so an addition moves p95 the wrong way; and the live-execution maps cover 19
#: symbols, every one of which this book either already trades or dropped above.
#: A SWAP MADE AND REVERTED THE SAME DAY, 2026-09-02: `usdjpy:pullback` OUT,
#: `usdjpy:cci` IN, then back. The membership below is unchanged; what follows is
#: why, because the reason is a measurement lesson and not a verdict on the cell.
#:
#: IT WON ON THE DISTRIBUTION AND LOST ON THE PATH. `usdjpy:cci` was the best of
#: 46 single changes that raised return in BOTH eras -- 2022-2024, where the
#: sleeve parameters were fitted, and 2025-2026, where they were not -- and it
#: also lowered the resampled p95 drawdown on the holdout window. Then the sealed
#: single-path replay put 2025-2026 drawdown UP, 13.6% -> 17.1%, and the whole
#: 17.05% was one 2.5-hour event on 2026-07-30 10:00-12:32, $2,742 -> $2,275.
#: `usdjpy:cci` bore 43.8% of it, `usdjpy:volume_thrust` 39.0% and `gbpjpy:trap`
#: 16.5%: three sleeves, all long yen, all open at the same instant, for a total
#: contribution from the new cell of $181.
#:
#: A BLOCK BOOTSTRAP CANNOT SEE THIS AND THAT IS THE POINT. Resampling fortnights
#: averages over WHICH positions happen to coincide, so a concentration that
#: exists in the realised sequence is diluted across 250 shuffles into a lower
#: median. The Monte Carlo answers "how bad can this process be"; it does not
#: answer "what is open at once", and simultaneity is what `gross_cap` bounds
#: ([[gross-exposure-cap-beats-its-null]], [[book-drawdown-is-one-intraday-position]]).
#: Read the sealed path AND the distribution before seating a member; either one
#: alone would have passed this cell or refused it for the wrong reason.
#:
#: The measured case for the cell, kept so it can be retried behind a
#: simultaneity cap rather than rediscovered. 1,000 / 250 block-bootstrap paths,
#: live execution:
#:
#:     window        canon return   swap return   canon p95 dd   swap p95 dd
#:     2022-2026          2,911%        4,223%          27.1%         27.5%
#:     2025-2026 (oos)      523%          575%          22.7%         21.9%
#:     2022-2024 (is)       641%          871%          31.6%         33.0%
#:
#: WHAT THE SEARCH FAILED TO DO, WHICH MATTERS MORE THAN WHAT IT FOUND. The
#: target was a p5-p95 drawdown band of 10-25% over 2022-2026 and NOTHING
#: reached it. Canon itself is 27.1% there; every book tested came back 26.8 to
#: 28.0. Three levers were measured and all three are nearly inert:
#:
#:     risk_scale   p95 is 33.6-33.7 across 0.075-0.105 on one candidate and
#:                  26.4 vs 27.1 across 0.120-0.135 on canon. On $400 the
#:                  sleeves are pinned at the broker minimum, so cutting risk
#:                  cannot shrink a position that is already the smallest the
#:                  broker accepts ([[min-lot-pinned-sleeves-do-not-compound]]).
#:     drops        three of them moved p95 27.46 -> 26.78 while costing 18% of
#:                  the return. Reaching 25% needs roughly ten.
#:     adds         about +1 point of p95 each; a new sleeve brings a whole risk
#:                  budget ([[combined-book-stacks-risk-budgets]]).
#:
#: The band was only ever met on 2025-2026 -- the window canon was selected on
#: ([[selection-gate-manufactures-drawdown-and-consistency]]). The drawdown that
#: breaks it lives in 2022-2024.
#:
#: A SCREEN THAT LOOKED RIGHT AND WAS NOT, recorded so it is not repeated. Every
#: candidate was first ranked on the 30 block orders that hurt canon most, paired
#: seed by seed. Five adds appeared to CUT tail drawdown 1.8-2.4 points; on the
#: full distribution all five RAISED p95. Conditioning on one book's worst orders
#: and then scoring a different book there measures regression to the mean, not
#: the candidate. It worked for drops in the 2025-2026 search because a drop
#: really does remove risk; it cannot be trusted for adds.
#:
#: THE CELL WAS ALSO NEVER CLEAN. `usdjpy:cci` came from a pool screened on
#: 2025-2026 ([[exness-survivor-pool-is-oos-conditioned]]) and earned most of its
#: advantage in the FITTED years (+36% against +10% out of sample); at 250 paths
#: a median of 575% against 523% is close to noise. The concentration is what
#: decided it, but the evidence was thin either way.
#: THE FIVE NQ SLEEVES WERE REMOVED 2026-09-03 and nothing here is a rename of
#: them: `nq` is barred as a symbol (see EXCLUDE_SYMBOLS) and `ustec` cells are
#: separate searches on a separate price series. The book that remains is
#: whatever survived that removal plus what the drawdown-band search seated in
#: the freed slots.
#: DECAY-SCREENED 2026-09-03, and the screen is the whole reason this book is
#: twenty rather than twenty-nine. Seven sleeves were removed for failing a
#: 2026 edge test measured in R-multiples -- `gross / distance`, the trade's
#: outcome in units of the risk it took, which is the same quantity on any
#: balance and in any year, unlike a dollar contribution inside a compounding
#: book. See `_seventh_decay.py`.
#:
#: THE REFILL WAS TRIED AND REJECTED, AND THAT IS THE LESSON HERE. A greedy
#: forward selection put four then seven fresh cells into the freed slots and
#: looked excellent on the realised path -- slot four ran +19,274% at 15.6%
#: marked drawdown. Its Monte Carlo p95 was 35.13%. The realised ordering was a
#: lucky draw and the greedy could not see it, because it was guarded on
#: REALISED drawdown instead of the distribution
#: ([[canon-drawdown-is-sequence-risk]], [[refill-guarded-on-one-path-hides-its-tail]]).
#: The screen alone beats every refill of it on the tail by 6 to 22 points.
#: EVERY JP225 SLEEVE LEFT 2026-09-22, and the reason is a measurement error
#: rather than six bad cells. `fill_models.exness` keyed its spread and fill
#: maps on the SHIFTED stamp and read the broker's unshifted table, so every
#: jp225 execution number in this book was drawn from a window six hours away
#: ([[shifted-markets-misread-the-broker-table]]). The fix landed 2026-09-20 but
#: the tracked maps file was never rebuilt, so the book was still scoring on the
#: poisoned reduction ([[live-fill-maps-were-never-rebuilt]]). Corrected, the
#: go-live fortnight reads -28.47% against the -13.68% on file, and the
#: pre-swap 8-sleeve book -46.14% against -28.11%.
#:
#: WHAT THE SIX ACTUALLY WERE. On corrected fills none of them clears t=2 out of
#: sample -- cusum t=0.15 pf 1.14, volume_thrust t=1.04, momentum_stack t=1.00
#: at 30.1% drawdown -- and their breakeven cost is 4.1-7.6 bp against a
#: measured late-exit displacement of 11.6 bp median and 40.9 bp p90, the worst
#: exit-to-spread ratio in the book at 16.6x. The edge was smaller than the
#: error in where the exit lands. They also were not six bets: 16 of 20 live
#: jp225 entries arrived as a same-bar same-direction bloc, and on 2026-09-17
#: three sleeves entered at 64133.1 and exited at 64040.9 together.
#:
#: THE THREE ARRIVALS ARE DIVERSIFIERS, NOT EARNERS, and the numbers say so:
#: 6.6% of P&L between them over 2022-2026. They were picked from 67 feedable
#: candidates as the only ones adding no drawdown to ANY window -- every btc
#: cell adds 9-13pp of holdout drawdown alone, which is why none is here.
#:
#:     2022-2026 $500 live fills    base 16      this book (19)
#:     return                        +4,209%          +5,726%
#:     MTM drawdown                   15.91%           14.98%
#:     MC median / p99 dd       3,437% / 34.14%  4,608% / 34.33%
#:
#: THE TAIL COST IS WINDOW-DEPENDENT AND MUST NOT BE QUOTED AS ONE NUMBER. Over
#: 2022-2026 the three cost 0.19pp of Monte Carlo p99 drawdown; over the
#: 2025-2026 holdout alone they cost 3.85pp (25.00% -> 28.85%) and double the
#: odds of exceeding 20%. The realised path shows drawdown FALLING, which is one
#: lucky ordering and was the trap the first pass of this search fell into
#: ([[canon-drawdown-is-sequence-risk]]).
#:
#: RESEARCH ONLY SO FAR. The live rows, cost rules, market tables and the Rust
#: const registry still carry the jp225 book ([[activating-a-book-is-four-places]]).
BOOK = ("usdjpy:volume_thrust", "audusd:zscore",
        "ethusd:confluence",
        "ethusd:volatility_breakout", "gbpjpy:trap",
        #: `ukoil:xma_cross` LEFT 2026-09-23 on decay, the one sleeve where every
        #: test agreed: 0 wins in 6 over Aug-Sep 2026 (2.5% by chance at its 46%
        #: win rate), its worst loss streak on record, bottom 13% of its own
        #: 16-week windows for Jun-Sep, 2026 R +0.153 against +0.284 in
        #: 2022-24 at t=0.48, and the only sleeve negative in 2026 dollars --
        #: while UKOIL trended +23% at 90th-pct efficiency, the tape an MA cross
        #: is built to win. Any single one of those is luck; five together are not.
        "eurjpy:two_stage",
        "usdjpy:pullback",
        "ethusd:obv_break",
        "ukoil:level_confluence",
        #: `usdjpy:kendall` REMOVED 2026-09-26, operator decision, against the
        #: advice below: 22 sleeves ran 964%/13.97 realised, MC median 731%,
        #: dd p95/p99 26.51/32.68, P(dd>20) 25.4% (23 with it: 832%, 24.59/29.72).
        "eurjpy:gated_orb",
        #: SEATED 2026-09-22 into the freed jp225 seats. `usdjpy:aroon` is an
        #: Aroon breakout, 43 OOS trades at pf 1.88 and the best win rate of the
        #: three; `ethusd:break_retest` is a PDR level retested at VWAP, 193
        #: trades pf 1.29; `eurjpy:swing_ma` is a 28/140 swing cross that earns
        #: its seat on correlation rather than return -- it is the smallest
        #: contributor in the book and lost money in 2023 and 2025.
        #:
        #: `eurjpy:xma_cross` WAS THE FOURTH AND IS DELIBERATELY ABSENT. It
        #: scored better paired than `eurjpy:swing_ma` alone, and it shares that
        #: cell's ENTRY exactly -- fast 28, slow 140, direction breakout -- so
        #: seating both buys one moving-average cross twice and pays for two
        #: ([[inert-gate-clones-a-family]]). `ethusd:day_of_week` is out for the
        #: same class of reason: its axis is `weekday=3`, a label with no
        #: neighbourhood, so its robustness gate reports 1/1 by construction
        #: ([[all-categorical-axes-void-the-robustness-gate]]).
        "usdjpy:aroon", "ethusd:break_retest",
        #: `eurjpy:swing_ma` REMOVED 2026-09-26, operator decision (-$4 over
        #: 2025-26, 103 trades).
        #: SEATED 2026-09-04 on the HOLDOUT, not the long window. All three are
        #: decay-clean, and `usdjpy:fracdiff` is accelerating -- 18.5R in eight
        #: months of 2026 against 5.3R in all of 2025, a 2.49x ratio.
        #:
        #: `es:regime_breakout` WAS SEATED HERE AND REMOVED THE SAME DAY, on a
        #: data dependency rather than on its record: it signals off `es_1m`,
        #: the back-adjusted futures continuum, and the operator does not carry
        #: that feed. It is the second cell lost to a subscription rather than
        #: to a result ([[one-sleeve-cannot-pay-for-a-data-feed]]).
        #:
        #: IT WAS THE BEST HOLDOUT-TAIL SLEEVE MEASURED ALL SESSION and nothing
        #: replaces it. With it the OOS band was p95 18.85 / p99 24.89; every
        #: replacement lands at 19.0-20.1 / 26.2-27.5. `ethusd:pullback` and
        #: `hk50:level_confluence` were taken together as the operator's choice:
        #: they recover the RETURN (+844% OOS against +735% with es, +9,078%
        #: full) and a positive month, and give back tail -- OOS p99 27.48,
        #: spread 7.79 (1,000 paths, risk 0.13).
        #:
        #: THE FITTED WINDOW PAYS FOR THE HOLDOUT THROUGHOUT THIS BLOCK: full
        #: p99 is 27.94 against the 20-sleeve book's 26.79, so canon sits
        #: FURTHER from the operator's "p99 close to p95" target than before.
        #: Taken knowingly -- 2022-2024 is in-sample for these cells' parameters
        #: ([[exness-survivor-pool-is-oos-conditioned]]) and the holdout is the
        #: window the account is about to trade.
        "usdjpy:fracdiff", "ethusd:pullback",
        #: SEATED 2026-09-19, REPLACING `jp225:volatility_breakout` AND
        #: `jp225:break_retest`, and the reason is concentration rather than
        #: either cell's record -- both were profitable over 2022-2026.
        #:
        #: EIGHT OF TWENTY-TWO SLEEVES STOOD ON ONE INDEX. Over 2026-09-07..18,
        #: the worst opening fortnight in 123, jp225 carried 45 of 85 trades and
        #: ukoil and jp225 together 96% of the loss, at a flat 3.0-lot minimum
        #: that `risk_scale` cannot shrink ([[risk-dial-is-inert-on-a-pinned-account]],
        #: [[sep-2026-fortnight-was-a-severe-cold-start]]). The eight are not one
        #: rule eight times, but they share one session, one clock and one gap,
        #: and on a fortnight when JP225 chopped sideways -- net +0.07% -- every
        #: one of them was sawn up together.
        #:
        #: CHOSEN BY EXHAUSTIVE SEARCH, NOT GREEDILY. All 256 subsets of the
        #: eight were replayed, then every pair from a 45-cell shortlist bounded
        #: by the DATA FEED rather than by the gates -- a candidate on a symbol
        #: `idk_market_live_data_feeds` does not carry is a vendor dependency,
        #: not a strategy choice ([[one-sleeve-cannot-pay-for-a-data-feed]]).
        #:
        #: WHAT IT BUYS, on 1,000 paths at 14-day blocks, live fills:
        #: p99 marked drawdown 35.30% -> 31.32%, worst path 60.26% -> 40.66%,
        #: median return 13,065% -> 22,261%, and no path halves the account
        #: where canon had one. The worst COLD opening fortnight at $450 goes
        #: -28.12% -> -16.86% and its drawdown 34.59% -> 23.45%.
        #:
        #: WHAT IT COSTS: median path drawdown rises 13.56% -> 15.52%. A bumpier
        #: ordinary ride for a materially shorter tail, taken knowingly.
        #:
        #: `usdjpy:half_life` REQUIRED A NEW RUST FAMILY -- the OU fit and its
        #: prefix-sum z-score -- so it is the first cell seated here whose port
        #: was written for the replacement rather than for the original book.
        #: `ethusd:kalman` reuses `jp225:kalman`'s filter at its other arm, the
        #: residual, which is a mean-reversion claim rather than a trend one.
        #:
        #: STILL NOT A HOLDOUT. The search window sits inside the period the
        #: survivor pool was screened on ([[exness-survivor-pool-is-oos-conditioned]]).
        "ethusd:kalman", "usdjpy:half_life",
        #: SEATED 2026-09-23 after `ukoil:xma_cross` left, chosen on MONTE CARLO
        #: RETURN with realised holdout drawdown held under 15% -- the operator's
        #: rule, not the drawdown-neutral one used for the three above. A strict
        #: screen (no window's drawdown up more than 0.5pp) admitted nothing that
        #: also added return, so the trade here is explicit:
        #:
        #:     1,000 paths, live fills, 2025-26, $500     18 sleeves   +these 3
        #:     median return                                527%        724%
        #:     p5 return                                    242%        309%
        #:     MTM dd p95 / p99                      23.38/28.81  25.28/29.92
        #:     P(dd > 20%)                                  13.0%       22.7%
        #:
        #: Best of 130 memberships from the top nine single adds; the two runners-
        #: up had LOWER return and a FATTER tail (p99 32.5% and 35.7%), so this
        #: is not the highest-return row bought with the worst tail. Realised
        #: holdout drawdown 13.84%, but 19.51% over 2022-2026 -- the 15% line
        #: holds on the holdout only.
        #:
        #: Params diffed against every seated sleeve on the same symbol: only
        #: generic knobs are shared, the signal-defining ones all differ.
        #: CONCENTRATION RISES: ethusd 6 -> 8 sleeves, usdjpy 6 -> 7, and that is
        #: where the extra tail comes from.
        #: REMOVED 2026-09-26, operator decision, after the 2022-2026 Monte
        #: Carlo reversed the 2025-26 one. Over five years the three cost far
        #: more tail than the recent window showed:
        #:
        #:     2022-2026, 1,000 paths, live fills   base 18    with the three
        #:     median return                          4,138%       6,734%
        #:     MTM dd median / p99             17.93 / 34.48  22.30 / 44.35
        #:     P(dd > 20%)                             33.1%        69.4%
        #:
        #: Every replacement tried sits on the same fed markets (usdjpy, ethusd)
        #: as the sleeves already seated, so each added correlated risk rather
        #: than spreading it -- the concentration the jp225 exit was meant to end.
        #: "ethusd:roofing", "ethusd:level_confluence", "usdjpy:rvol"
        #:
        #: RE-SEATED 2026-09-26, operator decision, together with seven
        #: WEEKEND-ONLY crypto cells (`WEEKEND_ONLY` below): the best-return
        #: membership of all 4,096 subsets of these three plus nine weekend
        #: candidates, on 2025-26.
        #:
        #:     2025-26, live fills, $500, 500 paths   base 18   A (+3)   this (+10)
        #:     realised return / MTM dd       662% / 12.10  953% / 13.84  1,148% / 13.78
        #:     median return                          528%      731%       888%
        #:     MTM dd p95 / p99               23.30/29.05  25.22/29.92  25.68/30.86
        #:     P(dd > 20%)                           11.8%     23.2%      23.8%
        #:
        #: THE 2022-2026 TAIL IS THE PRICE, and it was shown before the choice:
        #: A + all nine weekend cells there ran median 7,431%, p99 dd 50.3%,
        #: P(dd > 20%) 77.8% (base 18: 4,138%, 34.9%, 33.1%). The weekend cells
        #: were picked on the 2025-26 window they are scored on, and `efficiency`
        #: and `cci` only broke even over 2018-24.
        "ethusd:roofing", "ethusd:level_confluence", "usdjpy:rvol",
        "ethusd:efficiency", "ethusd:cci", "ethusd:linreg_trend",
        #: REMOVED the same day, operator decision on their 2025-26 dollars:
        #: `eurjpy:swing_ma` (above) and the weekend `ethusd:kendall` (+$1),
        #: `ethusd:aroon` (+$29), `btc:dmi` (+$43, 7 trades), `ethusd:idio_break`
        #: (+$55). 2025-26, live fills, $500, 500 paths:
        #:
        #:                           28 sleeves   23 (this)   22 (also no usdjpy:kendall)
        #:     realised / MTM dd   1,148%/13.78  1,063%/13.97   964%/13.97
        #:     MC median               888%          832%          731%
        #:     MC dd p95 / p99     25.68/30.86   24.59/29.72   26.51/32.68
        #:     P(dd > 20%)            23.8%         22.2%         25.4%
        #:
        #: `usdjpy:kendall` was advised to stay (dropping it costs ~100 points
        #: of return AND fattens the tail); the operator removed it anyway.
        )

#: Cells that ENTER ON SATURDAY AND SUNDAY ONLY, inside their ordinary 09:30-16:00
#: New York session. Their trades are `cfd_families.backtest` run with
#: `ENTRY_DAYS` set to the weekend for that cell alone (`sleeve_trades`), which
#: is exactly `EXNESS_ENTRY_DAYS=sat,sun`. None of them shares a family with an
#: every-day ethusd sleeve, so no signal is traded twice.
WEEKEND_ONLY = frozenset({
    "ethusd:efficiency", "ethusd:cci", "ethusd:linreg_trend",
})



#: Ported from `live_trade/src/sizing/volatility_target.rs` so the book sizes the
#: way the live engine does. The difference from what this module used before is
#: that it is TWO-SIDED: the old throttle was `min(1.0, target/realized)`, which
#: could only ever cut risk, so in a calm regime every sleeve sat at full size
#: and the book's risk was whatever the raw fraction happened to produce.
VOL_TARGET = 0.20                 # annualised, matching VolTargetConfig::default
VOL_HALFLIFE = 20.0
VOL_MAX_MULTIPLIER = 3.0
VOL_MIN_DAYS = 30


def daily_multipliers(closes_by_ts):
    """`{day: multiplier}` from an EWMA of daily returns.

    NO LOOKAHEAD. The multiplier offered for a day is computed from days
    STRICTLY BEFORE it -- the day's own return is folded in only after the
    multiplier for that day has been recorded. Reading it the other way would
    let a sleeve size down on the morning of a crash it has not seen yet, which
    is the most flattering bug available in a risk model.
    """
    by_day = {}
    for ts, close in closes_by_ts.items():
        day = ts // 86_400
        if day not in by_day or ts > by_day[day][0]:
            by_day[day] = (ts, close)

    lam = 0.5 ** (1.0 / VOL_HALFLIFE)
    out, variance, seen, previous = {}, 0.0, 0, None
    for day in sorted(by_day):
        if seen < VOL_MIN_DAYS or variance <= 0.0:
            out[day] = 1.0                      # warm-up sits at base size
        else:
            annual = math.sqrt(variance * 252.0)
            out[day] = (min(VOL_TARGET / annual, VOL_MAX_MULTIPLIER)
                        if annual > 0 else 1.0)
        close = by_day[day][1]
        if previous is not None and previous > 0 and close > 0:
            change = close / previous - 1.0
            variance = (change * change if seen == 0
                        else lam * variance + (1.0 - lam) * change * change)
            seen += 1
        previous = close
    return out


#: Let the self-imposed notional ceiling SHRINK a position but never REFUSE one.
#:
#: `MARGIN_FRACTION` is a 4x notional cap this repository chose; it is not the
#: broker's margin. Measured from the terminal on 2026-09-01, against a
#: 1:2000000000 account, one minimum lot costs:
#:
#:     de40 $10.66   jp225 $6.18   ukoil $4.51   nq $3.69   uk100 $3.66
#:     xniusd $3.36  es $2.69      btc $1.98     hk50 $1.14  ethusd $0.62
#:     audusd, eurjpy, gbpjpy, gbpusd, usdjpy  $0.00
#:
#: So the recorded note that "a $400 account still cannot place 0.07 DE40 lots"
#: is FALSE for this account -- it needs $10.66 and holds $392. `de40` was
#: dropped twice on that premise. Every refusal this book has ever booked was
#: the 4x policy, never the broker.
#:
#: WITH THIS ON, a request that would otherwise be refused outright is placed at
#: `volume_min` provided REAL equity covers the broker's own margin for it with
#: `MIN_LOT_MARGIN_BUFFER` to spare. The ceiling still bounds every request
#: above the minimum, so this raises no discretionary size -- it only stops the
#: policy vetoing participation.
#:
#: WHAT IT COSTS, STATED PLAINLY. A minimum lot can be worth more than the 4x
#: cap allows: de40 is 5.41x equity at $392, gbpjpy 3.46x, nq 3.76x. Per trade
#: the loss is still bounded by the stop, not by notional -- but GAP risk scales
#: with notional, and if many sleeves are open at once the simultaneous total
#: can reach ~37x. The lever that bounds THAT is `gross_cap`, which canon
#: currently has OFF ([[gross-exposure-cap-beats-its-null]]). Turning this on
#: without reconsidering the gross cap trades a refusal problem for a
#: simultaneity problem.
#:
#: It also removes a pro-cyclical de-diversification: refusals bite hardest when
#: equity is lowest, so a drawdown used to switch sleeves off and concentrate
#: the book exactly when it hurt ([[minimum-lot-de-diversifies-pro-cyclically]]).
#: ON BY DEFAULT SINCE 2026-09-01, by operator decision. `EXNESS_MIN_LOT_ALWAYS=0`
#: restores the old veto for a comparison. Measured on the canon book with
#: `de40:consecutive` seated, over 2025-01-02..2026-08-21:
#:
#:     $392.25   4x cap  +870.9%  MTM dd 14.9%   1 refused (de40)
#:     $392.25   this on +871.1%  MTM dd 14.9%   0 refused
#:     $400.00   4x cap  +853.4%  MTM dd 14.7%   1 refused (de40)
#:     $400.00   this on +852.7%  MTM dd 14.6%   0 refused
#:     $500.00   either  +694.6%  MTM dd 13.5%   0 refused -- the cap never binds
#:
#: Return moves by under a point and drawdown does not move: this is one trade
#: in ~6,900. It is not a performance change and must not be reported as one.
#: What it buys is that the backtest and the account place the SAME orders.
MIN_LOT_ALWAYS = os.environ.get("EXNESS_MIN_LOT_ALWAYS", "1") == "1"

#: How many times the broker's own minimum-lot margin real equity must cover
#: before the minimum lot is allowed through. Margin here is single-digit
#: dollars, so this is cheap insurance against a stop-out on margin rather than
#: on the strategy's own stop.
MIN_LOT_MARGIN_BUFFER = 5.0

#: `{symbol: broker margin in USD for ONE minimum lot}`, read from the terminal
#: by the block that wrote `exness_min_lot_margin.json`. Absent symbols fall
#: back to the 4x ceiling, which is the conservative direction.
_MIN_LOT_MARGIN = {}


def min_lot_margin(symbol):
    """Broker margin for one minimum lot, or None if it was never measured."""
    if not _MIN_LOT_MARGIN:
        path = os.path.join(RESULTS, "exness_min_lot_margin.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as handle:
            for key, row in json.load(handle).items():
                _MIN_LOT_MARGIN[key] = row["min_lot_margin_usd"]
    return _MIN_LOT_MARGIN.get(symbol)


def size(sleeve, equity, trade, ctx, scale, sizing_cap, risk_scale, shown=None,
         vol_mult=1.0, force_minimum=False):
    """Lots for one entry, sized the way `combined_book.combine` sizes one.

    Three multiplicative knobs, matching that module rather than inventing new
    ones:

      `risk_scale`   one global factor on `RISK_FRACTION`, the book-wide dial.
      `scale`        per-sleeve, its `SLEEVE_SCALE`. A sleeve held at 0.0 is
                     stood down, which is not the same thing as unaffordable.
      `sizing_cap`   per-sleeve ceiling on the equity a sleeve is allowed to
                     SIZE against, its `SIZING_EQUITY_CAP`. Compounding is what
                     turns a 20-month run into a leverage schedule, and this is
                     the knob that stops one sleeve riding the whole balance up.

    The margin ceiling reads the FULL equity, not the scaled or capped figure:
    margin is a broker constraint on the real account and does not shrink
    because the book chose to bet less. `volume_min` is a refusal, not a
    rounding ([[four-hundred-dollars-selects-the-sleeves-for-you]]).

    THE ONE EXCEPTION IS `MARGIN_EQUITY_FLOOR`, AND IT IS AN ASSUMPTION, NOT A
    MEASUREMENT. A sleeve named there is sized and margined against at least
    that many dollars however small the real balance is, which is the only way
    to hold a symbol whose minimum lot costs more margin than the account has.
    Read the constant's own note before trusting any number it produces.
    """
    spec = ctx["cfg"]
    # `multiplier` is ALREADY in the account currency -- MT5 reports
    # `trade_tick_value` converted -- so it must not be multiplied by
    # `fx_to_usd` again. That double conversion was a 159x error on a JPY cross
    # ([[mt5-tick-value-is-account-currency]]). `margin_lot` below keeps its
    # conversion because `entry * contract_size` really is in profit currency.
    money = spec["multiplier"]
    if money <= 0 or trade["distance"] <= 0 or equity <= 0:
        return 0.0, money
    factor = scale.get(sleeve, 1.0) * risk_scale
    if factor <= 0:
        return 0.0, money
    # A sleeve with a floor is treated as holding at least that much equity for
    # BOTH legs -- the risk request and the broker's margin ceiling. Flooring
    # only the request would change nothing at all, which is exactly why
    # SHOWN_EQUITY could never bring DE40 back
    # ([[shown-equity-cannot-fix-a-margin-refusal]]).
    floor = MARGIN_EQUITY_FLOOR.get(sleeve, 0.0)
    margin_equity = max(equity, floor)
    # THE FLOOR IS FOR THE MARGIN GATE ONLY. `sizing_equity` keeps REAL equity,
    # so a floored sleeve never sizes its RISK against money the account does
    # not hold: below its floor it places the broker minimum and nothing more.
    # Letting the floor through to here made a sleeve bet a rising fraction of
    # the balance exactly as that balance fell, which is the opposite of what
    # a floor is for.
    sizing_equity = (min(equity, sizing_cap.get(sleeve, float("inf")))
                     * (shown or SHOWN_EQUITY).get(sleeve, 1.0))
    # `vol_mult` REPLACES the old one-sided `min(1.0, vol_target/realized)`
    # throttle rather than stacking with it -- applying both would charge the
    # same regime twice and leave the book unable to size up at all.
    risk = sizing_equity * ef.RISK_FRACTION * factor * vol_mult
    raw = risk / (trade["distance"] * money)
    margin_lot = (trade["entry"] * spec["contract_size"]
                  * spec["fx_to_usd"] * ef.MARGIN_FRACTION)
    ceiling = (min(spec["volume_max"], margin_equity / margin_lot)
               if margin_lot > 0 else 0.0)
    step = spec["volume_step"]
    lots = math.floor(min(raw, ceiling) / step + 1e-10) * step
    if force_minimum and raw > 0 and lots + 1e-10 < spec["volume_min"]:
        # The 4x policy ceiling, as before.
        allowed = ceiling + 1e-10 >= spec["volume_min"]
        if not allowed and MIN_LOT_ALWAYS:
            # The BROKER's own requirement, which is what actually decides
            # whether the order can be placed at all. `equity` and not
            # `margin_equity`: a floor is a modelling device and must not be
            # spent as if it were money.
            needed = min_lot_margin(ctx["symbol"])
            allowed = (needed is not None
                       and equity >= needed * MIN_LOT_MARGIN_BUFFER)
        if allowed:
            lots = spec["volume_min"]
    return (round(lots, 8) if lots + 1e-10 >= spec["volume_min"] else 0.0), money


def replay(members, trades_by_sleeve, bars_by_symbol, ctx_by_symbol,
           initial=ef.INITIAL_BALANCE, scale=None, sizing_cap=None,
           risk_scale=1.0, gross_cap=None, lo=None, hi=None, shown=None,
           fair_cap=False, force_minimum=None, regime_gate=None,
           marking_cache=None):
    """Every member's trades against ONE compounding balance, in entry order.

    Lots are recomputed at entry from the live shared equity, so a sleeve's size
    depends on what every other sleeve has already done to the account. That
    coupling is the whole reason this is not an addition of return streams.

    DRAWDOWN IS REPORTED TWICE, for the reason `combined_book` reports it twice.
    `max_dd_pct` books a position only when it closes, which is the optimistic
    number -- a closed-trade replay once read 21% where the engine read 37%.
    `mtm_dd_pct` re-marks every open position against its own symbol's bars, so
    intraday open risk is visible. **Read the MTM one.** The closed figure is
    kept only so the gap between them can be seen, and on an intraday book that
    gap IS the risk ([[engine-drawdown-is-mark-to-market]],
    [[book-drawdown-is-one-intraday-position]]).

    `gross_cap` bounds SIMULTANEOUS exposure: an entry that would push the
    summed notional of everything currently open past `gross_cap` multiples of
    equity is refused outright rather than trimmed, because trimming would
    change the sleeve's own risk model instead of the book's. It is the one
    lever that can reach an intraday drawdown, because that drawdown is made of
    positions open at the same moment, and it is the only risk lever that has
    ever cleared a random-refusal control ([[gross-exposure-cap-beats-its-null]]).

    `fair_cap` changes WHO gets that budget. A single shared pool is
    first-come-first-served, and that is not neutral: the sleeve that trades
    most often is usually already holding the exposure when the others want it,
    so the cap allocates the book by trade frequency instead of by quality. On
    the 7-sleeve book a 3x shared pool refused 99.8% of nq:ofi's entries, 96.2%
    of de40:ib's and 92.6% of de40:consecutive's, while refusing only 7.1% of
    ethusd:consecutive's -- which is why ETHUSD produced 95% of the P&L in a
    book where it was one of several comparable sleeves standalone.

    With `fair_cap` each sleeve gets `gross_cap / n` of equity as its OWN
    budget, so a high-frequency member cannot monopolise the pool. Total
    exposure is still bounded by `gross_cap`, by construction.

    `regime_gate` is an optional `f(sleeve, entry_ts) -> bool` consulted before
    a trade is sized. False refuses the entry outright, which is what makes a
    sleeve genuinely DEAD in a regime rather than merely small -- a scale of
    zero would still be counted as a fill by every downstream table. Refusals
    are counted separately from the broker-minimum and gross-cap ones so the
    three reasons a sleeve went quiet stay distinguishable
    (`exness_regime_switch` drives this).
    """
    scale = {} if scale is None else scale
    sizing_cap = {} if sizing_cap is None else sizing_cap
    force_minimum = (FORCE_MINIMUM_LOT if force_minimum is None
                     else force_minimum)
    lo = ef.IS_END if lo is None else lo
    hi = ef.OOS_END if hi is None else hi
    pending = []
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        pending.extend(trades_by_sleeve[key])
    # Trades outside the window are dropped HERE rather than being left to the
    # grid to ignore. The event loop opens everything with `entry_ts <= stamp`,
    # so anything earlier than the first grid stamp would otherwise be opened
    # on that stamp -- all of it, simultaneously. That is how a mis-windowed
    # price series turned 4,600 stale NQ entries into a 78.8% drawdown.
    outside = sum(1 for t in pending if not lo <= t["entry_ts"] < hi)
    pending = [t for t in pending if lo <= t["entry_ts"] < hi]
    pending.sort(key=lambda t: (t["entry_ts"], t["sleeve"]))

    #: A revaluation grid: every timestamp any member's symbol printed a bar.
    #:
    #: WINDOW-INDEPENDENT, WHICH IS WHY `marking_cache` CAN EXIST. `price_at`
    #: holds every bar of every symbol and is not filtered by `lo`/`hi` at all;
    #: the full sorted stamp list is, but only by a slice at the end. A caller
    #: that replays MANY windows over the SAME bars -- the Monte Carlo chains
    #: 121 of them per path -- otherwise rebuilds both from scratch every time,
    #: and that dominated the run: a 14-day block cost 0.81s against 15.7s for
    #: the whole 4.7-year window, one twentieth of the cost for one hundred and
    #: twenty-first of the trades.
    #:
    #: `marking_cache` is opt-in and defaults to None, so every existing caller
    #: computes exactly what it computed before. When passed, it must be a dict
    #: owned by the caller and used only for replays over the same
    #: `bars_by_symbol`; it is keyed by the external market set because that is
    #: the only thing besides the bars that changes what goes in.
    #:
    #: ONE PRECONDITION, AND IT IS A REAL FOOTGUN. `external_prices` is called
    #: with a span derived from THIS replay's `lo`/`hi`, so without
    #: `exness_combined_montecarlo.pin_external_window` the first cached call
    #: would store one block's worth of external stamps and every later window
    #: would silently mark against a grid that stops early. Pin the span before
    #: passing a cache, or do not pass one.
    cache = marking_cache
    key = frozenset(t["symbol"] for t in pending if t.get("external"))
    if cache is not None and key in cache:
        price_at, stamps_all, vol_mult = cache[key]
        external = set(key)
    else:
        price_at = {symbol: {bar[ef.TS]: bar[ef.C] for bar in bars}
                    for symbol, bars in bars_by_symbol.items()}
        stamps_all = None
    grid = sorted({bar[ef.TS] for symbol in bars_by_symbol
                   for bar in bars_by_symbol[symbol]
                   if lo <= bar[ef.TS] < hi}) if stamps_all is None else None

    # External sleeves mark against their own market's series, which is minute
    # or second data rather than this study's 30m bars. Their stamps are added
    # to the revaluation grid so an NQ position that opens and closes between
    # two 30m bars is still marked -- otherwise its whole excursion would be
    # invisible and the book's drawdown would be understated.
    if stamps_all is None:
        external = {t["symbol"] for t in pending if t.get("external")}
        if external:
            span = (datetime.fromtimestamp(lo, tz=timezone.utc).strftime("%Y-%m-%d"),
                    datetime.fromtimestamp(hi, tz=timezone.utc).strftime("%Y-%m-%d"))
            price_at.update(external_prices(external, window=span))
            for market in external:
                grid = sorted(set(grid) | {ts for ts in price_at[market]
                                           if lo <= ts < hi})

        #: One EWMA vol multiplier series per market, built AFTER the external
        #: grids are merged so imported sleeves get the same treatment as native
        #: ones. Built from the full series rather than the windowed one so the
        #: 30-day warm-up is already served by the time the window opens.
        vol_mult = {symbol: daily_multipliers(closes)
                    for symbol, closes in price_at.items()}
        if cache is not None:
            # The FULL stamp list, so a later window is a slice rather than a
            # rebuild. Stored only once the external grids are merged in, since
            # those add stamps of their own.
            cache[key] = (price_at,
                          sorted({ts for prices in price_at.values()
                                  for ts in prices}),
                          vol_mult)
    else:
        left = bisect.bisect_left(stamps_all, lo)
        right = bisect.bisect_left(stamps_all, hi)
        grid = stamps_all[left:right]

    equity = initial
    closed_peak, closed_dd = initial, 0.0
    marked_peak, marked_dd = initial, 0.0
    worst_at = None
    #: Per-sleeve books. A sleeve sharing an account has no balance of its own,
    #: so its drawdown is the worst DOLLAR giveback from its own high-water mark
    #: of cumulative contribution -- realised plus its own open positions. Same
    #: definition as `combined_book.sleeve_drawdown`, and it is why the sleeve
    #: drawdowns below do not sum to the book's: they peak at different moments.
    names = sorted({t["sleeve"] for t in pending})
    realized = {name: 0.0 for name in names}
    sleeve_peak = {name: 0.0 for name in names}
    sleeve_dd = {name: 0.0 for name in names}
    sleeve_closed_peak = {name: 0.0 for name in names}
    sleeve_closed_dd = {name: 0.0 for name in names}
    #: Per-sleeve state at the high-water mark preceding the worst marked fall,
    #: and at the trough itself. The difference is what each sleeve actually
    #: gave back during the book's worst moment, which is a different question
    #: from `sleeve_dd` -- that is each sleeve's own worst giveback whenever it
    #: happened, and those moments do not coincide.
    peak_state, dd_from, dd_to = {}, {}, {}
    sleeve_trades = {name: 0 for name in names}
    open_positions = []
    settled = []
    skipped, refused = {}, 0
    refused_by = {}
    gated_by = {}
    exposed_by = {}
    exposed = 0.0
    index = 0
    curve, marked_curve = [], []

    for stamp in grid:
        # 1. close anything that exits at or before this instant
        still = []
        for position in open_positions:
            if position["exit_ts"] <= stamp:
                equity += position["pnl"]
                exposed -= position["notional"]
                exposed_by[position["sleeve"]] = (
                    exposed_by.get(position["sleeve"], 0.0) - position["notional"])
                realized[position["sleeve"]] += position["pnl"]
                sleeve_trades[position["sleeve"]] += 1
                settled.append(position)
                closed_peak = max(closed_peak, equity)
                if closed_peak > 0:
                    closed_dd = max(closed_dd, (closed_peak - equity) / closed_peak)
                curve.append((position["exit_ts"], equity))
            else:
                still.append(position)
        open_positions = still

        # 2. open anything that enters at this instant, sized off live equity
        while index < len(pending) and pending[index]["entry_ts"] <= stamp:
            trade = pending[index]
            index += 1
            sleeve = trade["sleeve"]
            # Consulted BEFORE sizing, so a dead sleeve costs no equity and
            # takes no gross-cap budget from the sleeves that are alive.
            if regime_gate is not None and not regime_gate(sleeve,
                                                           trade["entry_ts"]):
                gated_by[sleeve] = gated_by.get(sleeve, 0) + 1
                continue
            if trade.get("external"):
                # `units_per_dollar` already carries this strategy's own risk
                # fraction, stop and margin ceiling, so the shared equity is the
                # only thing left to multiply by. Same shape as
                # `combined_book.combine`: units x equity x multiplier, floored
                # to the broker's step.
                factor = scale.get(sleeve, 1.0) * risk_scale
                sizing_equity = (min(equity, sizing_cap.get(sleeve, float("inf")))
                                 * (shown or SHOWN_EQUITY).get(sleeve, 1.0))
                mult = vol_mult.get(trade["symbol"], {}).get(
                    trade["entry_ts"] // 86_400, 1.0)
                raw = trade["units_per_dollar"] * sizing_equity * factor * mult
                step = trade["step"]
                lots = math.floor(raw / step) * step if step else raw
                if force_minimum and raw > 0 and lots <= 0 and step > 0:
                    lots = step
                money = trade["point_value"]
            else:
                ctx = ctx_by_symbol[trade["symbol"]]
                mult = vol_mult.get(trade["symbol"], {}).get(
                    trade["entry_ts"] // 86_400, 1.0)
                lots, money = size(sleeve, equity, trade, ctx, scale,
                                   sizing_cap, risk_scale, shown, vol_mult=mult,
                                   force_minimum=force_minimum)
            if lots <= 0:
                # The shared balance could not afford this at broker minimum --
                # a refusal, not a rounding, and on a $1,000 account often the
                # binding constraint
                # ([[small-balance-hides-drawdown-by-dropping-trades]]).
                if scale.get(sleeve, 1.0) * risk_scale > 0:
                    skipped[sleeve] = skipped.get(sleeve, 0) + 1
                continue
            notional = abs(lots * trade["entry"] * money)
            # Fair mode measures against this sleeve's OWN share of the budget;
            # shared mode against the single pool every sleeve competes for.
            if fair_cap and gross_cap is not None:
                budget = gross_cap / max(1, len(names)) * equity
                over = exposed_by.get(sleeve, 0.0) + notional > budget
            else:
                over = (gross_cap is not None
                        and exposed + notional > gross_cap * equity)
            if over and equity > 0:
                refused += 1
                # Counted per sleeve because the cap is FIRST-COME-FIRST-SERVED
                # and that is not neutral: whichever sleeve trades most often
                # holds the exposure when everyone else wants it, so the cap
                # silently allocates the book by trade frequency rather than by
                # quality. Without this counter a crowded-out sleeve is
                # indistinguishable from one that had no signals.
                refused_by[sleeve] = refused_by.get(sleeve, 0) + 1
                continue
            exposed += notional
            exposed_by[sleeve] = exposed_by.get(sleeve, 0.0) + notional
            open_positions.append({
                **trade, "lots": lots, "money_per_point": money,
                "notional": notional,
                "pnl": trade["points"] * lots * money})

        # 3. mark the book to market on this bar, per sleeve and in total
        unrealized = 0.0
        open_by_sleeve = {}
        for position in open_positions:
            close = price_at[position["symbol"]].get(stamp)
            if close is None:
                continue
            move = position["side"] * (close - position["entry"])
            value = move * position["lots"] * position["money_per_point"]
            unrealized += value
            open_by_sleeve[position["sleeve"]] = (
                open_by_sleeve.get(position["sleeve"], 0.0) + value)
        for name in names:
            contribution = realized[name] + open_by_sleeve.get(name, 0.0)
            if contribution > sleeve_peak[name]:
                sleeve_peak[name] = contribution
            sleeve_dd[name] = max(sleeve_dd[name],
                                  sleeve_peak[name] - contribution)
            # The same giveback on CLOSED contribution only. Reported beside the
            # marked figure so the gap between them -- the sleeve's open risk --
            # is visible per sleeve, not just for the book as a whole.
            if realized[name] > sleeve_closed_peak[name]:
                sleeve_closed_peak[name] = realized[name]
            sleeve_closed_dd[name] = max(
                sleeve_closed_dd[name], sleeve_closed_peak[name] - realized[name])
        marked = equity + unrealized
        here = {name: realized[name] + open_by_sleeve.get(name, 0.0)
                for name in names}
        if marked > marked_peak:
            # Snapshot who was holding what at each new high-water mark, so the
            # eventual worst fall can be attributed without a second pass.
            marked_peak, peak_state = marked, here
        if marked_peak > 0:
            fall = (marked_peak - marked) / marked_peak
            if fall > marked_dd:
                marked_dd, worst_at = fall, stamp
                dd_from, dd_to = peak_state, here
        marked_curve.append((stamp, marked))
        if marked <= 0:
            break

    for position in open_positions:
        equity += position["pnl"]
        realized[position["sleeve"]] += position["pnl"]
        sleeve_trades[position["sleeve"]] += 1
        settled.append(position)
        closed_peak = max(closed_peak, equity)
        if closed_peak > 0:
            closed_dd = max(closed_dd, (closed_peak - equity) / closed_peak)
        curve.append((position["exit_ts"], equity))

    # Share of the worst marked fall borne by each sleeve. Denominator is the
    # sum of the LOSING moves only: sleeves that gained through the window are
    # not netted off, because the question is who caused the fall, not what the
    # book's net was.
    losses = {name: dd_to.get(name, 0.0) - dd_from.get(name, 0.0) for name in names}
    total_loss = sum(v for v in losses.values() if v < 0)
    dd_share = ({name: v / total_loss for name, v in losses.items() if v < 0}
                if total_loss < 0 else {})

    worst_day = (datetime.fromtimestamp(worst_at, tz=timezone.utc)
                 .strftime("%Y-%m-%d") if worst_at else None)
    return {"final": equity, "initial": initial,
            "return_pct": 100.0 * (equity - initial) / initial,
            "max_dd_pct": 100.0 * closed_dd,
            "mtm_dd_pct": 100.0 * marked_dd,
            "mtm_dd_trough": worst_day,
            "trades": len(settled),
            "below_broker_minimum": skipped,
            "refused_by_gross_cap": refused,
            "refused_by_sleeve": refused_by,
            "refused_by_regime": gated_by,
            "outside_window": outside,
            "by_sleeve": {name: {"pnl": round(realized[name], 2),
                                 "trades": sleeve_trades[name],
                                 "mtm_dd_usd": round(sleeve_dd[name], 2),
                                 "closed_dd_usd": round(sleeve_closed_dd[name], 2),
                                 "peak_contribution": round(sleeve_peak[name], 2),
                                 # What this sleeve gave back during the BOOK's
                                 # worst marked window, and its share of the
                                 # total give-back across losing sleeves.
                                 "dd_event_usd": round(
                                     dd_to.get(name, 0.0) - dd_from.get(name, 0.0), 2),
                                 "dd_event_share": round(dd_share.get(name, 0.0), 3)}
                          for name in names},
            "dd_concentration": round(max(dd_share.values()) if dd_share else 0.0, 3),
            "settled": settled, "curve": curve, "marked": marked_curve}


def consistency(book, initial=ef.INITIAL_BALANCE, min_months=12):
    """How reliably the BOOK makes money month to month.

    The score is deliberately dominated by the share of positive months, with
    monthly Sharpe only breaking ties and the worst month as a penalty. Ranking
    on monthly Sharpe alone rewards a book that is merely quiet, and ranking on
    the worst month alone rewards one that barely trades.

    A run covering fewer than `min_months` months scores -inf rather than well.
    A late-starting sleeve can leave three months standing, and three good
    months score a spectacular monthly Sharpe that means nothing
    ([[short-month-series-fakes-monthly-sharpe]]).
    """
    series, sharpe, positive, total = monthly(book["settled"], initial)
    if total < min_months:
        return -math.inf, series, sharpe, positive, total
    values = [row["return_pct"] for row in series.values()]
    worst = min(values) if values else 0.0
    score = (positive / total) + 0.05 * sharpe + 0.01 * worst
    return score, series, sharpe, positive, total


def symbol_budget(members):
    """`{sleeve: 1/n}` where n is how many members trade that sleeve's symbol.

    THE RISK BUDGET BELONGS TO THE SYMBOL, NOT THE SLEEVE. Two ETHUSD rules
    inevitably lose on the same days -- they are reading one instrument in one
    session -- and the pairwise `loss_lift` gate therefore refuses every ETHUSD
    sibling once one is seated, which is why the book stalls at six members
    while five of the ten best candidates sit outside it.

    Splitting one budget across them answers the same objection differently:
    two half-size ETHUSD sleeves carry the SAME joint loss as one full-size
    sleeve, so nothing is stacked ([[combined-book-stacks-risk-budgets]]), while
    the entry is now an average of two rules instead of one bet. That is
    diversification of signal rather than of exposure, and it is the only kind
    available on a six-instrument universe.

    Cross-symbol coupling is a different problem and is still gated normally.
    """
    counts = {}
    for member in members:
        counts[member["symbol"]] = counts.get(member["symbol"], 0) + 1
    return {f"{m['symbol']}:{m['family']}": 1.0 / counts[m["symbol"]]
            for m in members}


def choose_by_consistency(rows, streams, logs, bars_by, ctx_by, limit,
                          max_rho, max_lift, forced=(), initial=ef.INITIAL_BALANCE,
                          risk_scale=1.0, gross_cap=None, quiet=False,
                          per_symbol=False):
    """Greedy FORWARD selection on the book's own month-to-month consistency.

    The earlier `choose_members` ranked candidates by standalone holdout return
    and admitted them if they passed the pairwise downside gates. That is not
    the same as improving the book, and it demonstrably was not: at real
    spreads it took fr40:swing_ma (+15.7%) over xagaud:failed_break (+4.1%) and
    the book got WORSE on three of four measures -- return 192 -> 118, Sharpe
    2.66 -> 2.09, positive months 16/20 -> 12/20, all while every pairwise gate
    passed.

    So each step here REPLAYS the whole book with each remaining candidate added
    and keeps whichever actually raises `consistency`. It is far more expensive
    -- O(members x candidates) replays instead of one pass -- and it optimises
    the thing being reported, which the standalone ranking never did.

    The downside gates still apply as a hard filter on top: a candidate that
    happens to lift consistency while losing on the same days as a sitting
    member is still refused, because that coupling is what the user asked to
    avoid and a 20-month sample is too short to trust the objective over it.

    `forced` members are seated first and never dropped. They are the operator's
    choice, not the search's.
    """
    by_key = {f"{r['symbol']}:{r['family']}": r for r in rows}
    chosen = [by_key[k] for k in forced if k in by_key]
    remaining = [r for r in rows
                 if f"{r['symbol']}:{r['family']}" not in set(forced)]
    trail = []
    seated_book = None

    while len(chosen) < limit and remaining:
        # Concentration of the CURRENTLY seated book. After the first round this
        # is just the winning trial from the previous round -- recomputing it
        # would double the replays, which are the whole cost of this search.
        if MAX_DD_CONCENTRATION is None or not chosen:
            current_concentration = 0.0
        elif seated_book is not None:
            current_concentration = seated_book.get("dd_concentration", 0.0)
        else:
            seated_book = replay(
                chosen, logs, bars_by, ctx_by,
                scale=symbol_budget(chosen) if per_symbol else SLEEVE_SCALE,
                sizing_cap=sizing_caps(chosen), risk_scale=risk_scale,
                gross_cap=gross_cap, initial=initial,
                          fair_cap=FAIR_CAP)
            current_concentration = seated_book.get("dd_concentration", 0.0)
        best = None
        for candidate in remaining:
            key = f"{candidate['symbol']}:{candidate['family']}"
            worst_rho, worst_lift = -1.0, 0.0
            for member in chosen:
                other = f"{member['symbol']}:{member['family']}"
                # Under a per-symbol budget a same-symbol pair is not a stacked
                # bet -- both shrink to make room for each other -- so the
                # pairwise gate is skipped for them and applied only across
                # symbols, which is where coupling actually adds risk.
                if per_symbol and member["symbol"] == candidate["symbol"]:
                    continue
                worst_rho = max(worst_rho, down_rho(streams[key], streams[other]))
                worst_lift = max(worst_lift, loss_lift(streams[key], streams[other]))
            if chosen and (worst_rho > max_rho or worst_lift > max_lift):
                continue
            trial = chosen + [candidate]
            scale = symbol_budget(trial) if per_symbol else SLEEVE_SCALE
            book = replay(trial, logs, bars_by, ctx_by, scale=scale,
                          sizing_cap=sizing_caps(trial), risk_scale=risk_scale,
                          gross_cap=gross_cap, initial=initial,
                          fair_cap=FAIR_CAP)
            score, _series, sharpe, positive, total = consistency(book, initial)
            # Concentration is a property of the WHOLE book, and the way to cut
            # it is to add sleeves -- so a hard filter here is self-defeating:
            # the seed book sat at 64.7%, every addition still exceeded 40%, and
            # rejecting them all left the seed untouched at 64.7%. Instead, while
            # the book is over the limit the objective SWITCHES to reducing
            # concentration; once under, it goes back to consistency. A single
            # position marked through one bad session is not a portfolio
            # drawdown ([[book-drawdown-is-one-intraday-position]]).
            crowded = (MAX_DD_CONCENTRATION is not None
                       and current_concentration > MAX_DD_CONCENTRATION)
            rank = (-book.get("dd_concentration", 0.0)) if crowded else score
            if best is None or rank > best[0]:
                best = (rank, candidate, book, sharpe, positive, total,
                        worst_rho, worst_lift, score)
        if best is None:
            break
        rank, candidate, book, sharpe, positive, total, rho, lift, score = best
        # Stop when the best available addition no longer helps. More sleeves is
        # more risk budget, so "does not improve consistency" is a reason to
        # stop rather than something to spend the cap on.
        crowded = (MAX_DD_CONCENTRATION is not None
                   and current_concentration > MAX_DD_CONCENTRATION)
        if trail and score <= trail[-1]["score"] and not crowded:
            if not quiet:
                print(f"  stopping: best remaining addition "
                      f"({candidate['symbol']}:{candidate['family']}) would "
                      f"lower consistency {trail[-1]['score']:.4f} -> {score:.4f}")
            break
        candidate = {**candidate, "max_down_rho": round(rho, 3),
                     "max_loss_lift": round(lift, 3)}
        chosen.append(candidate)
        seated_book = book          # this trial becomes next round's seated book
        remaining = [r for r in remaining if r is not by_key.get(
            f"{candidate['symbol']}:{candidate['family']}", None)
            and f"{r['symbol']}:{r['family']}" !=
            f"{candidate['symbol']}:{candidate['family']}"]
        trail.append({"added": f"{candidate['symbol']}:{candidate['family']}",
                      "score": score, "return_pct": round(book["return_pct"], 1),
                      "mtm_dd_pct": round(book["mtm_dd_pct"], 1),
                      "monthly_sharpe": sharpe,
                      "positive_months": f"{positive}/{total}"})
        if not quiet:
            row = trail[-1]
            print(f"  + {row['added']:24} score {score:.4f}  "
                  f"{row['return_pct']:+8.1f}%  MTM dd {row['mtm_dd_pct']:5.1f}%"
                  f"  Sharpe {sharpe:5.2f}  {row['positive_months']}")
    return chosen, trail


def monthly(settled, initial):
    """Month-by-month P&L and a monthly Sharpe on the book's own returns."""
    months = {}
    for trade in settled:
        moment = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc)
        months[f"{moment.year}-{moment.month:02d}"] = (
            months.get(f"{moment.year}-{moment.month:02d}", 0.0) + trade["pnl"])
    equity, series = initial, {}
    for key in sorted(months):
        series[key] = {"pnl": round(months[key], 2),
                       "return_pct": round(100 * months[key] / equity, 2)}
        equity += months[key]
    values = [row["return_pct"] for row in series.values()]
    sharpe = 0.0
    if len(values) > 1 and statistics.stdev(values) > 0:
        sharpe = statistics.fmean(values) / statistics.stdev(values) * math.sqrt(12)
    return series, round(sharpe, 2), sum(1 for v in values if v > 0), len(values)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def build(limit, max_rho, max_lift, null_seed=None, stale=True,
          risk_scale=1.0, gross_cap=None, include=(), by_consistency=False,
          min_is_t=None, per_symbol=False, symbols=None, min_trades=None,
          members_exact=(), initial=CANON_INITIAL):
    # Exact books do not need candidate discovery. Apart from being wasted
    # work, that path resolves every symbol in the research pool before these
    # rows are replaced with `members_exact`; one stale unrelated table can
    # therefore prevent a recorded book from running at all.
    rows = ([] if members_exact else
            candidates(min_is_t=min_is_t, min_trades=min_trades))
    if members_exact:
        # An EXACT book: no selection at all. `BOOK` was found by `book_search`,
        # which can drop and exchange members; `build`'s greedy forward pass
        # cannot reassemble it and silently returns a different book instead.
        # Without this the canon record is not reproducible by the tool that
        # reports it.
        keep = list(members_exact)
        by_key = {f"{r['symbol']}:{r['family']}": r for r in rows}
        for name in keep:
            if name not in by_key and name in EXTERNAL:
                symbol, family = name.split(":", 1)
                by_key[name] = {"symbol": symbol, "family": family,
                                "asset_class": "external", "external": True,
                                "oos_return": float("nan"),
                                "oos_dd": float("nan"), "oos_trades": 0,
                                "oos_pf": float("nan"), "is_t": None,
                                "flip": None, "null_seeds": None}
            elif name not in by_key:
                symbol, family = name.split(":", 1)
                for f_name in os.listdir(RESULTS):
                    if f_name.startswith("exness_families_") and f"_{symbol}_" in f_name and "null" not in f_name:
                        with open(os.path.join(RESULTS, f_name), encoding="utf-8") as handle:
                            payload = json.load(handle)
                        fam_data = payload.get("families", {}).get(family)
                        if fam_data:
                            oos = (payload.get("validation", {}).get(family) or {}).get("oos") or {}
                            by_key[name] = {
                                "symbol": symbol, "family": family, "params": _retuple(fam_data["params"]),
                                "asset_class": payload.get("asset_class", "unknown"),
                                "oos_return": oos.get("return_pct", float("nan")),
                                "oos_dd": oos.get("max_dd_pct", float("nan")),
                                "oos_trades": oos.get("trades", 0),
                                "oos_pf": oos.get("pf", float("nan")),
                                "is_t": fam_data.get("in_sample", {}).get("edge_vs_drift_t_stat"),
                                "flip": None, "null_seeds": None
                            }
                            break
        missing = [n for n in keep if n not in by_key]
        if missing:
            raise SystemExit(f"--members not in the candidate pool: {missing}")
        rows = [by_key[n] for n in keep]
        include = tuple(keep)
        limit = len(keep)
    if symbols:
        # Restricting the pool to one instrument is a deliberate narrowing, not
        # a diversified book: every sleeve then reads the same session of the
        # same symbol, so the downside gate below is measuring how differently
        # several rules see ONE price series, not independent bets.
        # Accepts either a bare symbol or a specific `symbol:family` cell, so a
        # single named sleeve can be run without hand-editing the pool.
        keep = set(symbols)
        rows = [r for r in rows
                if r["symbol"] in keep or f"{r['symbol']}:{r['family']}" in keep]
    if not rows:
        raise SystemExit("no candidate cleared the null and the size gates")
    for row in rows:
        ef.resolve(row["symbol"], allow_stale=stale)

    # Sleeves the operator named that the gates would not have produced: a
    # forced exness cell (xauaud:high_52w needs min_is_t lowered) or one of the
    # `combined_book` imports, which have no sealed JSON here at all.
    for name in include:
        if name in EXTERNAL and not any(
                f"{r['symbol']}:{r['family']}" == name for r in rows):
            symbol, family = name.split(":", 1)
            rows.insert(0, {"symbol": symbol, "family": family,
                            "asset_class": "external", "oos_return": float("nan"),
                            "oos_dd": float("nan"), "oos_trades": 0,
                            "oos_pf": float("nan"), "is_t": None,
                            "flip": None, "null_seeds": None, "external": True})

    print(f"{len(rows)} candidates cleared the null and the size gates\n")
    print(f"{'symbol':9}{'family':17}{'OOS%':>8}{'dd':>6}{'n':>6}"
          f"{'PF':>6}{'IS t':>6}{'flip%':>8}{'B&H%':>8}{'B&H dd':>8}"
          f"{'ret/dd':>8}{'vs B&H':>8}  as")
    for row in rows:
        if row.get("external"):
            print(f"{row['symbol']:9}{row['family']:17}"
                  f"{'imported from combined_book':>36}")
            continue
        bh = row.get("buy_hold_ratio")
        print(f"{row['symbol']:9}{row['family']:17}{row['oos_return']:>8.1f}"
              f"{row['oos_dd']:>6.1f}{row['oos_trades']:>6}{row['oos_pf']:>6.2f}"
              f"{(row['is_t'] or 0):>6.2f}"
              + (f"{row['flip']:>8.1f}" if row["flip"] is not None else f"{'none':>8}")
              + f"{row.get('buy_hold_return', float('nan')):>8.1f}"
                f"{row.get('buy_hold_dd', float('nan')):>8.1f}"
                f"{row.get('own_ratio', float('nan')):>8.2f}"
              + (f"{bh:>8.2f}" if bh is not None else f"{'-':>8}")
              + f"  {row.get('admitted_as', '?')}")

    streams, logs, bars_by, ctx_by, standalone = {}, {}, {}, {}, {}
    for row in rows:
        key = f"{row['symbol']}:{row['family']}"
        if row.get("external"):
            # No coin-flip variant exists for an imported sleeve -- it is not an
            # cfd_families cell and `null_seed` has nothing to randomise. It
            # therefore appears UNCHANGED in the null book, which makes that
            # control weaker, and the report says so.
            # Explicit, because the default would end 2026-08-05 and quietly
            # give this sleeve eleven fewer days than every other one.
            log = external_trades(key, window=("2025-01-01", CANON_DATA_END))
            logs[key] = log
            streams[key] = daily_stream(log)
            standalone[key] = {"return_pct": float("nan"),
                               "max_dd_pct": float("nan"), "trades": len(log)}
            continue
        result, log, bars, ctx = sleeve_trades(row, null_seed)
        streams[key] = daily_stream(log)
        logs[key] = log
        bars_by[row["symbol"]] = bars
        ctx_by[row["symbol"]] = ctx
        standalone[key] = result

    # Sealed family files are admission snapshots, not proof that the same
    # parameters remain positive when the data window grows.  In particular,
    # the ES snapshot said +19.93% / 188 trades while its metadata's last row
    # was 2026-06-24; replaying the complete labeled window produced -7.07% /
    # 220 trades.  Gate on the replay we just performed so a stale/truncated
    # survivor cannot silently enter either a selected or an exact book.
    fresh_nonpositive = {
        key for key, result in standalone.items()
        if result["return_pct"] == result["return_pct"]
        and result["return_pct"] <= 0
    }
    if null_seed is None and fresh_nonpositive:
        forced_nonpositive = fresh_nonpositive.intersection(
            set(members_exact) | set(include))
        # A DELIBERATE PERTURBATION OF THE BOOK, of which there are now two.
        # `SIGNAL_FEED` re-decides the cells on a series they were not selected
        # on; a non-zero `DECISION_LAG_BARS` acts them on later than they were
        # selected to be acted on. Under either, a member turning negative is
        # the measurement rather than a fault in it.
        perturbed = SIGNAL_FEED or ef.DECISION_LAG_BARS or TICK_COSTS
        if forced_nonpositive and perturbed:
            # A forced member going non-positive is normally a STALE SURVIVOR
            # and must stop the run. Under a perturbation it is the opposite:
            # the cell is being run in conditions it was never selected under,
            # and finding which members do not survive that is the entire point
            # of the mode. So it is reported rather than raised -- otherwise the
            # run cannot produce a book at all and the one number the mode
            # exists to produce is unreachable.
            #
            # THEY ARE KEPT, NOT DROPPED, AND THAT IS THE WHOLE POINT. Dropping
            # them changes the book's MEMBERSHIP at the same time as its signal
            # feed, and the two effects are then inseparable. Measured once:
            # dropping these two cost 52pp of a 113pp fall, so a book reported
            # without them charges the membership change to the signal change.
            # Carrying a losing member is the only way the vendor and broker
            # books are the same twenty sleeves.
            print(f"\n{'BROKER-SIGNAL' if SIGNAL_FEED else 'DECISION-LAG'} "
                  "CASUALTIES -- non-positive once "
                  + ("re-decided on the broker's own bars: " if SIGNAL_FEED else
                     f"acted on {ef.DECISION_LAG_BARS} bar(s) late: ")
                  + ", ".join(sorted(forced_nonpositive))
                  + ("\n  DROPPED at request; membership no longer matches the "
                     "vendor book, so the difference mixes two changes."
                     if DROP_SIGNAL_CASUALTIES else
                     "\n  KEPT, so this book is the same sleeves as the "
                     "unperturbed run and the difference is the perturbation "
                     "alone."))
            if DROP_SIGNAL_CASUALTIES:
                members_exact = tuple(m for m in members_exact
                                      if m not in forced_nonpositive)
        elif forced_nonpositive:
            raise SystemExit(
                "fresh complete-window standalone return is non-positive for "
                f"forced member(s): {sorted(forced_nonpositive)}")
        # A member kept on purpose above must survive this filter too, or it is
        # dropped anyway one line later and the "KEPT" message is a lie. Only
        # the sleeves NOT being deliberately carried are removed here.
        carried = (set() if DROP_SIGNAL_CASUALTIES or not perturbed
                   else fresh_nonpositive.intersection(
                       set(members_exact) | set(include)))
        removed = fresh_nonpositive - carried
        rows = [row for row in rows
                if f"{row['symbol']}:{row['family']}" not in removed]
        if removed:
            print("\nfresh replay rejected non-positive standalone sleeve(s): "
                  + ", ".join(sorted(removed)))

    if members_exact:
        members = rows
        rejected = []
        trail = []
    elif by_consistency:
        print(f"\ngreedy forward selection on BOOK monthly consistency"
              + (f", forcing {', '.join(include)}" if include else ""))
        members, trail = choose_by_consistency(
            rows, streams, logs, bars_by, ctx_by, limit, max_rho, max_lift,
            forced=include, risk_scale=risk_scale, gross_cap=gross_cap,
            per_symbol=per_symbol)
        rejected = []
    else:
        members, rejected = choose_members(rows, streams, limit, max_rho,
                                           max_lift)
        trail = []

    print(f"\nadmitted {len(members)} members "
          f"(down_rho <= {max_rho}, loss_lift <= {max_lift}, cap {limit})")
    for member in members:
        # Forced members are seated before any pair exists, so they carry no
        # dependence figures. Printed as `forced` rather than 0.000, which would
        # read as "measured and independent".
        rho, lift = member.get("max_down_rho"), member.get("max_loss_lift")
        detail = ("forced by operator" if rho is None else
                  f"worst down_rho {rho:+.3f}   worst loss_lift {lift:.2f}")
        print(f"  {member['symbol']:9}{member['family']:17}{detail}")
    if rejected:
        print(f"\nrefused {len(rejected)} for losing together")
        for row in rejected[:16]:
            print(f"  {row['symbol']:9}{row['family']:17}"
                  f"{row['reason']:10} down_rho {row['down_rho']:+.3f} "
                  f"lift {row['loss_lift']:.2f}  vs {row['blocked_by']}")

    keys = [f"{m['symbol']}:{m['family']}" for m in members]
    width = max(len(k) for k in keys) + 1
    matrix = {}
    for label, fn, spec in (
            ("downside correlation (losing days only)", down_rho, "{:>7.2f}"),
            ("joint-loss lift (1.00 = independent)", loss_lift, "{:>7.2f}"),
            ("plain correlation, all days (reference)", correlation, "{:>7.2f}")):
        print(f"\n{label}")
        print(" " * width + "".join(f"{i:>7}" for i in range(len(keys))))
        grid = []
        for i, left in enumerate(keys):
            line = [fn(streams[left], streams[right]) for right in keys]
            grid.append([round(v, 3) for v in line])
            print(f"{left:<{width}}"
                  + "".join(spec.format(v) for v in line) + f"   {i}")
        matrix[label] = grid

    print(f"\nworst-{TAIL_MONTHS}-months overlap "
          f"(row's worst months, share also losing for column)")
    print(" " * width + "".join(f"{i:>7}" for i in range(len(keys))))
    overlap = []
    for i, left in enumerate(keys):
        line = [bad_overlap(streams[left], streams[right]) for right in keys]
        overlap.append([round(v, 3) for v in line])
        print(f"{left:<{width}}" + "".join(f"{v:>7.0%}" for v in line)
              + f"   {i}")
    matrix["worst_months_overlap"] = overlap

    scale = symbol_budget(members) if per_symbol else SLEEVE_SCALE
    book = replay(members, logs, bars_by, ctx_by, scale=scale,
                  sizing_cap=sizing_caps(members), risk_scale=risk_scale,
                  gross_cap=gross_cap, fair_cap=FAIR_CAP, initial=initial)
    # A PER-TRADE DUMP, for reconciling a book total against the Rust port when
    # the trade COUNTS already match. At that point the disagreement is in
    # sizing or in cost rather than in the signal, and only the lots and the
    # points per trade can say which. `exness_book --DUMP_TRADES` writes the
    # same shape on the other side.
    if os.environ.get("DUMP_TRADES"):
        with open(os.environ["DUMP_TRADES"], "w", encoding="utf-8") as handle:
            json.dump([{"s": t["sleeve"], "et": t["entry_ts"],
                        "xt": t["exit_ts"], "qty": t["lots"],
                        "ep": t["entry"], "points": t["points"],
                        "money": t["money_per_point"], "pnl": t["pnl"]}
                       for t in book["settled"]], handle)
        print(f"wrote {len(book['settled'])} trades to "
              f"{os.environ['DUMP_TRADES']}")
    series, sharpe, positive, total = monthly(book["settled"], initial)

    thin = [m for m in members
            if (m.get("fill_rate") or 100.0) < WARN_FILL_RATE]
    if thin and FORCE_MINIMUM_LOT:
        print("\nminimum-lot execution is ON: every positive under-minimum "
              "request is rounded up and its excess risk is included in MTM DD")
        thin = []
    if thin:
        print(f"\n!! LOW FILL RATE -- these members trade a different strategy "
              f"live than the backtest ran:")
        for member in thin:
            print(f"   {member['symbol']}:{member['family']:<16} "
                  f"{member['fill_rate']:.1f}% of {member['signals']} signals "
                  f"filled; the rest were below the broker minimum on "
                  f"${initial:,.0f}")
    if EXCLUDE:
        print(f"\nexcluded by operator decision:")
        for name, reason in EXCLUDE.items():
            print(f"   {name}: {reason.splitlines()[0]}")

    # MANDATORY per-sleeve table. Every reported result must carry, per sleeve:
    # its contribution, its drawdown BOTH marked-to-market and closed-trade, and
    # its standalone figures next to them -- otherwise a book that merely tracks
    # one member, or a sleeve that inverts inside the combination, is invisible.
    # Do not summarise this away ([[show-per-sleeve-detail-every-time]]).
    # NOT `total` -- that name is the month count in the summary further down,
    # and shadowing it printed "18/5048.05 months positive".
    book_total = sum(v["pnl"] for v in book["by_sleeve"].values()) or 1.0
    print(f"\n{'sleeve':26}{'P&L $':>9}{'share':>7}{'MTM dd$':>9}"
          f"{'clsd dd$':>9}{'ddEvt%':>8}{'n':>6}{'alone%':>8}{'alone dd':>9}"
          f"{'alone$':>8}{'ratio':>7}")
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        one, row = standalone[key], book["by_sleeve"][key]
        share = 100.0 * row["pnl"] / book_total
        head = (f"{key:26}{row['pnl']:>9,.0f}{share:>6.1f}%"
                f"{row['mtm_dd_usd']:>9,.0f}{row['closed_dd_usd']:>9,.0f}"
                f"{100 * row.get('dd_event_share', 0.0):>7.1f}%"
                f"{row['trades']:>6}")
        if one["return_pct"] != one["return_pct"]:
            print(head + f"{'imported':>8}{'-':>9}{'-':>8}{'-':>7}")
            continue
        # Standalone % is of its own INITIAL_BALANCE, so the dollar equivalent
        # is what makes it comparable with the book contribution beside it.
        alone = one["return_pct"] / 100.0 * initial
        ratio = row["pnl"] / alone if alone else float("nan")
        print(head + f"{one['return_pct']:>8.1f}{one['max_dd_pct']:>9.1f}"
                     f"{alone:>8,.0f}{ratio:>7.2f}")
    weak = [f"{m['symbol']}:{m['family']}" for m in members
            if standalone[f"{m['symbol']}:{m['family']}"]["return_pct"] ==
            standalone[f"{m['symbol']}:{m['family']}"]["return_pct"]
            and book["by_sleeve"][f"{m['symbol']}:{m['family']}"]["pnl"] <
            standalone[f"{m['symbol']}:{m['family']}"]["return_pct"] / 100.0
            * initial]
    print(f"  ratio < 1.00 (worse in the book than alone): "
          f"{', '.join(weak) if weak else 'none'}")
    # `ddEvt%` is each sleeve's share of the give-back during the BOOK's worst
    # marked window -- not its own worst moment, which is what `MTM dd$` is. A
    # high value means the headline drawdown is one sleeve's position rather
    # than a portfolio event, and no amount of adding sleeves will dilute it.
    hot = max(book["by_sleeve"].items(),
              key=lambda kv: kv[1].get("dd_event_share", 0.0), default=None)
    if hot and hot[1].get("dd_event_share", 0.0) > 0:
        print(f"  worst-drawdown concentration: {hot[0]} bore "
              f"{100 * hot[1]['dd_event_share']:.1f}% of the fall on "
              f"{book['mtm_dd_trough']}")
    # `nan` for the imported sleeves, which have no standalone run in this
    # module. Skipped rather than propagated, so one import does not turn the
    # whole comparison into nan.
    solo = [standalone[f"{m['symbol']}:{m['family']}"]["return_pct"]
            for m in members]
    solo = [v for v in solo if v == v]
    best = max(solo) if solo else float("nan")
    summed = sum(solo) if solo else float("nan")

    print(f"\nBOOK  {len(members)} sleeves, one ${initial:,.0f} account"
          f"   risk_scale {risk_scale}"
          + (f"  gross_cap {gross_cap}x" if gross_cap else "  gross_cap off"))
    print(f"  return          {book['return_pct']:+.1f}%")
    print(f"  final equity    ${book['final']:,.2f}")
    print(f"  closed dd       {book['max_dd_pct']:.1f}%   (optimistic)")
    print(f"  MTM dd          {book['mtm_dd_pct']:.1f}%   "
          f"<- read this, trough {book['mtm_dd_trough']}")
    print(f"  trades          {book['trades']}"
          f"   refused by gross cap {book['refused_by_gross_cap']}"
          f"   below broker min {sum(book['below_broker_minimum'].values())}")
    print(f"  monthly Sharpe  {sharpe}  ({positive}/{total} months positive)")
    print(f"  best sleeve     {best:+.1f}%   sum of sleeves {summed:+.1f}%")

    values = [row["return_pct"] for row in series.values()]
    if values:
        print(f"  worst month     {min(values):+.2f}%   "
              f"best {max(values):+.2f}%   "
              f"median {statistics.median(values):+.2f}%")
        print("\n  month-by-month")
        for key in sorted(series):
            row = series[key]
            bar = ("+" if row["return_pct"] >= 0 else "-") * min(
                40, int(abs(row["return_pct"])))
            print(f"    {key}  {row['return_pct']:+7.2f}%  "
                  f"${row['pnl']:>10,.2f}  {bar}")

    payload = {
        "window": f"2025-01-01..{CANON_DATA_THROUGH}", "bar_minutes": BAR,
        "initial_balance": initial,
        "null_seed": null_seed,
        "gates": {"max_down_rho": max_rho, "max_loss_lift": max_lift,
                  "max_members": limit, "min_oos_return": MIN_OOS_RETURN,
                  "min_oos_trades": MIN_OOS_TRADES, "min_is_t": MIN_IS_T},
        "sizing": {"risk_fraction": ef.RISK_FRACTION, "risk_scale": risk_scale,
                   "effective_risk_pct": 100 * ef.RISK_FRACTION * risk_scale,
                   "gross_cap": gross_cap, "sleeve_scale": SLEEVE_SCALE,
                   "sizing_equity_cap": sizing_caps(members),
                   "uncapped": UNCAPPED,
                   "force_minimum_lot": FORCE_MINIMUM_LOT},
        "candidates": rows,
        "members": members,
        "rejected_for_dependence": rejected,
        "selection_trail": trail,
        "selected_by": "book monthly consistency" if by_consistency
                       else "standalone holdout return",
        "forced_members": list(include),
        "dependence": {"keys": keys, **matrix},
        "standalone": {k: {kk: vv for kk, vv in v.items()
                           if kk not in ("trade_log", "annual")}
                       for k, v in standalone.items()},
        "book": {k: v for k, v in book.items()
                 if k not in ("settled", "curve")},
        "monthly": series, "monthly_sharpe": sharpe,
        "best_sleeve_pct": best, "sum_of_sleeves_pct": summed,
    }
    path = OUT_PATH if null_seed is None else OUT_PATH.replace(
        ".json", f"_null{null_seed}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {path}")
    return payload


#: The sweep grids. Coarse by default; `--fine` swaps in the dense ones.
#:
#: A DENSER GRID IS MORE SEARCH, NOT MORE TRUTH. Every extra cell is another
#: draw against the same 20-month window, and the best of them is selected by
#: construction -- the same argument that makes `why` mandatory upstream. The
#: fine grid exists to answer "is the coarse pick near a cliff or on a plateau",
#: which is a question about robustness; it is not licence to report its maximum
#: as if it were the coarse grid's.
RISK_GRID = (1.0, 0.75, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1)
GROSS_GRID = (None, 6.0, 4.0, 3.0, 2.0)
FINE_RISK_GRID = tuple(round(0.20 + 0.025 * i, 3) for i in range(19))
FINE_GROSS_GRID = (None, 10.0, 8.0, 6.0, 5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0)


def sweep(limit, max_rho, max_lift, target=TARGET_MTM_DD, stale=True,
          fine=False, band=None):
    """Both risk levers against the MTM drawdown, to find the `target` setting.

    Two levers, and they are not interchangeable:

      `risk_scale` shrinks every position. It reaches the drawdown reliably and
                   costs return roughly as the square, because a smaller book
                   also compounds more slowly.
      `gross_cap`  refuses an entry that would push simultaneous notional past a
                   multiple of equity. It only touches the moments when several
                   sleeves are open at once, which is exactly what an intraday
                   book's worst trough is made of
                   ([[book-drawdown-is-one-intraday-position]]).

    THE SWEEP IS FITTED TO THE HOLDOUT AND IS NOT A RESULT. It is chosen by
    reading drawdown, never return, which is the same discipline `combined_book`
    used for its own scale -- but it is still a parameter picked on the window
    it is measured on, so the return that comes with it is optimistic.
    """
    rows = candidates()
    for row in rows:
        ef.resolve(row["symbol"], allow_stale=stale)
    streams, logs, bars_by, ctx_by = {}, {}, {}, {}
    for row in rows:
        key = f"{row['symbol']}:{row['family']}"
        _result, log, bars, ctx = sleeve_trades(row)
        streams[key] = daily_stream(log)
        logs[key] = log
        bars_by[row["symbol"]] = bars
        ctx_by[row["symbol"]] = ctx
    members, _rejected = choose_members(rows, streams, limit, max_rho, max_lift)

    risk_grid = FINE_RISK_GRID if fine else RISK_GRID
    gross_grid = FINE_GROSS_GRID if fine else GROSS_GRID
    print(f"{len(members)} sleeves, target MTM drawdown {target}%, "
          f"{len(risk_grid) * len(gross_grid)} cells\n")
    show = not fine        # the fine grid is far too long to print in full
    if show:
        print(f"{'risk':>6}{'gross':>8}{'return%':>10}{'closed dd':>11}"
              f"{'MTM dd':>9}{'ret/dd':>9}{'refused':>9}")
    table = []
    for gross_cap in gross_grid:
        for risk_scale in risk_grid:
            book = replay(members, logs, bars_by, ctx_by, scale=SLEEVE_SCALE,
                          sizing_cap=sizing_caps(members), risk_scale=risk_scale,
                          gross_cap=gross_cap, fair_cap=FAIR_CAP)
            ratio = (book["return_pct"] / book["mtm_dd_pct"]
                     if book["mtm_dd_pct"] > 0 else 0.0)
            row = {"risk_scale": risk_scale, "gross_cap": gross_cap,
                   "return_pct": round(book["return_pct"], 1),
                   "max_dd_pct": round(book["max_dd_pct"], 1),
                   "mtm_dd_pct": round(book["mtm_dd_pct"], 1),
                   "return_per_dd": round(ratio, 2),
                   "refused": book["refused_by_gross_cap"]}
            table.append(row)
            if show:
                print(f"{risk_scale:>6.2f}"
                      f"{('off' if gross_cap is None else f'{gross_cap:.1f}x'):>8}"
                      f"{book['return_pct']:>10.1f}{book['max_dd_pct']:>11.1f}"
                      f"{book['mtm_dd_pct']:>9.1f}{ratio:>9.2f}"
                      f"{book['refused_by_gross_cap']:>9}")

    if band:
        low, high = band
        inside = sorted((r for r in table if low <= r["mtm_dd_pct"] <= high),
                        key=lambda r: -r["return_pct"])
        print(f"\nevery cell with MTM drawdown in [{low}%, {high}%], "
              f"best return first -- {len(inside)} of {len(table)}")
        print(f"{'risk':>6}{'gross':>8}{'return%':>10}{'closed dd':>11}"
              f"{'MTM dd':>9}{'ret/dd':>9}{'refused':>9}")
        for row in inside[:25]:
            cap = row["gross_cap"]
            cap_text = "off" if cap is None else f"{cap:.1f}x"
            print(f"{row['risk_scale']:>6.3f}{cap_text:>8}"
                  f"{row['return_pct']:>10.1f}{row['max_dd_pct']:>11.1f}"
                  f"{row['mtm_dd_pct']:>9.1f}{row['return_per_dd']:>9.2f}"
                  f"{row['refused']:>9}")

    # The BEST cell inside the drawdown budget, not the one nearest to it.
    # Ranking by proximity to the target picked risk 0.40 + 2x (+72.3% at 14.7%)
    # over risk 0.40 + 3x (+192.0% at 14.3%) -- strictly worse on both axes, and
    # chosen only because its drawdown was closer to the number. The budget is a
    # ceiling to stay under, not a quantity to maximise.
    under = [r for r in table if r["mtm_dd_pct"] <= target]
    pick = (max(under, key=lambda r: r["return_pct"]) if under
            else min(table, key=lambda r: r["mtm_dd_pct"]))
    print(f"\nclosest to {target}% without exceeding it:")
    print(f"  risk_scale {pick['risk_scale']}  gross_cap {pick['gross_cap']}"
          f"  -> {pick['return_pct']:+.1f}%  MTM dd {pick['mtm_dd_pct']}%"
          f"  closed dd {pick['max_dd_pct']}%")
    path = os.path.join(RESULTS, "exness_combined_strategies_sizing.json")
    if fine:
        path = path.replace(".json", "_fine.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"target_mtm_dd": target, "members": members, "band": band,
                   "fine": fine, "sweep": table, "chosen": pick},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {path}")
    return pick


def yearly(start_year=2018, risk_scale=CANON_RISK_SCALE, gross_cap=CANON_GROSS_CAP,
           path=OUT_PATH, add=(), out_path=None,
           initial=CANON_INITIAL):
    """The sealed book replayed from `start_year` to the end of the holdout.

    READ THE WINDOW LABELS BEFORE THE RETURNS. Every member's parameters were
    chosen on data ending 2024-12-31, so 2018-2024 is IN-SAMPLE for them: those
    years are what the search was fitted to and are not evidence. Only 2025 and
    2026 are out of sample. The table marks each year so the two cannot be read
    as one track record.

    Sleeve availability also changes underneath the book. xauaud starts
    2021-06-30, xznusd 2023-02, and nq:ofi's level-two features begin
    2025-02-12 -- so an early year is a different, smaller book, not the same
    one performing differently. The active-sleeve count is printed per year for
    that reason ([[cold-start-oos-fakes-regime-edges]]).
    """
    with open(path, encoding="utf-8") as handle:
        sealed = json.load(handle)
    members = sealed.get("members")
    if members is None and sealed.get("keys"):
        with open(OUT_PATH, encoding="utf-8") as handle:
            canon_members = json.load(handle)["members"]
        by_key = candidate_rows_exact(sealed["keys"])
        by_key.update({f"{m['symbol']}:{m['family']}": m
                       for m in canon_members
                       if f"{m['symbol']}:{m['family']}" in EXTERNAL})
        missing = [key for key in sealed["keys"] if key not in by_key]
        if missing:
            raise SystemExit(f"input book keys absent from pool: {missing}")
        members = [by_key[key] for key in sealed["keys"]]
    if members is None:
        raise SystemExit("input result has neither members nor keys")
    # JSON turned every tuple parameter into a list on the way out, and several
    # families index on theirs -- `macd_hist` does `ctx["macd_hist"][macd_set]`,
    # which raises "unhashable type: list" on a round-tripped param. `candidates`
    # and `candidate_rows_exact` both `_retuple` for this reason; members read
    # straight from the sealed file were the one path that did not, so the bug
    # only appeared when a family carrying a tuple param first entered BOOK.
    for member in members:
        # EXTERNAL sleeves carry no params -- their trades are imported, not
        # regenerated from a family signal.
        if "params" in member:
            member["params"] = _retuple(member["params"])
    if add:
        by_key = {f"{m['symbol']}:{m['family']}": m for m in members}
        pool = {f"{m['symbol']}:{m['family']}": m for m in candidates()}
        missing = [key for key in add if key not in pool]
        if missing:
            raise SystemExit(f"--add sleeves absent from candidate pool: {missing}")
        for key in add:
            by_key.setdefault(key, pool[key])
        members = list(by_key.values())
    lo = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
    external_window = (f"{start_year}-01-01", CANON_DATA_END)

    logs, bars_by, ctx_by = {}, {}, {}
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        print(f"  loading full-history {key}", flush=True)
        if key in EXTERNAL:
            logs[key] = external_trades(key, window=external_window)
            continue
        ef.resolve(member["symbol"], allow_stale=True)
        _r, log, bars, ctx = sleeve_trades(member, lo=lo, hi=ef.OOS_END)
        logs[key] = log
        bars_by[member["symbol"]] = bars
        ctx_by[member["symbol"]] = ctx

    book = replay(members, logs, bars_by, ctx_by, scale=SLEEVE_SCALE,
                  sizing_cap=sizing_caps(members), risk_scale=risk_scale,
                  gross_cap=gross_cap, lo=lo, hi=ef.OOS_END,
                  fair_cap=FAIR_CAP, initial=initial)

    # Per-year segments of the ONE continuous marked curve, so each year starts
    # from the equity the previous one actually left behind.
    marked = book["marked"]
    years = {}
    for stamp, value in marked:
        years.setdefault(
            datetime.fromtimestamp(stamp, tz=timezone.utc).year, []).append(
                (stamp, value))
    active = {}
    for key, log in logs.items():
        for trade in log:
            year = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc).year
            active.setdefault(year, set()).add(key)

    print(f"\nBOOK {len(members)} sleeves, ${initial:,.0f}, "
          f"risk_scale {risk_scale}, "
          f"gross_cap {gross_cap if gross_cap else 'off'}   {start_year}-2026")
    print(f"\n{'year':6}{'start $':>11}{'end $':>11}{'return':>9}"
          f"{'MTM dd':>9}{'sleeves':>9}  window")
    rows = []
    for year in sorted(years):
        segment = years[year]
        opening = segment[0][1]
        closing = segment[-1][1]
        peak, worst = opening, 0.0
        for _stamp, value in segment:
            peak = max(peak, value)
            worst = max(worst, (peak - value) / peak if peak > 0 else 0.0)
        label = "OUT of sample" if year >= 2025 else "in-sample (fitted)"
        rows.append({"year": year, "start": round(opening, 2),
                     "end": round(closing, 2),
                     "return_pct": round(100 * (closing / opening - 1), 2),
                     "mtm_dd_pct": round(100 * worst, 2),
                     "sleeves": len(active.get(year, ())), "window": label})
        print(f"{year:<6}{opening:>11,.0f}{closing:>11,.0f}"
              f"{100 * (closing / opening - 1):>8.1f}%{100 * worst:>8.1f}%"
              f"{len(active.get(year, ())):>9}  {label}")

    total = sum(v["pnl"] for v in book["by_sleeve"].values()) or 1.0
    print(f"\n{'sleeve':26}{'P&L $':>12}{'share':>8}{'trades':>8}"
          f"{'own MTM dd $':>14}{'peak $':>10}{'P&L/dd':>8}")
    for name, row in sorted(book["by_sleeve"].items(),
                            key=lambda kv: -kv[1]["pnl"]):
        ratio = row["pnl"] / row["mtm_dd_usd"] if row["mtm_dd_usd"] > 0 else float("inf")
        print(f"{name:26}{row['pnl']:>12,.0f}{100 * row['pnl'] / total:>7.1f}%"
              f"{row['trades']:>8}{row['mtm_dd_usd']:>14,.0f}"
              f"{row['peak_contribution']:>10,.0f}"
              + (f"{ratio:>8.2f}" if ratio != float("inf") else f"{'-':>8}"))
    print(f"\n  book return {book['return_pct']:+.1f}%   "
          f"MTM dd {book['mtm_dd_pct']:.1f}%   closed dd {book['max_dd_pct']:.1f}%"
          f"   trades {book['trades']}")
    print("  sleeve drawdowns do NOT sum to the book's: each peaks at a "
          "different moment, which is the diversification.")

    month_rows, month_sharpe, positive, month_count = monthly(
        book["settled"], initial)
    print(f"  monthly Sharpe {month_sharpe:.2f}   "
          f"{positive}/{month_count} positive months")

    out = (out_path or
           os.path.join(RESULTS, "exness_combined_strategies_yearly.json"))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"start_year": start_year, "risk_scale": risk_scale,
                   "initial_balance": initial,
                   "gross_cap": gross_cap,
                   "uncapped": UNCAPPED,
                   "force_minimum_lot": FORCE_MINIMUM_LOT,
                   "members": members,
                   "by_year": rows, "by_sleeve": book["by_sleeve"],
                   "monthly": month_rows,
                   "monthly_sharpe": month_sharpe,
                   "positive_months": positive,
                   "month_count": month_count,
                   "book": {k: v for k, v in book.items()
                            if k not in ("settled", "curve", "marked",
                                         "by_sleeve")}},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {out}")


def standalone(start_year=2018, risk_scale=CANON_RISK_SCALE, gross_cap=CANON_GROSS_CAP,
               path=OUT_PATH, initial=CANON_INITIAL):
    """Every member alone, each on its OWN fresh $1,000, all in per cent.

    The `yearly` per-sleeve table reports dollar contributions inside the shared
    account, which is dominated by WHEN a sleeve traded rather than how good it
    is: 2024's balance is 30x 2018's, so a sleeve that only exists from 2021
    books larger dollars for identical behaviour. Running each on its own
    account removes that entirely -- every sleeve starts at $1,000 whenever its
    data starts, and the percentages are directly comparable.

    Same `risk_scale` and `gross_cap` as the book, so these are the same
    positions the book took, not a differently-sized strategy.
    """
    with open(path, encoding="utf-8") as handle:
        sealed = json.load(handle)
    members = sealed["members"]
    lo = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
    years = list(range(start_year, 2027))
    external_window = (f"{start_year}-01-01", CANON_DATA_END)

    rows = []
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        logs, bars_by, ctx_by = {}, {}, {}
        if key in EXTERNAL:
            logs[key] = external_trades(key, window=external_window)
        else:
            ef.resolve(member["symbol"], allow_stale=True)
            _r, log, bars, ctx = sleeve_trades(member, lo=lo, hi=ef.OOS_END)
            logs[key] = log
            bars_by[member["symbol"]] = bars
            ctx_by[member["symbol"]] = ctx
        book = replay([member], logs, bars_by, ctx_by, scale=SLEEVE_SCALE,
                      sizing_cap=sizing_caps(members), risk_scale=risk_scale,
                      gross_cap=gross_cap, lo=lo, hi=ef.OOS_END,
                      fair_cap=FAIR_CAP, initial=initial)
        per_year = {}
        buckets = {}
        for stamp, value in book["marked"]:
            buckets.setdefault(
                datetime.fromtimestamp(stamp, tz=timezone.utc).year,
                []).append(value)
        for year, values in buckets.items():
            opening, closing = values[0], values[-1]
            peak, worst = opening, 0.0
            for value in values:
                peak = max(peak, value)
                worst = max(worst, (peak - value) / peak if peak > 0 else 0.0)
            # A year in which the sleeve never traded is left out rather than
            # printed as 0.0%: no data and a flat year are different claims.
            if abs(closing - opening) < 1e-9 and worst < 1e-9:
                continue
            per_year[year] = (100 * (closing / opening - 1), 100 * worst)
        _series, sharpe, positive, total = monthly(book["settled"],
                                                   initial)
        rows.append({"sleeve": key, "book": book, "per_year": per_year,
                     "sharpe": sharpe, "positive": positive, "months": total})

    print(f"\nSTANDALONE -- each sleeve on its own ${initial:,.0f}, risk_scale "
          f"{risk_scale}, gross_cap {gross_cap if gross_cap else 'off'}")
    print(f"\n{'sleeve':26}{'return':>10}{'MTM dd':>9}{'closed dd':>11}"
          f"{'ret/dd':>8}{'trades':>8}{'mSharpe':>9}{'+months':>9}")
    for row in sorted(rows, key=lambda r: -(
            r["book"]["return_pct"] / r["book"]["mtm_dd_pct"]
            if r["book"]["mtm_dd_pct"] > 0 else 0)):
        book = row["book"]
        ratio = (book["return_pct"] / book["mtm_dd_pct"]
                 if book["mtm_dd_pct"] > 0 else float("nan"))
        print(f"{row['sleeve']:26}{book['return_pct']:>9.1f}%"
              f"{book['mtm_dd_pct']:>8.1f}%{book['max_dd_pct']:>10.1f}%"
              f"{ratio:>8.2f}{book['trades']:>8}{row['sharpe']:>9.2f}"
              f"{row['positive']:>5}/{row['months']:<3}")

    print(f"\nreturn % by year (blank = the sleeve had no data or no trades)")
    print(f"{'sleeve':26}" + "".join(f"{y:>9}" for y in years))
    for row in sorted(rows, key=lambda r: -r["book"]["return_pct"]):
        line = "".join(
            (f"{row['per_year'][y][0]:>8.1f}%" if y in row["per_year"]
             else f"{'':>9}") for y in years)
        print(f"{row['sleeve']:26}{line}")

    print(f"\nMTM drawdown % by year")
    print(f"{'sleeve':26}" + "".join(f"{y:>9}" for y in years))
    for row in sorted(rows, key=lambda r: -r["book"]["return_pct"]):
        line = "".join(
            (f"{row['per_year'][y][1]:>8.1f}%" if y in row["per_year"]
             else f"{'':>9}") for y in years)
        print(f"{row['sleeve']:26}{line}")

    out = os.path.join(RESULTS, "exness_combined_strategies_standalone.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"start_year": start_year, "risk_scale": risk_scale,
                   "gross_cap": gross_cap,
                   "sleeves": [{"sleeve": r["sleeve"],
                                "per_year": {str(k): {"return_pct": round(v[0], 2),
                                                      "mtm_dd_pct": round(v[1], 2)}
                                             for k, v in r["per_year"].items()},
                                "monthly_sharpe": r["sharpe"],
                                "positive_months": f"{r['positive']}/{r['months']}",
                                **{k: v for k, v in r["book"].items()
                                   if k not in ("settled", "curve", "marked",
                                                "by_sleeve")}}
                               for r in rows]},
                  handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {out}")


def main():
    parser = argparse.ArgumentParser(
        description="Combine uncorrelated cfd_families cells in one account.")
    parser.add_argument("command",
                        choices=("build", "size", "yearly", "standalone",
                                 "cache"))
    parser.add_argument("--max-down-rho", type=float, default=MAX_DOWN_RHO)
    parser.add_argument("--max-loss-lift", type=float, default=MAX_LOSS_LIFT)
    parser.add_argument("--max-members", type=int, default=MAX_MEMBERS)
    parser.add_argument("--null", action="store_true",
                        help="rebuild the book from coin-flip sleeves")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--risk-scale", type=float, default=CANON_RISK_SCALE,
                        help="global multiplier on RISK_FRACTION")
    parser.add_argument("--gross-cap", type=float, default=CANON_GROSS_CAP,
                        help="max simultaneous notional, in multiples of equity; "
                             "canon is 8.0, pass 0 to switch the cap off")
    parser.add_argument("--target-dd", type=float, default=TARGET_MTM_DD)
    parser.add_argument("--symbols", default=None,
                        help="restrict the candidate pool to these symbols")
    parser.add_argument("--members", default=None,
                        help="run EXACTLY these sleeves, skipping selection; "
                             "pass 'canon' for the recorded BOOK")
    parser.add_argument("--fair-cap", action="store_true",
                        help="give each sleeve gross_cap/n of equity as its own "
                             "exposure budget instead of one shared pool")
    parser.add_argument("--max-dd-share", type=float, default=None,
                        help="reject any book where one sleeve bears more than "
                             "this share (0-1) of the worst marked fall")
    parser.add_argument("--min-trades", type=int, default=None,
                        help="minimum holdout trades a cell needs to be "
                             "eligible; below it a sleeve has too few events "
                             "to trust or to correlate")
    parser.add_argument("--sizing-cap", type=float, default=1500.0,
                        help="dollar ceiling on the equity each sleeve sizes "
                             "against; stops compounding becoming leverage")
    parser.add_argument("--uncapped", dest="uncapped", action="store_true",
                        default=None,
                        help="ignore every per-sleeve sizing-equity cap and "
                             "compound each sleeve against the full account")
    parser.add_argument("--capped", dest="uncapped", action="store_false",
                        help="force the legacy per-sleeve/global sizing caps; "
                             "canon is uncapped by default")
    parser.add_argument("--force-minimum-lot", dest="force_minimum_lot",
                        action="store_true", default=None,
                        help="round every positive under-minimum sizing request "
                             "up to the broker minimum and include the excess "
                             "risk in MTM drawdown")
    parser.add_argument("--skip-under-minimum", dest="force_minimum_lot",
                        action="store_false",
                        help="restore refusal of orders whose requested size is "
                             "below the broker minimum")
    parser.add_argument("--fine", action="store_true",
                        help="dense sizing grid (209 cells) instead of 45")
    parser.add_argument("--include", default=None,
                        help="comma-separated sleeves to seat first and never "
                             "drop, e.g. xauaud:high_52w,nq:ofi,nq:drift_vwap")
    parser.add_argument("--consistency", action="store_true",
                        help="pick members by greedy forward selection on the "
                             "BOOK's monthly consistency instead of ranking "
                             "them by standalone holdout return")
    parser.add_argument("--min-is-t", type=float, default=None,
                        help="override MIN_IS_T; 0 admits xauaud:high_52w")
    parser.add_argument("--per-symbol", action="store_true",
                        help="split one risk budget across all sleeves on the "
                             "same symbol, so siblings shrink instead of "
                             "being refused")
    parser.add_argument("--start-year", type=int, default=2018,
                        help="`yearly` first year; 2018-2024 is IN-SAMPLE")
    parser.add_argument("--band", default=None,
                        help="report every cell whose MTM drawdown falls in "
                             "this range, e.g. 12,18")
    parser.add_argument("--add", default=None,
                        help="for `yearly`, append these comma-separated fresh "
                             "candidate sleeves without changing canon")
    parser.add_argument("--output", default=None,
                        help="alternate result path for a `yearly` comparison")
    parser.add_argument("--input", default=None,
                        help="alternate sealed/member-search result consumed "
                             "by `yearly`")
    parser.add_argument("--broker-signals", action="store_true",
                        help="decide AND fill on the broker's own bars "
                             "(exness_<broker>_1m), not just fill. nq and "
                             "ethusd keep vendor signals -- see "
                             "BROKER_SIGNAL_EXCLUDE. Implies --broker-fills")
    parser.add_argument("--broker-volume", action="store_true",
                        help="under --broker-signals, also take VOLUME from "
                             "the broker. Off by default: the two feeds' "
                             "volume differs 0.21x-7606x at correlation 0.33, "
                             "so it compares a different statistic, not a "
                             "different venue")
    parser.add_argument("--drop-broker-signal-casualties", action="store_true",
                        help="under --broker-signals, retire members that go "
                             "non-positive instead of carrying them; changes "
                             "membership, so the result stops isolating the "
                             "signal feed")
    parser.add_argument("--force-mismatched-fills", action="store_true",
                        help="swap fills even for symbols whose broker series "
                             "is a different instrument (both NQ cells, "
                             "ukoil); measures the mismatch, not execution")
    parser.add_argument("--broker-fills", action="store_true",
                        help="fill every sleeve at the Exness bars "
                             "(exness_<broker>_1m) instead of the vendor's; "
                             "signals, context and stop distances stay on the "
                             "vendor series. Symbols whose broker series is a "
                             "different instrument are rescaled onto the "
                             "deciding level first")
    parser.add_argument("--initial", type=float, default=CANON_INITIAL,
                        help="starting account equity for `yearly`; canon is "
                             "$400, the account actually being funded")
    args = parser.parse_args()

    # `--gross-cap 0` is how the cap is switched OFF from the command line, now
    # that the default is on. `replay` reads None, not 0.0, as "no cap".
    if args.gross_cap is not None and args.gross_cap <= 0:
        args.gross_cap = None

    global GLOBAL_SIZING_CAP, MAX_DD_CONCENTRATION, FAIR_CAP, UNCAPPED
    global FORCE_MINIMUM_LOT
    GLOBAL_SIZING_CAP = args.sizing_cap
    MAX_DD_CONCENTRATION = args.max_dd_share
    FAIR_CAP = args.fair_cap
    global FILL_FEED, FILL_FEED_SKIP_MISMATCH, SIGNAL_FEED
    global DROP_SIGNAL_CASUALTIES
    DROP_SIGNAL_CASUALTIES = args.drop_broker_signal_casualties
    global BROKER_SIGNAL_KEEP_VENDOR_VOLUME
    BROKER_SIGNAL_KEEP_VENDOR_VOLUME = not args.broker_volume
    # Deciding on a feed you do not fill on is not a mode anyone wants, so the
    # signal switch turns the fill switch on rather than being a third state.
    SIGNAL_FEED = args.broker_signals
    FILL_FEED = args.broker_fills or args.broker_signals
    FILL_FEED_SKIP_MISMATCH = not args.force_mismatched_fills
    if FILL_FEED:
        # The two imported sleeves live in `combined_book` and are built by a
        # different loop, so the switch has to be set there too or a
        # "broker fills" book is quietly eighteen sleeves, not twenty.
        from sandbox.research import combined_book as _cb
        _cb.BROKER_FILLS = True
    if FILL_FEED:
        if SIGNAL_FEED:
            print("BROKER SIGNALS + FILLS -- every sleeve decides and fills on "
                  "exness_<broker>_1m, except "
                  f"{', '.join(BROKER_SIGNAL_EXCLUDE)}, which keep vendor "
                  "signals. Selection was done on the VENDOR series, so this "
                  "is out-of-sample for the signal.")
        else:
            print("BROKER FILLS ON -- all 20 sleeves priced off "
                  "exness_<broker>_1m. Symbols whose broker series is a "
                  "different instrument are rescaled onto the deciding "
                  "series' level.")
    canon_requested = bool(args.members and
                           args.members.strip().lower() == "canon")
    FORCE_MINIMUM_LOT = (
        args.force_minimum_lot if args.force_minimum_lot is not None else
        args.command in ("yearly", "standalone") or canon_requested)
    UNCAPPED = (args.uncapped if args.uncapped is not None else
                args.command in ("yearly", "standalone") or canon_requested)

    if args.command == "cache":
        warm_external_cache()
        return
    if args.command == "yearly":
        add = tuple(s for s in (args.add or "").replace(" ", "").split(",")
                    if s)
        yearly(args.start_year, args.risk_scale, args.gross_cap,
               path=args.input or OUT_PATH, add=add, out_path=args.output,
               initial=args.initial)
        return
    if args.command == "standalone":
        standalone(args.start_year, args.risk_scale, args.gross_cap,
                   initial=args.initial)
        return
    if args.command == "size":
        band = None
        if args.band:
            low, high = (float(v) for v in args.band.split(","))
            band = (low, high)
        sweep(args.max_members, args.max_down_rho, args.max_loss_lift,
              args.target_dd, fine=args.fine, band=band)
        return
    include = tuple(s for s in (args.include or "").replace(" ", "").split(",")
                    if s)
    symbols = tuple(s for s in (args.symbols or "").replace(" ", "").split(",")
                    if s)
    members_exact = ()
    if args.members:
        members_exact = (BOOK if args.members.strip().lower() == "canon"
                         else tuple(s for s in args.members.replace(" ", "")
                                    .split(",") if s))
    common = dict(risk_scale=args.risk_scale, gross_cap=args.gross_cap,
                  initial=args.initial,
                  include=include, by_consistency=args.consistency,
                  min_is_t=args.min_is_t, per_symbol=args.per_symbol,
                  symbols=symbols, min_trades=args.min_trades,
                  members_exact=members_exact)
    if args.null:
        for seed in (int(s) for s in args.seeds.split(",") if s):
            print(f"\n{'=' * 70}\nNULL BOOK seed {seed}\n{'=' * 70}")
            build(args.max_members, args.max_down_rho, args.max_loss_lift,
                  null_seed=seed, **common)
        return
    build(args.max_members, args.max_down_rho, args.max_loss_lift, **common)


if __name__ == "__main__":
    main()
