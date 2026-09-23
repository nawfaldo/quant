# Candidate A: L2-confirmed opening-range breakout

## Decision

Killed before optimization. The predeclared 2025 event did not have positive
net edge, did not improve on its matched no-L2 control, and had negative signed
mean returns at every fixed horizon. Searching SL/TP, weekday/time, VIX, or
sizing after those results would optimize noise rather than rescue a supported
hypothesis. The 2026 holdout was not evaluated.

## Fixed test

- Instrument/account: NQ under the Forex sizing model, $1,000 initial equity.
- Cost: 0.2 NQ points, charged once at entry (one round-trip spread).
- In-sample: 2025-02-12 through 2025-12-31, New York wall-clock convention.
- Default: 30-minute range, zero-point buffer, 0.20 absolute top-five
  imbalance, 15-point stop, 30-point target, 90-minute time stop.
- Entry: next executable minute, maximum one trade per session, no entry after
  15:00, flat before 16:00.

## Results

| Measure | L2 candidate | No-L2 control | Candidate minus control |
| --- | ---: | ---: | ---: |
| Trades | 196 | 222 | -26 |
| Net points | -245.20 | -270.15 | +24.95 total |
| Mean net points/trade | -1.2510 | -1.2169 | -0.0341 |
| Sized PnL | -$47.88 | -$43.24 | -$4.64 |
| Profit factor | 0.871 | 0.899 | -0.028 |
| Maximum drawdown | $77.43 | $64.49 | -$12.94 |
| Positive months | 3/11 | 5/11 | -2 months |

The candidate's bootstrap 95% confidence interval for mean bracket edge was
[-4.3888, +1.9224] points, so it was also statistically inconclusive rather
than merely a small confirmed loss.

| Horizon | Candidate mean net points | Control mean net points | Difference |
| --- | ---: | ---: | ---: |
| 1 minute | -2.2115 | -2.4804 | +0.2689 |
| 5 minutes | -3.6056 | -3.6336 | +0.0280 |
| 15 minutes | -4.7651 | +1.8642 | -6.6293 |
| 30 minutes | -0.5189 | -0.0784 | -0.4405 |
| 60 minutes | -0.5291 | +2.9059 | -3.4350 |

Machine-readable details, monthly PnL, confidence intervals, data coverage, and
the cumulative trial count are in `candidate_a_result.json`.
