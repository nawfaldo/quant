
### US500 5m_fastbar: 22/53 passed IS, 17/22 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| fb_camarilla | +36.1% | 18.0% | 434 | +59.4% | 10.8% | 350 | 1.31 | trail_0.25 stop=0.2 fade none none level=3 |
| fb_trix | +49.8% | 14.3% | 346 | +42.0% | 10.0% | 188 | 1.53 | time_24 stop=0.2 fade none calm line=signal period=30 |
| fb_macd | +19.4% | 19.3% | 369 | +40.8% | 8.0% | 211 | 1.54 | time_24 stop=0.1 follow none calm line=signal macd_set=[3, 10, 16] |
| fb_heikin | +25.3% | 12.4% | 367 | +29.7% | 6.1% | 210 | 1.46 | time_24 stop=0.1 follow none calm run=5 |
| fb_candle | +40.8% | 10.4% | 203 | +22.7% | 3.2% | 111 | 1.63 | trail_0.25 stop=0.2 fade none calm context_bars=10 pattern=star |
| fb_triple_screen | +27.4% | 12.0% | 366 | +21.3% | 10.2% | 209 | 1.19 | rr_1 stop=0.4 follow none calm multiple=6 osc=force |
| fb_tsi | +24.8% | 17.6% | 367 | +15.3% | 10.3% | 210 | 1.16 | time_24 stop=0.2 follow none calm period=13 |
| fb_trend_pullback | +48.3% | 8.6% | 352 | +14.4% | 12.0% | 266 | 1.13 | time_24 stop=0.2 fade ema_20d none regime_filter=supertrend trend_bars=50 trigger=rsi2 |
| fb_rsi | +10.8% | 8.5% | 68 | +13.8% | 5.8% | 54 | 1.60 | time_24 stop=0.4 follow none none period=9 threshold=90.0 |
| fb_roc | +43.0% | 9.0% | 359 | +12.4% | 16.2% | 286 | 1.08 | time_24 stop=0.2 fade none none lookback=3 threshold_atr=3.0 |
| fb_crsi | +32.6% | 5.6% | 142 | +9.3% | 10.1% | 76 | 1.19 | rr_2 stop=0.4 follow none calm threshold=5.0 |
| fb_klinger | +21.1% | 14.7% | 367 | +7.7% | 12.9% | 209 | 1.09 | trail_0.25 stop=0.4 follow none calm scale=1.0 |
| fb_bollinger | +25.9% | 6.0% | 168 | +6.2% | 11.9% | 97 | 1.14 | rr_2 stop=0.2 follow ema_20d calm k=2.5 mode=reenter period=20 |
| fb_volume_breakout | +33.9% | 11.5% | 320 | +3.9% | 24.6% | 275 | 1.02 | rr_2 stop=0.2 fade none none period=55 surge=1.5 |
| fb_dual_osc | +31.5% | 5.2% | 183 | +3.7% | 9.7% | 95 | 1.09 | time_24 stop=0.2 follow ema_20d calm pair=['bb', 'vwap'] window=1 |
| fb_stoch | +59.4% | 14.2% | 367 | +0.6% | 18.8% | 210 | 1.01 | time_24 stop=0.4 follow none calm period=9 zone_edge=30.0 |
| fb_kst | +38.3% | 14.9% | 489 | +0.5% | 31.5% | 380 | 1.00 | time_24 stop=0.2 follow ema_20d none scale=1.0 |
| fb_dpo | +35.7% | 9.5% | 201 | -6.7% | 14.6% | 104 | 0.86 | rr_2 stop=0.4 fade ema_20d calm period=40 threshold_z=1.5 |
| fb_supertrend | +38.3% | 15.2% | 429 | -13.5% | 28.8% | 328 | 0.91 | time_24 stop=0.2 fade none none atr_period=7 mult=3.0 |
| fb_ma_zoo | +56.9% | 11.1% | 440 | -13.7% | 29.6% | 331 | 0.88 | time_24 stop=0.4 fade none none ma=mcginley period=50 |
| fb_ema_cross | +24.9% | 12.3% | 457 | -19.0% | 32.1% | 350 | 0.86 | time_24 stop=0.2 fade none none fast_bars=5 slow_bars=34 |
| fb_mass | +32.6% | 11.8% | 281 | -24.6% | 28.0% | 266 | 0.79 | rr_1 stop=0.4 fade none none sum_bars=25 |

