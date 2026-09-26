
### HK50 240m_swingbar: 52/53 passed IS, 48/52 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_rsi | +192.5% | 10.0% | 240 | +209.2% | 19.7% | 207 | 1.99 | trail_0.5 stop=0.5 fade none none period=2 threshold=70.0 |
| sb_vwap_band | +494.7% | 9.3% | 245 | +207.3% | 13.0% | 212 | 1.97 | trail_0.5 stop=0.5 follow none none k=1.0 mode=break |
| sb_rvi | +202.0% | 10.9% | 204 | +177.6% | 12.7% | 162 | 1.95 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_obv_ema | +250.2% | 14.0% | 187 | +172.3% | 11.3% | 155 | 2.30 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_roc | +205.2% | 12.2% | 210 | +172.2% | 8.0% | 170 | 2.14 | trail_0.5 stop=0.5 follow none none lookback=6 threshold_atr=1.0 |
| sb_macd | +185.5% | 12.2% | 182 | +159.7% | 12.3% | 146 | 2.02 | trail_0.5 stop=0.5 follow none none line=signal macd_set=[3, 10, 16] |
| sb_tsi | +196.6% | 10.3% | 180 | +154.3% | 10.8% | 154 | 2.00 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_heikin | +166.4% | 19.8% | 223 | +149.6% | 11.4% | 170 | 1.88 | trail_0.5 stop=0.5 follow none none run=2 |
| sb_camarilla | +256.7% | 8.2% | 229 | +146.8% | 18.8% | 169 | 1.78 | trail_0.5 stop=0.5 follow none none level=3 |
| sb_psar | +132.1% | 19.6% | 202 | +134.6% | 17.1% | 163 | 1.71 | trail_0.5 stop=0.5 follow none none step=0.04 |
| sb_vortex | +141.9% | 13.7% | 183 | +133.0% | 7.8% | 159 | 1.93 | trail_0.5 stop=0.5 follow none none period=7 |
| sb_vote | +171.7% | 5.6% | 90 | +113.7% | 10.0% | 76 | 2.62 | trail_0.5 stop=0.5 follow none calm votes=8 |
| sb_klinger | +282.1% | 10.4% | 213 | +107.0% | 14.8% | 175 | 1.70 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_ma_zoo | +358.4% | 8.8% | 235 | +97.8% | 11.2% | 210 | 1.52 | trail_0.5 stop=0.5 follow none none ma=zlema period=10 |
| sb_donchian | +107.8% | 11.4% | 166 | +96.6% | 7.2% | 126 | 1.95 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_williams | +180.3% | 11.7% | 143 | +91.9% | 13.7% | 109 | 1.74 | trail_1.0 stop=0.5 fade none none period=7 zone_edge=20.0 |
| sb_elder_ray | +126.9% | 6.3% | 83 | +90.2% | 11.0% | 71 | 2.31 | trail_1.0 stop=0.5 follow none none period=13 |
| sb_cmo | +105.2% | 10.2% | 138 | +86.1% | 13.8% | 96 | 2.23 | trail_0.5 stop=0.5 fade none none period=9 threshold=50.0 |
| sb_ichimoku | +147.0% | 12.0% | 168 | +81.9% | 9.7% | 132 | 1.68 | trail_0.5 stop=0.5 follow none none mode=kijun_cross scale=0.5 |
| sb_stochrsi | +194.7% | 12.3% | 180 | +81.4% | 15.0% | 152 | 1.69 | trail_0.5 stop=0.5 follow none none period=7 zone_edge=20.0 |
| sb_cci | +150.9% | 9.2% | 183 | +81.4% | 13.2% | 161 | 1.57 | trail_0.5 stop=0.5 follow none none period=10 threshold=100.0 |
| sb_force | +161.1% | 8.5% | 168 | +79.9% | 9.7% | 138 | 1.71 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_coppock | +127.9% | 7.6% | 165 | +77.8% | 9.6% | 128 | 1.74 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_bop | +126.5% | 9.5% | 103 | +77.1% | 13.2% | 85 | 2.05 | trail_1.0 stop=0.5 follow none none period=14 threshold=0.1 |
| sb_dual_osc | +138.2% | 13.1% | 179 | +69.3% | 14.9% | 137 | 1.73 | trail_0.5 stop=0.5 fade none none pair=['cci', 'vwap'] window=3 |
| sb_schaff | +97.2% | 7.7% | 95 | +67.3% | 4.7% | 77 | 2.55 | trail_0.5 stop=0.5 fade none none cycle=20 |
| sb_bollinger | +181.7% | 7.3% | 137 | +66.8% | 13.9% | 114 | 1.74 | trail_0.5 stop=0.5 follow none none k=1.5 mode=break period=20 |
| sb_zscore | +181.7% | 7.3% | 137 | +66.8% | 13.9% | 114 | 1.74 | trail_0.5 stop=0.5 follow none none period=20 threshold_z=1.5 |
| sb_alligator | +139.7% | 11.0% | 133 | +66.4% | 21.5% | 103 | 1.60 | trail_1.0 stop=0.5 follow none none scale=0.5 |
| sb_stoch | +193.7% | 10.8% | 146 | +63.0% | 17.8% | 122 | 1.46 | trail_1.0 stop=0.5 fade none none period=9 zone_edge=30.0 |
| sb_crsi | +168.1% | 13.8% | 114 | +62.3% | 21.8% | 89 | 1.67 | trail_1.0 stop=0.5 fade none none threshold=15.0 |
| sb_aroon | +82.7% | 6.7% | 116 | +60.7% | 8.5% | 98 | 1.80 | trail_0.5 stop=0.5 follow none none period=14 threshold=50.0 |
| sb_chaikin | +141.2% | 5.7% | 144 | +53.0% | 9.6% | 119 | 1.61 | trail_0.5 stop=0.5 follow none none scale=1 |
| sb_trix | +98.4% | 7.1% | 99 | +52.9% | 8.0% | 83 | 1.84 | trail_0.5 stop=0.5 follow none none line=signal period=15 |
| sb_chop | +80.0% | 7.9% | 102 | +44.6% | 12.6% | 95 | 1.58 | trail_0.5 stop=0.5 follow none none period=14 threshold=50.0 |
| sb_mfi | +107.3% | 9.2% | 74 | +43.5% | 14.6% | 57 | 1.77 | trail_1.0 stop=0.5 fade none calm period=7 threshold=80.0 |
| sb_awesome | +189.5% | 13.2% | 112 | +41.4% | 24.2% | 94 | 1.43 | trail_1.0 stop=0.5 fade none none ao_set=[5, 34] mode=ac |
| sb_supertrend | +128.0% | 12.3% | 140 | +39.0% | 13.4% | 109 | 1.41 | trail_0.5 stop=0.5 follow none none atr_period=10 mult=1.5 |
| sb_triple_screen | +200.2% | 8.4% | 104 | +37.1% | 15.5% | 83 | 1.52 | trail_1.0 stop=0.5 follow none none multiple=4 osc=force |
| sb_linreg_channel | +103.5% | 9.4% | 96 | +34.5% | 20.3% | 70 | 1.61 | trail_0.5 stop=0.5 fade none none k=2.0 period=20 |
| sb_trend_pullback | +100.3% | 9.2% | 63 | +32.1% | 19.2% | 59 | 1.61 | trail_1.0 stop=0.5 follow none none regime_filter=supertrend trend_bars=200 trigger=stoch |
| sb_dpo | +61.6% | 8.1% | 111 | +23.5% | 8.0% | 79 | 1.43 | trail_0.5 stop=0.5 fade none none period=10 threshold_z=1.5 |
| sb_volume_breakout | +87.5% | 8.6% | 100 | +22.6% | 8.3% | 74 | 1.48 | trail_0.5 stop=0.5 follow none none period=10 surge=1.5 |
| sb_candle | +162.8% | 12.1% | 103 | +20.6% | 24.8% | 83 | 1.27 | trail_1.0 stop=0.5 follow none none context_bars=5 pattern=three |
| sb_ema_cross | +105.6% | 8.5% | 61 | +19.3% | 13.1% | 61 | 1.34 | trail_1.0 stop=0.5 follow none none fast_bars=5 slow_bars=55 |
| sb_squeeze | +112.1% | 13.4% | 102 | +12.8% | 22.4% | 82 | 1.16 | trail_1.0 stop=0.5 follow none none kc=1.5 period=10 |
| sb_keltner | +86.7% | 10.3% | 84 | +7.3% | 7.6% | 55 | 1.19 | trail_0.5 stop=0.5 follow none none k=1.5 period=10 |
| sb_eom | +96.6% | 8.3% | 78 | +3.1% | 23.4% | 64 | 1.05 | trail_0.5 stop=0.5 fade none calm period=14 |
| sb_ultimate | +48.1% | 11.1% | 55 | -2.2% | 10.2% | 46 | 0.93 | trail_0.5 stop=0.5 fade none none base=4 threshold=25.0 |
| sb_adx | +59.6% | 7.2% | 91 | -3.7% | 22.0% | 73 | 0.95 | trail_0.5 stop=0.5 fade none calm floor=20.0 period=7 |
| sb_laguerre | +123.7% | 13.4% | 62 | -28.4% | 37.3% | 73 | 0.60 | days_10 stop=0.5 follow none none gamma=0.5 threshold=0.2 |
| sb_kst | +145.7% | 12.3% | 112 | -29.1% | 46.3% | 93 | 0.71 | days_5 stop=0.5 fade none none scale=0.5 |

