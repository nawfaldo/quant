# @quanttactic (Quant Tactics) — strategy notes (read 2026-10-04)

44 posts on the grid; 32 unique strategy videos read from English captions
(the rest are repeats and Freqtrade how-tos). Every strategy is coded in
Freqtrade and backtested on 8 liquid crypto pairs (mostly May 2020 - 2026,
several timeframes; "4h won" is the recurring finding). Module
`tt_quanttactic.py`, group `quanttactic`, 16 families (`qtt_`).

| family | video | rule | their result |
|---|---|---|---|
| qtt_triple_ema | Triple EMA, 6 years | above EMA200 and EMA55; EMA34 crosses EMA55 -> enter; exit on the cross back; ATR stop | 3,714% (4h) |
| qtt_bos_second | SMC 6 years / Smart Money BOS truth | 2nd consecutive BOS, above EMA200; stop 2 ATR; exit on a close through the latest opposite swing (CHoCH) | 4,968% 4h; -81% on 15m |
| qtt_bb_force | BB + Force Index 6 years | close above BB(40, 2.5) upper above EMA200 -> long; exit Force Index (EMA55) crosses below 0; ATR stop | timeframe made all the difference |
| qtt_vwap_rsi_vol | VWAP + MACD 6 years (as described) | cross above VWAP, RSI > 50, volume > 20-bar avg, above EMA200 -> long; exit 2 closes below VWAP; 3 ATR stop | 3,856% |
| qtt_supertrend_ema200 | Supertrend + EMA200 6 years (long AND short) | ST(8, 4) flip with the EMA200 side; exit on the flip back; 1.5 ATR stop | 2,529% |
| qtt_ema_macd | EMA + MACD 6 years | above EMA200; MACD crossed signal within 3 bars and > 0 -> long; exit MACD cross back or close < EMA200; 1.5 ATR | 2,311% |
| qtt_rsi_ema_retest | RSI 6 years | RSI crosses its EMA9 (EMA9 > 50), or RSI retests 44-55 with EMA9 > 50; above EMA200; exit EMA20 < EMA50; 1.5 ATR | 6,047% |
| qtt_bb_breakout_vol | BB breakout 6 years | close > upper BB(20, 1), above EMA200, volume > avg -> long; exit below the middle band; 2 ATR | 5,277%, 12% DD |
| qtt_rsi_ema_smooth | RSI strategy 314% | EMA of RSI crosses 65 above EMA200 (35 below) -> enter; stop beyond the signal candle | 314% |
| qtt_mr_bb_htf_rsi | Mean reversion 179% | close below lower BB(20, 2) with 4h RSI > 55 -> long (mirror < 45) | 179% |
| qtt_cmf_ema | CMF + EMA 325%, CMF 316% | CMF(20) crosses +0.05 / +0.07 above EMA200 -> long (mirror); stop 2 ATR; 2:1 | 325% |
| qtt_st_macd_rsi | Supertrend + MACD + RSI 153% | close > ST, RSI > 50, MACD > signal -> long (mirror); stop at swing; 1.5:1 | 153% |
| qtt_vwap_macd | Master VWAP & MACD 191% | close crosses VWAP + MACD crosses signal -> enter; stop 2.5 ATR beyond the signal candle; 3:1 | 191% (OOS) |
| qtt_ao_macd_rsi | Awesome Oscillator 189% | AO crosses 0, MACD crosses signal, RSI crosses 50 -> enter; stop beyond the signal candle | 189% |
| qtt_williams_rsi_macd | Williams %R 305% | %R below -80 then back above -40, RSI > 55, MACD > signal -> long (mirror) | 305% |
| qtt_alma_macd | ALMA + MACD | close above ALMA + MACD cross -> enter; 3 ATR stop, 2.5:1 | BCH 1h walk-forward |

## Not built (near-duplicates of the above or of other creators' families)

Golden cross SMA50/200 with an optimised fixed stop (`tr_golden_cross`),
Supertrend + ATR(4h) + ADX + EMA100, VWAP + DMI, Aroon + MACD, Alligator +
ADX, Donchian + ADX + Choppiness, Keltner + ADX, KAMA 8/13/21 + MACD, WMA
13/21/34 + MACD, ADX + OBV, MACD + RSI + Stochastic, MFI (captions empty or
the same MACD-confirmation template), ETF momentum (portfolio), DCA bot.
