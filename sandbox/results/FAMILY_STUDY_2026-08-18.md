# Exness family study -- the 33 new families, 30m, 44 symbols

Complete run, 2026-08-18: search, holdout, and coin-flip null.
$1,000 initial balance, Exness Pro 416209807, spread-only cost, 1.5%
volatility-throttled stop risk, 4x notional ceiling. In-sample is each
symbol's first full year through 2024; the holdout is 2025-01-01 to
2026-08-16 and was untouched until `validate` ran.

## The funnel

| stage | count | what it means |
|---|---|---|
| cells scored | 1,964,988 | the search budget |
| selected | 329 | best cell per family per symbol, in-sample |
| validated | 329 | scored on the untouched holdout |
| positive out of sample | 142 | made money in the holdout |
| not beaten by any flip seed | 103 | survived the null |
| **tested against all 3 seeds** | **21** | **the only complete tests** |
| of those, also better return/drawdown | 19 | risk-adjusted too |

READ THE `seeds` COLUMN BEFORE THE RETURN COLUMN. `why` re-runs each
family's whole grid with entry directions replaced by coin flips, three
times. `seeds` is how many of the three found ANY cell clearing the
in-sample gates. A row at 0 seeds was never tested -- the control could not
run, because on that instrument random entries cannot clear a 1.05 profit
factor against the spread. That is a fact about the gates and the
instrument, not evidence for the strategy.

## Fully tested: beat all three coin-flip seeds

| # | symbol | family | group | IS % | OOS % | dd | PF | n | flip % | flip dd | ret/dd wins |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ethusd | cci | oscillator | +573.4 | +71.3 | 11.3 | 1.31 | 312 | +68.4 | 9.9 | NO |
| 2 | ethusd | efficiency | regime | +446.6 | +53.4 | 15.4 | 1.29 | 267 | +48.2 | 11.1 | NO |
| 3 | ethusd | floor_pivot | geometry | +399.1 | +51.6 | 9.2 | 1.27 | 440 | +43.1 | 10.9 | yes |
| 4 | ethusd | rvol | flow | +549.2 | +42.8 | 11.1 | 1.28 | 245 | +12.1 | 9.1 | yes |
| 5 | ethusd | volatility_breakout | geometry | +557.0 | +31.9 | 20.8 | 1.34 | 103 | +11.1 | 19.2 | yes |
| 6 | btc | volatility_breakout | geometry | +611.9 | +28.6 | 7.8 | 1.38 | 190 | +17.6 | 9.9 | yes |
| 7 | tsla | floor_pivot | geometry | +137.7 | +21.3 | 10.5 | 1.29 | 88 | +11.3 | 13.2 | yes |
| 8 | nvda | structure | geometry | +70.9 | +16.6 | 5.2 | 1.78 | 38 | +0.9 | 8.2 | yes |
| 9 | xniusd | cci | oscillator | +34.2 | +14.4 | 15.6 | 1.19 | 86 | -22.5 | 33.0 | yes |
| 10 | de40 | volatility_breakout | geometry | +152.2 | +11.9 | 9.9 | 1.12 | 159 | +0.8 | 11.0 | yes |
| 11 | usdjpy | rvol | flow | +95.3 | +11.1 | 9.5 | 1.18 | 161 | +4.9 | 8.1 | yes |
| 12 | usdjpy | xma_cross | xma | +96.2 | +9.7 | 7.1 | 1.16 | 197 | +5.2 | 7.7 | yes |
| 13 | amzn | xma_cross | xma | +62.0 | +6.7 | 11.6 | 1.22 | 46 | -10.2 | 20.4 | yes |
| 14 | xaueur | xma_cross | xma | +27.3 | +6.7 | 0.8 | 2.78 | 15 | +3.5 | 7.6 | yes |
| 15 | gbpjpy | xma_cross | xma | +36.4 | +6.1 | 4.9 | 1.26 | 76 | -0.4 | 7.4 | yes |
| 16 | nq | floor_pivot | geometry | +106.7 | +3.6 | 22.0 | 1.04 | 147 | -0.9 | 12.0 | yes |
| 17 | hk50 | xma_cross | xma | +55.6 | +3.0 | 11.0 | 1.09 | 58 | -3.6 | 15.0 | yes |
| 18 | nvda | day_of_week | calendar | +371.8 | +2.8 | 18.0 | 1.06 | 42 | -15.5 | 25.7 | yes |
| 19 | tsla | structure | geometry | +48.2 | +2.5 | 9.3 | 1.08 | 64 | -0.5 | 18.3 | yes |
| 20 | es | structure | geometry | +31.7 | +2.0 | 6.4 | 1.10 | 53 | -2.2 | 8.1 | yes |
| 21 | usdjpy | volatility_breakout | geometry | +347.7 | +1.7 | 18.9 | 1.02 | 262 | -7.5 | 17.3 | yes |

