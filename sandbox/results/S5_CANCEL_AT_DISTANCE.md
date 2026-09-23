# S5 Cancel At Distance

Status: **rejected**. Do not promote this version.

The fixed 15-second hypothesis failed before parameter selection. Its
non-overlapping full-study sample had 2,168 trades, -1,435.85 net points,
-0.6623 point mean edge, t=-2.05, and a bootstrap 95% interval of
[-1.2983, -0.0311]. All 12 anchored walk-forward folds therefore selected no
cell. The resulting deployable OOS strategy was flat: zero trades and zero PnL
in every month.

## Fixed definition

- Normalize `log1p` add and cancel volume causally by second-of-session using
  only the prior 20 observations.
- Find the strongest same-side added layer in the preceding five contiguous
  seconds, then require a same-side cancellation.
- A canceled bid layer signals short; a canceled ask layer signals long.
- Skip seconds where both sides qualify.
- Enter on the next contiguous valid second and exit after 15 seconds.
- Charge 0.20 point per round trip.
- Search only 27 coarse cells:
  `layer_z ∈ {2,3,4}`, `distance_ticks ∈ {2,4,6}`, and
  `cancel_ratio ∈ {0.5,1,1.5}`. Cancellation z-score stays fixed at 2.

The study covers 330 sessions from 2025-02-12 through 2026-07-16. Read-only
raw reconstruction produced 143,492 minimum-grid candidates and retained
143,167 after a fixed 80%-125% reconstruction-coverage gate. Of the sessions,
301 used complete cached reconstruction and 29 used bounded causal seed
queries. No raw table or server file was changed.

## Honest OOS result

The anchored walk-forward OOS window is 2025-08 through 2026-07. No training
fold found a cell that cleared the selection rules, so the selector remained
flat. This is the strategy result, not a missing-data result.

| Month | Selected trades | Selected PnL | Fixed default trades | Fixed default points | Fixed default sized PnL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-08 | 0 | 0.00 | 95 | 30.25 | 51.90 |
| 2025-09 | 0 | 0.00 | 151 | -26.45 | -40.19 |
| 2025-10 | 0 | 0.00 | 237 | -10.53 | -30.31 |
| 2025-11 | 0 | 0.00 | 145 | -28.63 | -47.05 |
| 2025-12 | 0 | 0.00 | 12 | -8.02 | -12.51 |
| 2026-01 | 0 | 0.00 | 131 | 175.30 | 272.23 |
| 2026-02 | 0 | 0.00 | 79 | 104.45 | 169.74 |
| 2026-03 | 0 | 0.00 | 139 | -692.43 | -1,125.94 |
| 2026-04 | 0 | 0.00 | 215 | -297.00 | -418.29 |
| 2026-05 | 0 | 0.00 | 267 | -222.65 | -263.01 |
| 2026-06 | 0 | 0.00 | 203 | 32.15 | 34.62 |
| 2026-07* | 0 | 0.00 | 9 | 14.83 | 17.27 |
| **Total** | **0** | **0.00** | **1,683** | **-928.72** | **-1,391.53** |

`2026-07` is partial because the available Databento history ends on July 16.
The fixed-default columns are diagnostics for the fold-median/default
configuration `(layer_z=3, distance_ticks=4, cancel_ratio=1)`; they are not a
deployable selected result. Its point profit factor was 0.8742, mean edge was
-0.5518 points, t=-1.92, and 5 of 12 months were positive.

## Robustness checks

The full-study non-overlapping forward-effect checks were:

| Horizon | Trades | Gross edge | Net edge | Net t | Net 95% interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 second | 3,663 | -0.0081 | -0.2081 | -3.30 | [-0.3315, -0.0860] |
| 15 seconds | 2,168 | -0.4623 | -0.6623 | -2.05 | [-1.2983, -0.0311] |
| 60 seconds | 1,660 | 1.0666 | 0.8666 | 1.24 | [-0.4202, 2.2697] |

The positive 60-second estimate was not significant and the holding horizon
was not searched after seeing it.

Execution-cost sensitivity remained negative even at zero modeled cost:

| Cost (points) | OOS points | Mean edge | Sized PnL |
| ---: | ---: | ---: | ---: |
| 0.00 | -592.12 | -0.3518 | -935.91 |
| 0.20 | -928.72 | -0.5518 | -1,391.53 |
| 0.25 | -1,012.88 | -0.6018 | -1,499.53 |
| 0.40 | -1,265.32 | -0.7518 | -1,824.24 |
| 0.50 | -1,433.62 | -0.8518 | -2,030.69 |

Fixed risk fractions of 0.25%, 0.50%, and 1.00%, plus a causal inverse-ATR
multiplier clipped to 0.50x-1.50x, all produced the same -1,391.53 sized PnL,
1,890.82 maximum drawdown, and 0.871 profit factor because the margin cap
bound position size. Sizing cannot repair the negative per-trade edge.

## Filters and machine learning

Day, session-time, and side filters were evaluated only as diagnostics. Long
only produced +128.95 points and late-session only produced +143.38 points,
but each improved only 8/12 and 7/12 folds respectively. These are post-hoc
subsets and were not adopted.

The fixed population had 2,168 observations, exceeding the predeclared 2,000
observation threshold, so one standardized L2-logistic meta-labeler was run
with fixed `C=0.1`, threshold 0.5, and one predeclared feature set. Anchored
OOS results:

| Variant | Trades | Points | Mean edge | Positive months | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed default | 1,683 | -928.72 | -0.5518 | 5/12 | 1,890.82 |
| ML-selected | 493 | -273.22 | -0.5542 | 5/12 | 602.11 |

ML improved total points in only 6/12 folds, had positive PnL in 5 folds, and
did not improve per-trade edge. Against 1,000 matched random subsets, its
selection score had `p=0.5884`. The smaller loss is explained by lower
exposure, not demonstrated predictive skill.

The cumulative S5 search budget is 79 trials. The walk-forward promotion gate
failed on OOS profitability, fold breadth, confidence interval, and deflated
t-statistic. Attractive diagnostic subsets were deliberately left as
hypotheses to avoid overfitting.

Machine-readable details are in
[`s5_cancel_at_distance_result.json`](s5_cancel_at_distance_result.json).