### DE40 1d_swingbar: 0/53 passed IS, 0/0 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|

### DE40 60m_swingbar: 9/53 passed IS, 5/9 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_volume_breakout | +32.9% | 13.5% | 98 | +39.6% | 7.8% | 61 | 2.05 | trail_0.5 stop=0.5 fade none calm period=55 surge=2.0 |
| sb_trend_pullback | +61.6% | 17.3% | 229 | +34.1% | 11.8% | 115 | 1.31 | rr_2 stop=0.5 fade none none regime_filter=supertrend trend_bars=100 trigger=stoch |
| sb_aroon | +26.2% | 13.9% | 176 | +12.5% | 14.7% | 89 | 1.21 | trail_0.5 stop=0.5 follow ema_50d none period=25 threshold=70.0 |
| sb_mfi | +51.5% | 13.9% | 92 | +3.2% | 17.3% | 50 | 1.07 | days_2 stop=0.5 fade none calm period=14 threshold=90.0 |
| sb_dpo | +27.8% | 10.9% | 51 | +1.1% | 20.9% | 30 | 1.03 | trail_1.0 stop=0.5 follow ema_50d none period=20 threshold_z=2.0 |
| sb_ultimate | +68.7% | 19.0% | 303 | -0.1% | 22.4% | 136 | 1.00 | trail_0.5 stop=0.5 fade none none base=4 threshold=30.0 |
| sb_cmo | +26.1% | 19.8% | 117 | -3.7% | 26.6% | 75 | 0.95 | trail_1.0 stop=0.5 fade ema_50d calm period=9 threshold=30.0 |
| sb_candle | +60.4% | 7.5% | 60 | -6.3% | 16.4% | 36 | 0.79 | trail_1.0 stop=0.5 fade none calm context_bars=5 pattern=star |
| sb_macd | +27.3% | 17.5% | 260 | -13.8% | 32.5% | 145 | 0.87 | trail_0.5 stop=0.5 fade none calm line=signal macd_set=[3, 10, 16] |

### UK100 240m_swingbar: 10/53 passed IS, 1/10 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_macd | +6.2% | 6.2% | 250 | +11.2% | 2.4% | 107 | 1.51 | trail_0.5 stop=1.5 fade none none line=signal macd_set=[12, 26, 9] |
| sb_aroon | +21.0% | 12.4% | 144 | -2.3% | 11.3% | 56 | 0.89 | trail_0.5 stop=1.0 fade none calm period=25 threshold=50.0 |
| sb_triple_screen | +17.7% | 9.8% | 268 | -2.7% | 12.1% | 103 | 0.93 | trail_0.5 stop=1.0 fade none calm multiple=4 osc=force |
| sb_supertrend | +26.1% | 9.2% | 186 | -3.3% | 9.3% | 59 | 0.86 | trail_1.0 stop=1.5 fade none none atr_period=7 mult=1.5 |
| sb_trend_pullback | +66.1% | 12.9% | 117 | -6.8% | 11.0% | 36 | 0.75 | trail_0.5 stop=0.5 follow none calm regime_filter=supertrend trend_bars=200 trigger=cci |
| sb_stoch | +29.4% | 16.7% | 112 | -8.7% | 14.7% | 33 | 0.75 | rr_3 stop=1.0 follow none calm period=14 zone_edge=30.0 |
| sb_dual_osc | +16.6% | 10.5% | 431 | -15.8% | 26.7% | 166 | 0.78 | trail_0.5 stop=1.0 follow none none pair=['stoch', 'vwap'] window=1 |
| sb_williams | +19.3% | 19.7% | 140 | -20.1% | 26.6% | 59 | 0.67 | rr_3 stop=1.0 follow none none period=14 zone_edge=20.0 |
| sb_ultimate | +15.4% | 18.9% | 260 | -23.9% | 34.6% | 114 | 0.77 | rr_2 stop=0.5 follow none none base=4 threshold=30.0 |
| sb_candle | +105.0% | 10.3% | 114 | -27.9% | 29.9% | 64 | 0.45 | trail_0.5 stop=0.5 follow none none context_bars=20 pattern=outside |