## Everything that passed, by how complete the test was

### 3 seeds -- complete test (21)

| symbol | family | IS % | OOS % | dd | PF | n | flip % |
|---|---|---|---|---|---|---|---|
| ethusd | cci | +573.4 | +71.3 | 11.3 | 1.31 | 312 | +68.4 |
| ethusd | efficiency | +446.6 | +53.4 | 15.4 | 1.29 | 267 | +48.2 |
| ethusd | floor_pivot | +399.1 | +51.6 | 9.2 | 1.27 | 440 | +43.1 |
| ethusd | rvol | +549.2 | +42.8 | 11.1 | 1.28 | 245 | +12.1 |
| ethusd | volatility_breakout | +557.0 | +31.9 | 20.8 | 1.34 | 103 | +11.1 |
| btc | volatility_breakout | +611.9 | +28.6 | 7.8 | 1.38 | 190 | +17.6 |
| tsla | floor_pivot | +137.7 | +21.3 | 10.5 | 1.29 | 88 | +11.3 |
| nvda | structure | +70.9 | +16.6 | 5.2 | 1.78 | 38 | +0.9 |
| xniusd | cci | +34.2 | +14.4 | 15.6 | 1.19 | 86 | -22.5 |
| de40 | volatility_breakout | +152.2 | +11.9 | 9.9 | 1.12 | 159 | +0.8 |
| usdjpy | rvol | +95.3 | +11.1 | 9.5 | 1.18 | 161 | +4.9 |
| usdjpy | xma_cross | +96.2 | +9.7 | 7.1 | 1.16 | 197 | +5.2 |
| amzn | xma_cross | +62.0 | +6.7 | 11.6 | 1.22 | 46 | -10.2 |
| xaueur | xma_cross | +27.3 | +6.7 | 0.8 | 2.78 | 15 | +3.5 |
| gbpjpy | xma_cross | +36.4 | +6.1 | 4.9 | 1.26 | 76 | -0.4 |
| nq | floor_pivot | +106.7 | +3.6 | 22.0 | 1.04 | 147 | -0.9 |
| hk50 | xma_cross | +55.6 | +3.0 | 11.0 | 1.09 | 58 | -3.6 |
| nvda | day_of_week | +371.8 | +2.8 | 18.0 | 1.06 | 42 | -15.5 |
| tsla | structure | +48.2 | +2.5 | 9.3 | 1.08 | 64 | -0.5 |
| es | structure | +31.7 | +2.0 | 6.4 | 1.10 | 53 | -2.2 |
| usdjpy | volatility_breakout | +347.7 | +1.7 | 18.9 | 1.02 | 262 | -7.5 |

### 2 seeds (17)

| symbol | family | IS % | OOS % | dd | PF | n | flip % |
|---|---|---|---|---|---|---|---|
| de40 | floor_pivot | +436.5 | +60.3 | 18.1 | 1.21 | 313 | +32.0 |
| btc | supertrend | +570.4 | +44.0 | 11.7 | 1.44 | 120 | +7.4 |
| nq | volatility_breakout | +421.8 | +37.9 | 11.1 | 1.29 | 201 | -13.2 |
| btc | vol_regime | +847.0 | +34.0 | 11.2 | 1.24 | 303 | +32.4 |
| ethusd | xma_slope | +187.6 | +26.9 | 7.9 | 1.36 | 117 | +23.7 |
| ethusd | linreg_trend | +151.1 | +25.4 | 7.9 | 1.32 | 202 | +17.1 |
| xaugbp | stochastic | +29.5 | +16.2 | 4.7 | 1.81 | 61 | +7.7 |
| nq | xma_cross | +93.5 | +16.1 | 8.3 | 1.39 | 82 | +15.7 |
| usdjpy | cci | +157.9 | +14.7 | 10.8 | 1.17 | 256 | +6.5 |
| uk100 | xma_cross | +47.3 | +14.3 | 4.4 | 1.56 | 59 | -4.5 |
| usdjpy | pullback | +40.9 | +13.6 | 5.9 | 1.20 | 199 | +4.9 |
| jp225 | macd_hist | +91.6 | +13.2 | 11.4 | 1.16 | 221 | -2.4 |
| es | day_of_week | +181.5 | +7.5 | 8.6 | 1.14 | 55 | -14.8 |
| xauaud | xma_ribbon | +14.9 | +5.6 | 1.1 | 3.45 | 13 | -1.7 |
| jp225 | xma_cross | +67.6 | +3.8 | 7.3 | 1.15 | 48 | +2.3 |
| nq | xma_ribbon | +61.2 | +1.4 | 9.8 | 1.05 | 50 | -4.6 |
| xniusd | volatility_breakout | +72.3 | +0.8 | 37.1 | 1.01 | 101 | -15.7 |

