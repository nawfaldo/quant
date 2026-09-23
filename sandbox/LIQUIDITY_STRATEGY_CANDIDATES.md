# NQ L2 Strategy Candidates and Optimization Protocol

## Status and scope

This document defines the research candidates before implementation or PnL
inspection. It is the contract for the first experiment. Changing a signal,
parameter grid, cost assumption, or acceptance rule after viewing 2026 results
creates a new experiment; the original 2026 period must then be considered
contaminated and cannot remain an untouched holdout.

No raw market-data table may be modified. Any additional line-level feature
must be written to a separate derived table or cache.

## Available data and fixed split

The requested `nq_features_1s` data is stored under the canonical table name
`nq_l2_features_1s`.

| Dataset | Role | Observed coverage on 2026-08-01 |
| --- | --- | --- |
| `nq_l2_features_1s` | Causal one-second book and order-flow features | 2025-02-12 through 2026-07-31 |
| `dbento_nq_ticks` | Historical trades, BBO, entry/exit paths, volume, delta | 2025-02-12 through 2026-07-16 |
| `dbento_nq_depth` | Historical absolute MBP-10 price-level updates | 2025-02-12 through 2026-07-16 |
| `bm_nq_ticks` | Live Bookmap trades and BBO after the Databento handoff | From 2026-07-17 |
| `bm_nq_depth` | Live Bookmap absolute price-level updates | From 2026-07-17 |

All timestamps are New York wall-clock values encoded as fake UTC. Research
must preserve that convention.

The immutable experiment split is:

- In-sample: 2025-02-12 09:30 through 2025-12-31 16:00 New York time.
- Out-of-sample: 2026-01-01 through the last complete session available when
  the experiment is run.
- The Databento-to-Bookmap source handoff on 2026-07-17 must be reported as a
  separate OOS diagnostic. It must not be used to choose parameters.

The optimizer may read 2025 only while selecting a model. It may reveal 2026
once, after parameters and all gates have been frozen.

## Shared causal rules

Every signal uses information available at or before the decision timestamp.
An event completed in second or minute `t` may enter no earlier than the first
executable quote or bar in `t + 1`. Opening ranges, normalization windows, and
liquidity thresholds may use prior observations only.

Common restrictions:

- NQ regular session only, 09:30-16:00 New York time.
- No more than one concurrent position per candidate.
- No new entries after 15:00; flatten before 16:00.
- Charge the configured spread on every round trip and run additional cost
  sensitivity at 0.20, 0.50, and 1.00 NQ points.
- When both stop and target occur in one minute, use the raw tick path. If the
  path is unavailable, resolve stop first.
- Missing, crossed, stale, or invalid books fail closed.
- Normalize Databento and Bookmap features independently. Their distributions
  must never share a fitted mean, variance, or percentile.
- Economic/news filters, weekday filters, hand-picked dates, and ML gates are
  excluded from the first experiment.

## Required liquidity-line representation

`nq_l2_features_1s` currently contains total standing depth and weighted depth
distance, but it does not identify the price and size of the largest individual
resting level. Total depth is not an honest substitute for a price-specific
liquidity line.

Before testing the two line strategies, replay `dbento_nq_depth` and
`bm_nq_depth` causally and derive, per source and second:

- side and price of the largest observable level;
- displayed size and share of same-side top-10 displayed depth;
- distance from BBO in ticks;
- uninterrupted age at the same price;
- additions, cancellations, and executions at that price during the second;
- whether the line remained present at the end of the second; and
- the line-size percentile using only the trailing 20 completed sessions for
  the same source, side, time-of-day bucket, and distance bucket.

Databento is MBP-10, so Bookmap must also be restricted to its nearest ten
levels for comparable research. The derived output should be a separate
`nq_liquidity_lines_1s` table or a fingerprinted research cache. The raw
`dbento_*` and `bm_*` tables remain immutable.

A line is eligible only if it is at or above the configured trailing percentile
and has met the minimum persistence requirement. This guards against calling a
single update or brief spoof-like flash a tradable wall.

## Candidate A: L2-confirmed opening-range breakout

Hypothesis: a break of a completed opening range is more likely to continue
when aggression, microprice, and displayed imbalance agree with the break.