### HK50 1d_swingbar: 1/53 passed IS, 0/1 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_vwap_band | +74.9% | 11.1% | 53 | -52.3% | 54.4% | 96 | 0.63 | trail_0.5 stop=0.5 fade none none k=1.5 mode=break |

### HK50 60m_swingbar: 52/53 passed IS, 50/52 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_vwap_band | +171.2% | 7.4% | 152 | +251.3% | 7.2% | 262 | 1.86 | trail_0.5 stop=0.5 follow none none k=1.0 mode=break |
| sb_klinger | +130.9% | 6.8% | 98 | +243.3% | 10.0% | 146 | 2.86 | trail_0.5 stop=0.5 follow none calm scale=1.0 |
| sb_rvi | +106.4% | 10.7% | 155 | +221.5% | 11.4% | 246 | 1.92 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_tsi | +117.4% | 8.4% | 134 | +213.3% | 11.8% | 224 | 1.94 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_kst | +59.2% | 9.6% | 146 | +205.2% | 12.4% | 235 | 1.88 | trail_0.5 stop=0.5 follow none none scale=0.5 |
| sb_ma_zoo | +145.7% | 6.5% | 138 | +187.1% | 9.7% | 231 | 1.85 | trail_0.5 stop=0.5 follow none none ma=dema period=20 |
| sb_obv_ema | +101.5% | 7.4% | 133 | +160.7% | 15.6% | 235 | 1.76 | trail_0.5 stop=0.5 follow none none period=10 |
| sb_cci | +107.9% | 6.4% | 147 | +156.3% | 10.8% | 255 | 1.68 | trail_0.5 stop=0.5 follow none none period=10 threshold=100.0 |
| sb_triple_screen | +92.8% | 8.6% | 115 | +156.2% | 9.3% | 220 | 1.78 | trail_0.5 stop=0.5 follow none none multiple=6 osc=force |
| sb_vote | +75.5% | 9.6% | 131 | +141.9% | 9.3% | 207 | 1.78 | trail_0.5 stop=0.5 follow none none votes=8 |
| sb_awesome | +94.6% | 11.8% | 155 | +141.1% | 17.8% | 273 | 1.48 | trail_0.5 stop=0.5 fade none none ao_set=[3, 10] mode=ac |
| sb_macd | +117.1% | 9.5% | 120 | +124.7% | 6.2% | 211 | 1.70 | trail_0.5 stop=0.5 follow none none line=zero macd_set=[3, 10, 16] |
| sb_trix | +92.7% | 13.3% | 117 | +119.3% | 12.5% | 178 | 1.71 | trail_0.5 stop=0.5 follow none none line=signal period=9 |
| sb_coppock | +74.8% | 9.9% | 130 | +118.8% | 14.5% | 218 | 1.64 | trail_0.5 stop=0.5 fade none none scale=0.5 |
| sb_candle | +80.0% | 6.5% | 95 | +118.1% | 13.9% | 188 | 1.87 | trail_0.5 stop=0.5 follow none none context_bars=5 pattern=hammer |
| sb_supertrend | +78.6% | 10.5% | 68 | +95.0% | 11.4% | 111 | 1.82 | trail_1.0 stop=0.5 follow none none atr_period=10 mult=1.5 |
| sb_camarilla | +109.6% | 7.9% | 125 | +90.2% | 15.4% | 219 | 1.42 | trail_0.5 stop=0.5 follow none none level=3 |
| sb_bop | +103.6% | 7.4% | 130 | +89.7% | 19.2% | 216 | 1.51 | trail_0.5 stop=0.5 fade none none period=14 threshold=0.1 |
| sb_crsi | +60.6% | 8.4% | 86 | +89.4% | 9.6% | 142 | 1.62 | days_2 stop=0.5 fade none none threshold=15.0 |
| sb_dual_osc | +63.0% | 6.3% | 144 | +88.2% | 13.1% | 251 | 1.41 | trail_0.5 stop=0.5 fade none none pair=['stoch', 'vwap'] window=3 |
| sb_keltner | +56.5% | 10.4% | 117 | +84.6% | 17.4% | 189 | 1.50 | trail_0.5 stop=0.5 follow none none k=1.5 period=20 |
| sb_volume_breakout | +86.9% | 7.4% | 121 | +77.8% | 14.3% | 196 | 1.48 | trail_0.5 stop=0.5 follow none none period=10 surge=1.5 |
| sb_elder_ray | +51.0% | 13.8% | 94 | +69.0% | 12.3% | 165 | 1.58 | trail_0.5 stop=0.5 follow none none period=13 |
| sb_linreg_channel | +38.6% | 9.3% | 96 | +68.6% | 11.7% | 165 | 1.51 | trail_0.5 stop=0.5 fade none none k=2.0 period=20 |
| sb_schaff | +63.2% | 15.4% | 87 | +66.2% | 15.6% | 140 | 1.42 | days_2 stop=0.5 fade none none cycle=5 |
| sb_dpo | +48.7% | 10.5% | 84 | +65.8% | 17.8% | 135 | 1.53 | trail_0.5 stop=0.5 follow none none period=20 threshold_z=1.5 |
| sb_stoch | +90.9% | 16.6% | 74 | +59.2% | 9.2% | 108 | 1.58 | days_2 stop=0.5 fade none calm period=14 zone_edge=20.0 |
| sb_ichimoku | +89.5% | 6.5% | 69 | +58.1% | 9.6% | 105 | 1.67 | trail_0.5 stop=0.5 fade none calm mode=kijun_cross scale=1.0 |
| sb_trend_pullback | +96.9% | 4.0% | 67 | +57.2% | 12.2% | 100 | 1.80 | trail_0.5 stop=0.5 follow none calm regime_filter=supertrend trend_bars=100 trigger=rsi2 |
| sb_chop | +69.9% | 5.4% | 86 | +53.9% | 8.0% | 121 | 1.64 | trail_0.5 stop=0.5 follow none none period=14 threshold=38.2 |
| sb_ema_cross | +55.4% | 8.1% | 54 | +50.1% | 12.6% | 95 | 1.54 | trail_1.0 stop=0.5 follow none none fast_bars=5 slow_bars=21 |
| sb_bollinger | +94.5% | 6.6% | 78 | +48.2% | 11.7% | 136 | 1.35 | trail_1.0 stop=0.5 follow none none k=1.5 mode=break period=10 |
| sb_zscore | +94.5% | 6.6% | 78 | +48.2% | 11.7% | 136 | 1.35 | trail_1.0 stop=0.5 follow none none period=10 threshold_z=1.5 |
| sb_heikin | +95.3% | 7.5% | 126 | +46.3% | 14.0% | 228 | 1.28 | trail_0.5 stop=0.5 follow none none run=5 |
| sb_aroon | +46.9% | 10.6% | 60 | +45.9% | 21.3% | 103 | 1.51 | trail_1.0 stop=0.5 fade none none period=25 threshold=50.0 |
| sb_laguerre | +98.3% | 4.4% | 106 | +45.1% | 13.1% | 190 | 1.33 | trail_0.5 stop=0.5 follow none none gamma=0.65 threshold=0.15 |
| sb_squeeze | +63.1% | 6.2% | 66 | +43.4% | 24.0% | 103 | 1.47 | trail_1.0 stop=0.5 fade none none kc=2.0 period=10 |
| sb_vortex | +95.1% | 6.1% | 59 | +34.9% | 13.1% | 105 | 1.34 | days_2 stop=0.5 fade none calm period=7 |
| sb_cmo | +100.3% | 5.6% | 120 | +34.7% | 14.9% | 208 | 1.21 | trail_0.5 stop=0.5 follow none none period=9 threshold=50.0 |
| sb_psar | +121.5% | 7.6% | 123 | +34.7% | 21.7% | 230 | 1.21 | trail_0.5 stop=0.5 fade none none step=0.02 |
| sb_stochrsi | +80.7% | 12.1% | 131 | +33.0% | 18.0% | 219 | 1.24 | trail_0.5 stop=0.5 follow none none period=14 zone_edge=10.0 |
| sb_roc | +89.8% | 7.2% | 114 | +32.4% | 15.3% | 208 | 1.23 | trail_0.5 stop=0.5 fade none none lookback=6 threshold_atr=2.0 |
| sb_eom | +55.2% | 6.1% | 51 | +31.4% | 13.5% | 88 | 1.49 | trail_0.5 stop=0.5 fade none calm period=28 |
| sb_chaikin | +55.8% | 6.6% | 80 | +29.1% | 11.1% | 139 | 1.32 | trail_0.5 stop=0.5 fade none none scale=2 |
| sb_adx | +60.5% | 8.8% | 70 | +28.2% | 11.7% | 118 | 1.32 | trail_0.5 stop=0.5 fade ema_50d none floor=25.0 period=7 |
| sb_ultimate | +47.5% | 7.7% | 106 | +27.5% | 10.9% | 170 | 1.28 | trail_0.5 stop=0.5 follow none none base=4 threshold=30.0 |
| sb_donchian | +81.7% | 10.8% | 95 | +11.8% | 26.7% | 178 | 1.06 | days_2 stop=0.5 follow none none period=10 |
| sb_force | +138.2% | 9.6% | 107 | +9.0% | 22.9% | 187 | 1.06 | days_2 stop=0.5 fade none none period=2 |
| sb_rsi | +110.5% | 15.4% | 74 | +8.0% | 22.1% | 124 | 1.07 | days_2 stop=0.5 fade none calm period=2 threshold=70.0 |
| sb_alligator | +111.6% | 6.8% | 60 | +5.8% | 22.5% | 106 | 1.05 | days_2 stop=0.5 follow none calm scale=0.5 |
| sb_mfi | +58.1% | 13.3% | 132 | -0.2% | 27.4% | 231 | 1.00 | trail_0.5 stop=0.5 follow none none period=7 threshold=80.0 |
| sb_williams | +72.5% | 11.4% | 98 | -0.5% | 26.9% | 168 | 1.00 | days_2 stop=0.5 fade none none period=14 zone_edge=20.0 |