### 1 seed (30)

| symbol | family | IS % | OOS % | dd | PF | n | flip % |
|---|---|---|---|---|---|---|---|
| ethusd | macd_hist | +628.6 | +60.6 | 12.6 | 1.36 | 232 | +10.3 |
| ethusd | day_of_week | +312.0 | +47.7 | 8.2 | 1.91 | 40 | +7.3 |
| ethusd | sar | +235.8 | +36.5 | 13.0 | 1.27 | 221 | +7.4 |
| jp225 | obv_divergence | +135.2 | +35.2 | 10.0 | 1.71 | 81 | +1.4 |
| usdjpy | sar | +68.8 | +31.5 | 11.3 | 1.30 | 283 | -22.2 |
| ukoil | xma_cross | +186.0 | +31.5 | 11.0 | 1.40 | 101 | -15.2 |
| btc | mfi | +430.7 | +31.4 | 8.8 | 1.32 | 230 | +5.0 |
| btc | xma_ribbon | +461.6 | +29.8 | 11.8 | 1.58 | 73 | +20.5 |
| ethusd | obv_break | +209.8 | +24.1 | 6.0 | 1.43 | 111 | +9.0 |
| ethusd | aroon | +259.2 | +21.8 | 18.7 | 1.22 | 76 | -10.1 |
| tsla | volatility_breakout | +292.9 | +21.3 | 10.4 | 1.19 | 131 | -8.8 |
| jp225 | swing_break | +251.1 | +20.4 | 20.8 | 1.26 | 124 | -2.8 |
| jp225 | vol_regime | +46.4 | +15.2 | 5.5 | 1.30 | 177 | -4.7 |
| xalusd | macd_hist | +30.6 | +15.2 | 29.4 | 1.12 | 122 | -60.6 |
| aapl | skew | +46.6 | +12.6 | 9.5 | 1.21 | 58 | -29.2 |
| fr40 | swing_break | +149.2 | +11.4 | 15.3 | 1.15 | 133 | -12.2 |
| es | fvg | +77.8 | +9.9 | 7.1 | 1.25 | 79 | -0.9 |
| msft | xma_cross | +16.5 | +8.1 | 7.6 | 1.33 | 64 | -18.2 |
| jp225 | pullback | +131.1 | +7.8 | 24.5 | 1.09 | 134 | -8.1 |
| nq | pullback | +116.8 | +6.7 | 13.0 | 1.09 | 148 | -5.7 |
| jp225 | obv_break | +18.8 | +6.3 | 3.8 | 1.57 | 43 | -10.6 |
| jp225 | xma_ribbon | +90.3 | +6.0 | 3.6 | 1.29 | 53 | -5.0 |
| nq | vol_regime | +200.1 | +4.7 | 13.7 | 1.05 | 157 | -5.8 |
| tsla | vol_regime | +50.2 | +2.8 | 11.9 | 1.04 | 140 | -8.8 |
| tsm | xma_cross | +5.2 | +2.7 | 4.2 | 1.19 | 52 | -5.9 |
| aapl | volatility_breakout | +67.7 | +2.1 | 27.3 | 1.03 | 70 | -1.6 |
| aapl | day_of_week | +74.4 | +1.6 | 9.9 | 1.04 | 44 | -4.5 |
| usdjpy | obv_divergence | +6.1 | +1.0 | 5.0 | 1.08 | 101 | -2.6 |
| avgo | supertrend | +15.7 | +0.2 | 2.4 | 1.02 | 39 | -0.1 |
| nq | linreg_trend | +88.4 | +0.2 | 10.7 | 1.00 | 111 | -3.6 |

