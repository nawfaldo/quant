# Fourth wave sweep: 48 symbol/bar jobs, 492 family slots, 26 passed

Pass rate 26/492 = 5.3%.

## Passes by group

| group | slots | passes | rate |
|---|---:|---:|---:|
| night | 156 | 16 | 10.3% |
| almanac | 96 | 2 | 2.1% |
| horizon | 168 | 1 | 0.6% |
| crossasset | 72 | 7 | 9.7% |

## Passes by family

| family | group | slots | passes | symbols |
|---|---|---:|---:|---|
| `night_drift` | night | 24 | 5 | avgo, gbpjpy, msft, nvda, tsla |
| `lead_lag` | crossasset | 24 | 4 | amd, nvda, tsla |
| `night_vol` | night | 24 | 2 | amzn, orcl |
| `night_day_return` | night | 24 | 2 | nvda, tsla |
| `night_close_location` | night | 24 | 2 | nvda, tsla |
| `night_gap_echo` | night | 24 | 2 | aapl, tsla |
| `night_streak` | night | 24 | 2 | nvda, tsla |
| `residual_momentum` | crossasset | 24 | 2 | tsla |
| `almanac_window` | almanac | 24 | 2 | jpm, tsla |
| `night_relative` | night | 12 | 1 | tsla |
| `calendar_break` | horizon | 24 | 1 | tsla |
| `bench_correlation` | crossasset | 24 | 1 | tsla |

## Passes by asset class

| class | symbols | slots | passes |
|---|---:|---:|---:|
| crypto | 3 | 58 | 0 |
| forex | 8 | 136 | 1 |
| future | 2 | 34 | 0 |
| stock | 11 | 264 | 25 |

## Every survivor, ranked by drift-adjusted t

**t is the statistic that matters.** The return is the outcome of one cell chosen from a 163,000-cell search; `edge_bp` strips the unconditional drift a mechanical rule with the same long/short mix and holding period would have earned anyway, and `t` says whether what is left is distinguishable from zero. `robust` below 3/3 means the neighbour gate asked almost nothing ([[all-categorical-axes-void-the-robustness-gate]]).

