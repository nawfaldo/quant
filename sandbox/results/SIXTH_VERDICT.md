# Sixth wave -- final verdict, 30m

PASS = made money on the 2025-01-01 -> 2026-08-16 holdout AND beat the
best of its own three coin-flip seeds on that same holdout.

25 symbols scored: audjpy, audusd, aus200, btc, de40, es, ethusd, eurjpy, fr40, gbpjpy, gbpusd, hk50, jp225, nq, stoxx50, uk100, ukoil, usdcad, usdjpy, xageur, xaggbp, xalusd, xauaud, xaueur, xaugbp
not run (10): ethbtc, eurusd, xagaud, xagusd, xcuusd, xngusd, xniusd, xpdusd, xptusd, xznusd

## Pooled

    scored          177
    PASS            66  (37%)
    fail vs null    33
    fail on holdout 78
    null not run    0

## Per symbol

| symbol | selected | +holdout | PASS | fail-null | distinct clusters |
|---|---|---|---|---|---|
| audjpy | 8 | 4 | 4 | 0 | 4/4 |
| audusd | 3 | 2 | 2 | 0 | 2/2 |
| aus200 | 2 | 0 | 0 | 0 | - |
| btc | 19 | 16 | 6 | 10 | 3/6 |
| de40 | 10 | 8 | 2 | 6 | 2/2 |
| es | 11 | 5 | 4 | 1 | 4/4 |
| ethusd | 19 | 18 | 9 | 9 | 7/9 |
| eurjpy | 2 | 1 | 1 | 0 | 1/1 |
| fr40 | 5 | 0 | 0 | 0 | - |
| gbpjpy | 6 | 3 | 3 | 0 | 3/3 |
| gbpusd | 2 | 0 | 0 | 0 | - |
| hk50 | 5 | 1 | 1 | 0 | 1/1 |
| jp225 | 17 | 13 | 10 | 3 | 7/10 |
| nq | 22 | 10 | 10 | 0 | 7/10 |
| stoxx50 | 5 | 4 | 2 | 2 | 2/2 |
| uk100 | 6 | 1 | 1 | 0 | 1/1 |
| ukoil | 1 | 0 | 0 | 0 | - |
| usdcad | 6 | 2 | 2 | 0 | 2/2 |
| usdjpy | 9 | 8 | 7 | 1 | 7/7 |
| xageur | 3 | 1 | 1 | 0 | 1/1 |
| xaggbp | 2 | 0 | 0 | 0 | - |
| xalusd | 4 | 0 | 0 | 0 | - |
| xauaud | 1 | 1 | 0 | 1 | - |
| xaueur | 5 | 1 | 1 | 0 | 1/1 |
| xaugbp | 4 | 0 | 0 | 0 | - |

### Passes that are the same rule

- **btc**: amihud, entropy, estimator, vol_of_vol -- one signal set, 4 names
- **ethusd**: amihud, jump, vol_of_vol -- one signal set, 3 names
- **jp225**: entropy, estimator, value_area, vol_of_vol -- one signal set, 4 names
- **nq**: amihud, entropy, estimator, vol_of_vol -- one signal set, 4 names

## Every passing cell