Construction:

1. Build the high and low from 09:30 through either 09:44 or 09:59.
2. After the range is complete, require a close outside the range plus the
   selected buffer.
3. For a long, require positive trade delta, positive top-five imbalance, and
   microprice at or above midprice. Reverse all signs for a short.
4. Enter on the next executable observation. Allow one trade per session.
5. Use a fixed 2.0 reward-to-risk ratio and a 90-minute time stop.

Coarse optimization grid:

| Parameter | Values |
| --- | --- |
| Opening range | 15, 30 minutes |
| Breakout buffer | 0, 1 NQ point |
| Absolute top-five imbalance | 0.10, 0.20, 0.30 |
| Stop | 10, 15 NQ points |

Search budget: 24 cells. The reward-to-risk ratio, time stop, sign agreement,
and session window are fixed and are not additional optimization axes.

Matched control: the identical opening-range break without the L2 confirmation.
The candidate must improve net per-trade edge and downside behavior, not merely
reduce the trade count until the remaining sample looks attractive.

## Candidate B: failed opening-range breakout reversal

Hypothesis: an opening-range break that immediately returns inside while trade
delta flips is a failed auction and should move toward the opposite side of the
range.

Construction:

1. Use a fixed 30-minute opening range.
2. Require an outward trade beyond the range with delta in the break direction.
3. Within the allowed rejection window, require a close back inside the range
   and delta in the opposite direction.
4. Enter the reversal on the next executable observation, once per range side.
5. Fix exits at a 12-point stop, 20-point target, and 60-minute time stop.

Coarse optimization grid:

| Parameter | Values |
| --- | --- |
| Minimum penetration | 2, 4, 6 NQ points |
| Maximum rejection delay | 1, 3, 5 minutes |
| Opposite/sweep delta ratio | 0, 0.25, 0.50 |

Search budget: 27 cells.

Matched control: identical range penetration faded without the return-inside
and delta-flip requirements.

## Candidate C: large-liquidity-line reversal

Hypothesis: aggressive trading into a persistent, unusually large displayed
level followed by replenishment and price rejection indicates absorption by
the passive side.

Long setup:

1. A qualifying bid line is at or below the BBO and persists for the minimum
   age.
2. Price touches or trades through the line while trade delta is negative.
3. Bid additions/replenishment at the exact line are at least 25% of executed
   sell volume at that price during the observation window.
4. Price closes at least the rejection distance back above the line while the
   line remains present.
5. Enter next observation. Place the stop one point behind the line, reject the
   trade if stop distance exceeds 12 points, target 1.5 times initial risk, and
   apply a 45-minute time stop.

The short setup is the exact sign-reversed ask-line case.

Coarse optimization grid:

| Parameter | Values |
| --- | --- |
| Trailing line-size percentile | 95.0, 97.5, 99.0 |
| Minimum uninterrupted age | 15, 30, 60 seconds |
| Rejection distance | 1, 2 ticks |

Search budget: 18 cells. Replenishment ratio, stop buffer, maximum stop,
reward-to-risk ratio, and time stop remain fixed.

Matched controls:

- same price-touch and rejection sequence at non-large levels;
- qualifying wall touch without exact-price replenishment; and
- side-shuffled lines within the same session and time bucket.

## Candidate D: large-liquidity-line magnet chase

Hypothesis: price is attracted toward a persistent large displayed level when
aggression and near-book pressure point toward it and the level does not pull.

Construction:

1. Select a qualifying line ahead of price, never one already traded through.
2. Require the line to remain present, directional trade delta, directional
   top-five imbalance, and microprice displacement toward the line.
3. Enter on the next executable quote only while the line is still present.
4. Target one tick in front of the line. Cancel the setup if the line pulls
   before entry and exit immediately if the line pulls materially after entry.
5. Require the initial target distance to exceed spread plus one tick. Use a
   one-to-one maximum stop and a 10-minute time stop.

Coarse optimization grid:

| Parameter | Values |
| --- | --- |
| Trailing line-size percentile | 95.0, 97.5, 99.0 |
| Minimum uninterrupted age | 15, 30 seconds |
| Absolute top-five imbalance | 0.10, 0.20, 0.30 |