| symbol | bar | family | group | ret | dd | n | pf | gross bp | edge bp | t | robust | exit | stop | trend | cell |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|
| orcl | 30m | `night_vol` | night | +40.3% | 9.7% | 183 | 1.483 | +32.20 | +34.37 | +4.05 | 2/2 | nights_1 | 0.7 | ema_20d | ratio=0.8, side=short, state=quiet |
| tsla | 30m | `night_day_return` | night | +485.7% | 16.2% | 474 | 1.496 | +55.14 | +54.37 | +3.83 | 2/3 | nights_1 | 0.4 | ema_20d | direction=breakout, threshold_day=0.25 |
| tsla | 30m | `night_close_location` | night | +139.3% | 8.6% | 496 | 1.587 | +50.88 | +49.90 | +3.63 | 2/2 | nights_1 | 0.7 | ema_20d | direction=breakout, edge=0.7 |
| tsla | 30m | `night_drift` | night | +226.3% | 10.8% | 145 | 2.095 | +199.51 | +152.44 | +2.82 | 1/1 | nights_3 | 0.7 | ema_20d | side=long, weekday=3 |
| tsla | 30m | `night_gap_echo` | night | +215.7% | 13.3% | 334 | 1.697 | +73.40 | +68.62 | +2.52 | 2/2 | nights_2 | 0.7 | ema_20d | direction=breakout, threshold_day=0.25 |
| tsla | 30m | `night_streak` | night | +417.2% | 16.2% | 344 | 1.436 | +57.51 | +54.00 | +2.22 | 2/3 | nights_2 | 0.4 | ema_20d | count=2, direction=follow |
| nvda | 30m | `lead_lag` | crossasset | +259.6% | 18.1% | 326 | 1.503 | +55.08 | +33.43 | +2.12 | 3/5 | rr_2 | 0.4 | ema_20d | catchup=1.0, direction=fade, period=540, threshold=2.0 |
| tsla | 1d | `residual_momentum` | crossasset | +189.2% | 19.6% | 229 | 1.431 | +112.24 | +103.94 | +1.99 | 2/3 | days_5 | 0.7 | ema_20d | direction=breakout, period=20, threshold=3.0 |
| amd | 30m | `lead_lag` | crossasset | +92.5% | 14.0% | 154 | 1.525 | +87.45 | +86.83 | +1.89 | 3/4 | days_5 | 0.7 | ema_20d | catchup=1.0, direction=fade, period=540, threshold=1.0 |
| tsla | 1d | `lead_lag` | crossasset | +164.0% | 18.1% | 215 | 1.384 | +105.75 | +99.26 | +1.82 | 3/4 | rr_3 | 0.7 | ema_20d | catchup=0.5, direction=fade, period=20, threshold=1.0 |
| gbpjpy | 30m | `night_drift` | night | +38.7% | 15.6% | 207 | 1.297 | +6.87 | +5.73 | +1.62 | 1/1 | nights_3 | 0.2 | ema_50d | side=long, weekday=4 |
| tsla | 30m | `residual_momentum` | crossasset | +221.9% | 19.6% | 170 | 1.518 | +154.40 | +141.59 | +1.57 | 2/3 | days_15 | 0.7 | ema_20d | direction=breakout, period=180, threshold=3.0 |
| nvda | 30m | `night_day_return` | night | +36.2% | 10.9% | 280 | 1.296 | +22.68 | +21.43 | +1.57 | 2/3 | nights_1 | 0.7 | ema_20d | direction=breakout, threshold_day=0.5 |
| tsla | 30m | `night_relative` | night | +77.3% | 11.7% | 343 | 1.316 | +43.70 | +38.88 | +1.45 | 2/2 | nights_2 | 0.7 | ema_20d | direction=fade, threshold=0.25 |
| tsla | 30m | `lead_lag` | crossasset | +198.5% | 12.9% | 146 | 1.581 | +150.61 | +131.81 | +1.36 | 3/4 | days_15 | 0.7 | none | catchup=1.0, direction=fade, period=180, threshold=1.0 |
| tsla | 1d | `calendar_break` | horizon | +129.4% | 12.4% | 133 | 1.672 | +156.35 | +105.90 | +1.35 | 2/2 | rr_3 | 0.7 | none | buffer_atr=0.0, direction=breakout, unit=quarter |
| amzn | 30m | `night_vol` | night | +20.4% | 7.5% | 155 | 1.38 | +21.51 | +19.50 | +1.22 | 2/3 | nights_1 | 0.4 | none | ratio=1.2, side=long, state=violent |
| nvda | 30m | `night_drift` | night | +271.5% | 14.2% | 347 | 1.538 | +75.82 | +27.21 | +1.17 | 1/1 | nights_3 | 0.7 | ema_20d | side=long, weekday=any |
| tsla | 1d | `bench_correlation` | crossasset | +85.0% | 16.3% | 133 | 1.604 | +72.03 | +55.51 | +1.05 | 2/3 | rr_2 | 0.7 | ema_50d | direction=breakout, period=60, threshold=0.5 |
| avgo | 30m | `night_drift` | night | +18.1% | 6.4% | 180 | 1.307 | +13.80 | +10.11 | +1.02 | 1/1 | nights_1 | 0.7 | ema_50d | side=long, weekday=4 |
| aapl | 30m | `night_gap_echo` | night | +69.2% | 12.8% | 306 | 1.239 | +18.80 | +14.56 | +1.00 | 2/2 | nights_3 | 0.7 | ema_20d | direction=fade, threshold_day=0.1 |
| nvda | 30m | `night_streak` | night | +49.0% | 12.0% | 162 | 1.29 | +41.21 | +29.39 | +0.82 | 2/3 | nights_3 | 0.7 | ema_20d | count=3, direction=follow |
| msft | 30m | `night_drift` | night | +34.0% | 11.0% | 138 | 1.302 | +30.08 | +14.89 | +0.79 | 1/1 | nights_3 | 0.7 | ema_20d | side=long, weekday=0 |
| nvda | 30m | `night_close_location` | night | +64.4% | 14.4% | 241 | 1.369 | +26.69 | +16.20 | +0.73 | 2/2 | nights_2 | 0.7 | ema_20d | direction=breakout, edge=0.85 |
| jpm | 1d | `almanac_window` | almanac | +37.0% | 14.4% | 138 | 1.219 | +39.92 | +16.80 | +0.57 | 1/1 | rr_3 | 0.7 | none | side=long, window=summer |
| tsla | 1d | `almanac_window` | almanac | +142.0% | 19.5% | 178 | 1.54 | +100.39 | +23.00 | +0.50 | 1/1 | rr_2 | 0.7 | none | side=long, window=summer |

