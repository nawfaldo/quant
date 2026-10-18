# FundedNext canon report -- risk 0.05, $200,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $200,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +727.6% | 1,655,238 | 14.3% (low on 2024-02-09) | 12.0% | 11,210 | 3.11 | 27/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +250.5% | 701,011 | 14.3% (low on 2024-02-09) | 12.0% | 8,133 | 2.69 | 18/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +135.8% | 471,613 | 5.4% (low on 2026-10-02) | 5.4% | 3,077 | 4.19 | 9/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +388.9% | +772.2% | +1,483.1% | 9.5% | 14.2% | 25.7% | 0.0% | 41.6% | +727.6%, dd 11.4% |
| 2024-2026 | 40% | +31.5% | +134.6% | +336.7% | 17.9% | 28.7% | 46.6% | 0.7% | 99.3% | +124.7%, dd 20.9% |
| 2024-2025 IS | 100% | +135.6% | +278.0% | +531.3% | 9.4% | 14.6% | 26.7% | 0.0% | 40.5% | +250.5%, dd 11.4% |
| 2024-2025 IS | 40% | +8.2% | +72.7% | +185.5% | 16.2% | 27.7% | 45.6% | 2.8% | 96.5% | +61.3%, dd 18.9% |
| 2026 OOS | 100% | +73.3% | +137.2% | +223.8% | 6.3% | 9.5% | 17.0% | 0.0% | 3.5% | +135.8%, dd 4.8% |
| 2026 OOS | 40% | +2.5% | +38.8% | +90.3% | 11.4% | 19.6% | 34.7% | 3.8% | 65.2% | +39.3%, dd 7.5% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $200,000 two-step challenge at risk 0.05

Pass = +$16,000 (phase 1) then +$10,000 (phase 2), never -$10,000 in a day, never below $180,000.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 99.4% | 0 | 4 | 31.5 | 17.3 | 57.4 |
| 2024-2025 IS | 522 | 99.0% | 0 | 4 | 31.0 | 18.3 | 55.5 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 22.2 | 12.2 | 39.0 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 98.4% (±0.2) | 1.6% | 0.0% | 0.0% | 53 |
| 2024-2026 | 40% | 72.8% (±0.6) | 27.2% | 0.0% | 0.0% | 76 |
| 2024-2025 IS | 100% | 97.5% (±0.2) | 2.5% | 0.0% | 0.0% | 61 |
| 2024-2025 IS | 40% | 70.0% (±0.7) | 30.0% | 0.0% | 0.0% | 85 |
| 2026 OOS | 100% | 99.9% (±0.1) | 0.1% | 0.0% | 0.0% | 37 |
| 2026 OOS | 40% | 81.1% (±0.6) | 18.9% | 0.0% | 0.0% | 63 |