| symbol | family | OOS % | null % | margin | dd | n | pf | IS t | bars fired |
|---|---|---|---|---|---|---|---|---|---|
| es | regime_breakout | +44.8 | -12.8 | +57.6 | 4.7 | 222 | 1.50 | 4.32 | 27% |
| ethusd | cusum | +77.2 | +30.1 | +47.1 | 11.4 | 490 | 1.21 | 3.72 | 20% |
| ethusd | kalman | +67.2 | +25.4 | +41.8 | 14.6 | 465 | 1.20 | 3.32 | 30% |
| ethusd | roofing | +48.7 | +7.2 | +41.5 | 12.1 | 204 | 1.37 | 2.93 | 10% |
| nq | regime_breakout | +51.7 | +15.0 | +36.7 | 9.7 | 202 | 1.40 | 3.00 | 37% |
| gbpjpy | adaptive_pullback | +1.1 | -33.4 | +34.4 | 12.7 | 160 | 1.02 | 2.16 | 18% |
| jp225 | momentum_stack | +34.3 | +2.1 | +32.1 | 30.1 | 169 | 1.31 | 3.25 | 36% |
| ethusd | kendall | +56.9 | +26.6 | +30.4 | 16.6 | 145 | 1.33 | 3.46 | 63% |
| de40 | regime_breakout | +27.0 | +1.4 | +25.6 | 12.8 | 180 | 1.22 | 2.08 | 37% |
| jp225 | value_area | +29.9 | +6.5 | +23.4 | 21.7 | 162 | 1.26 | 2.01 | 72% |
| ethusd | vol_of_vol | +57.4 | +34.1 | +23.2 | 10.3 | 273 | 1.28 | 3.27 | 84% |
| xaueur | autocorr | +13.3 | -8.1 | +21.3 | 11.4 | 102 | 1.28 | 2.14 | - |
| jp225 | kalman | +14.6 | -6.5 | +21.1 | 10.7 | 71 | 1.41 | 3.44 | 10% |
| jp225 | vol_of_vol | +17.9 | -2.9 | +20.8 | 5.0 | 165 | 1.39 | 1.91 | 84% |
| ethusd | amihud | +54.4 | +34.0 | +20.4 | 10.3 | 273 | 1.27 | 3.50 | 84% |
| btc | roofing | +38.3 | +19.0 | +19.3 | 13.5 | 436 | 1.19 | 3.41 | 8% |
| jp225 | entropy | +15.2 | -3.9 | +19.1 | 5.5 | 177 | 1.30 | 1.68 | 87% |
| jp225 | estimator | +15.2 | -3.9 | +19.1 | 5.5 | 177 | 1.30 | 1.68 | 87% |
| nq | adaptive_pullback | +7.1 | -12.0 | +19.0 | 10.0 | 141 | 1.11 | 0.95 | 40% |
| nq | estimator | +4.9 | -13.2 | +18.1 | 14.0 | 158 | 1.05 | 1.91 | 67% |
| usdjpy | fracdiff | +16.9 | -1.2 | +18.1 | 4.7 | 107 | 1.40 | 2.37 | 3% |
| nq | semivariance | +4.5 | -12.7 | +17.1 | 11.6 | 106 | 1.08 | 1.21 | 50% |
| nq | bulk_flow | +10.6 | -5.5 | +16.1 | 10.4 | 120 | 1.19 | 1.73 | 43% |
| stoxx50 | quantile_break | +13.8 | -0.8 | +14.6 | 9.0 | 79 | 1.31 | 2.05 | 13% |
| usdjpy | half_life | +22.0 | +7.9 | +14.1 | 10.0 | 314 | 1.19 | 2.83 | 27% |
| nq | vol_of_vol | +1.1 | -12.5 | +13.6 | 16.9 | 146 | 1.01 | 1.95 | 65% |
| audusd | roofing | +5.4 | -7.3 | +12.7 | 7.6 | 55 | 1.30 | 1.63 | 1% |
| gbpjpy | half_life | +8.9 | -3.5 | +12.4 | 7.9 | 162 | 1.15 | 2.81 | 4% |
| ethusd | regime_breakout | +20.7 | +9.7 | +11.0 | 23.3 | 119 | 1.19 | 3.32 | 26% |
| es | pivot_exhaustion | +6.3 | -3.3 | +9.6 | 7.3 | 68 | 1.16 | 2.21 | 4% |
| jp225 | quantile_break | +3.3 | -6.2 | +9.4 | 21.9 | 169 | 1.03 | 2.79 | 18% |
| audjpy | half_life | +2.9 | -6.4 | +9.3 | 5.1 | 51 | 1.14 | 2.73 | 5% |
| btc | vol_of_vol | +32.2 | +23.1 | +9.1 | 11.2 | 305 | 1.23 | 3.75 | 71% |
| nq | amihud | +4.9 | -4.2 | +9.1 | 14.0 | 158 | 1.05 | 1.84 | 67% |
| usdjpy | regime_breakout | +1.1 | -8.0 | +9.1 | 18.9 | 255 | 1.01 | 5.54 | 17% |
| btc | estimator | +31.7 | +22.9 | +8.8 | 11.2 | 304 | 1.22 | 3.81 | 71% |
| ethusd | momentum_stack | +32.1 | +24.1 | +8.0 | 8.1 | 278 | 1.29 | 3.76 | 61% |
| btc | jump | +27.5 | +20.8 | +6.7 | 8.3 | 239 | 1.25 | 3.16 | 53% |
| usdjpy | adaptive_pullback | +11.1 | +5.1 | +6.1 | 5.5 | 201 | 1.16 | 1.71 | 38% |
| ethusd | jump | +45.2 | +40.9 | +4.3 | 11.5 | 258 | 1.24 | 3.26 | 80% |
| nq | runs | +6.9 | +3.3 | +3.7 | 3.1 | 21 | 1.93 | 2.20 | 7% |
| nq | stat_confluence | +1.5 | -1.8 | +3.3 | 15.7 | 91 | 1.02 | 2.96 | 22% |
| es | roofing | +1.0 | -2.2 | +3.3 | 2.7 | 30 | 1.11 | 0.98 | 3% |
| hk50 | adaptive_pullback | +0.4 | -2.2 | +2.7 | 10.8 | 135 | 1.01 | 2.33 | - |
| btc | amihud | +32.2 | +30.0 | +2.1 | 11.2 | 305 | 1.23 | 3.80 | 71% |
| btc | entropy | +32.0 | +30.9 | +1.1 | 11.2 | 305 | 1.23 | 3.82 | 71% |
| audjpy | jump | +0.2 | - | - | 16.3 | 235 | 1.00 | 0.67 | 45% |
| audjpy | kalman | +2.2 | - | - | 3.0 | 82 | 1.15 | 2.11 | 6% |
| audjpy | regime_breakout | +4.2 | - | - | 3.9 | 101 | 1.18 | 2.95 | 19% |
| audusd | half_life | +3.7 | - | - | 12.9 | 75 | 1.11 | 1.76 | 4% |
| de40 | value_area | +37.3 | - | - | 17.7 | 312 | 1.13 | 2.38 | 87% |
| es | runs | +13.2 | - | - | 3.7 | 31 | 2.60 | 2.84 | 12% |
| eurjpy | regime_breakout | +7.1 | - | - | 8.5 | 382 | 1.07 | 2.82 | - |
| gbpjpy | fracdiff | +7.7 | - | - | 5.0 | 52 | 1.37 | 2.47 | 3% |
| jp225 | cusum | +18.0 | - | - | 24.8 | 209 | 1.14 | 1.88 | 23% |
| jp225 | regime_breakout | +23.1 | - | - | 16.5 | 337 | 1.11 | 3.28 | 45% |
| jp225 | roofing | +10.6 | - | - | 5.5 | 120 | 1.39 | 0.57 | 10% |
| nq | entropy | +4.9 | - | - | 14.0 | 158 | 1.05 | 1.87 | 67% |
| stoxx50 | regime_breakout | +7.2 | - | - | 14.9 | 251 | 1.04 | 1.59 | 36% |
| uk100 | adaptive_pullback | +4.9 | - | - | 8.8 | 181 | 1.08 | 1.70 | - |
| usdcad | cycle | +3.0 | - | - | 11.8 | 267 | 1.04 | 2.39 | 92% |
| usdcad | roofing | +9.4 | - | - | 3.4 | 115 | 1.42 | 1.79 | 1% |
| usdjpy | kendall | +49.5 | - | - | 12.3 | 274 | 1.43 | 3.24 | 82% |
| usdjpy | kurtosis | +3.0 | - | - | 20.4 | 412 | 1.02 | 2.10 | 85% |
| usdjpy | roofing | +10.9 | - | - | 3.9 | 104 | 1.39 | 2.66 | 1% |
| xageur | quantile_break | +12.7 | - | - | 12.4 | 75 | 1.21 | 2.65 | - |