## Two confounds, before anything above is read as a finding

### 1. The gate is a function of history length, and stocks have less

`passes` demands `trades >= max(50, 25 * years)` and `positive_years >= years - 1`. Those are not constants -- they scale with the in-sample window, and the Dukascopy stock tables start in 2020 while everything else starts in 2018. So the stocks were scored against a materially weaker bar than the symbols they are being compared with:

| class | IS years | trade floor | positive years needed |
|---|---:|---:|---|
| crypto | 7 (2018-2024) | 175 | 6/7 |
| forex | 7 (2018-2024) | 175 | 6/7 |
| future | 7 (2018-2024) | 175 | 6/7 |
| stock | 5 (2020-2024) | 125 | 4/5 |

**25 of the 26 passes are stocks.** Some unknown share of that is the gate being easier rather than the strategies being better, and this sweep cannot separate the two. The clean test is to re-run the non-stock symbols restricted to 2020-2024 so every symbol faces the same floors; until that is done, do not read the class table as a statement about which asset class the fourth wave works on.

### 2. TSLA and NVDA are 16 of the 26, which is a known artefact

| symbol | passes | share |
|---|---:|---:|
| tsla | 13 | 50% |
| nvda | 5 | 19% |
| orcl | 1 | 4% |
| amd | 1 | 4% |
| gbpjpy | 1 | 4% |
| amzn | 1 | 4% |
| avgo | 1 | 4% |
| aapl | 1 | 4% |
| msft | 1 | 4% |
| jpm | 1 | 4% |

This is the result `exness_families` already documents and explains ([[stock-cost-share-explains-the-tsla-result]]): risk per trade is fixed and the stop is a fraction of the daily range, so what decides a stock sweep is the ratio of spread to range -- the share of each unit of risk handed to the broker before a rule has done anything. TSLA pays 1.3% of its stop and ORCL 15.2%, a twelve-fold advantage. A fourth-wave sweep reproducing the same ranking is evidence the sweep is measuring cost share again, not evidence the night families like TSLA.

**7 of 26 survivors reach |t| >= 2.**

- orcl 30m `night_vol`: +40.3%, n=183, edge +34.37bp t=+4.05, robust 2/2
- tsla 30m `night_day_return`: +485.7%, n=474, edge +54.37bp t=+3.83, robust 2/3
- tsla 30m `night_close_location`: +139.3%, n=496, edge +49.90bp t=+3.63, robust 2/2
- tsla 30m `night_drift`: +226.3%, n=145, edge +152.44bp t=+2.82, robust 1/1
- tsla 30m `night_gap_echo`: +215.7%, n=334, edge +68.62bp t=+2.52, robust 2/2
- tsla 30m `night_streak`: +417.2%, n=344, edge +54.00bp t=+2.22, robust 2/3
- nvda 30m `lead_lag`: +259.6%, n=326, edge +33.43bp t=+2.12, robust 3/5

No null control has been run. A coin-flip search on this data has returned +622% at t=4.19 ([[coin-flip-control-beats-real-signals]]), so nothing above is a result until `why` has priced it at the same budget.
