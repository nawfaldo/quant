# exness_families -- what survived

Written 2026-08-19. 164 survivors from three study waves.

A survivor is a strategy that did **all three** of these, in order:

1. `select` -- won its family's in-sample parameter search and passed the
   shared gates (profit factor, drawdown, per-year consistency, fill rate,
   and a neighbour-robustness test)
2. `validate` -- cleared the untouched **2025-01-01 to 2026-08-16** holdout at
   return > 0, PF >= 1.05, n >= 30
3. `why` -- beat its own **coin-flip null**: the identical search with entry
   directions randomised, up to three seeds, and the real holdout return had
   to exceed the best of them

Step 3 is the one that matters. On this data a coin flip given the same
search budget has produced a **+1,434% in-sample** result on BTC. Every
in-sample number in this study is uninterpretable without its null, so a row
that cleared the holdout but has no null on file is **not** in this folder.

## The funnel

| wave | run | families | IS winners | cleared holdout | had a null | survived | rate | p vs 25% |
|---|---|---|---|---|---|---|---|---|
| original | 2026-08-16/17 | 27 | 385 | 96 | 59 | **47** | 80% | 2e-18 |
| new | 2026-08-18 | 33 | 329 | 117 | 117 | **84** | 72% | 3e-26 |
| combined | 2026-08-19 | 13 | 158 | 46 | 46 | **33** | 72% | 4e-11 |
| **total** | | | **872** | **259** | **222** | **164** | **74%** | **2e-52** |

**How to read the rate.** If the real signal were exchangeable with a coin
flip, the real holdout return would beat the best of three null seeds about
25% of the time. It did so 74% of the time. That is the single strongest
piece of evidence in this study, and it is a statement about the *aggregate*
-- it does not make any individual row below trustworthy.

`p` is a plain binomial tail and assumes the rows are independent draws.
They are not: the same family appears on many symbols and the same symbol
carries many families, so the true p is weaker than shown. No
multiple-testing or deflated-Sharpe adjustment has been applied.

## Survivors

`margin` = holdout return minus the best coin-flip seed; `inf` means the null
never found an in-sample cell. `BE` = breakeven spread in bp, the cost at
which the edge is gone. `mSh` = monthly Sharpe on the holdout. `t` = t-stat
of per-trade edge over drift.

### combined (33)