### UK100 1d_swingbar: 1/53 passed IS, 0/1 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_vwap_band | +4.4% | 12.2% | 107 | -18.7% | 22.2% | 30 | 0.36 | days_5 stop=1.5 follow none calm k=1.5 mode=break |

### UK100 60m_swingbar: 6/53 passed IS, 1/6 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_volume_breakout | +75.5% | 14.2% | 203 | +16.1% | 14.5% | 81 | 1.20 | rr_2 stop=1.0 fade none none period=20 surge=2.0 |
| sb_vote | +11.2% | 10.6% | 248 | -3.4% | 6.8% | 77 | 0.88 | trail_1.0 stop=1.5 fade none calm votes=9 |
| sb_aroon | +49.6% | 11.7% | 398 | -7.8% | 10.3% | 136 | 0.83 | trail_0.5 stop=1.0 fade none calm period=25 threshold=50.0 |
| sb_trend_pullback | +19.5% | 10.2% | 128 | -8.2% | 10.6% | 41 | 0.65 | trail_1.0 stop=1.0 fade none none regime_filter=supertrend trend_bars=200 trigger=crsi |
| sb_dual_osc | +9.9% | 10.2% | 139 | -10.8% | 11.1% | 45 | 0.43 | trail_0.5 stop=1.0 fade none calm pair=['rsi', 'vwap'] window=1 |
| sb_ema_cross | +19.3% | 18.5% | 121 | -18.1% | 18.1% | 54 | 0.68 | rr_3 stop=1.0 follow none none fast_bars=5 slow_bars=34 |

