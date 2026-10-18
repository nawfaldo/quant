# FundedNext canon report -- risk 0.05, $50,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $50,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +706.9% | 403,446 | 14.2% (low on 2024-02-09) | 12.0% | 11,210 | 3.10 | 27/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +242.6% | 171,279 | 14.2% (low on 2024-02-09) | 12.0% | 8,133 | 2.67 | 18/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +132.4% | 116,189 | 5.3% (low on 2026-10-02) | 5.3% | 3,077 | 4.16 | 9/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +379.4% | +751.7% | +1,430.3% | 9.4% | 14.1% | 25.3% | 0.0% | 39.8% | +706.9%, dd 11.4% |
| 2024-2026 | 40% | +30.6% | +132.5% | +329.5% | 17.7% | 28.5% | 46.2% | 0.7% | 99.1% | +122.6%, dd 20.7% |
| 2024-2025 IS | 100% | +131.4% | +269.4% | +510.9% | 9.3% | 14.4% | 26.4% | 0.0% | 38.5% | +242.6%, dd 11.4% |
| 2024-2025 IS | 40% | +7.7% | +70.9% | +180.4% | 16.0% | 27.4% | 45.3% | 2.8% | 96.1% | +59.9%, dd 18.7% |
| 2026 OOS | 100% | +71.8% | +133.6% | +217.8% | 6.1% | 9.2% | 16.6% | 0.0% | 2.9% | +132.4%, dd 4.5% |
| 2026 OOS | 40% | +2.8% | +38.1% | +88.1% | 11.1% | 19.2% | 34.2% | 3.7% | 62.5% | +38.5%, dd 7.5% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $50,000 two-step challenge at risk 0.05

Pass = +$4,000 (phase 1) then +$2,500 (phase 2), never -$2,500 in a day, never below $45,000.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 99.4% | 0 | 4 | 32.8 | 18.3 | 58.0 |
| 2024-2025 IS | 522 | 99.0% | 0 | 4 | 32.2 | 19.3 | 56.5 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 22.3 | 12.2 | 39.0 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 98.5% (±0.2) | 1.5% | 0.0% | 0.0% | 53 |
| 2024-2026 | 40% | 73.1% (±0.6) | 26.9% | 0.0% | 0.0% | 77 |
| 2024-2025 IS | 100% | 97.6% (±0.2) | 2.4% | 0.0% | 0.0% | 62 |
| 2024-2025 IS | 40% | 70.4% (±0.7) | 29.5% | 0.0% | 0.0% | 88 |
| 2026 OOS | 100% | 99.9% (±0.0) | 0.1% | 0.0% | 0.0% | 38 |
| 2026 OOS | 40% | 81.9% (±0.5) | 18.1% | 0.0% | 0.0% | 65 |
