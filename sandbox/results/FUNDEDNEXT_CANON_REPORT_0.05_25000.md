# FundedNext canon report -- risk 0.05, $25,000 per window

26 sleeves, FundedNext live fills, spreads and commission, forced minimum lot. Every window starts from a fresh $25,000. Monte Carlo: 5,000 paths, 10-day blocks of the book's own days, chained in order.

## 1. Summary, every window

| window | realised return | final $ | realised MTM drawdown | realised closed drawdown | trades | monthly Sharpe | positive months |
|---|---|---|---|---|---|---|---|
| 2024-2026 (2024-01-01 to 2026-10-13) | +688.1% | 197,017 | 13.9% (low on 2024-02-09) | 11.7% | 11,210 | 3.10 | 26/34 |
| 2024-2025 IS (2024-01-01 to 2026-01-01) | +235.3% | 83,832 | 13.9% (low on 2024-02-09) | 11.7% | 8,133 | 2.66 | 17/24 |
| 2026 OOS (2026-01-01 to 2026-10-13) | +128.8% | 57,190 | 5.2% (low on 2026-10-02) | 5.2% | 3,077 | 4.16 | 9/10 |

## 2. Monte Carlo of the book (return and drawdown over the window's length)

Edge kept 100% = the backtest as it is. 40% = live keeps only 40% of the average day's profit.

| window | edge kept | return: bad case (5th pct) | return: typical (median) | return: good case (95th pct) | drawdown: typical | drawdown: bad case (95th pct) | drawdown: worst path | chance of losing money | chance drawdown > 10% | CHECK: real order gives |
|---|---|---|---|---|---|---|---|---|---|---|
| 2024-2026 | 100% | +373.3% | +732.4% | +1,384.8% | 9.3% | 13.9% | 24.6% | 0.0% | 37.6% | +688.1%, dd 11.2% |
| 2024-2026 | 40% | +30.5% | +130.3% | +323.5% | 17.5% | 28.1% | 45.6% | 0.7% | 99.1% | +120.7%, dd 20.6% |
| 2024-2025 IS | 100% | +127.8% | +260.7% | +491.9% | 9.2% | 14.2% | 26.0% | 0.0% | 36.4% | +235.3%, dd 11.2% |
| 2024-2025 IS | 40% | +7.6% | +69.5% | +175.9% | 15.8% | 27.0% | 44.9% | 2.8% | 95.4% | +58.7%, dd 18.5% |
| 2026 OOS | 100% | +70.2% | +130.3% | +210.8% | 5.9% | 8.9% | 16.3% | 0.0% | 2.1% | +128.8%, dd 4.4% |
| 2026 OOS | 40% | +2.7% | +37.5% | +86.0% | 10.8% | 18.7% | 33.5% | 3.5% | 59.4% | +37.7%, dd 7.3% |

CHECK: at 100% edge, chaining the real days in their real order should give the realised return in section 1 (drawdown reads a little lower: days, not 30-minute marks).

## 3. The $25,000 two-step challenge at risk 0.05

Pass = +$2,000 (phase 1) then +$1,250 (phase 2), never -$1,250 in a day, never below $22,500.

**A. Real history: a challenge started on every past day** (calendar days, median of the starts that passed)

| window | start days | passed both | failed daily-loss | failed max-loss | days to pass phase 1 | days to pass phase 2 | days to pass both |
|---|---|---|---|---|---|---|---|
| 2024-2026 | 725 | 99.7% | 0 | 2 | 34.5 | 18.3 | 58.8 |
| 2024-2025 IS | 522 | 99.5% | 0 | 2 | 35.3 | 21.0 | 57.9 |
| 2026 OOS | 202 | 100.0% | 0 | 0 | 23.2 | 12.3 | 40.0 |

**B. Monte Carlo** (trading days, median of the paths that passed)

| window | edge kept | passed both | failed max-loss | failed daily-loss | not finished in 500 days | trading days to pass both |
|---|---|---|---|---|---|---|
| 2024-2026 | 100% | 98.6% (±0.2) | 1.4% | 0.0% | 0.0% | 54 |
| 2024-2026 | 40% | 73.7% (±0.6) | 26.3% | 0.0% | 0.0% | 80 |
| 2024-2025 IS | 100% | 97.9% (±0.2) | 2.1% | 0.0% | 0.0% | 63 |
| 2024-2025 IS | 40% | 71.2% (±0.6) | 28.7% | 0.0% | 0.0% | 91 |
| 2026 OOS | 100% | 99.9% (±0.0) | 0.1% | 0.0% | 0.0% | 38 |
| 2026 OOS | 40% | 82.9% (±0.5) | 17.1% | 0.0% | 0.0% | 68 |
