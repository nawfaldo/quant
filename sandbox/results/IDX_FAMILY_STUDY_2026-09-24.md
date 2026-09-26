# IDX family study, 2026-09-24

Daily bars, Stockbit costs, half/half split per stock. 28 of 138 stock-family pairs selected a cell in-sample; 13 of 28 made money on the holdout.

Refused (split/rights-adjusted history): akra, isat, mapi, mdka, medc, unvr

| stock | family | params | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | OOS PF | OOS avg trade |
|---|---|---|---|---|---|---|---|---|---|---|
| admr | rsi2 | exit=trail_2 level=10 regime=none rsi_len=2 stop_atr=3.0 trend=none | +9.2% | 9.2% | 22 | +0.4% | 7.9% | 19 | 1.03 | +0.90% |
| adro | dip | drop_atr=1.5 drop_days=3 exit=sma5 regime=none stop_atr=3.0 trend=sma50 | +4.5% | 1.1% | 13 | +5.6% | 2.1% | 14 | 9.59 | +1.53% |
| adro | dividend | days_before=15 exit=ex_5 regime=none stop_atr=2.0 | +11.4% | 6.0% | 8 | +8.7% | 8.4% | 9 | 2.14 | +3.13% |
| adro | ma_trend | exit=channel_20 pair=10/50 regime=none stop_atr=2.0 | +15.9% | 13.9% | 13 | +13.7% | 7.3% | 11 | 2.99 | +3.37% |
| adro | rsi2 | exit=days_10 level=20 regime=market rsi_len=3 stop_atr=2.0 trend=none | +24.8% | 8.1% | 26 | +9.5% | 9.4% | 28 | 1.56 | +1.12% |
| antm | donchian | channel=20 exit=trail_2 regime=market stop_atr=3.0 | +7.4% | 9.3% | 14 | -0.4% | 7.8% | 12 | 0.96 | +1.06% |
| antm | ma_trend | exit=trail_3 pair=10/50 regime=none stop_atr=3.0 | +13.3% | 16.5% | 13 | -12.4% | 14.8% | 14 | 0.29 | -4.74% |
| cpin | dip | drop_atr=1.5 drop_days=5 exit=sma5 regime=none stop_atr=2.0 trend=none | +9.5% | 8.5% | 51 | -25.7% | 27.3% | 56 | 0.29 | -1.31% |
| cpin | rsi2 | exit=sma5 level=20 regime=none rsi_len=3 stop_atr=2.0 trend=none | +6.3% | 8.2% | 54 | -27.6% | 29.9% | 62 | 0.31 | -1.24% |
| essa | rsi2 | exit=trail_2 level=20 regime=none rsi_len=2 stop_atr=2.0 trend=sma200 | +42.9% | 9.8% | 17 | -4.9% | 8.2% | 18 | 0.67 | -1.32% |
| excl | dip | drop_atr=1.5 drop_days=3 exit=days_5 regime=market stop_atr=2.0 trend=none | +12.7% | 4.6% | 22 | -2.5% | 7.2% | 26 | 0.83 | -0.59% |
| excl | rsi2 | exit=trail_2 level=5 regime=none rsi_len=2 stop_atr=2.0 trend=none | +22.2% | 9.6% | 29 | -8.4% | 13.5% | 28 | 0.67 | -1.47% |
| hrta | ma_trend | exit=trail_2 pair=10/50 regime=none stop_atr=5.0 | +0.8% | 5.3% | 13 | +0.4% | 3.4% | 12 | 1.08 | +2.38% |
| icbp | dip | drop_atr=1.5 drop_days=3 exit=sma5 regime=none stop_atr=3.0 trend=none | +6.1% | 3.1% | 42 | +0.6% | 4.7% | 45 | 1.05 | +0.12% |
| icbp | rsi2 | exit=sma5 level=10 regime=none rsi_len=3 stop_atr=3.0 trend=none | +10.2% | 1.9% | 23 | +2.5% | 3.6% | 25 | 1.44 | +0.66% |
| indf | dip | drop_atr=3.5 drop_days=10 exit=sma5 regime=none stop_atr=5.0 trend=none | +3.2% | 0.5% | 16 | +0.0% | 1.0% | 12 | 1.01 | +0.00% |
| indy | donchian | channel=20 exit=trail_2 regime=none stop_atr=3.0 | +9.6% | 7.4% | 13 | -8.2% | 14.9% | 19 | 0.49 | -1.81% |
| indy | ma_trend | exit=trail_2 pair=10/50 regime=none stop_atr=5.0 | +6.3% | 5.1% | 14 | -1.5% | 6.0% | 13 | 0.71 | +0.00% |
| inkp | rsi2 | exit=trail_2 level=5 regime=none rsi_len=2 stop_atr=5.0 trend=none | +7.6% | 3.3% | 18 | -4.1% | 7.8% | 26 | 0.56 | -1.45% |
| itmg | dip | drop_atr=1.5 drop_days=3 exit=trail_2 regime=market stop_atr=2.0 trend=none | +48.8% | 7.7% | 16 | +12.9% | 12.2% | 20 | 1.93 | +1.03% |
| jpfa | donchian | channel=20 exit=trail_2 regime=none stop_atr=3.0 | +2.5% | 11.3% | 17 | -2.8% | 7.6% | 17 | 0.76 | -0.85% |
| jpfa | rsi2 | exit=trail_2 level=20 regime=none rsi_len=2 stop_atr=5.0 trend=none | +1.0% | 6.6% | 45 | +1.4% | 8.5% | 46 | 1.09 | +0.93% |
| klbf | dip | drop_atr=2.5 drop_days=10 exit=sma5 regime=none stop_atr=3.0 trend=none | +3.6% | 5.4% | 37 | -0.6% | 4.8% | 28 | 0.93 | -0.10% |
| klbf | rsi2 | exit=sma5 level=5 regime=market rsi_len=2 stop_atr=5.0 trend=none | +3.8% | 2.3% | 16 | +1.6% | 2.1% | 23 | 1.43 | +0.35% |
| ptba | dip | drop_atr=1.5 drop_days=3 exit=days_10 regime=market stop_atr=5.0 trend=none | +0.9% | 5.0% | 16 | +1.3% | 4.3% | 21 | 1.36 | +0.21% |
| tlkm | dip | drop_atr=2.5 drop_days=10 exit=trail_2 regime=none stop_atr=3.0 trend=none | +13.6% | 7.2% | 29 | -9.9% | 13.4% | 34 | 0.53 | -0.33% |
| tlkm | rsi2 | exit=sma5 level=20 regime=market rsi_len=3 stop_atr=2.0 trend=none | +15.5% | 2.2% | 31 | -23.7% | 25.7% | 41 | 0.30 | -1.53% |
| untr | dividend | days_before=5 exit=ex regime=none stop_atr=2.0 | +8.0% | 1.6% | 9 | -1.2% | 5.8% | 7 | 0.37 | -0.72% |