### ETHUSD 240m_swingbar: 11/53 passed IS, 11/11 positive OOS

| family | IS | IS dd | IS n | OOS | OOS dd | OOS n | PF | settings |
|---|---|---|---|---|---|---|---|---|
| sb_bollinger | +279.9% | 15.9% | 152 | +118.4% | 13.9% | 45 | 2.38 | days_5 stop=0.5 follow ema_50d calm k=1.5 mode=break period=50 |
| sb_zscore | +279.9% | 15.9% | 152 | +118.4% | 13.9% | 45 | 2.38 | days_5 stop=0.5 follow ema_50d calm period=50 threshold_z=1.5 |
| sb_roc | +234.5% | 15.2% | 130 | +97.3% | 8.7% | 45 | 2.13 | days_5 stop=0.5 follow ema_50d calm lookback=24 threshold_atr=3.0 |
| sb_keltner | +167.4% | 15.9% | 217 | +48.3% | 15.8% | 60 | 1.69 | trail_0.5 stop=0.5 follow none calm k=2.0 period=50 |
| sb_ma_zoo | +118.3% | 19.8% | 201 | +35.7% | 11.3% | 59 | 1.61 | trail_0.5 stop=0.5 follow ema_50d calm ma=t3 period=20 |
| sb_vote | +41.5% | 11.3% | 190 | +26.2% | 6.4% | 53 | 2.44 | trail_0.5 stop=1.0 follow ema_50d calm votes=7 |
| sb_squeeze | +88.8% | 17.1% | 167 | +25.2% | 17.9% | 57 | 1.31 | days_2 stop=0.5 follow none calm kc=2.0 period=20 |
| sb_linreg_channel | +60.6% | 18.9% | 176 | +19.0% | 19.4% | 72 | 1.21 | days_5 stop=0.5 follow none none k=2.5 period=20 |
| sb_cmo | +82.1% | 17.6% | 153 | +14.9% | 6.6% | 49 | 1.47 | trail_0.5 stop=0.5 fade ema_50d none period=20 threshold=50.0 |
| sb_eom | +25.2% | 9.3% | 168 | +10.7% | 11.5% | 58 | 1.40 | trail_0.5 stop=1.0 follow ema_50d calm period=14 |
| sb_ichimoku | +152.3% | 16.0% | 173 | +1.1% | 11.2% | 52 | 1.02 | trail_0.5 stop=0.5 follow ema_50d calm mode=cloud_break scale=0.5 |
