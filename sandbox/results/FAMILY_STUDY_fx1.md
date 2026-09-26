
### USDJPY 5m_fastbar: 5/53 passed IS, 3/5 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| fb_aroon | +23.2% | 11.1% | 744 | +1.6% | 16.0% | 298 | 1.02 | trail_0.25 stop=0.4 follow none calm period=25 threshold=70.0 |
| fb_volume_breakout | +48.3% | 13.5% | 446 | +1.0% | 13.9% | 155 | 1.02 | trail_0.25 stop=0.2 follow none none period=55 surge=3.0 |
| fb_trend_pullback | +46.9% | 19.1% | 903 | +0.1% | 10.2% | 288 | 1.00 | trail_0.25 stop=0.2 follow none none regime_filter=ichimoku trend_bars=50 trigger=bb |
| fb_zscore | +43.1% | 17.4% | 774 | -14.6% | 15.1% | 244 | 0.77 | trail_0.25 stop=0.2 follow ema_20d none period=50 threshold_z=3.0 |
| fb_dual_osc | +41.3% | 18.3% | 900 | -21.0% | 24.9% | 297 | 0.71 | trail_0.25 stop=0.2 fade none none pair=['bb', 'mfi'] window=3 |

### USDJPY 240m_swingbar: 44/53 passed IS, 6/44 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_vortex | +120.1% | 15.6% | 414 | +14.1% | 19.7% | 152 | 1.17 | trail_0.5 stop=0.5 follow none none period=14 |
| sb_cci | +66.2% | 13.4% | 601 | +6.2% | 14.3% | 205 | 1.10 | trail_0.5 stop=1.0 fade none none period=10 threshold=100.0 |
| sb_chaikin | +17.6% | 14.9% | 315 | +4.1% | 10.0% | 95 | 1.11 | days_2 stop=1.0 follow none none scale=1 |
| sb_camarilla | +89.2% | 18.8% | 552 | +3.5% | 24.0% | 193 | 1.04 | trail_0.5 stop=0.5 follow none none level=4 |
| sb_ichimoku | +104.2% | 14.6% | 508 | +1.9% | 18.5% | 177 | 1.02 | trail_0.5 stop=0.5 follow none none mode=kijun_cross scale=0.5 |
| sb_kst | +53.2% | 9.7% | 329 | +0.7% | 5.9% | 109 | 1.02 | trail_0.5 stop=1.0 fade ema_50d none scale=0.5 |
| sb_triple_screen | +30.7% | 8.1% | 227 | -0.0% | 8.3% | 82 | 1.00 | trail_0.5 stop=1.0 fade none none multiple=6 osc=stoch |
| sb_tsi | +65.1% | 12.7% | 348 | -2.2% | 6.7% | 124 | 0.94 | trail_0.5 stop=1.0 follow ema_50d none period=13 |
| sb_squeeze | +94.1% | 15.1% | 238 | -3.2% | 14.6% | 87 | 0.92 | trail_0.5 stop=0.5 follow ema_50d none kc=1.5 period=10 |
| sb_chop | +13.7% | 9.8% | 154 | -3.6% | 8.8% | 45 | 0.74 | trail_0.5 stop=1.5 follow none none period=28 threshold=50.0 |
| sb_alligator | +9.1% | 12.6% | 153 | -3.6% | 7.2% | 73 | 0.84 | trail_0.5 stop=1.0 follow ema_50d calm scale=1.0 |
| sb_coppock | +55.6% | 10.2% | 284 | -4.1% | 7.0% | 103 | 0.86 | trail_0.5 stop=1.0 follow ema_50d none scale=0.5 |
| sb_adx | +18.5% | 10.2% | 238 | -4.4% | 6.8% | 94 | 0.77 | trail_0.5 stop=1.5 follow none calm floor=20.0 period=7 |
| sb_psar | +98.4% | 18.4% | 421 | -4.9% | 13.1% | 147 | 0.93 | trail_0.5 stop=0.5 follow ema_50d none step=0.04 |
| sb_rvi | +16.9% | 14.2% | 445 | -5.0% | 13.8% | 158 | 0.89 | trail_0.5 stop=1.0 fade ema_50d none period=20 |
| sb_macd | +191.1% | 16.1% | 403 | -5.3% | 17.4% | 133 | 0.92 | trail_0.5 stop=0.5 follow ema_50d none line=signal macd_set=[3, 10, 16] |
| sb_roc | +131.7% | 18.0% | 434 | -5.5% | 26.7% | 151 | 0.93 | trail_0.5 stop=0.5 follow ema_50d none lookback=3 threshold_atr=1.0 |
| sb_candle | +125.9% | 13.7% | 322 | -5.6% | 19.9% | 101 | 0.88 | trail_0.5 stop=0.5 follow none none context_bars=10 pattern=three |
| sb_vwap_band | +57.6% | 17.0% | 457 | -6.7% | 20.4% | 197 | 0.90 | trail_0.5 stop=1.0 fade none calm k=1.0 mode=break |
| sb_eom | +65.1% | 12.4% | 192 | -7.7% | 15.5% | 78 | 0.80 | trail_0.5 stop=0.5 follow ema_50d none period=28 |
| sb_keltner | +63.5% | 18.6% | 216 | -8.4% | 23.1% | 68 | 0.77 | trail_0.5 stop=0.5 follow ema_50d none k=1.5 period=10 |
| sb_donchian | +91.3% | 17.2% | 350 | -8.5% | 21.0% | 122 | 0.86 | trail_0.5 stop=0.5 follow ema_50d none period=10 |
| sb_bollinger | +116.6% | 13.8% | 377 | -8.5% | 20.4% | 130 | 0.87 | trail_0.5 stop=0.5 follow ema_50d none k=1.5 mode=break period=10 |
| sb_zscore | +116.6% | 13.8% | 377 | -8.5% | 20.4% | 130 | 0.87 | trail_0.5 stop=0.5 follow ema_50d none period=10 threshold_z=1.5 |
| sb_williams | +69.6% | 19.7% | 362 | -8.8% | 19.3% | 129 | 0.88 | days_2 stop=0.5 fade ema_50d none period=7 zone_edge=10.0 |
| sb_cmo | +58.0% | 16.3% | 324 | -8.9% | 25.4% | 136 | 0.86 | trail_0.5 stop=0.5 follow none calm period=9 threshold=30.0 |
| sb_heikin | +15.8% | 11.1% | 305 | -8.9% | 11.6% | 119 | 0.69 | trail_0.5 stop=1.5 follow none calm run=3 |
| sb_dual_osc | +120.8% | 18.4% | 362 | -9.5% | 14.6% | 120 | 0.84 | trail_0.5 stop=0.5 fade ema_50d none pair=['stoch', 'vwap'] window=1 |
| sb_awesome | +139.8% | 18.4% | 584 | -9.6% | 23.0% | 202 | 0.90 | trail_0.5 stop=0.5 follow none none ao_set=[5, 34] mode=ac |
| sb_crsi | +95.2% | 15.5% | 496 | -10.2% | 18.4% | 170 | 0.89 | trail_0.5 stop=0.5 fade none none threshold=15.0 |
| sb_mfi | +13.8% | 13.6% | 245 | -11.6% | 13.5% | 90 | 0.66 | trail_0.5 stop=1.0 fade ema_50d none period=7 threshold=80.0 |
| sb_rsi | +98.3% | 15.4% | 268 | -12.9% | 19.5% | 101 | 0.80 | trail_1.0 stop=0.5 fade ema_50d none period=2 threshold=90.0 |
| sb_schaff | +119.5% | 14.1% | 216 | -13.6% | 16.9% | 71 | 0.70 | trail_1.0 stop=0.5 follow ema_50d none cycle=10 |
| sb_ma_zoo | +63.5% | 14.7% | 345 | -14.4% | 14.6% | 128 | 0.68 | trail_0.5 stop=1.0 follow ema_50d none ma=t3 period=10 |
| sb_ema_cross | +55.1% | 11.3% | 158 | -14.8% | 17.7% | 56 | 0.64 | trail_1.0 stop=0.5 follow ema_50d none fast_bars=9 slow_bars=21 |
| sb_klinger | +10.1% | 19.1% | 347 | -15.9% | 15.9% | 146 | 0.68 | trail_0.5 stop=1.0 follow none calm scale=1.0 |
| sb_aroon | +78.7% | 16.2% | 218 | -16.1% | 20.3% | 76 | 0.64 | trail_1.0 stop=0.5 follow ema_50d none period=14 threshold=50.0 |
| sb_vote | +129.7% | 17.3% | 293 | -16.4% | 18.6% | 101 | 0.69 | trail_0.5 stop=0.5 follow ema_50d none votes=8 |
| sb_force | +24.3% | 16.8% | 409 | -16.5% | 18.9% | 147 | 0.65 | trail_0.5 stop=1.0 fade none none period=13 |
| sb_dpo | +45.4% | 17.2% | 177 | -16.8% | 18.1% | 59 | 0.65 | trail_1.0 stop=0.5 fade ema_50d none period=10 threshold_z=1.5 |
| sb_laguerre | +87.7% | 10.6% | 295 | -17.0% | 17.7% | 95 | 0.67 | trail_0.5 stop=0.5 follow ema_50d none gamma=0.5 threshold=0.2 |
| sb_stochrsi | +70.0% | 15.8% | 214 | -17.8% | 19.7% | 77 | 0.64 | trail_1.0 stop=0.5 fade ema_50d none period=14 zone_edge=10.0 |
| sb_supertrend | +132.6% | 13.8% | 211 | -23.4% | 23.4% | 78 | 0.54 | trail_1.0 stop=0.5 follow ema_50d none atr_period=7 mult=1.5 |
| sb_trend_pullback | +116.0% | 17.9% | 203 | -32.1% | 33.0% | 74 | 0.38 | days_10 stop=0.5 fade none none regime_filter=supertrend trend_bars=100 trigger=rsi2 |

