# FundedNext canon report -- risk 0.05, $15,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $15,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +640.1% | 111,013 | 13.1% (low on 2024-02-12) | 10.9% | 11,210 | 3.06 | 27/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +217.7% | 47,659 | 13.1% (low on 2024-02-12) | 10.9% | 8,133 | 2.63 | 18/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +124.2% | 33,626 | 5.0% (low on 2026-10-02) | 5.0% | 3,077 | 4.06 | 10/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +351.9% | +678.8% | +1,273.5% | 9.0% | 13.4% | 24.0% | 0.0% | 32.4% | +640.1%, dd 10.4% |
| 2024-2026 | 40% | +29.9% | +124.6% | +305.2% | 17.0% | 27.5% | 44.8% | 0.8% | 98.8% | +115.6%, dd 20.2% |
| 2024-2025 IS | 100% | +119.8% | +241.8% | +450.2% | 8.8% | 13.7% | 24.8% | 0.0% | 31.2% | +217.7%, dd 10.4% |
| 2024-2025 IS | 40% | +7.4% | +66.2% | +165.0% | 15.2% | 26.1% | 42.6% | 2.7% | 94.3% | +55.5%, dd 18.0% |
| 2026 OOS | 100% | +67.1% | +125.0% | +201.5% | 5.6% | 8.5% | 15.9% | 0.0% | 1.5% | +124.2%, dd 4.3% |
| 2026 OOS | 40% | +2.5% | +36.1% | +83.5% | 10.5% | 18.3% | 34.9% | 3.6% | 55.6% | +36.7%, dd 8.2% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $15,000 two-step challenge at risk 0.05

Pass = +$1,200 (phase 1) then +$750 (phase 2), never -$750 in a day, never below $13,500.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 99.9% | 0 | 1 | 34.3 | 21.9 | 60.4 |
| 2024-2025 IS | 522 | 99.8% | 0 | 1 | 35.2 | 21.9 | 60.5 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 24.5 | 13.3 | 42.5 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 98.9% (±0.1) | 1.1% | 0.0% | 0.0% | 56 |
| 2024-2026 | 40% | 74.7% (±0.6) | 25.3% | 0.0% | 0.0% | 84 |
| 2024-2025 IS | 100% | 98.3% (±0.2) | 1.7% | 0.0% | 0.0% | 65 |
| 2024-2025 IS | 40% | 72.4% (±0.6) | 27.6% | 0.0% | 0.0% | 95 |
| 2026 OOS | 100% | 99.9% (±0.0) | 0.1% | 0.0% | 0.0% | 39 |
| 2026 OOS | 40% | 83.0% (±0.5) | 17.0% | 0.0% | 0.0% | 71 |
