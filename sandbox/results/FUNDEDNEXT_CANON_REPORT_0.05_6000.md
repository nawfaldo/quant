# FundedNext canon report -- risk 0.05, $6,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $6,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +540.2% | 38,413 | 12.1% (low on 2024-03-14) | 10.4% | 11,210 | 3.13 | 28/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +182.2% | 16,934 | 12.1% (low on 2024-03-14) | 10.4% | 8,133 | 2.71 | 18/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +118.1% | 13,083 | 6.8% (low on 2026-03-16) | 5.2% | 3,077 | 3.89 | 9/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +304.4% | +569.3% | +1,033.0% | 8.5% | 12.8% | 22.8% | 0.0% | 24.8% | +540.2%, dd 9.8% |
| 2024-2026 | 40% | +27.7% | +111.9% | +263.5% | 15.9% | 25.8% | 41.2% | 0.8% | 97.1% | +104.3%, dd 18.9% |
| 2024-2025 IS | 100% | +102.2% | +201.8% | +358.1% | 8.4% | 13.3% | 22.8% | 0.0% | 25.9% | +182.2%, dd 9.8% |
| 2024-2025 IS | 40% | +6.4% | +57.7% | +142.0% | 14.1% | 24.5% | 41.7% | 2.9% | 89.3% | +48.8%, dd 16.6% |
| 2026 OOS | 100% | +61.7% | +120.7% | +201.6% | 6.6% | 10.2% | 18.4% | 0.0% | 5.9% | +118.1%, dd 6.4% |
| 2026 OOS | 40% | +1.1% | +36.1% | +85.3% | 11.4% | 19.9% | 32.5% | 4.4% | 65.6% | +35.0%, dd 8.8% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $6,000 two-step challenge at risk 0.05

Pass = +$480 (phase 1) then +$300 (phase 2), never -$300 in a day, never below $5,400.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 100.0% | 0 | 0 | 33.8 | 19.2 | 55.8 |
| 2024-2025 IS | 522 | 100.0% | 0 | 0 | 32.3 | 22.2 | 54.3 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 22.8 | 11.8 | 39.9 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 99.2% (±0.1) | 0.8% | 0.0% | 0.0% | 59 |
| 2024-2026 | 40% | 77.5% (±0.6) | 22.5% | 0.0% | 0.0% | 94 |
| 2024-2025 IS | 100% | 98.4% (±0.2) | 1.6% | 0.0% | 0.0% | 71 |
| 2024-2025 IS | 40% | 75.5% (±0.6) | 24.5% | 0.0% | 0.1% | 107 |
| 2026 OOS | 100% | 99.7% (±0.1) | 0.3% | 0.0% | 0.0% | 39 |
| 2026 OOS | 40% | 81.2% (±0.6) | 18.8% | 0.0% | 0.0% | 69 |