### USDJPY 1d_swingbar: 2/53 passed IS, 1/2 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_vwap_band | +66.2% | 19.7% | 186 | +10.8% | 14.7% | 72 | 1.20 | trail_0.5 stop=0.5 follow none calm k=1.0 mode=break |
| sb_camarilla | +60.7% | 19.4% | 289 | -30.6% | 36.0% | 102 | 0.62 | trail_0.5 stop=0.5 follow none none level=4 |

### USDJPY 60m_swingbar: 53/53 passed IS, 11/53 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_camarilla | +285.7% | 17.6% | 712 | +20.3% | 10.1% | 239 | 1.20 | trail_0.5 stop=0.5 follow none none level=4 |
| sb_eom | +149.5% | 18.3% | 428 | +15.0% | 10.0% | 167 | 1.20 | trail_0.5 stop=0.5 fade ema_50d calm period=14 |
| sb_rvi | +31.0% | 13.6% | 288 | +14.7% | 6.9% | 108 | 1.34 | trail_1.0 stop=1.0 follow none calm period=10 |
| sb_tsi | +111.5% | 10.8% | 572 | +8.7% | 10.3% | 218 | 1.14 | trail_0.5 stop=1.0 follow none calm period=13 |
| sb_stochrsi | +164.7% | 16.5% | 960 | +7.2% | 9.2% | 317 | 1.09 | trail_0.5 stop=1.0 fade none none period=14 zone_edge=10.0 |
| sb_klinger | +48.3% | 18.7% | 269 | +6.8% | 9.5% | 101 | 1.19 | trail_1.0 stop=1.0 follow ema_50d calm scale=1.0 |
| sb_ma_zoo | +275.2% | 17.7% | 957 | +5.0% | 24.7% | 328 | 1.04 | trail_0.5 stop=0.5 fade none none ma=mcginley period=10 |
| sb_dpo | +264.3% | 15.5% | 444 | +3.7% | 15.9% | 151 | 1.04 | trail_1.0 stop=0.5 follow none none period=20 threshold_z=1.5 |
| sb_stoch | +178.5% | 11.8% | 979 | +3.5% | 9.4% | 321 | 1.05 | trail_0.5 stop=1.0 fade none none period=21 zone_edge=20.0 |
| sb_trend_pullback | +188.8% | 11.3% | 330 | +1.2% | 16.6% | 115 | 1.02 | trail_1.0 stop=0.5 follow none none regime_filter=supertrend trend_bars=100 trigger=cci |
| sb_elder_ray | +89.0% | 9.3% | 483 | +1.0% | 10.9% | 192 | 1.02 | trail_0.5 stop=1.0 fade none calm period=13 |
| sb_supertrend | +169.8% | 13.9% | 363 | -0.1% | 8.5% | 110 | 1.00 | trail_0.5 stop=0.5 follow ema_50d none atr_period=10 mult=3.0 |
| sb_dual_osc | +399.5% | 19.0% | 1009 | -0.8% | 16.2% | 338 | 0.99 | trail_0.5 stop=0.5 fade none none pair=['stoch', 'cci'] window=3 |
| sb_force | +32.1% | 14.2% | 460 | -1.1% | 11.2% | 179 | 0.98 | trail_0.5 stop=1.0 follow ema_50d calm period=13 |
| sb_heikin | +153.1% | 17.4% | 1050 | -1.6% | 8.9% | 336 | 0.98 | trail_0.5 stop=1.0 fade none none run=2 |
| sb_roc | +465.5% | 15.6% | 622 | -2.8% | 19.4% | 211 | 0.96 | trail_0.5 stop=0.5 follow ema_50d none lookback=12 threshold_atr=2.0 |
| sb_vwap_band | +415.3% | 18.5% | 1091 | -4.0% | 17.0% | 367 | 0.97 | trail_0.5 stop=0.5 follow none none k=1.5 mode=break |
| sb_ema_cross | +181.7% | 16.0% | 686 | -4.1% | 11.9% | 230 | 0.95 | trail_0.5 stop=0.5 follow none none fast_bars=9 slow_bars=21 |
| sb_schaff | +166.6% | 10.3% | 239 | -4.3% | 17.0% | 104 | 0.91 | trail_1.0 stop=1.0 follow none calm cycle=10 |
| sb_aroon | +312.0% | 17.3% | 710 | -5.5% | 20.6% | 240 | 0.94 | trail_0.5 stop=0.5 follow none none period=25 threshold=70.0 |
| sb_mfi | +155.1% | 18.7% | 621 | -5.5% | 9.9% | 207 | 0.88 | trail_0.5 stop=1.0 fade ema_50d none period=7 threshold=80.0 |
| sb_adx | +175.9% | 16.5% | 366 | -6.3% | 19.1% | 118 | 0.91 | trail_1.0 stop=0.5 fade none none floor=20.0 period=14 |
| sb_mass | +108.5% | 13.0% | 304 | -6.5% | 12.2% | 84 | 0.79 | trail_1.0 stop=1.0 fade none none sum_bars=15 |
| sb_coppock | +104.3% | 20.0% | 773 | -6.6% | 9.1% | 242 | 0.90 | trail_0.5 stop=1.0 fade ema_50d none scale=0.5 |
| sb_chaikin | +200.4% | 17.0% | 508 | -6.7% | 24.8% | 198 | 0.93 | trail_0.5 stop=0.5 fade none calm scale=1 |
| sb_donchian | +380.1% | 18.8% | 867 | -7.9% | 18.1% | 296 | 0.94 | trail_0.5 stop=0.5 follow none none period=20 |
| sb_chop | +288.6% | 15.1% | 267 | -8.7% | 22.1% | 92 | 0.84 | trail_1.0 stop=0.5 follow ema_50d none period=14 threshold=38.2 |
| sb_laguerre | +78.2% | 18.9% | 685 | -8.8% | 12.1% | 234 | 0.85 | trail_0.5 stop=1.0 fade ema_50d none gamma=0.5 threshold=0.2 |
| sb_triple_screen | +126.2% | 12.9% | 302 | -9.1% | 16.1% | 100 | 0.80 | trail_0.5 stop=0.5 fade ema_50d none multiple=6 osc=stoch |
| sb_keltner | +310.5% | 14.3% | 522 | -9.2% | 17.9% | 166 | 0.86 | trail_0.5 stop=0.5 follow ema_50d none k=1.5 period=50 |
| sb_vortex | +112.3% | 19.9% | 282 | -9.2% | 23.3% | 115 | 0.87 | trail_1.0 stop=0.5 fade none calm period=21 |
| sb_linreg_channel | +186.9% | 14.4% | 238 | -9.3% | 11.3% | 75 | 0.81 | trail_1.0 stop=0.5 follow ema_50d none k=2.0 period=100 |
| sb_ichimoku | +288.3% | 13.8% | 588 | -9.4% | 19.1% | 201 | 0.88 | trail_0.5 stop=0.5 follow ema_50d none mode=cloud_break scale=0.5 |
| sb_awesome | +170.6% | 13.6% | 482 | -9.5% | 19.6% | 159 | 0.85 | trail_0.5 stop=0.5 follow ema_50d none ao_set=[5, 34] mode=ao |
| sb_cci | +215.7% | 14.2% | 405 | -9.7% | 16.4% | 140 | 0.83 | trail_0.5 stop=0.5 fade ema_50d none period=10 threshold=200.0 |
| sb_vote | +362.8% | 14.7% | 590 | -10.0% | 24.2% | 195 | 0.86 | trail_0.5 stop=0.5 follow ema_50d none votes=9 |
| sb_bollinger | +353.7% | 13.5% | 468 | -10.3% | 15.4% | 163 | 0.84 | trail_0.5 stop=0.5 follow ema_50d none k=1.5 mode=break period=50 |
| sb_zscore | +353.7% | 13.5% | 468 | -10.3% | 15.4% | 163 | 0.84 | trail_0.5 stop=0.5 follow ema_50d none period=50 threshold_z=1.5 |
| sb_psar | +250.7% | 19.6% | 758 | -10.7% | 22.6% | 256 | 0.91 | trail_0.5 stop=0.5 fade ema_50d none step=0.02 |
| sb_squeeze | +332.8% | 16.0% | 788 | -10.9% | 21.2% | 276 | 0.92 | trail_0.5 stop=0.5 follow none none kc=1.0 period=10 |
| sb_ultimate | +438.6% | 18.3% | 745 | -11.6% | 16.8% | 259 | 0.89 | trail_0.5 stop=0.5 fade none none base=4 threshold=30.0 |
| sb_rsi | +179.6% | 16.6% | 1040 | -13.3% | 15.0% | 342 | 0.83 | trail_0.5 stop=1.0 fade none none period=2 threshold=90.0 |
| sb_bop | +383.5% | 14.1% | 661 | -14.7% | 16.0% | 235 | 0.85 | trail_0.5 stop=0.5 follow none none period=28 threshold=0.1 |
| sb_kst | +314.2% | 18.7% | 813 | -15.3% | 24.3% | 268 | 0.88 | trail_0.5 stop=0.5 fade ema_50d none scale=0.5 |
| sb_obv_ema | +78.0% | 12.0% | 349 | -16.9% | 17.8% | 144 | 0.79 | trail_1.0 stop=0.5 fade none calm period=10 |
| sb_cmo | +259.7% | 12.6% | 445 | -17.6% | 26.6% | 149 | 0.71 | trail_0.5 stop=0.5 fade ema_50d none period=14 threshold=50.0 |
| sb_alligator | +134.2% | 16.7% | 515 | -20.2% | 30.5% | 174 | 0.73 | trail_0.5 stop=0.5 fade ema_50d none scale=1.0 |
| sb_crsi | +221.3% | 19.7% | 690 | -21.4% | 24.6% | 206 | 0.76 | trail_0.5 stop=0.5 fade none none threshold=10.0 |
| sb_trix | +197.8% | 16.7% | 492 | -22.2% | 27.2% | 157 | 0.66 | trail_0.5 stop=0.5 follow ema_50d none line=zero period=9 |
| sb_macd | +190.5% | 11.1% | 463 | -24.2% | 26.4% | 159 | 0.66 | trail_0.5 stop=0.5 follow ema_50d none line=zero macd_set=[5, 35, 5] |
| sb_williams | +301.4% | 19.4% | 578 | -24.9% | 29.5% | 199 | 0.77 | trail_1.0 stop=0.5 fade none none period=7 zone_edge=10.0 |
| sb_candle | +304.6% | 16.8% | 543 | -27.5% | 29.5% | 183 | 0.73 | days_2 stop=0.5 fade none none context_bars=10 pattern=engulfing |
| sb_volume_breakout | +210.0% | 19.8% | 322 | -30.8% | 32.0% | 114 | 0.53 | trail_1.0 stop=0.5 follow ema_50d none period=20 surge=1.5 |