| symbol | family | class | tf | OOS % | dd % | n | PF | BE bp | mSh | t | null | margin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| btc | nested | crypto | 30m | +108.0 | 13.8 | 216 | 1.46 | 11.3 | 0.51 | 1.92 | +28.0 | +80.0 |
| ethusd | confluence | crypto | 30m | +78.6 | 9.5 | 77 | 2.05 | 40.3 | 0.62 | 2.45 | +65.9 | +12.7 |
| xalusd | gated_fade | commodity | 30m | +76.7 | 28.2 | 119 | 1.53 | 37.1 | 0.33 | 1.76 | -8.1 | +84.8 |
| ethusd | gated_orb | crypto | 30m | +60.4 | 15.1 | 366 | 1.28 | 17.0 | 0.46 | 1.87 | +26.7 | +33.7 |
| ethusd | level_confluence | crypto | 30m | +58.8 | 8.8 | 160 | 1.59 | 29.6 | 0.52 | 2.33 | +19.8 | +39.0 |
| de40 | confluence | index | 30m | +54.6 | 14.5 | 260 | 1.31 | 5.8 | 0.43 | 1.50 | +1.4 | +53.2 |
| xalusd | regime_router | commodity | 30m | +48.2 | 29.5 | 121 | 1.33 | 27.5 | 0.22 | 1.42 | -42.7 | +90.9 |
| ethusd | break_retest | crypto | 30m | +37.2 | 11.8 | 193 | 1.29 | 19.4 | 0.38 | 1.70 | +18.1 | +19.1 |
| btc | gated_orb | crypto | 30m | +34.8 | 17.1 | 409 | 1.20 | 8.5 | 0.28 | 1.65 | +5.4 | +29.4 |
| ethusd | gated_swing | crypto | 30m | +34.1 | 26.2 | 64 | 1.35 | 75.5 | 0.18 | 0.75 | none | inf |
| gbpjpy | trap | forex | 30m | +32.8 | 5.0 | 103 | 2.45 | 9.0 | 0.68 | 2.44 | none | inf |
| usdjpy | gated_fade | forex | 30m | +31.7 | 10.7 | 350 | 1.22 | 3.4 | 0.25 | 1.73 | +13.7 | +18.0 |
| de40 | break_retest | index | 30m | +31.6 | 8.2 | 84 | 1.64 | 12.6 | 0.36 | 1.92 | +6.6 | +25.0 |
| usdjpy | gated_orb | forex | 30m | +30.7 | 6.0 | 310 | 1.41 | 4.1 | 0.37 | 1.83 | +0.2 | +30.4 |
| ethusd | idio_break | crypto | 30m | +27.2 | 5.4 | 131 | 1.59 | 35.7 | 0.40 | 2.12 | +12.7 | +14.5 |
| ethusd | gated_donchian | crypto | 30m | +24.8 | 16.6 | 66 | 1.28 | 23.8 | 0.17 | 1.09 | +24.5 | +0.3 |
| ukoil | level_confluence | commodity | 30m | +24.6 | 8.5 | 44 | 1.88 | 13.4 | 0.35 | 1.33 | +21.6 | +3.0 |
| btc | level_confluence | crypto | 30m | +24.2 | 15.6 | 109 | 1.25 | 6.8 | 0.20 | 0.89 | +3.9 | +20.3 |
| uk100 | gated_fade | index | 30m | +17.7 | 11.2 | 177 | 1.20 | 3.5 | 0.28 | 2.00 | -19.0 | +36.7 |
| jp225 | gated_swing | index | 30m | +16.3 | 9.4 | 53 | 1.38 | 8.6 | 0.32 | 0.04 | +3.7 | +12.7 |
| es | gated_orb | future | 30m | +15.2 | 10.3 | 80 | 1.31 | 6.7 | 0.27 | 0.95 | -8.8 | +23.9 |
| btc | gated_donchian | crypto | 30m | +12.1 | 9.6 | 59 | 1.24 | 4.5 | 0.19 | 0.44 | -4.2 | +16.2 |
| eurjpy | two_stage | forex | 30m | +11.9 | 4.1 | 87 | 1.74 | 5.0 | 0.47 | 1.46 | -5.5 | +17.5 |
| nq | level_confluence | future | 30m | +10.9 | 14.8 | 118 | 1.13 | 4.8 | 0.11 | 0.75 | +0.6 | +10.3 |
| jp225 | break_retest | index | 30m | +9.8 | 15.3 | 121 | 1.14 | 4.1 | 0.14 | 0.41 | -4.0 | +13.8 |
| eurjpy | gated_orb | forex | 30m | +8.7 | 17.7 | 211 | 1.12 | 2.3 | 0.11 | 1.45 | -5.2 | +13.9 |
| hk50 | level_confluence | index | 30m | +7.1 | 2.6 | 122 | 1.33 | 6.7 | 0.30 | 1.78 | none | inf |
| audjpy | gated_donchian | forex | 30m | +5.5 | 6.6 | 88 | 1.16 | 2.9 | 0.20 | 0.66 | -5.3 | +10.8 |
| btc | gated_swing | crypto | 30m | +4.9 | 11.4 | 35 | 1.14 | -6.6 | 0.07 | -0.14 | -1.8 | +6.7 |
| audusd | trap | forex | 30m | +4.7 | 10.1 | 109 | 1.11 | 2.5 | 0.08 | 0.54 | none | inf |
| usdcad | gated_fade | forex | 30m | +2.5 | 5.9 | 64 | 1.13 | 2.8 | 0.14 | 0.87 | none | inf |
| xaueur | break_retest | commodity | 30m | +2.2 | 8.8 | 49 | 1.12 | 2.0 | 0.11 | -0.04 | +1.3 | +0.9 |
| nq | two_stage | future | 30m | +1.3 | 7.1 | 50 | 1.09 | 1.6 | 0.06 | 0.12 | none | inf |

### new (84)