Search budget: 18 cells. Entry distance is constrained by the observable MBP-10
book and is not tuned.

This candidate has small targets and therefore requires tick/BBO-path execution
validation. Minute-only results cannot promote it.

Matched controls:

- directionally identical entries aimed at a randomly selected same-side price;
- qualifying lines without directional L2 confirmation; and
- entries where the line disappears before the next executable quote.

## Optimization protocol

### 1. Preflight and event-study kill test

Before any parameter sweep:

- verify complete-session coverage and source handoff dates;
- verify that every derived line is causal and reproducible from raw depth;
- measure the fixed-horizon signed move after each default event at 1, 5, 15,
  30, and 60 minutes;
- globally de-overlap event windows; and
- compare each event with its matched control.

Kill a candidate before optimization if the default event has non-positive net
edge, the directional effect is absent, or the result is worse than its matched
control. A failed definition does not earn a larger grid.

### 2. Selection inside 2025 only

For each surviving candidate:

1. Generate all grid-cell fills once over the full causal bar stream, but slice
   every selection statistic strictly to 2025.
2. Run anchored monthly walk-forward selection within 2025 with a one-session
   embargo between training and validation.
3. Run four purged 2025 regime blocks as a veto so the early months are tested,
   not permanently used only for training.
4. Rank cells by net per-trade t-statistic, subject to all hard filters below.
5. Require every immediate grid neighbor to be profitable in 2025 and rank on
   the weaker of the cell score and mean-neighbor score. This selects a plateau,
   not an isolated peak.
6. Choose the per-axis median of the walk-forward picks, snapped to an existing
   grid value. Freeze that configuration before revealing 2026.

Hard 2025 filters:

- at least 60 completed trades, or at least 30 for a once-per-session setup;
- positive total net points;
- positive net points in at least three of four regime blocks;
- mean net edge per trade at least three times the configured round-trip spread;
- maximum drawdown no greater than 15% of starting equity;
- no more than two consecutive losing months;
- best month no more than 40% of gross positive monthly PnL; and
- no immediate grid neighbor with negative total 2025 PnL.

Count every evaluated grid cell, discarded variant, rerun, and post-hoc filter
as a trial. Report both raw and deflated t-statistics using the cumulative trial
count. Trial counts are never reset because a result was disappointing.

### 3. One-shot 2026 evaluation

After freezing one configuration per candidate, score 2026 exactly once. Do not
rank candidates or change parameters using these results. Report:

- net points and sized PnL;
- trade count, win rate, profit factor, and maximum drawdown;
- mean per-trade edge with bootstrap 95% confidence interval;
- monthly PnL and positive-month rate;
- quarterly PnL and source-specific PnL;
- performance at 0.20, 0.50, and 1.00 point costs;
- matched-control difference; and
- parameter distance from each 2025 walk-forward pick.

OOS promotion requires all of the following:

- positive net points after the base cost assumption;
- positive net points in at least two-thirds of completed OOS quarters;
- positive candidate-minus-control difference;
- no single month contributing more than 50% of gross positive OOS PnL;
- no catastrophic source-handoff failure; and
- for the magnet candidate, the same conclusion under raw tick-path execution.

The confidence interval is reported, not silently converted into another tuning
lever. If it includes zero, label the result inconclusive rather than changing
the strategy.

## Portfolio decision

The candidates are independent hypotheses, not components to combine during
the first search. Test and gate them separately. Only candidates that pass the
one-shot OOS protocol may enter a later portfolio study. Correlation weights,
regime switching, and ensembles would be a separate pre-declared experiment
with a new untouched holdout.

## Planned implementation order

1. Implement and test the causal liquidity-line reconstruction without changing
   raw tables.
2. Implement the two ORB candidates and their matched controls.
3. Implement the line-reversal candidate and controls.
4. Implement the magnet candidate with raw tick/BBO-path fills.
5. Implement one optimizer that enforces the fixed split and refuses to expose
   OOS data before the selected parameters and trial count are written.
6. Run event-study kill tests, then the 2025 optimization, freeze the manifest,
   and finally reveal the single 2026 report.