### 0 seeds -- NO CONTROL RAN, not evidence (35)

| symbol | family | IS % | OOS % | dd | PF | n | flip % |
|---|---|---|---|---|---|---|---|
| ethusd | swing_break | +454.9 | +49.8 | 9.4 | 1.34 | 184 | none |
| googl | day_of_week | +38.6 | +48.7 | 7.5 | 2.03 | 52 | none |
| ethusd | pullback | +248.5 | +48.5 | 10.8 | 1.62 | 200 | none |
| tsm | skew | +216.8 | +29.9 | 13.9 | 1.58 | 64 | none |
| jp225 | volatility_breakout | +192.3 | +24.3 | 16.5 | 1.12 | 333 | none |
| nq | sar | +227.9 | +21.9 | 14.4 | 1.24 | 133 | none |
| es | volatility_breakout | +204.6 | +19.9 | 12.1 | 1.21 | 188 | none |
| avgo | day_of_week | +208.5 | +15.6 | 4.8 | 1.60 | 38 | none |
| usdjpy | aroon | +40.2 | +11.3 | 3.4 | 1.88 | 43 | none |
| nq | supertrend | +210.6 | +9.3 | 10.0 | 1.16 | 96 | none |
| nvda | skew | +134.3 | +9.3 | 12.4 | 1.22 | 39 | none |
| gbpusd | cci | +16.9 | +9.0 | 6.2 | 1.29 | 80 | none |
| gbpusd | obv_break | +58.9 | +8.4 | 6.0 | 1.26 | 104 | none |
| xaueur | stochastic | +70.3 | +8.4 | 11.6 | 1.30 | 63 | none |
| de40 | rvol | +120.8 | +8.0 | 25.9 | 1.08 | 148 | none |
| eurjpy | volatility_breakout | +88.1 | +7.1 | 8.5 | 1.07 | 382 | none |
| tsla | sar | +110.4 | +6.8 | 23.2 | 1.07 | 136 | none |
| es | rvol | +65.4 | +6.5 | 9.1 | 1.28 | 46 | none |
| hk50 | supertrend | +53.4 | +6.1 | 7.1 | 1.16 | 126 | none |
| btc | dmi | +61.5 | +5.2 | 7.8 | 1.19 | 63 | none |
| aapl | rel_momentum | +70.2 | +4.1 | 8.5 | 1.09 | 51 | none |
| tsla | wick | +30.5 | +4.0 | 10.0 | 1.13 | 45 | none |
| xauaud | obv_divergence | +8.2 | +3.7 | 3.3 | 1.33 | 26 | none |
| ukoil | volatility_breakout | +77.2 | +3.6 | 15.1 | 1.09 | 49 | none |
| aapl | obv_divergence | +21.8 | +3.2 | 11.7 | 1.07 | 49 | none |
| eurjpy | xma_cross | +24.4 | +3.1 | 4.5 | 1.17 | 78 | none |
| aapl | supertrend | +26.7 | +2.5 | 10.8 | 1.07 | 48 | none |
| hk50 | floor_pivot | +42.5 | +2.4 | 13.1 | 1.02 | 306 | none |
| amzn | linreg_trend | +55.8 | +2.0 | 17.3 | 1.06 | 44 | none |
| audjpy | rvol | +115.4 | +1.7 | 7.3 | 1.04 | 125 | none |
| gbpjpy | floor_pivot | +73.2 | +1.2 | 8.1 | 1.02 | 196 | none |
| msft | rvol | +31.8 | +0.7 | 9.6 | 1.03 | 74 | none |
| amzn | efficiency | +21.2 | +0.6 | 18.0 | 1.01 | 116 | none |
| tsm | xma_ribbon | +33.6 | +0.2 | 11.1 | 1.00 | 58 | none |
| msft | day_of_week | +67.3 | +0.1 | 18.1 | 1.00 | 36 | none |

## Beaten by their own coin flips