| symbol | family | class | tf | OOS % | dd % | n | PF | BE bp | mSh | t | null | margin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ethusd | cci | crypto | 30m | +71.3 | 11.3 | 312 | 1.31 | 22.1 | 0.41 | 1.98 | +68.4 | +3.0 |
| ethusd | macd_hist | crypto | 30m | +60.6 | 12.6 | 232 | 1.36 | 24.8 | 0.41 | 2.21 | +10.3 | +50.3 |
| de40 | floor_pivot | index | 30m | +60.3 | 18.1 | 313 | 1.21 | 5.4 | 0.29 | 1.11 | +32.0 | +28.3 |
| ethusd | efficiency | crypto | 30m | +53.4 | 15.4 | 267 | 1.29 | 18.4 | 0.38 | 1.52 | +48.2 | +5.2 |
| ethusd | floor_pivot | crypto | 30m | +51.6 | 9.2 | 440 | 1.27 | 23.0 | 0.49 | 2.25 | +43.1 | +8.6 |
| ethusd | swing_break | crypto | 30m | +49.8 | 9.4 | 184 | 1.34 | 26.3 | 0.42 | 1.91 | none | inf |
| googl | day_of_week | stock | 30m | +48.7 | 7.5 | 52 | 2.03 | 94.5 | 0.34 | 0.73 | none | inf |
| ethusd | pullback | crypto | 30m | +48.5 | 10.8 | 200 | 1.62 | 39.5 | 0.54 | 2.72 | none | inf |
| ethusd | day_of_week | crypto | 30m | +47.7 | 8.2 | 40 | 1.91 | 152.0 | 0.49 | 1.49 | +7.3 | +40.3 |
| btc | supertrend | crypto | 30m | +44.0 | 11.7 | 120 | 1.44 | 5.2 | 0.29 | 0.55 | +7.4 | +36.5 |
| ethusd | rvol | crypto | 30m | +42.8 | 11.1 | 245 | 1.28 | 23.2 | 0.36 | 1.82 | +12.1 | +30.6 |
| nq | volatility_breakout | future | 30m | +37.9 | 11.1 | 201 | 1.29 | 5.2 | 0.32 | 1.13 | -13.2 | +51.1 |
| ethusd | sar | crypto | 30m | +36.5 | 13.0 | 221 | 1.27 | 28.6 | 0.27 | 2.11 | +7.4 | +29.1 |
| jp225 | obv_divergence | index | 30m | +35.2 | 10.0 | 81 | 1.71 | 17.8 | 0.43 | 1.58 | +1.4 | +33.8 |
| btc | vol_regime | crypto | 30m | +34.0 | 11.2 | 303 | 1.24 | 8.6 | 0.38 | 1.59 | +32.4 | +1.6 |
| ethusd | volatility_breakout | crypto | 30m | +31.9 | 20.8 | 103 | 1.34 | 14.3 | 0.26 | 1.09 | +11.1 | +20.8 |
| usdjpy | sar | forex | 30m | +31.5 | 11.3 | 283 | 1.30 | 4.3 | 0.37 | 2.06 | -22.2 | +53.7 |
| ukoil | xma_cross | commodity | 30m | +31.5 | 11.0 | 101 | 1.40 | 12.6 | 0.30 | 1.52 | -15.2 | +46.7 |
| btc | mfi | crypto | 30m | +31.4 | 8.8 | 230 | 1.32 | 8.4 | 0.45 | 1.37 | +5.0 | +26.5 |
| tsm | skew | stock | 30m | +29.9 | 13.9 | 64 | 1.58 | 67.0 | 0.25 | -0.10 | none | inf |
| btc | xma_ribbon | crypto | 30m | +29.8 | 11.8 | 73 | 1.58 | 11.5 | 0.27 | 1.52 | +20.5 | +9.3 |
| btc | volatility_breakout | crypto | 30m | +28.6 | 7.8 | 190 | 1.38 | 10.6 | 0.39 | 1.72 | +17.6 | +11.0 |
| ethusd | xma_slope | crypto | 30m | +26.9 | 7.9 | 117 | 1.36 | 27.3 | 0.36 | 1.54 | +23.7 | +3.2 |
| ethusd | linreg_trend | crypto | 30m | +25.4 | 7.9 | 202 | 1.32 | 23.0 | 0.46 | 1.73 | +17.1 | +8.3 |
| jp225 | volatility_breakout | index | 30m | +24.3 | 16.5 | 333 | 1.12 | 1.8 | 0.20 | 0.64 | none | inf |
| ethusd | obv_break | crypto | 30m | +24.1 | 6.0 | 111 | 1.43 | 22.4 | 0.38 | 1.74 | +9.0 | +15.2 |
| nq | sar | future | 30m | +21.9 | 14.4 | 133 | 1.24 | 4.5 | 0.28 | 0.77 | none | inf |
| ethusd | aroon | crypto | 30m | +21.8 | 18.7 | 76 | 1.22 | 19.4 | 0.13 | 0.93 | -10.1 | +31.9 |
| tsla | floor_pivot | stock | 30m | +21.3 | 10.5 | 88 | 1.29 | 16.5 | 0.25 | 1.08 | +11.3 | +10.0 |
| tsla | volatility_breakout | stock | 30m | +21.3 | 10.4 | 131 | 1.19 | 10.5 | 0.29 | 0.88 | -8.8 | +30.1 |
| jp225 | swing_break | index | 30m | +20.4 | 20.8 | 124 | 1.26 | 8.1 | 0.25 | 0.73 | -2.8 | +23.2 |
| es | volatility_breakout | future | 30m | +19.9 | 12.1 | 188 | 1.21 | 3.6 | 0.27 | 0.77 | none | inf |
| nvda | structure | stock | 30m | +16.6 | 5.2 | 38 | 1.78 | 36.5 | 0.38 | 2.04 | +0.9 | +15.7 |
| xaugbp | stochastic | commodity | 30m | +16.2 | 4.7 | 61 | 1.81 | 9.0 | 0.45 | 0.75 | +7.7 | +8.6 |
| nq | xma_cross | future | 30m | +16.1 | 8.3 | 82 | 1.39 | 4.3 | 0.26 | 0.67 | +15.7 | +0.4 |
| avgo | day_of_week | stock | 30m | +15.6 | 4.8 | 38 | 1.60 | 111.5 | 0.30 | 0.68 | none | inf |
| jp225 | vol_regime | index | 30m | +15.2 | 5.5 | 177 | 1.30 | 5.5 | 0.46 | 1.31 | -4.7 | +19.9 |
| xalusd | macd_hist | commodity | 30m | +15.2 | 29.4 | 122 | 1.12 | 15.1 | 0.08 | 0.52 | -60.6 | +75.7 |
| usdjpy | cci | forex | 30m | +14.7 | 10.8 | 256 | 1.17 | 2.1 | 0.15 | 1.07 | +6.5 | +8.2 |
| xniusd | cci | commodity | 30m | +14.4 | 15.6 | 86 | 1.19 | 17.2 | 0.17 | 2.13 | -22.5 | +36.9 |
| uk100 | xma_cross | index | 30m | +14.3 | 4.4 | 59 | 1.56 | 7.5 | 0.38 | 1.55 | -4.5 | +18.8 |
| usdjpy | pullback | forex | 30m | +13.6 | 5.9 | 199 | 1.20 | 2.5 | 0.24 | 1.03 | +4.9 | +8.8 |
| jp225 | macd_hist | index | 30m | +13.2 | 11.4 | 221 | 1.16 | 4.4 | 0.24 | 0.26 | -2.4 | +15.6 |
| aapl | skew | stock | 30m | +12.6 | 9.5 | 58 | 1.21 | 17.4 | 0.19 | -0.04 | -29.2 | +41.8 |
| de40 | volatility_breakout | index | 30m | +11.9 | 9.9 | 159 | 1.12 | 2.7 | 0.17 | 0.42 | +0.8 | +11.2 |
| fr40 | swing_break | index | 30m | +11.4 | 15.3 | 133 | 1.15 | 5.0 | 0.17 | 1.18 | -12.2 | +23.7 |
| usdjpy | aroon | forex | 30m | +11.3 | 3.4 | 43 | 1.88 | 9.2 | 0.32 | 1.60 | none | inf |
| usdjpy | rvol | forex | 30m | +11.1 | 9.5 | 161 | 1.18 | 3.1 | 0.16 | 1.05 | +4.9 | +6.3 |
| es | fvg | future | 30m | +9.9 | 7.1 | 79 | 1.25 | 5.5 | 0.25 | 0.76 | -0.9 | +10.8 |
| usdjpy | xma_cross | forex | 30m | +9.7 | 7.1 | 197 | 1.16 | 2.2 | 0.19 | 0.91 | +5.2 | +4.5 |
| nq | supertrend | future | 30m | +9.3 | 10.0 | 96 | 1.16 | 1.1 | 0.13 | 0.13 | none | inf |
| nvda | skew | stock | 30m | +9.3 | 12.4 | 39 | 1.22 | 2.9 | 0.17 | -0.65 | none | inf |
| gbpusd | cci | forex | 30m | +9.0 | 6.2 | 80 | 1.29 | 3.6 | 0.24 | 1.11 | none | inf |
| gbpusd | obv_break | forex | 30m | +8.4 | 6.0 | 104 | 1.26 | 3.0 | 0.23 | 1.22 | none | inf |
| xaueur | stochastic | commodity | 30m | +8.4 | 11.6 | 63 | 1.30 | 4.7 | 0.24 | 0.44 | none | inf |
| msft | xma_cross | stock | 30m | +8.1 | 7.6 | 64 | 1.33 | 14.9 | 0.28 | 2.08 | -18.2 | +26.2 |
| de40 | rvol | index | 30m | +8.0 | 25.9 | 148 | 1.08 | 2.2 | 0.07 | 0.50 | none | inf |
| jp225 | pullback | index | 30m | +7.8 | 24.5 | 134 | 1.09 | 2.8 | 0.08 | -0.04 | -8.1 | +15.9 |
| es | day_of_week | future | 30m | +7.5 | 8.6 | 55 | 1.14 | 8.2 | 0.14 | 0.17 | -14.8 | +22.3 |
| eurjpy | volatility_breakout | forex | 30m | +7.1 | 8.5 | 382 | 1.07 | 1.5 | 0.23 | 0.97 | none | inf |
| tsla | sar | stock | 30m | +6.8 | 23.2 | 136 | 1.07 | 7.3 | 0.08 | 0.69 | none | inf |
| nq | pullback | future | 30m | +6.7 | 13.0 | 148 | 1.09 | 3.2 | 0.13 | -0.28 | -5.7 | +12.4 |
| amzn | xma_cross | stock | 30m | +6.7 | 11.6 | 46 | 1.22 | 2.5 | 0.09 | 0.21 | -10.2 | +16.8 |
| es | rvol | future | 30m | +6.5 | 9.1 | 46 | 1.28 | 3.8 | 0.14 | 0.68 | none | inf |
| jp225 | obv_break | index | 30m | +6.3 | 3.8 | 43 | 1.57 | 9.7 | 0.32 | 1.01 | -10.6 | +16.9 |
| gbpjpy | xma_cross | forex | 30m | +6.1 | 4.9 | 76 | 1.26 | 3.2 | 0.20 | 1.06 | -0.4 | +6.5 |
| hk50 | supertrend | index | 30m | +6.1 | 7.1 | 126 | 1.16 | 6.5 | 0.14 | 0.80 | none | inf |
| jp225 | xma_ribbon | index | 30m | +6.0 | 3.6 | 53 | 1.29 | 5.9 | 0.29 | 0.21 | -5.0 | +11.0 |
| btc | dmi | crypto | 30m | +5.2 | 7.8 | 63 | 1.19 | 13.8 | 0.17 | 1.02 | none | inf |
| aapl | rel_momentum | stock | 30m | +4.1 | 8.5 | 51 | 1.09 | 24.0 | 0.07 | 0.42 | none | inf |
| tsla | wick | stock | 30m | +4.0 | 10.0 | 45 | 1.13 | 14.0 | 0.10 | 0.77 | none | inf |
| jp225 | xma_cross | index | 30m | +3.8 | 7.3 | 48 | 1.15 | 0.9 | 0.11 | 0.07 | +2.3 | +1.6 |
| ukoil | volatility_breakout | commodity | 30m | +3.6 | 15.1 | 49 | 1.09 | 4.2 | 0.07 | 0.47 | none | inf |
| aapl | obv_divergence | stock | 30m | +3.2 | 11.7 | 49 | 1.07 | 10.7 | 0.05 | 1.00 | none | inf |
| eurjpy | xma_cross | forex | 30m | +3.1 | 4.5 | 78 | 1.17 | 2.8 | 0.13 | 0.87 | none | inf |
| hk50 | xma_cross | index | 30m | +3.0 | 11.0 | 58 | 1.09 | 4.5 | 0.07 | 0.70 | -3.6 | +6.6 |
| nvda | day_of_week | stock | 30m | +2.8 | 18.0 | 42 | 1.06 | 32.5 | 0.03 | -0.73 | -15.5 | +18.2 |
| tsm | xma_cross | stock | 30m | +2.7 | 4.2 | 52 | 1.19 | 10.1 | 0.12 | 0.72 | -5.9 | +8.7 |
| tsla | structure | stock | 30m | +2.5 | 9.3 | 64 | 1.08 | 0.1 | 0.07 | 0.01 | -0.5 | +3.1 |
| aapl | supertrend | stock | 30m | +2.5 | 10.8 | 48 | 1.07 | -1.3 | 0.06 | -0.20 | none | inf |
| amzn | linreg_trend | stock | 30m | +2.0 | 17.3 | 44 | 1.06 | 8.0 | 0.04 | 0.70 | none | inf |
| es | structure | future | 30m | +2.0 | 6.4 | 53 | 1.10 | 5.6 | 0.08 | 1.27 | -2.2 | +4.2 |
| nq | xma_ribbon | future | 30m | +1.4 | 9.8 | 50 | 1.05 | 2.3 | 0.04 | -0.14 | -4.6 | +6.0 |
| usdjpy | obv_divergence | forex | 30m | +1.0 | 5.0 | 101 | 1.08 | 1.0 | 0.05 | 0.51 | -2.6 | +3.7 |