## Families ranked by how often they pass

| family | group | scored | PASS | rate | median margin |
|---|---|---|---|---|---|
| vol_of_vol | adaptive | 4 | 4 | 100% | +20.8 |
| runs | pathstat | 2 | 2 | 100% | +3.7 |
| cycle | filter | 1 | 1 | 100% | +nan |
| regime_breakout | fusion | 14 | 9 | 64% | +25.6 |
| amihud | micro | 5 | 3 | 60% | +9.1 |
| entropy | pathstat | 5 | 3 | 60% | +1.1 |
| estimator | micro | 5 | 3 | 60% | +18.1 |
| cusum | adaptive | 4 | 2 | 50% | +47.1 |
| momentum_stack | fusion | 4 | 2 | 50% | +8.0 |
| roofing | filter | 16 | 7 | 44% | +19.3 |
| jump | micro | 7 | 3 | 43% | +6.7 |
| kalman | filter | 8 | 3 | 38% | +21.1 |
| adaptive_pullback | fusion | 14 | 5 | 36% | +2.7 |
| half_life | adaptive | 12 | 4 | 33% | -7.4 |
| kendall | pathstat | 6 | 2 | 33% | +30.4 |
| bulk_flow | micro | 3 | 1 | 33% | +16.1 |
| fracdiff | filter | 8 | 2 | 25% | -7.7 |
| semivariance | micro | 4 | 1 | 25% | -3.6 |
| kurtosis | pathstat | 4 | 1 | 25% | -14.9 |
| stat_confluence | fusion | 4 | 1 | 25% | +3.3 |
| autocorr | pathstat | 5 | 1 | 20% | +21.3 |
| quantile_break | adaptive | 16 | 3 | 19% | -1.1 |
| value_area | fusion | 12 | 2 | 17% | -3.8 |
| pivot_exhaustion | fusion | 8 | 1 | 12% | +9.6 |
| fisher | filter | 2 | 0 | 0% | +nan |
| tails | pathstat | 2 | 0 | 0% | +nan |
| hurst | pathstat | 2 | 0 | 0% | -3.4 |