### JP225 240m_swingbar: 52/53 passed IS, 52/52 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_force | +508.6% | 8.8% | 285 | +693.7% | 6.8% | 236 | 4.70 | trail_0.5 stop=0.5 follow none none period=2 |
| sb_awesome | +434.0% | 8.0% | 243 | +457.1% | 4.6% | 197 | 3.50 | trail_0.5 stop=0.5 follow none none ao_set=[3, 10] mode=ac |
| sb_roc | +493.3% | 8.7% | 259 | +349.5% | 6.8% | 203 | 3.05 | trail_0.5 stop=0.5 follow none none lookback=3 threshold_atr=1.0 |
| sb_camarilla | +497.5% | 6.8% | 258 | +341.7% | 11.1% | 205 | 3.34 | trail_0.5 stop=0.5 follow none none level=3 |
| sb_vortex | +245.4% | 9.3% | 207 | +318.3% | 6.8% | 178 | 3.51 | trail_0.5 stop=0.5 follow none none period=7 |
| sb_psar | +503.4% | 13.2% | 246 | +307.1% | 6.3% | 190 | 3.41 | trail_0.5 stop=0.5 follow none none step=0.04 |
| sb_klinger | +442.0% | 11.8% | 215 | +306.3% | 3.3% | 168 | 3.28 | trail_0.5 stop=0.5 follow ema_50d none scale=0.5 |
| sb_williams | +286.4% | 9.5% | 250 | +294.5% | 7.4% | 195 | 3.10 | trail_0.5 stop=0.5 fade none none period=7 zone_edge=20.0 |
| sb_cci | +314.5% | 9.4% | 236 | +275.8% | 6.1% | 172 | 3.06 | trail_0.5 stop=0.5 fade none none period=10 threshold=100.0 |
| sb_vwap_band | +604.4% | 7.8% | 291 | +259.1% | 8.4% | 233 | 2.43 | trail_0.5 stop=0.5 follow none none k=2.5 mode=reenter |
| sb_bollinger | +359.1% | 6.3% | 223 | +252.0% | 6.1% | 161 | 3.19 | trail_0.5 stop=0.5 follow none none k=1.5 mode=break period=10 |
| sb_zscore | +359.1% | 6.3% | 223 | +252.0% | 6.1% | 161 | 3.19 | trail_0.5 stop=0.5 follow none none period=10 threshold_z=1.5 |
| sb_coppock | +240.7% | 7.0% | 183 | +250.4% | 5.1% | 153 | 3.27 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_ichimoku | +316.1% | 9.4% | 208 | +249.1% | 5.6% | 156 | 3.36 | trail_0.5 stop=0.5 follow none none mode=kijun_cross scale=0.5 |
| sb_macd | +381.7% | 8.4% | 227 | +238.5% | 6.0% | 156 | 3.26 | trail_0.5 stop=0.5 follow none none line=signal macd_set=[3, 10, 16] |
| sb_tsi | +494.1% | 7.4% | 220 | +232.7% | 5.1% | 152 | 3.44 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_heikin | +324.4% | 12.8% | 236 | +232.2% | 6.6% | 175 | 3.08 | trail_0.5 stop=0.5 follow none none run=3 |
| sb_ma_zoo | +848.8% | 10.5% | 254 | +230.7% | 5.5% | 181 | 2.88 | trail_0.5 stop=0.5 follow none none ma=dema period=10 |
| sb_dual_osc | +316.1% | 8.6% | 224 | +226.3% | 13.4% | 145 | 3.37 | trail_0.5 stop=0.5 fade none none pair=['stoch', 'vwap'] window=3 |
| sb_kst | +250.4% | 9.9% | 210 | +220.0% | 6.0% | 146 | 3.08 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_stoch | +336.4% | 18.3% | 244 | +201.6% | 8.8% | 172 | 2.79 | trail_0.5 stop=0.5 fade none none period=14 zone_edge=30.0 |
| sb_schaff | +176.7% | 9.7% | 182 | +197.7% | 6.1% | 130 | 3.17 | trail_0.5 stop=0.5 follow none none cycle=5 |
| sb_alligator | +266.0% | 11.8% | 229 | +186.8% | 7.7% | 154 | 2.99 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_stochrsi | +442.0% | 8.2% | 229 | +186.7% | 6.3% | 179 | 2.30 | trail_0.5 stop=0.5 follow none none period=7 zone_edge=20.0 |
| sb_obv_ema | +320.6% | 12.1% | 227 | +186.5% | 5.5% | 161 | 2.85 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_linreg_channel | +164.6% | 8.6% | 132 | +179.1% | 3.9% | 64 | 5.45 | trail_0.5 stop=0.5 follow none none k=2.0 period=20 |
| sb_rvi | +465.5% | 9.2% | 222 | +174.0% | 6.6% | 158 | 2.51 | trail_0.5 stop=0.5 follow none none period=20 |
| sb_chaikin | +180.3% | 9.7% | 169 | +171.8% | 4.2% | 110 | 3.67 | trail_0.5 stop=0.5 follow none none scale=1 |
| sb_rsi | +301.9% | 9.8% | 278 | +164.1% | 8.2% | 177 | 2.25 | trail_0.5 stop=0.5 fade none none period=2 threshold=80.0 |
| sb_bop | +200.5% | 12.1% | 157 | +160.7% | 4.7% | 107 | 4.21 | trail_0.5 stop=0.5 fade none none period=14 threshold=0.1 |
| sb_candle | +206.3% | 11.0% | 154 | +157.6% | 8.5% | 86 | 4.50 | trail_0.5 stop=0.5 follow none none context_bars=5 pattern=three |
| sb_crsi | +169.1% | 6.2% | 163 | +155.1% | 4.9% | 101 | 3.64 | trail_0.5 stop=0.5 fade none none threshold=15.0 |
| sb_donchian | +200.5% | 12.6% | 135 | +137.3% | 6.6% | 76 | 4.99 | trail_0.5 stop=0.5 follow ema_50d none period=10 |
| sb_triple_screen | +255.5% | 9.4% | 176 | +136.8% | 5.3% | 113 | 3.30 | trail_0.5 stop=0.5 follow none none multiple=4 osc=force |
| sb_trix | +150.0% | 9.3% | 159 | +134.1% | 11.3% | 98 | 3.26 | trail_0.5 stop=0.5 follow none none line=signal period=9 |
| sb_elder_ray | +163.1% | 13.7% | 160 | +119.8% | 6.1% | 104 | 3.55 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_squeeze | +310.2% | 5.8% | 120 | +119.3% | 6.4% | 77 | 3.66 | trail_0.5 stop=0.5 follow none none kc=1.0 period=10 |
| sb_vote | +240.7% | 16.5% | 137 | +103.2% | 7.2% | 93 | 2.86 | trail_1.0 stop=0.5 follow none none votes=7 |
| sb_mfi | +203.3% | 9.1% | 104 | +99.1% | 5.1% | 69 | 2.90 | trail_1.0 stop=0.5 follow none none period=7 threshold=80.0 |
| sb_keltner | +271.9% | 6.2% | 135 | +99.1% | 6.3% | 80 | 3.11 | trail_0.5 stop=0.5 follow none none k=1.5 period=50 |
| sb_laguerre | +279.3% | 16.1% | 119 | +98.5% | 7.0% | 89 | 2.38 | trail_1.0 stop=0.5 follow none none gamma=0.5 threshold=0.15 |
| sb_adx | +166.2% | 14.6% | 119 | +80.7% | 6.5% | 76 | 2.70 | trail_1.0 stop=0.5 follow none none floor=20.0 period=7 |
| sb_chop | +115.6% | 7.7% | 72 | +70.5% | 3.2% | 39 | 5.60 | trail_0.5 stop=0.5 follow none none period=28 threshold=50.0 |
| sb_ema_cross | +134.2% | 7.0% | 110 | +65.0% | 3.7% | 64 | 3.13 | trail_0.5 stop=0.5 fade none none fast_bars=5 slow_bars=21 |
| sb_supertrend | +258.5% | 14.0% | 126 | +62.0% | 5.1% | 70 | 2.72 | trail_1.0 stop=0.5 follow none none atr_period=7 mult=1.5 |
| sb_eom | +161.6% | 12.6% | 123 | +61.7% | 9.1% | 76 | 2.23 | trail_1.0 stop=0.5 follow none none period=14 |
| sb_aroon | +227.4% | 10.1% | 118 | +57.5% | 6.2% | 72 | 2.15 | trail_1.0 stop=0.5 follow none none period=14 threshold=50.0 |
| sb_cmo | +199.0% | 8.4% | 159 | +51.2% | 10.2% | 88 | 2.07 | trail_0.5 stop=0.5 fade none none period=14 threshold=30.0 |
| sb_ultimate | +171.4% | 9.4% | 127 | +45.0% | 9.2% | 69 | 2.21 | trail_0.5 stop=0.5 fade none none base=4 threshold=30.0 |
| sb_dpo | +114.0% | 13.1% | 101 | +35.6% | 9.8% | 68 | 1.86 | trail_1.0 stop=0.5 fade none none period=10 threshold_z=1.5 |
| sb_trend_pullback | +220.1% | 15.0% | 100 | +28.4% | 11.0% | 47 | 1.86 | trail_1.0 stop=0.5 fade none none regime_filter=supertrend trend_bars=50 trigger=rsi2 |
| sb_volume_breakout | +141.0% | 11.2% | 64 | +3.2% | 7.1% | 28 | 1.23 | trail_1.0 stop=0.5 follow none none period=10 surge=1.5 |