### original (47)

| symbol | family | class | tf | OOS % | dd % | n | PF | BE bp | mSh | t | null | margin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| xaugbp | turn_of_month | commodity | 30m | +72.6 | 12.9 | 40 | 2.65 | 56.1 | 0.25 | 0.48 | none | inf |
| nq | swing_ma | future | 30m | +56.9 | 11.2 | 38 | 2.01 | 38.5 | 0.43 | 1.38 | -6.6 | +63.5 |
| ethusd | swing_ma | crypto | 30m | +44.0 | 23.4 | 64 | 1.44 | 89.0 | 0.23 | 0.98 | +41.1 | +2.8 |
| de40 | ib | index | 30m | +33.4 | 12.8 | 243 | 1.21 | 4.4 | 0.41 | 1.05 | none | inf |
| de40 | consecutive | index | 30m | +33.0 | 16.5 | 331 | 1.13 | 3.1 | 0.20 | 0.16 | none | inf |
| de40 | donchian | index | 30m | +32.5 | 11.9 | 132 | 1.34 | 6.6 | 0.31 | 1.13 | +27.4 | +5.1 |
| nq | volume_thrust | future | 30m | +32.2 | 9.1 | 76 | 1.66 | 12.9 | 0.38 | 1.89 | none | inf |
| usdjpy | volume_thrust | forex | 30m | +31.2 | 3.9 | 198 | 1.58 | 5.2 | 0.45 | 2.08 | +29.2 | +2.0 |
| tsla | failed_break | stock | 30m | +26.4 | 17.8 | 180 | 1.17 | 12.5 | 0.20 | 1.10 | +8.3 | +18.1 |
| jp225 | pdr | index | 30m | +26.0 | 22.9 | 125 | 1.33 | 11.0 | 0.28 | 1.19 | none | inf |
| btc | vwap | crypto | 30m | +23.6 | 11.3 | 371 | 1.14 | 4.3 | 0.27 | 0.83 | +8.1 | +15.5 |
| usdjpy | range_expansion | forex | 30m | +22.8 | 10.1 | 196 | 1.37 | 3.7 | 0.27 | 1.59 | none | inf |
| btc | orb | crypto | 30m | +22.0 | 16.7 | 361 | 1.15 | 5.9 | 0.23 | 1.19 | +16.5 | +5.5 |
| ethusd | failed_break | crypto | 30m | +16.6 | 7.0 | 70 | 1.44 | 32.8 | 0.32 | 1.65 | -13.2 | +29.8 |
| jp225 | swing_donchian | index | 30m | +16.3 | 9.4 | 53 | 1.38 | 8.6 | 0.32 | 0.04 | +0.2 | +16.1 |
| xznusd | ib | commodity | 30m | +15.8 | 11.9 | 106 | 1.19 | 18.9 | 0.17 | 0.91 | -10.2 | +26.0 |
| btc | overnight | crypto | 30m | +15.6 | 9.0 | 176 | 1.21 | 7.9 | 0.21 | 1.00 | +6.5 | +9.0 |
| ethusd | range_expansion | crypto | 30m | +15.2 | 10.9 | 99 | 1.27 | 22.3 | 0.23 | 1.40 | +8.0 | +7.3 |
| ethusd | ib | crypto | 30m | +14.6 | 15.2 | 166 | 1.15 | 13.4 | 0.15 | 1.15 | +10.1 | +4.5 |
| tsla | vwap | stock | 30m | +14.6 | 8.3 | 141 | 1.26 | 11.6 | 0.28 | 0.95 | +3.3 | +11.3 |
| xaueur | key_reversal | commodity | 30m | +14.6 | 3.6 | 45 | 2.01 | 10.4 | 0.54 | 1.29 | +12.4 | +2.2 |
| fr40 | swing_ma | index | 30m | +14.3 | 14.7 | 75 | 1.25 | 5.3 | 0.19 | 0.74 | none | inf |
| audusd | zscore | forex | 30m | +13.3 | 3.2 | 52 | 1.88 | 9.7 | 0.41 | 2.05 | none | inf |
| btc | momentum | crypto | 30m | +13.3 | 12.3 | 215 | 1.18 | 6.4 | 0.17 | 1.31 | +4.0 | +9.3 |
| xauaud | momentum | commodity | 30m | +12.8 | 5.4 | 43 | 1.85 | 10.4 | 0.36 | 0.97 | none | inf |
| eurjpy | swing_ma | forex | 30m | +12.8 | 6.3 | 61 | 1.32 | 8.7 | 0.23 | 0.56 | -2.6 | +15.3 |
| usdjpy | overnight | forex | 30m | +11.8 | 8.4 | 262 | 1.14 | 1.7 | 0.23 | 1.03 | -0.1 | +11.9 |
| xalusd | keltner | commodity | 30m | +11.4 | 25.6 | 119 | 1.11 | 10.0 | 0.08 | 0.10 | -21.2 | +32.6 |
| jp225 | climax | index | 30m | +10.7 | 12.2 | 122 | 1.17 | 5.3 | 0.17 | 1.16 | -0.2 | +10.9 |
| fr40 | squeeze | index | 30m | +9.0 | 3.9 | 60 | 1.32 | 3.8 | 0.20 | 0.68 | none | inf |
| jp225 | volume_thrust | index | 30m | +8.9 | 9.7 | 109 | 1.15 | 4.1 | 0.19 | 1.04 | none | inf |
| tsla | high_52w | stock | 30m | +7.9 | 5.5 | 35 | 1.29 | 86.0 | 0.20 | 0.78 | -4.1 | +12.0 |
| uk100 | range_expansion | index | 30m | +7.5 | 6.6 | 127 | 1.20 | 3.8 | 0.19 | 1.19 | none | inf |
| ethusd | key_reversal | crypto | 30m | +6.0 | 14.0 | 80 | 1.10 | 10.1 | 0.08 | 1.03 | none | inf |
| stoxx50 | climax | index | 30m | +5.7 | 5.2 | 81 | 1.20 | 4.6 | 0.16 | 0.56 | +0.2 | +5.5 |
| tsla | range_expansion | stock | 30m | +5.7 | 2.4 | 109 | 1.35 | 14.3 | 0.36 | 1.25 | +3.6 | +2.0 |
| usdjpy | pdr | forex | 30m | +5.6 | 14.7 | 251 | 1.06 | 1.4 | 0.08 | 0.74 | none | inf |
| usdjpy | ma_cross | forex | 30m | +5.6 | 6.8 | 76 | 1.23 | 3.0 | 0.13 | 0.80 | none | inf |
| fr40 | volume_thrust | index | 30m | +5.1 | 10.8 | 34 | 1.28 | 4.8 | 0.11 | 0.49 | +0.4 | +4.8 |
| xagaud | failed_break | commodity | 30m | +5.1 | 7.4 | 45 | 1.16 | 6.6 | 0.08 | -0.03 | +3.6 | +1.6 |
| eurjpy | vwap | forex | 30m | +5.1 | 12.5 | 172 | 1.12 | 1.8 | 0.12 | 0.85 | none | inf |
| usdjpy | key_reversal | forex | 30m | +4.7 | 6.6 | 213 | 1.07 | 1.1 | 0.08 | 0.60 | none | inf |
| tsla | ib | stock | 30m | +4.3 | 4.2 | 109 | 1.25 | 11.7 | 0.25 | 0.86 | +2.4 | +1.9 |
| xaueur | orb | commodity | 30m | +3.2 | 10.5 | 41 | 1.16 | 3.5 | 0.16 | 0.01 | none | inf |
| tsla | inside | stock | 30m | +3.0 | 7.7 | 77 | 1.07 | 6.5 | 0.07 | 0.59 | -0.6 | +3.6 |
| usdcad | squeeze | forex | 30m | +1.6 | 4.5 | 39 | 1.19 | 2.4 | 0.12 | 0.88 | none | inf |
| gbpusd | climax | forex | 30m | +1.1 | 4.9 | 54 | 1.07 | 0.7 | 0.04 | 0.28 | -0.6 | +1.7 |