### EURJPY 240m_swingbar: 42/53 passed IS, 5/42 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_stoch | +127.5% | 13.1% | 565 | +14.4% | 24.1% | 177 | 1.10 | rr_3 stop=0.5 fade none none period=5 zone_edge=30.0 |
| sb_keltner | +18.1% | 14.6% | 289 | +10.2% | 15.4% | 90 | 1.18 | trail_1.0 stop=0.5 fade none none k=1.5 period=50 |
| sb_ichimoku | +44.0% | 14.0% | 199 | +6.3% | 9.8% | 70 | 1.19 | trail_1.0 stop=1.0 fade none none mode=tk_cross scale=1.0 |
| sb_adx | +28.3% | 10.5% | 254 | +2.1% | 4.8% | 93 | 1.10 | trail_0.5 stop=1.5 fade none calm floor=20.0 period=7 |
| sb_rsi | +22.1% | 10.4% | 303 | +1.1% | 7.6% | 106 | 1.04 | trail_0.5 stop=1.5 fade ema_50d calm period=2 threshold=80.0 |
| sb_cci | +49.1% | 14.9% | 217 | -0.7% | 8.5% | 60 | 0.97 | trail_0.5 stop=0.5 fade none none period=14 threshold=200.0 |
| sb_vortex | +172.6% | 18.1% | 308 | -0.8% | 18.7% | 107 | 0.99 | trail_1.0 stop=0.5 fade none none period=14 |
| sb_trend_pullback | +55.4% | 19.6% | 243 | -1.1% | 16.6% | 86 | 0.98 | trail_1.0 stop=0.5 fade none none regime_filter=supertrend trend_bars=100 trigger=stoch |
| sb_supertrend | +30.6% | 13.1% | 224 | -3.4% | 15.0% | 77 | 0.91 | trail_1.0 stop=1.0 fade none none atr_period=7 mult=2.0 |
| sb_williams | +60.2% | 18.2% | 316 | -3.9% | 23.3% | 105 | 0.95 | trail_1.0 stop=0.5 fade ema_50d none period=7 zone_edge=20.0 |
| sb_obv_ema | +49.9% | 10.2% | 178 | -6.6% | 21.9% | 65 | 0.84 | trail_1.0 stop=1.0 follow ema_50d none period=50 |
| sb_roc | +43.1% | 13.3% | 227 | -8.7% | 23.1% | 81 | 0.85 | trail_1.0 stop=0.5 follow ema_50d none lookback=24 threshold_atr=1.0 |
| sb_dual_osc | +44.1% | 19.4% | 260 | -8.9% | 11.9% | 88 | 0.80 | trail_0.5 stop=0.5 fade ema_50d none pair=['stoch', 'bb'] window=3 |
| sb_squeeze | +47.3% | 11.7% | 175 | -9.2% | 11.6% | 59 | 0.76 | trail_1.0 stop=0.5 follow ema_50d none kc=1.0 period=10 |
| sb_mfi | +77.6% | 13.0% | 270 | -10.5% | 17.6% | 93 | 0.81 | days_2 stop=0.5 fade ema_50d none period=7 threshold=80.0 |
| sb_chaikin | +68.6% | 18.4% | 248 | -10.7% | 15.8% | 80 | 0.80 | trail_1.0 stop=0.5 follow none none scale=2 |
| sb_kst | +108.7% | 17.9% | 181 | -11.2% | 14.7% | 58 | 0.67 | trail_1.0 stop=0.5 fade ema_50d none scale=1.0 |
| sb_volume_breakout | +12.9% | 15.0% | 176 | -11.5% | 15.2% | 52 | 0.57 | trail_0.5 stop=0.5 follow none calm period=10 surge=1.5 |
| sb_force | +38.5% | 15.8% | 314 | -11.6% | 14.7% | 111 | 0.73 | trail_0.5 stop=1.0 fade none calm period=13 |
| sb_awesome | +37.5% | 6.0% | 170 | -12.1% | 13.2% | 68 | 0.48 | trail_1.0 stop=1.5 fade none calm ao_set=[5, 34] mode=ac |
| sb_schaff | +112.1% | 18.0% | 434 | -13.7% | 17.5% | 160 | 0.82 | trail_0.5 stop=0.5 fade none none cycle=5 |
| sb_alligator | +109.3% | 13.5% | 237 | -14.6% | 21.6% | 89 | 0.73 | trail_1.0 stop=0.5 follow ema_50d none scale=1.0 |
| sb_eom | +15.2% | 12.2% | 173 | -14.6% | 19.1% | 60 | 0.62 | trail_1.0 stop=1.0 fade none calm period=14 |
| sb_macd | +68.4% | 17.9% | 425 | -14.9% | 21.7% | 135 | 0.75 | trail_0.5 stop=0.5 fade none none line=signal macd_set=[12, 26, 9] |
| sb_heikin | +80.4% | 16.5% | 523 | -15.7% | 22.4% | 183 | 0.84 | trail_0.5 stop=0.5 follow ema_50d none run=2 |
| sb_vote | +62.8% | 12.5% | 298 | -15.9% | 20.6% | 108 | 0.70 | trail_0.5 stop=0.5 follow ema_50d none votes=10 |
| sb_coppock | +84.8% | 9.3% | 283 | -16.3% | 22.8% | 96 | 0.67 | trail_1.0 stop=1.0 fade none none scale=0.5 |
| sb_zscore | +32.0% | 6.4% | 304 | -16.4% | 20.2% | 122 | 0.60 | trail_0.5 stop=1.0 follow ema_50d none period=20 threshold_z=1.5 |
| sb_donchian | +62.9% | 15.7% | 279 | -16.5% | 16.6% | 99 | 0.65 | trail_0.5 stop=0.5 follow ema_50d none period=20 |
| sb_bop | +49.1% | 16.1% | 181 | -17.3% | 17.4% | 61 | 0.54 | trail_1.0 stop=0.5 follow ema_50d none period=28 threshold=0.1 |
| sb_tsi | +160.5% | 8.8% | 259 | -17.3% | 19.3% | 85 | 0.60 | trail_1.0 stop=1.0 fade none none period=25 |
| sb_elder_ray | +26.7% | 9.5% | 190 | -18.1% | 19.8% | 56 | 0.53 | trail_1.0 stop=1.0 follow ema_50d none period=13 |
| sb_psar | +95.5% | 8.8% | 264 | -18.5% | 25.8% | 86 | 0.61 | trail_1.0 stop=1.0 fade none none step=0.01 |
| sb_ma_zoo | +101.9% | 16.1% | 312 | -19.9% | 22.2% | 115 | 0.61 | trail_0.5 stop=0.5 fade none none ma=vidya period=20 |
| sb_aroon | +74.2% | 16.1% | 218 | -20.9% | 23.1% | 77 | 0.60 | trail_1.0 stop=0.5 follow ema_50d none period=14 threshold=70.0 |
| sb_triple_screen | +73.6% | 16.8% | 268 | -21.8% | 25.6% | 96 | 0.67 | trail_1.0 stop=0.5 follow ema_50d none multiple=4 osc=force |
| sb_candle | +147.2% | 18.0% | 446 | -22.1% | 25.3% | 148 | 0.70 | trail_0.5 stop=0.5 fade none none context_bars=5 pattern=harami |
| sb_laguerre | +59.0% | 8.9% | 264 | -22.5% | 25.1% | 90 | 0.56 | trail_1.0 stop=1.0 fade none none gamma=0.65 threshold=0.2 |
| sb_cmo | +113.9% | 13.7% | 257 | -24.1% | 26.1% | 87 | 0.62 | days_5 stop=0.5 fade none none period=20 threshold=30.0 |
| sb_bollinger | +35.5% | 12.8% | 214 | -24.5% | 24.5% | 69 | 0.59 | rr_2 stop=1.0 fade none none k=2.0 mode=reenter period=10 |
| sb_stochrsi | +153.2% | 18.5% | 421 | -32.9% | 34.9% | 138 | 0.59 | trail_1.0 stop=0.5 fade none none period=7 zone_edge=10.0 |
| sb_trix | +181.7% | 13.4% | 314 | -35.1% | 37.5% | 104 | 0.41 | trail_1.0 stop=0.5 fade none none line=signal period=9 |

### EURJPY 1d_swingbar: 4/53 passed IS, 1/4 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_dual_osc | +22.5% | 13.1% | 149 | +3.6% | 11.8% | 51 | 1.12 | trail_0.5 stop=1.0 follow none calm pair=['stoch', 'vwap'] window=1 |
| sb_camarilla | +17.5% | 19.1% | 237 | -3.5% | 12.0% | 62 | 0.94 | rr_2 stop=0.5 follow none calm level=4 |
| sb_stoch | +79.7% | 14.7% | 151 | -13.6% | 16.1% | 46 | 0.68 | days_5 stop=0.5 follow none calm period=5 zone_edge=30.0 |
| sb_vwap_band | +54.0% | 13.3% | 183 | -14.9% | 17.1% | 59 | 0.57 | trail_0.5 stop=1.0 follow none calm k=1.0 mode=break |
