# USTEC fastbar/swingbar, live fills

## USTEC 5m_fastbar

| family | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | OOS PF | chosen params |
|---|---|---|---|---|---|---|---|---|
| fb_adx | +18.9% | 15.9% | 266 | +20.0% | 7.6% | 138 | 1.38 | time_24 stop=0.4 follow trend=none vol=calm floor=20.0 period=14 |
| fb_aroon | +59.5% | 7.4% | 205 | +16.2% | 6.8% | 85 | 1.38 | rr_2 stop=0.2 fade trend=ema_20d vol=calm period=25 threshold=70.0 |
| fb_rvi | +63.6% | 16.0% | 515 | +15.7% | 13.4% | 398 | 1.08 | trail_0.25 stop=0.2 follow trend=ema_20d vol=none period=10 |
| fb_williams | +47.3% | 12.5% | 349 | +11.7% | 17.6% | 182 | 1.12 | rr_2 stop=0.2 fade trend=ema_20d vol=calm period=14 zone_edge=20.0 |
| fb_klinger | +91.8% | 18.3% | 357 | +10.6% | 14.4% | 186 | 1.10 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm scale=0.5 |
| fb_ma_zoo | +78.8% | 15.8% | 357 | +7.8% | 13.3% | 186 | 1.09 | time_24 stop=0.1 follow trend=none vol=calm ma=dema period=10 |
| fb_schaff | +46.6% | 12.4% | 339 | +6.0% | 12.8% | 179 | 1.06 | rr_2 stop=0.2 fade trend=ema_20d vol=calm cycle=10 |
| fb_candle | +56.9% | 8.5% | 238 | +5.7% | 13.0% | 110 | 1.13 | rr_1 stop=0.4 fade trend=ema_20d vol=calm context_bars=10 pattern=engulfing |
| fb_triple_screen | +50.2% | 14.9% | 301 | +4.8% | 9.4% | 160 | 1.08 | trail_0.25 stop=0.4 follow trend=ema_20d vol=calm multiple=4 osc=force |
| fb_donchian | +40.9% | 10.9% | 348 | +3.9% | 10.9% | 177 | 1.06 | trail_0.25 stop=0.4 follow trend=ema_20d vol=calm period=10 |
| fb_psar | +62.5% | 16.7% | 357 | +3.6% | 10.8% | 185 | 1.04 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm step=0.04 |
| fb_roc | +57.9% | 7.9% | 354 | +1.9% | 12.2% | 181 | 1.03 | trail_0.25 stop=0.4 follow trend=ema_20d vol=calm lookback=3 threshold_atr=1.0 |
| fb_keltner | +60.0% | 17.9% | 240 | +0.8% | 12.9% | 217 | 1.01 | trail_0.25 stop=0.2 follow trend=ema_20d vol=none k=2.0 period=10 |
| fb_force | +58.4% | 11.5% | 349 | +0.4% | 18.8% | 178 | 1.00 | rr_1 stop=0.4 fade trend=ema_20d vol=calm period=13 |
| fb_bollinger | +65.6% | 13.3% | 190 | +0.3% | 11.6% | 113 | 1.00 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm k=2.5 mode=break period=20 |
| fb_zscore | +65.6% | 13.3% | 190 | +0.3% | 11.6% | 113 | 1.00 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm period=20 threshold_z=2.5 |
| fb_vwap_band | +83.6% | 11.3% | 339 | +0.1% | 19.2% | 170 | 1.00 | rr_2 stop=0.4 follow trend=ema_20d vol=calm k=1.0 mode=break |
| fb_cmo | +42.0% | 15.9% | 281 | -0.3% | 15.7% | 241 | 1.00 | trail_0.25 stop=0.2 fade trend=ema_20d vol=none period=20 threshold=50.0 |
| fb_chop | +75.8% | 10.3% | 264 | -0.9% | 14.7% | 130 | 0.99 | rr_2 stop=0.4 fade trend=ema_20d vol=calm period=14 threshold=50.0 |
| fb_macd | +52.0% | 11.8% | 339 | -2.2% | 18.4% | 178 | 0.98 | rr_2 stop=0.2 fade trend=ema_20d vol=calm line=signal macd_set=[12, 26, 9] |
| fb_trend_pullback | +59.5% | 10.0% | 238 | -2.4% | 17.1% | 132 | 0.96 | rr_2 stop=0.4 follow trend=ema_20d vol=calm regime_filter=ichimoku trend_bars=100 trigger=rsi2 |
| fb_dpo | +65.9% | 6.4% | 205 | -3.0% | 12.2% | 90 | 0.93 | rr_2 stop=0.4 fade trend=ema_20d vol=calm period=40 threshold_z=1.5 |
| fb_supertrend | +45.2% | 11.2% | 477 | -3.0% | 15.6% | 303 | 0.97 | time_24 stop=0.4 follow trend=ema_20d vol=none atr_period=10 mult=1.5 |
| fb_camarilla | +54.0% | 10.4% | 145 | -6.0% | 13.1% | 87 | 0.84 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm level=4 |
| fb_cci | +70.1% | 12.6% | 313 | -6.4% | 25.5% | 166 | 0.93 | rr_2 stop=0.2 follow trend=ema_20d vol=calm period=10 threshold=150.0 |
| fb_mfi | +61.2% | 8.3% | 195 | -7.1% | 10.8% | 108 | 0.87 | rr_2 stop=0.2 fade trend=ema_20d vol=calm period=7 threshold=90.0 |
| fb_dual_osc | +67.2% | 19.5% | 406 | -7.6% | 25.9% | 249 | 0.94 | rr_2 stop=0.4 fade trend=ema_20d vol=none pair=['stoch', 'vwap'] window=3 |
| fb_heikin | +53.9% | 12.2% | 355 | -8.4% | 26.7% | 181 | 0.92 | rr_2 stop=0.4 fade trend=ema_20d vol=calm run=2 |
| fb_chaikin | +48.1% | 17.2% | 351 | -8.8% | 13.8% | 185 | 0.88 | time_24 stop=0.1 follow trend=none vol=calm scale=1 |
| fb_stoch | +95.3% | 13.0% | 347 | -9.4% | 21.9% | 186 | 0.91 | rr_2 stop=0.2 follow trend=ema_20d vol=calm period=9 zone_edge=20.0 |
| fb_linreg_channel | +51.9% | 15.9% | 271 | -9.9% | 16.8% | 148 | 0.86 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm k=2.0 period=20 |
| fb_obv_ema | +29.2% | 14.8% | 348 | -10.0% | 21.9% | 176 | 0.85 | trail_0.25 stop=0.4 follow trend=ema_20d vol=calm period=20 |
| fb_tsi | +42.6% | 16.9% | 344 | -10.3% | 18.8% | 176 | 0.89 | rr_2 stop=0.4 fade trend=ema_20d vol=calm period=25 |
| fb_bop | +57.3% | 9.2% | 338 | -11.4% | 21.3% | 172 | 0.86 | rr_2 stop=0.4 follow trend=ema_20d vol=calm period=14 threshold=0.1 |
| fb_vortex | +44.8% | 17.2% | 353 | -11.5% | 16.2% | 185 | 0.86 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm period=7 |
| fb_rsi | +100.8% | 11.8% | 350 | -11.9% | 26.1% | 183 | 0.89 | rr_2 stop=0.2 follow trend=ema_20d vol=calm period=2 threshold=90.0 |
| fb_ichimoku | +58.5% | 6.2% | 295 | -12.3% | 23.9% | 152 | 0.85 | rr_1 stop=0.4 fade trend=ema_20d vol=calm mode=cloud_break scale=0.5 |
| fb_crsi | +99.9% | 12.3% | 464 | -13.5% | 25.5% | 353 | 0.92 | time_24 stop=0.2 follow trend=none vol=none threshold=10.0 |
| fb_vote | +54.5% | 9.6% | 296 | -14.4% | 26.2% | 146 | 0.81 | rr_1 stop=0.4 fade trend=ema_20d vol=calm votes=8 |
| fb_stochrsi | +60.5% | 10.2% | 347 | -15.5% | 29.7% | 177 | 0.82 | rr_2 stop=0.4 fade trend=ema_20d vol=calm period=14 zone_edge=10.0 |
| fb_coppock | +31.1% | 12.4% | 349 | -16.9% | 26.1% | 179 | 0.82 | rr_2 stop=0.4 fade trend=ema_20d vol=calm scale=0.5 |
| fb_awesome | +78.2% | 12.7% | 355 | -17.3% | 32.1% | 181 | 0.82 | rr_2 stop=0.4 fade trend=ema_20d vol=calm ao_set=[3, 10] mode=ac |
| fb_squeeze | +59.7% | 14.4% | 266 | -19.1% | 26.8% | 146 | 0.71 | trail_0.25 stop=0.2 follow trend=ema_20d vol=calm kc=2.0 period=10 |
| fb_trix | +54.9% | 13.2% | 314 | -20.1% | 26.5% | 161 | 0.75 | rr_2 stop=0.4 fade trend=ema_20d vol=calm line=signal period=15 |
| fb_ema_cross | +40.0% | 10.5% | 287 | -21.8% | 29.0% | 145 | 0.68 | rr_1 stop=0.4 fade trend=ema_20d vol=calm fast_bars=9 slow_bars=21 |
| fb_kst | +47.8% | 9.4% | 326 | -21.9% | 26.0% | 168 | 0.72 | rr_1 stop=0.4 fade trend=ema_20d vol=calm scale=1.0 |
| fb_laguerre | +51.2% | 14.3% | 356 | -22.4% | 27.5% | 248 | 0.78 | trail_0.25 stop=0.2 follow trend=ema_20d vol=none gamma=0.8 threshold=0.15 |
| fb_alligator | refused (mixed) best IS +42.7% | | | | | | | |
| fb_elder_ray | refused (mixed) best IS +43.8% | | | | | | | |
| fb_eom | refused (mixed) best IS +46.0% | | | | | | | |
| fb_mass | refused (mixed) best IS +28.3% | | | | | | | |
| fb_ultimate | refused (mixed) best IS +21.3% | | | | | | | |
| fb_volume_breakout | refused (mixed) best IS +22.3% | | | | | | | |