## The 13 that clear both quality bars

t >= 2 on per-trade edge over drift **and** a breakeven spread of at least
5 bp. Everything else in the tables above is either statistically thin or
priced too close to its own cost to read.

| symbol | family | wave | tf | OOS % | n | PF | BE bp | mSh | t | margin |
|---|---|---|---|---|---|---|---|---|---|---|
| ethusd | pullback | new | 30m | +48.5 | 200 | 1.62 | 39.5 | 0.54 | 2.72 | inf |
| ethusd | confluence | combined | 30m | +78.6 | 77 | 2.05 | 40.3 | 0.62 | 2.45 | +12.7 |
| gbpjpy | trap | combined | 30m | +32.8 | 103 | 2.45 | 9.0 | 0.68 | 2.44 | inf |
| ethusd | level_confluence | combined | 30m | +58.8 | 160 | 1.59 | 29.6 | 0.52 | 2.33 | +39.0 |
| ethusd | floor_pivot | new | 30m | +51.6 | 440 | 1.27 | 23.0 | 0.49 | 2.25 | +8.6 |
| ethusd | macd_hist | new | 30m | +60.6 | 232 | 1.36 | 24.8 | 0.41 | 2.21 | +50.3 |
| xniusd | cci | new | 30m | +14.4 | 86 | 1.19 | 17.2 | 0.17 | 2.13 | +36.9 |
| ethusd | idio_break | combined | 30m | +27.2 | 131 | 1.59 | 35.7 | 0.40 | 2.12 | +14.5 |
| ethusd | sar | new | 30m | +36.5 | 221 | 1.27 | 28.6 | 0.27 | 2.11 | +29.1 |
| msft | xma_cross | new | 30m | +8.1 | 64 | 1.33 | 14.9 | 0.28 | 2.08 | +26.2 |
| usdjpy | volume_thrust | original | 30m | +31.2 | 198 | 1.58 | 5.2 | 0.45 | 2.08 | +2.0 |
| audusd | zscore | original | 30m | +13.3 | 52 | 1.88 | 9.7 | 0.41 | 2.05 | inf |
| nvda | structure | new | 30m | +16.6 | 38 | 1.78 | 36.5 | 0.38 | 2.04 | +15.7 |

