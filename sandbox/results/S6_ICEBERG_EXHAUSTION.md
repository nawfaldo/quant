# S6 Iceberg Exhaustion Breakout

Status: **rejected**. Do not promote this version.

S6 tests continuation after unusually strong same-side replenishment
fails to hold price. It is a side-level proxy built from the retained
one-minute rollup, not price-level proof of an individual iceberg.

## Headline result

The fixed default produced **1,733 trades**, **-2,751.10 net points**, and a -1.5875-point mean edge (t=-3.33). The stitched walk-forward result was **+0.00** over 0 trades.

## Monthly performance

The selected walk-forward series is the deployable result. Fixed-rule
and ML columns are diagnostics over the same 2025-08 through 2026-07 span.

| Month | Selected WF | Fixed rule | ML |
| --- | ---: | ---: | ---: |
| 2025-08 | +0.00 | -102.84 | -90.98 |
| 2025-09 | +0.00 | -37.22 | -29.76 |
| 2025-10 | +0.00 | -39.55 | -27.79 |
| 2025-11 | +0.00 | -40.93 | +19.63 |
| 2025-12 | +0.00 | +20.32 | -0.38 |
| 2026-01 | +0.00 | -26.00 | -19.36 |
| 2026-02 | +0.00 | -55.24 | -54.67 |
| 2026-03 | +0.00 | -36.28 | -7.23 |
| 2026-04 | +0.00 | +1.28 | -9.65 |
| 2026-05 | +0.00 | -35.43 | -20.01 |
| 2026-06 | +0.00 | -15.37 | +6.34 |
| 2026-07 | +0.00 | -0.89 | +0.00 |

## Robustness

- Grid: 27 cells across three coarse axes.
- Profitable selected folds: 0/12.
- Purged-CV positive blocks: 0/6.
- Cumulative trial count used for deflation: 75.

## Machine learning

The single fixed L2-logistic meta-labeler saw 2,083 raw candidates and selected 573 anchored OOS trades. It produced -1,679.10 points, improved total points in 10/12 folds, and its matched-random p-value was 0.7393. Its mean edge was -2.9304 points/trade versus -2.1687 for the fixed rule, so the smaller dollar loss came from lower exposure, not a better edge.

Day/time/side filters and sizing variants are retained in the JSON as
post-selection diagnostics. None is adopted from this same history.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -B -m unittest sandbox.tests.test_s6_iceberg_exhaustion -v
py -B -m sandbox.research.s6_iceberg_exhaustion_research
```