### JP225 1d_swingbar: 7/53 passed IS, 5/7 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_ma_zoo | +175.8% | 16.7% | 82 | +96.2% | 25.5% | 64 | 1.80 | trail_1.0 stop=0.5 follow none none ma=zlema period=10 |
| sb_awesome | +104.8% | 17.0% | 56 | +36.1% | 14.3% | 36 | 1.76 | trail_0.5 stop=0.5 follow none none ao_set=[5, 34] mode=ac |
| sb_klinger | +74.4% | 18.5% | 95 | +24.9% | 16.3% | 60 | 1.30 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_stoch | +82.0% | 16.7% | 91 | +7.2% | 10.3% | 36 | 1.22 | trail_0.5 stop=1.0 follow none none period=5 zone_edge=30.0 |
| sb_camarilla | +178.8% | 14.6% | 59 | +1.8% | 26.5% | 43 | 1.03 | trail_1.0 stop=0.5 follow none calm level=4 |
| sb_dual_osc | +84.5% | 14.4% | 82 | -0.4% | 27.0% | 53 | 0.99 | days_2 stop=0.5 fade none none pair=['cci', 'vwap'] window=3 |
| sb_vwap_band | +80.6% | 17.6% | 98 | -35.6% | 40.0% | 44 | 0.56 | trail_1.0 stop=0.5 fade none none k=1.0 mode=break |