## Concentration

| symbol | survivors | | family | survivors |
|---|---|---|---|---|
| ethusd | 26 | | xma_cross | 11 |
| btc | 15 | | volatility_breakout | 9 |
| usdjpy | 15 | | gated_orb | 5 |
| jp225 | 15 | | level_confluence | 5 |
| tsla | 11 | | day_of_week | 5 |
| nq | 10 | | gated_fade | 4 |
| de40 | 8 | | break_retest | 4 |
| es | 6 | | cci | 4 |
| eurjpy | 6 | | pullback | 4 |
| xalusd | 4 | | supertrend | 4 |
| xaueur | 4 | | rvol | 4 |
| aapl | 4 | | sar | 4 |
| fr40 | 4 | | swing_ma | 4 |
| ukoil | 3 | | ib | 4 |
| uk100 | 3 | | volume_thrust | 4 |
| hk50 | 3 | | range_expansion | 4 |
| nvda | 3 | | gated_swing | 3 |
| gbpusd | 3 | | gated_donchian | 3 |
| gbpjpy | 2 | | macd_hist | 3 |
| audusd | 2 | | floor_pivot | 3 |
| usdcad | 2 | | swing_break | 3 |
| tsm | 2 | | obv_divergence | 3 |
| xaugbp | 2 | | skew | 3 |
| amzn | 2 | | xma_ribbon | 3 |
| audjpy | 1 | | obv_break | 3 |
| googl | 1 | | structure | 3 |
| avgo | 1 | | failed_break | 3 |
| xniusd | 1 | | vwap | 3 |
| msft | 1 | | key_reversal | 3 |
| xznusd | 1 | | climax | 3 |
| xauaud | 1 | | confluence | 2 |
| stoxx50 | 1 | | trap | 2 |
| xagaud | 1 | | two_stage | 2 |
|  |  | | vol_regime | 2 |
|  |  | | linreg_trend | 2 |
|  |  | | aroon | 2 |
|  |  | | stochastic | 2 |
|  |  | | pdr | 2 |
|  |  | | orb | 2 |
|  |  | | overnight | 2 |
|  |  | | momentum | 2 |
|  |  | | squeeze | 2 |
|  |  | | nested | 1 |
|  |  | | regime_router | 1 |
|  |  | | idio_break | 1 |
|  |  | | efficiency | 1 |
|  |  | | mfi | 1 |
|  |  | | xma_slope | 1 |
|  |  | | fvg | 1 |
|  |  | | dmi | 1 |
|  |  | | rel_momentum | 1 |
|  |  | | wick | 1 |
|  |  | | turn_of_month | 1 |
|  |  | | consecutive | 1 |
|  |  | | donchian | 1 |
|  |  | | swing_donchian | 1 |
|  |  | | zscore | 1 |
|  |  | | keltner | 1 |
|  |  | | high_52w | 1 |
|  |  | | ma_cross | 1 |
|  |  | | inside | 1 |

