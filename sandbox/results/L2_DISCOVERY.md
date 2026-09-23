# NQ level-two discovery screen, 2025

Status: **no candidate found**. 2026 was never read.

## What was run

Sixteen level-two signals — seven already traded or tested somewhere in the
repo, nine new combinations — at trailing windows of 1, 5 and 60 minutes,
against forward returns at 5, 15, 30 and 60 minutes. 192 tests in all, over
85,304 RTH minutes across 225 sessions with a valid Databento book.

Signals are z-scored against the same minute-of-day over the prior 20 sessions,
so nothing reads its own future. The reported quantity is the long-short spread
in NQ points: mean forward return when the signal is high minus when it is low.

## Result

Nothing clears 1.0 points with t > 3.0. The strongest hit:

| signal | horizon | long-short pts | t | bootstrap 95% CI |
| --- | ---: | ---: | ---: | --- |
| churn_unpaid@60m | 5 | +1.531 | 2.86 | [+0.56, +2.61] |
| churn_unpaid@60m | 15 | +3.836 | 2.51 | [+1.05, +6.83] |
| churn_unpaid@5m | 60 | +3.675 | 2.36 | [+0.70, +6.68] |
| pull_thin@60m | 5 | -3.807 | -2.22 | [-7.55, -0.61] |

At 192 tests the Bonferroni threshold is t ≈ 3.7. The best result is t = 2.86,
which is what one expects from noise alone at this many tests (192 × 0.004 ≈ 0.8
false positives). Nothing survives.

## The estimator that had to be corrected

The first pass (`l2_discovery.py`) averaged **per-session** long-short
differences with equal weight. A session whose high leg held one qualifying
minute then counted as much as a session with fifty, and one 60-minute NQ move
carries roughly the variance of the entire effect.

That estimator reported `add_ratio@5m` at **+15.8 points, t = 3.60**, stable
across 11 months, unaffected by dropping the ten most violent sessions, and
clean against a shuffled placebo. It was still an artifact:

- observation-weighted, the same quantity is **-0.28 points**;
- traded through `execution.resolve` at 0.2 spread it earned **-1.56 points per
  trade** over 860 trades, against a volatility-matched control of -0.10 —
  i.e. 1.5 points per trade *worse* than random entries;
- re-estimated, the largest hit in the original table (`trade_delta@60m` at
  h=60) flipped sign, -38.4 → +12.7, with a CI spanning zero.

`l2_discovery_v2.py` uses the pooled, observation-weighted mean with a session
block bootstrap. `build_panel`, `raw_signals`, `cumulative` and `slot_normalize`
were sound and are reused unchanged.

## One structural finding worth keeping

Adds and cancels on each side of the NQ book are near-identical in volume:

| | bid | ask |
| --- | ---: | ---: |
| mean add volume | 247.20 | 252.55 |
| mean cancel volume | 247.18 | 252.52 |

So `corr(add-side ratio, cancel-side ratio) = -1.000` at every window tested,
and deep OFI — which is `(bid_add - bid_cancel) - (ask_add - ask_cancel)` — is a
**1.53% residual of two nearly equal large numbers**. Its long-short spread is
flat at every window and horizon (|t| < 2.1 throughout).

This is a mechanical explanation for a number already on record: the measured
gross edge of `nq_ofi_momentum` is ~0.03 points per trade. The strategy is
differencing away almost everything the columns contain and trading the
rounding error.

## Bearing on the rest of the L2 record

Combined with the ~0.85 point minimum detectable edge on this sample, and the
volatility-dependent bracket baseline (-0.03 calm, -2.2 fast), the 0-for-21
record of L2 entry signals in `sandbox/` is consistent: these features carry
no directional information at minute-or-longer horizons that survives an
honest estimator.

---

# Candidate: `ofi_deep@30m`

Status: **not promoted**. The 2026 Databento window is now spent on it.

Deep OFI summed over 30 minutes, z-scored per minute-of-day against the prior
20 sessions, `|z| >= 2.5`, stop 200, no target, 30-minute time exit, one
position at a time, entries stop at 15:00, 0.2 spread. Frozen on 2025 at the
plateau centre — deliberately not the best cell, which was `|z| >= 3.0`.

| | 2025 in-sample | 2026 holdout |
| --- | ---: | ---: |
| trades | 751 | 392 |
| net points/trade | +2.719 | +1.267 |
| volatility-matched control | -0.533 | -0.521 |
| **edge vs control** | **+3.252** | **+1.789** |
| z vs control | +1.89 | +0.70 |
| beats control draws | — | 78.5% |
| profit factor | 1.152 | 1.054 |
| positive months | 9/11 | 3/7 |

The sign held and the edge stayed above its matched control in both periods,
which no other level-two family in `sandbox/` has managed. It is still not
tradeable: out of sample the edge is statistically indistinguishable from the
control, and +1.79 with z = 0.70 spans "three points" and "nothing".

Two supporting results, both on 2025 and both clean of the timing bug below:

- `corr(ofi_deep@30m, past 30m price move) = +0.115`, and residualising the
  signal on the price move leaves the edge unchanged (+3.30 -> +3.50). It is
  not intraday momentum wearing an order-flow label.
- The threshold is a real cut, not a tuned one: `|z| >= 1.5` is flat
  (edge +0.01), and 2.0 / 2.5 / 3.0 give +3.30 / +3.25 / +5.62.

Note this sits awkwardly against `optimize_ofi.py`, which concluded the OFI
threshold carries no information at all. The configurations differ — that work
used a 2-minute EWMA with a 60/120 bracket and a 20-minute stop, this is a
30-minute cumulative sum with no target — so both can hold, but the tension is
unresolved and is a reason for the caution above.

## The look-ahead trap

The strategy scripts originally built a signal from minute `i`'s close and its
completed order flow, then passed `Signal(i, ...)` to `execution.resolve`, which
enters at `bars[i][O]` — **the open of the very minute the signal is made of**.

With that bug, a pure price-momentum signal using no level-two data scored
**+12.005 points per trade, z = +5.62 against its control, pf 1.547**. Entering
at `i + 1` instead — the rule `LIQUIDITY_STRATEGY_CANDIDATES.md` already states —
takes it to **-0.561**. The entire result was one bar of hindsight.

`ofi_deep@30m` moved only +3.016 -> +2.882 under the same fix, because just one
of its thirty minutes was contaminated. That contrast is what justified taking
it to the holdout at all.

The conditional-mean screens (`l2_discovery_v2`, `l2_long_horizon`, `l2_tail`)
were never affected: they measure `close[i+h] - close[i]` against a signal
formed at the end of minute `i`, with no entry price involved.