| symbol | family | OOS % | flip % | seeds |
|---|---|---|---|---|
| ethusd | structure | +44.1 | +51.5 | 2 |
| ethusd | mfi | +40.5 | +60.6 | 1 |
| btc | floor_pivot | +34.1 | +38.3 | 3 |
| de40 | pullback | +33.5 | +66.6 | 3 |
| ethusd | vol_regime | +30.2 | +34.0 | 3 |
| btc | cci | +29.3 | +33.5 | 3 |
| btc | pullback | +25.8 | +35.2 | 3 |
| ethusd | xma_ribbon | +23.0 | +27.1 | 2 |
| btc | efficiency | +17.8 | +32.4 | 2 |
| ethusd | rel_zscore | +17.0 | +43.3 | 3 |
| btc | swing_break | +15.2 | +26.0 | 2 |
| xaugbp | macd_hist | +14.7 | +17.8 | 2 |
| xalusd | stochastic | +12.9 | +13.6 | 1 |
| de40 | cci | +12.6 | +28.9 | 3 |
| de40 | obv_divergence | +11.7 | +16.5 | 1 |
| de40 | obv_break | +10.4 | +14.5 | 1 |
| btc | wick | +9.9 | +14.8 | 2 |
| eurjpy | xma_ribbon | +8.6 | +10.2 | 2 |
| usdjpy | macd_hist | +8.0 | +15.7 | 1 |
| es | floor_pivot | +7.9 | +19.9 | 1 |
| usdjpy | linreg_trend | +7.2 | +12.0 | 1 |
| jp225 | cci | +7.0 | +9.1 | 3 |
| jp225 | floor_pivot | +6.9 | +7.7 | 1 |
| audusd | xma_cross | +5.9 | +14.4 | 2 |
| audjpy | volatility_breakout | +5.5 | +8.6 | 1 |
| jp225 | linreg_trend | +5.3 | +8.9 | 2 |
| xaueur | xma_ribbon | +5.0 | +14.4 | 1 |
| ethusd | wick | +4.5 | +16.6 | 2 |
| xniusd | xma_cross | +4.1 | +4.4 | 3 |
| btc | linreg_trend | +2.9 | +7.1 | 3 |
| xaueur | obv_break | +2.9 | +9.4 | 2 |
| es | xma_cross | +2.0 | +6.4 | 3 |
| xaugbp | linreg_trend | +1.3 | +16.3 | 2 |
| de40 | structure | +1.2 | +33.6 | 2 |
| tsla | skew | +1.1 | +13.6 | 3 |
| xaueur | volatility_breakout | +1.1 | +13.4 | 1 |
| tsla | rel_momentum | +1.1 | +6.7 | 3 |
| usdjpy | obv_break | +1.0 | +3.6 | 1 |
| msft | fvg | +0.3 | +5.2 | 1 |

## What survives scrutiny

`volatility_breakout` -- today's open plus or minus a fraction of
yesterday's range -- is the only family passing a COMPLETE null on
unrelated instruments: ethusd, btc, de40 and usdjpy, spanning crypto, a
German index and a JPY cross. It beats its flips on risk-adjusted terms in
every case. Larry Williams' construction, decades old and public, which
cuts both ways: no novelty, but no reason to think it was fitted here.

`xma_cross` -- the family where the CHOICE of moving average is the swept
axis -- passes complete nulls on usdjpy, amzn, gbpjpy, hk50 and xaueur, at
+9.7%, +6.7%, +6.1%, +3.0% and +6.7% over twenty months. So the answer to
'does the average type matter' is measurably yes, marginally. It led the
in-sample breadth table at 33/44 symbols largely because it carries the
biggest grid, and the null ate most of that margin.

`rsi_divergence` never passed on any of 44 symbols, across roughly fifty
thousand cells each. The one unambiguous negative in the study, and a
negative needs no null.

## Three results that pass and should not be believed

* `nvda day_of_week` +2.8% beat all three seeds. It is a weekday with no
  mechanism, carried as the study's canary. Flipping the direction of a
  rule whose signal is not direction-dependent does not produce a
  meaningful control, which is the limit of this whole method.
* `xaueur xma_cross` shows PF 2.78 on **15 trades**. Not a measurement.
* `nq floor_pivot` is PF 1.04 at 22% drawdown -- indistinguishable from
  flat.

## The stock question

The new families did not break the pattern where only TSLA passes; they
reproduced it more strongly. Spearman between spread-as-share-of-stop and
families passing went from -0.79 for the original 29 families to **-0.88**
for these 33. The null analysis shows the same mechanism from the other
side: on googl, avgo, msft and tsm the coin flips could not clear the
gates at all, because random entries there simply pay a 4-6 bp spread
repeatedly. Cost relative to range decides a cross-stock family sweep.
