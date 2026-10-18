# FundedNext canon report -- risk 0.05, $100,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $100,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +717.9% | 817,858 | 14.3% (low on 2024-02-09) | 12.0% | 11,210 | 3.10 | 26/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +246.8% | 346,772 | 14.3% (low on 2024-02-09) | 12.0% | 8,133 | 2.68 | 17/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +134.4% | 234,424 | 5.4% (low on 2026-10-02) | 5.4% | 3,077 | 4.16 | 9/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +384.8% | +762.9% | +1,460.3% | 9.5% | 14.2% | 25.6% | 0.0% | 41.3% | +717.9%, dd 11.4% |
| 2024-2026 | 40% | +31.1% | +133.5% | +334.1% | 17.9% | 28.7% | 46.4% | 0.7% | 99.2% | +123.7%, dd 20.9% |
| 2024-2025 IS | 100% | +133.6% | +273.9% | +522.4% | 9.4% | 14.6% | 26.7% | 0.0% | 39.9% | +246.8%, dd 11.4% |
| 2024-2025 IS | 40% | +7.8% | +72.0% | +183.4% | 16.2% | 27.6% | 45.4% | 2.8% | 96.3% | +60.7%, dd 18.9% |
| 2026 OOS | 100% | +72.8% | +135.7% | +221.3% | 6.2% | 9.3% | 16.6% | 0.0% | 3.1% | +134.4%, dd 4.7% |
| 2026 OOS | 40% | +2.7% | +38.7% | +89.3% | 11.2% | 19.4% | 34.5% | 3.7% | 64.0% | +39.0%, dd 7.5% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $100,000 two-step challenge at risk 0.05

Pass = +$8,000 (phase 1) then +$5,000 (phase 2), never -$5,000 in a day, never below $90,000.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 99.4% | 0 | 4 | 31.5 | 17.3 | 57.5 |
| 2024-2025 IS | 522 | 99.0% | 0 | 4 | 31.2 | 18.8 | 56.1 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 22.2 | 12.2 | 39.0 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 98.5% (±0.2) | 1.5% | 0.0% | 0.0% | 53 |
| 2024-2026 | 40% | 73.1% (±0.6) | 26.9% | 0.0% | 0.0% | 77 |
| 2024-2025 IS | 100% | 97.5% (±0.2) | 2.5% | 0.0% | 0.0% | 61 |
| 2024-2025 IS | 40% | 70.4% (±0.7) | 29.6% | 0.0% | 0.0% | 86 |
| 2026 OOS | 100% | 99.9% (±0.1) | 0.1% | 0.0% | 0.0% | 37 |
| 2026 OOS | 40% | 81.3% (±0.6) | 18.7% | 0.0% | 0.0% | 64 |