## Sessions

Not a footnote. Every intraday family is filtered to a session window, and
the window is per-symbol rather than exchange RTH. Two consequences worth
carrying into any decision made from this folder:

* **`nq` and `es` are unpinned**, and the volume-profile derivation stops
  before the closing half-hour. On `es` that excluded bucket is the single
  busiest of the day (11.65% of volume). Their survivors are midday-only.
* **The crypto symbols run a US-equity window on a 24/7 instrument**
  (09:30-16:00 NY), so ~73% of the tape is discarded by construction.

| symbol | window (NY) | hours | source |
|---|---|---|---|
| aapl | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| amzn | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| audjpy | 00:00-14:00 | 14.0 | derived from the 2024 volume profile |
| audusd | 01:00-14:00 | 13.0 | derived from the 2024 volume profile |
| avgo | 09:30-14:00 | 4.5 | derived from the 2024 volume profile |
| btc | 09:30-16:00 | 6.5 | pinned in SESSION |
| de40 | 03:00-11:30 | 8.5 | pinned in SESSION |
| es | 09:00-15:00 | 6.0 | derived from the 2024 volume profile |
| ethusd | 09:30-16:00 | 6.5 | pinned in SESSION |
| eurjpy | 01:00-14:30 | 13.5 | derived from the 2024 volume profile |
| fr40 | 03:00-11:30 | 8.5 | pinned in SESSION |
| gbpjpy | 00:00-13:30 | 13.5 | derived from the 2024 volume profile |
| gbpusd | 00:30-14:00 | 13.5 | derived from the 2024 volume profile |
| googl | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| hk50 | 03:00-10:00 | 7.0 | pinned in SESSION (+6h day shift) |
| jp225 | 01:00-08:00 | 7.0 | pinned in SESSION (+6h day shift) |
| msft | 09:30-14:00 | 4.5 | derived from the 2024 volume profile |
| nq | 09:30-15:00 | 5.5 | derived from the 2024 volume profile |
| nvda | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| stoxx50 | 03:00-11:30 | 8.5 | pinned in SESSION |
| tsla | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| tsm | 09:30-13:30 | 4.0 | derived from the 2024 volume profile |
| uk100 | 03:00-11:30 | 8.5 | pinned in SESSION |
| ukoil | 09:00-14:30 | 5.5 | pinned in SESSION |
| usdcad | 02:30-14:30 | 12.0 | derived from the 2024 volume profile |
| usdjpy | 00:00-13:00 | 13.0 | derived from the 2024 volume profile |
| xagaud | 08:00-16:00 | 8.0 | pinned in SESSION |
| xalusd | 03:00-14:00 | 11.0 | pinned in SESSION |
| xauaud | 08:00-16:00 | 8.0 | pinned in SESSION |
| xaueur | 08:00-16:00 | 8.0 | pinned in SESSION |
| xaugbp | 08:00-16:00 | 8.0 | pinned in SESSION |
| xniusd | 03:00-14:00 | 11.0 | pinned in SESSION |
| xznusd | 03:00-14:00 | 11.0 | pinned in SESSION |