## USTEC 240m_swingbar

| family | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | OOS PF | chosen params |
|---|---|---|---|---|---|---|---|---|
| sb_eom | +14.0% | 16.8% | 101 | +34.3% | 9.3% | 53 | 1.97 | trail_1.0 stop=0.5 follow trend=ema_50d vol=none period=14 |
| sb_ichimoku | +29.4% | 16.7% | 86 | +23.2% | 10.3% | 64 | 1.58 | trail_0.5 stop=0.5 follow trend=ema_50d vol=none mode=cloud_break scale=0.5 |
| sb_chop | +57.7% | 12.4% | 116 | +18.5% | 11.4% | 88 | 1.30 | trail_0.5 stop=0.5 fade trend=none vol=none period=14 threshold=50.0 |
| sb_roc | +56.6% | 13.0% | 81 | +16.7% | 15.2% | 69 | 1.34 | trail_0.5 stop=0.5 follow trend=ema_50d vol=none lookback=24 threshold_atr=1.0 |
| sb_awesome | +42.9% | 13.9% | 72 | +15.3% | 12.2% | 38 | 1.37 | days_5 stop=0.5 follow trend=ema_50d vol=calm ao_set=[3, 10] mode=ao |
| sb_camarilla | +46.2% | 15.0% | 134 | +13.0% | 6.3% | 70 | 1.29 | trail_0.5 stop=0.5 follow trend=ema_50d vol=calm level=3 |
| sb_alligator | +23.8% | 10.5% | 69 | +8.9% | 6.6% | 44 | 1.33 | trail_0.5 stop=0.5 follow trend=ema_50d vol=calm scale=1.0 |
| sb_stochrsi | +79.7% | 15.6% | 211 | +5.1% | 22.4% | 124 | 1.06 | trail_0.5 stop=0.5 follow trend=none vol=none period=7 zone_edge=10.0 |
| sb_cci | +54.2% | 9.9% | 78 | +2.5% | 7.8% | 41 | 1.07 | trail_0.5 stop=0.5 follow trend=ema_50d vol=calm period=14 threshold=100.0 |
| sb_tsi | +31.8% | 13.3% | 75 | +2.4% | 13.0% | 39 | 1.08 | trail_0.5 stop=0.5 follow trend=ema_50d vol=calm period=25 |
| sb_rvi | +25.4% | 5.3% | 63 | -0.6% | 8.6% | 24 | 0.96 | trail_1.0 stop=1.0 follow trend=ema_50d vol=calm period=20 |
| sb_vwap_band | +105.2% | 13.2% | 166 | -1.7% | 21.2% | 93 | 0.98 | days_2 stop=0.5 fade trend=ema_50d vol=calm k=1.5 mode=reenter |
| sb_heikin | +48.1% | 14.9% | 110 | -3.1% | 15.6% | 56 | 0.94 | trail_0.5 stop=0.5 follow trend=ema_50d vol=calm run=3 |
| sb_psar | +27.8% | 7.2% | 57 | -3.8% | 5.0% | 14 | 0.61 | trail_1.0 stop=1.0 follow trend=ema_50d vol=none step=0.01 |
| sb_stoch | +41.7% | 15.2% | 145 | -4.5% | 21.1% | 75 | 0.93 | trail_0.5 stop=0.5 fade trend=ema_50d vol=calm period=5 zone_edge=30.0 |
| sb_adx | +67.6% | 12.2% | 87 | -6.0% | 18.3% | 50 | 0.88 | days_2 stop=0.5 follow trend=ema_50d vol=calm floor=20.0 period=7 |
| sb_laguerre | +37.2% | 17.9% | 73 | -6.0% | 17.1% | 33 | 0.80 | trail_1.0 stop=0.5 follow trend=ema_50d vol=calm gamma=0.5 threshold=0.15 |
| sb_ema_cross | +47.1% | 12.4% | 77 | -6.1% | 16.7% | 45 | 0.83 | trail_0.5 stop=0.5 fade trend=none vol=none fast_bars=9 slow_bars=34 |
| sb_vortex | +46.4% | 13.2% | 118 | -6.6% | 13.5% | 63 | 0.87 | trail_0.5 stop=0.5 follow trend=none vol=none period=21 |
| sb_dual_osc | +48.6% | 10.4% | 90 | -7.8% | 17.3% | 55 | 0.82 | trail_0.5 stop=0.5 fade trend=ema_50d vol=calm pair=['stoch', 'vwap'] window=1 |
| sb_keltner | +53.4% | 8.0% | 78 | -8.8% | 21.1% | 61 | 0.81 | trail_0.5 stop=0.5 follow trend=ema_50d vol=none k=2.5 period=50 |
| sb_candle | +72.2% | 10.2% | 86 | -9.0% | 22.2% | 42 | 0.73 | trail_0.5 stop=0.5 follow trend=none vol=calm context_bars=5 pattern=harami |
| sb_bollinger | +57.9% | 11.8% | 104 | -12.1% | 24.9% | 61 | 0.78 | trail_1.0 stop=0.5 follow trend=ema_50d vol=none k=1.5 mode=break period=10 |
| sb_zscore | +57.9% | 11.8% | 104 | -12.1% | 24.9% | 61 | 0.78 | trail_1.0 stop=0.5 follow trend=ema_50d vol=none period=10 threshold_z=1.5 |
| sb_crsi | +43.0% | 16.4% | 97 | -13.5% | 22.0% | 73 | 0.76 | trail_0.5 stop=0.5 fade trend=ema_50d vol=none threshold=15.0 |
| sb_cmo | +13.9% | 18.6% | 128 | -13.8% | 20.7% | 66 | 0.78 | trail_0.5 stop=0.5 follow trend=ema_50d vol=none period=9 threshold=30.0 |
| sb_williams | +26.2% | 12.9% | 82 | -14.1% | 25.5% | 45 | 0.70 | trail_1.0 stop=0.5 fade trend=ema_50d vol=calm period=7 zone_edge=20.0 |
| sb_dpo | +7.0% | 9.4% | 68 | -14.7% | 15.9% | 46 | 0.60 | trail_0.5 stop=0.5 follow trend=ema_50d vol=none period=10 threshold_z=1.5 |
| sb_vote | +62.9% | 14.1% | 110 | -17.4% | 24.2% | 63 | 0.69 | days_2 stop=0.5 follow trend=ema_50d vol=none votes=7 |
| sb_aroon | +36.5% | 11.6% | 58 | -17.5% | 18.8% | 36 | 0.43 | trail_0.5 stop=0.5 fade trend=none vol=calm period=25 threshold=50.0 |
| sb_mfi | +42.6% | 16.4% | 114 | -24.9% | 29.6% | 69 | 0.61 | trail_0.5 stop=0.5 follow trend=none vol=none period=7 threshold=90.0 |
| sb_elder_ray | +32.8% | 12.3% | 103 | -25.1% | 25.1% | 54 | 0.46 | trail_0.5 stop=0.5 follow trend=none vol=none period=26 |
| sb_ma_zoo | +70.3% | 17.3% | 193 | -26.0% | 28.8% | 87 | 0.64 | trail_0.5 stop=0.5 follow trend=none vol=none ma=ema period=20 |
| sb_supertrend | +82.1% | 19.4% | 170 | -26.6% | 33.2% | 102 | 0.68 | trail_0.5 stop=0.5 follow trend=none vol=none atr_period=10 mult=1.5 |
| sb_trend_pullback | +59.6% | 11.1% | 122 | -30.1% | 30.4% | 56 | 0.41 | trail_0.5 stop=0.5 fade trend=none vol=none regime_filter=supertrend trend_bars=100 trigger=rsi2 |
| sb_volume_breakout | +75.1% | 19.5% | 78 | -31.2% | 31.7% | 37 | 0.19 | days_10 stop=0.5 fade trend=none vol=calm period=10 surge=1.5 |
| sb_macd | +61.9% | 15.2% | 195 | -32.8% | 33.9% | 89 | 0.60 | trail_0.5 stop=0.5 follow trend=none vol=none line=zero macd_set=[3, 10, 16] |
| sb_bop | refused (mixed) best IS +49.4% | | | | | | | |
| sb_chaikin | refused (mixed) best IS +39.5% | | | | | | | |
| sb_coppock | refused (mixed) best IS +90.2% | | | | | | | |
| sb_donchian | refused (mixed) best IS +82.9% | | | | | | | |
| sb_force | refused (mixed) best IS +49.9% | | | | | | | |
| sb_klinger | refused (mixed) best IS +42.6% | | | | | | | |
| sb_kst | refused (mixed) best IS +92.2% | | | | | | | |
| sb_linreg_channel | refused (mixed) best IS +63.2% | | | | | | | |
| sb_mass | refused (mixed) best IS +59.0% | | | | | | | |
| sb_obv_ema | refused (mixed) best IS +35.9% | | | | | | | |
| sb_rsi | refused (mixed) best IS +92.8% | | | | | | | |
| sb_schaff | refused (mixed) best IS +59.9% | | | | | | | |
| sb_squeeze | refused (mixed) best IS +55.7% | | | | | | | |
| sb_triple_screen | refused (mixed) best IS +58.3% | | | | | | | |
| sb_trix | refused (mixed) best IS +48.3% | | | | | | | |
| sb_ultimate | refused (mixed) best IS +44.1% | | | | | | | |

