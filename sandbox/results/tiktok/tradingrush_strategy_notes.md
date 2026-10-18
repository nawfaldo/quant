# Trading Rush (YouTube @TradingRush) — strategy notes (read 2026-10-03)

204 videos on the channel (2020-2026). The ~100 strategy tests and rule videos
were read from YouTube's English captions (captions fetched through the
player; the rest are commentary). Module: `sandbox/research/tt_tradingrush.py`,
group `tradingrush`, 31 families (`tr_`).

His frame for every test: 200 EMA picks the side; entry at the signal
candle's close; stop beyond the pullback/swing (or the indicator's line);
target 1.5x the stop (break-even 40%). First series: 100 trades on EURJPY 30m
in a "mostly good" market. Second series (2025): XAUUSD 30m, 100 trades in an
"extremely good" market (daily 9 EMA trend, hand-filtered) and 100 in an
"extremely bad" one. Win rates below are his, at 1.5R.

| family | videos | rule | his win rate (mostly good / ext. good / ext. bad) |
|---|---|---|---|
| tr_macd | 24, 204, 104, 149, 171 | MACD crosses signal below zero (long), 200 EMA; zero-lag variant; MACD+Stoch | 60-62 / 60 / 32; zero-lag 33; +Stoch 53 |
| tr_bb_breakout | 23, 125, 126, 142 | first close above upper BB(20,2) with the trend | %B ~40 / 76 / 42 |
| tr_donchian | 11, 182 | upper band turns up after flat; stop below lower band | 2nd best first series (~58) |
| tr_ichimoku | 21, 144 | tenkan x kijun above green cloud; stop below kijun; beep-boop variant | 53 / 58 / 39 |
| tr_aroon | 14, 189 | Aroon up 100 / down 0 first time; PSAR stop | 40 / 64 / ~40 |
| tr_alligator | 19 | lips x jaw, close above all lines | 50 / 53 / 32 |
| tr_psar_alligator | 17, 197 | PSAR flip with stacked Alligator | 43 / 52 / 34 |
| tr_supertrend | 15, 191 | flip with 200 EMA; stop at the line | 46 / - / - |
| tr_vortex | 12, 185 | VI+ x VI-; PSAR stop | 46 |
| tr_ma_cross | 20, 187, 203, 110 | 9/21 cross (McGinley ~ double-length EMA) | 48 / 58 / -; McGinley 43 |
| tr_golden_cross | 10, 180, 139, 133 | 50/200 cross, price beyond both (SMA / WMA / DEMA) | 42 / 76 (78 trades in 20y) ; WMA 48 |
| tr_hma_cross | 143 | Hull(100) x SMA(200), candle clear of both | 42 |
| tr_trend_breakout | 43 | close above the swing high in the trend; stop just below it | 52 (wide stop 40) |
| tr_fractal | 18, 162 | fractal reversal in Alligator trend / fractal breakout | 38 / 61 / 41; breakout 48 |
| tr_connors_rsi | 9, 178 | CRSI leaves <10 / >90 | 39 |
| tr_mfi | 16, 194 | MFI back above 10 (creator's level) | 42 / 67 (rare) / 35 |
| tr_oscillator_rebound | 22, 131/132, 170, 178 | CCI -100, %R -80, Stoch 20, RSI 30 rebound in trend | CCI 36/56; %R 44; Stoch 41; RSI 49/59/40 |
| tr_rsi_momentum | 146, 117/118, 150 | RSI crosses ABOVE 70 in trend + SuperTrend / cloud / ADX | 45 / 49 / 36 |
| tr_channel_break | 108, 109, 136, 135 | candle opens AND closes outside MA channel (SMA20 H/L) or Keltner | 49; Keltner ~BE (17-loss streak) |
| tr_rvi | 112, 113 | RVI x signal below / above zero | 40 / 45 |
| tr_zero_cross | 121, 127/128, 130, 158, 153, 166 | PPO / TRIX / CMO / AO / Chaikin osc / CMF zero-line cross | Chaikin 47, CMF 43, AO <45 |
| tr_awesome_turn | 147 | AO below zero rising 3 bars | 45 |
| tr_schaff | 154, 141 | STC crosses 25 | 50 (2R: 42) |
| tr_kst | 156 | KST x signal below zero | 52 |
| tr_fisher | 123, 124 | Fisher x trigger below zero | early profits given back |
| tr_beep_boop | 164, 114, 119 | his MACD-histogram + 50 EMA indicator; +SuperTrend / +Alligator | 48 |
| tr_livermore_pivot | 93 | SuperTrend-flip reversal point, breakout of it | 41 |
| tr_extreme_level | 67 | biggest daily reversal points; 5m rejection candle there -> reverse | 76 |
| tr_darvas | 85 | Darvas box breakout | 45 at 5:1 (closes), 27 with highs/lows |
| tr_engulfing | 174, 176 | engulfing candle, next 1-2 candles | 54 / 56 (direction) |
| tr_range_edge | 83 | in a range: MACD / RSI-vs-SMA / %B signals at the edges, 0.5:1 | 78 / 70 / 60 (BE 67) |

## Findings (not families)

Trailing PSAR stop on MACD: 62% -> 46% (#26). Break-even stop: no gain (#29).
1.5:1 is his best reward-risk (#141). Fibonacci levels: no significance (#48,
#173). Flipped MACD = break-even (#103); opposite trades flip the win rate
(#13). Most strategies win in trends and lose in ranges (#5-#8, 10,000 trades).
Higher timeframes raise win rate only in stocks, long side (#2).

## SKIP

Commentary / tier lists / psychology (#1-#8, 25, 27, 28, 30-42, 44-47, 49,
51, 55-58, 60-66, 71-82, 84, 86-91, 94-102, 105-107, 145, 151-152, 155, 157,
159, 161, 163, 165, 167, 169, 172, 175, 177, 179, 181, 183-184, 186, 188, 190,
192-193, 195-202); sizing / money management only (#31, #68, #69, #155 Martingale);
CPI news straddle (#52, needs a calendar); grid trading (#59, no stop);
Fibonacci tool uses (#70, discretionary); volume (#50) and MACD histogram (#53)
explainers; Heikin Ashi / Renko charts (#157, #196).