## What has NOT been tested

* **Buy and hold.** The coin-flip null is one control. An earlier study in
  this codebase had three candidates beat their null and still lose badly to
  simply holding the instrument. No buy-and-hold comparison exists here.
* **A second untouched holdout.** These beat one holdout once. Candidates
  here have repeatedly passed one window and died on the next.
* **A second session slice**, which is what killed the last ETH candidate
  that survived every other control.
* **Any multiple-testing correction** across the 222 null-tested hypotheses.
* **Correlation between survivors.** 164 rows is not 164 independent
  strategies; many will be the same trade wearing different family names.

## Excluded, and why

| reason | count |
|---|---|
| lost to their own coin-flip null | 58 |
| cleared the holdout but no null was ever run | 37 |
| in-sample winner, never validated on the holdout | 110 |

The 37 with no null are the notable gap -- they include `ethusd/zscore` at
+119.0% and `ethusd/consecutive` at +86.3% on the holdout. They are not
failures; they are **untested**, and on this data an untested holdout winner
is worth nothing until `why` has priced it. Running the null on those 37 is
the cheapest outstanding work in the study.

## Files

One JSON per survivor, `<symbol>_<family>_<tf>.json`, each carrying the
winning params, full in-sample and holdout statistics, the 7-point cost
sweep, the trading session and how it was chosen, every null seed it beat,
and the sha256 of the sealed file it came from. `SURVIVORS.json` is the
machine-readable index. Everything that died stays in the sealed per-symbol
files under `results/`.