## USTEC 1d_swingbar

| family | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | OOS PF | chosen params |
|---|---|---|---|---|---|---|---|---|
| sb_williams | +59.3% | 12.9% | 56 | +31.0% | 7.2% | 29 | 1.98 | days_2 stop=0.5 fade trend=none vol=none period=28 zone_edge=20.0 |
| sb_vwap_band | +53.0% | 11.6% | 73 | +5.9% | 9.3% | 41 | 1.12 | days_2 stop=0.5 fade trend=ema_50d vol=calm k=1.0 mode=break |
| sb_camarilla | +27.3% | 16.7% | 70 | -2.9% | 21.4% | 39 | 0.94 | days_2 stop=0.5 follow trend=ema_50d vol=calm level=3 |
| sb_stoch | +37.5% | 13.3% | 63 | -14.5% | 20.5% | 41 | 0.78 | rr_2 stop=0.5 follow trend=none vol=calm period=9 zone_edge=20.0 |
| sb_macd | +40.8% | 12.9% | 61 | -21.3% | 24.8% | 40 | 0.61 | days_2 stop=0.5 fade trend=none vol=none line=signal macd_set=[3, 10, 16] |
| sb_adx | refused (trades) best IS +60.1% | | | | | | | |
| sb_alligator | refused (mixed) best IS +36.4% | | | | | | | |
| sb_aroon | refused (trades) best IS +45.5% | | | | | | | |
| sb_awesome | refused (mixed) best IS +76.8% | | | | | | | |
| sb_bollinger | refused (mixed) best IS +50.0% | | | | | | | |
| sb_bop | refused (mixed) best IS +27.5% | | | | | | | |
| sb_candle | refused (trades) best IS +52.2% | | | | | | | |
| sb_cci | refused (mixed) best IS +55.0% | | | | | | | |
| sb_chaikin | refused (trades) best IS +16.4% | | | | | | | |
| sb_chop | refused (trades) best IS +10.4% | | | | | | | |
| sb_cmo | refused (mixed) best IS +63.1% | | | | | | | |
| sb_coppock | refused (trades) best IS +31.5% | | | | | | | |
| sb_crsi | refused (trades) best IS +38.7% | | | | | | | |
| sb_donchian | refused (mixed) best IS +21.1% | | | | | | | |
| sb_dpo | refused (trades) best IS +21.9% | | | | | | | |
| sb_dual_osc | refused (mixed) best IS +30.0% | | | | | | | |
| sb_elder_ray | refused (trades) best IS +51.8% | | | | | | | |
| sb_ema_cross | refused (trades) best IS +35.0% | | | | | | | |
| sb_eom | refused (trades) best IS +37.1% | | | | | | | |
| sb_force | refused (mixed) best IS +110.7% | | | | | | | |
| sb_heikin | refused (mixed) best IS +54.9% | | | | | | | |
| sb_ichimoku | refused (trades) best IS +84.7% | | | | | | | |
| sb_keltner | refused (trades) best IS +58.4% | | | | | | | |
| sb_klinger | refused (mixed) best IS +25.7% | | | | | | | |
| sb_kst | refused (trades) best IS +53.7% | | | | | | | |
| sb_laguerre | refused (trades) best IS +78.8% | | | | | | | |
| sb_linreg_channel | refused (trades) best IS +37.0% | | | | | | | |
| sb_ma_zoo | refused (mixed) best IS +115.5% | | | | | | | |
| sb_mass | refused (trades) best IS +3.1% | | | | | | | |
| sb_mfi | refused (trades) best IS +20.0% | | | | | | | |
| sb_obv_ema | refused (mixed) best IS +54.3% | | | | | | | |
| sb_psar | refused (mixed) best IS +55.7% | | | | | | | |
| sb_roc | refused (mixed) best IS +60.8% | | | | | | | |
| sb_rsi | refused (mixed) best IS +88.3% | | | | | | | |
| sb_rvi | refused (mixed) best IS +37.9% | | | | | | | |
| sb_schaff | refused (mixed) best IS +41.5% | | | | | | | |
| sb_squeeze | refused (trades) best IS +30.5% | | | | | | | |
| sb_stochrsi | refused (mixed) best IS +65.4% | | | | | | | |
| sb_supertrend | refused (trades) best IS +50.8% | | | | | | | |
| sb_trend_pullback | refused (trades) best IS +35.4% | | | | | | | |
| sb_triple_screen | refused (mixed) best IS +48.9% | | | | | | | |
| sb_trix | refused (trades) best IS +32.4% | | | | | | | |
| sb_tsi | refused (mixed) best IS +30.1% | | | | | | | |
| sb_ultimate | refused (trades) best IS +22.0% | | | | | | | |
| sb_volume_breakout | refused (trades) best IS +34.8% | | | | | | | |
| sb_vortex | refused (mixed) best IS +64.4% | | | | | | | |
| sb_vote | refused (trades) best IS +65.1% | | | | | | | |
| sb_zscore | refused (mixed) best IS +50.0% | | | | | | | |
