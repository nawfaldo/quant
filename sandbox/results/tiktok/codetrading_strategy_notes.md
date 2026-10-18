# CodeTrading (YouTube @CodeTradingCafe) — strategy notes (read 2026-10-03)

350 videos. Screened by title + description: 60 English strategy videos,
34 read from captions (incl. 9 recent uploads titled only in Indonesian).
Skipped: the numbered Python course lessons, AI/RL/GPT bots, Codility
puzzles, data/alerts/risk-sizing tutorials. Module `tt_codetrading.py`,
group `codetrading`, 15 families (`ct_`). He codes everything in
backtesting.py and grids the ATR stop and TP:SL ratio; "trend" is usually
"the last N candles fully above/below the MA".

| family | video | rule | his result |
|---|---|---|---|
| ct_hull_rsi_linreg | b6W3GsDZ_Kg, aOIRo4Q7qZE | HMA16 x HMA65 up + RSI14 > 52 + above daily linear regression; exit on the cross back | 1661% / 9 yrs (crypto 4H, long only) |
| ct_pin_bar_trend | z6OMf-HB5qE, XDJidg6s2DU | pin bar (wick >= 2 body, body < 33% range) with EMA50/200 trend; optional swing S/R filter; ATR stop beyond the pin, 2R, 50-bar time exit | gold 1H: -2% raw, +32% trend-filtered, better with S/R |
| ct_macd_ema_pullback | Ik0ILD0gPHA | 5 candles fully above EMA; MACD hist dipped below -thr within 7 candles; MACD crosses signal below zero -> long | 5.5%/yr, Sharpe 1.45 (FX) |
| ct_vwap_bb_rsi | RbQaARxEW9o | 15 x 5m candles above VWAP; close below BB(14,2) with RSI16 < 45 -> long; SL 1.2 ATR, TP 1.5R | ~300% / 3 yrs, Sharpe 1.65 |
| ct_rsi_scalp_ema | MzEX4XumtEE | above EMA200 and RSI(2) < 10 -> long (mirror > 90) | 250% / 3 yrs |
| ct_rsi_adx_engulf | NOBV08Im56Y | EMA50 trend, RSI(3) < 20 and ADX(5) > 30, then an engulfing candle with the trend; stop at the 3-candle extreme; ~1R | "advertised" scalp |
| ct_ema3_fast_cross | NoNucP_IVB0, ybmep_u5MeU, ZoCpme03sVI | 3 EMAs ordered with matching slopes; candle opens below / closes above the fast EMA; optional no-tail filter | 445% (1:100 leverage), big drawdowns |
| ct_alligator_break | 4rbGvaiD9Wk | Alligator ordered up for N candles then a close below the teeth -> short (mirror) | "best Gator strategy" claim, tested |
| ct_trend_bb_dip | wPOPRu8587s, f6Nh4guZPRs, IfoZaCGTJ_Q | Rayner Teo: 6 candles above the 200 MA, close below BB(20, 2.5) -> long; exit RSI(2) > 50 (opt. 65) | 424% vs B&H 49% (4H stocks) |
| ct_ichimoku_retrace | zRVkam1pVrs | EMA100 trend; >= 7 of 10 prior candles above the cloud; candle opens inside the cloud, closes above | ~40%/yr some assets (4H) |
| ct_vwap_candle | e9IDuLESYv8 | green candle closing above VWAP -> long (red below -> short); ATR trailing stop | ~100%/yr vs B&H -52% (15m) |
| ct_session_break_prev_day | cTJ0Qbz0eAI | previous day green -> after 2-6 h, buy stop at the session high so far; out at session end; 150-pip stop | positive 9 yrs (JPY, gold) |
| ct_harris_pattern | H23GLHD__yY | Michael Harris 4-candle rising-lows pattern -> long next open | 107%, 70% win (QQQ/SPY/DAX) |
| ct_ma_new_low | 7oNYZCMMHPA | above the MA, the candle's low under the previous N lows -> long | "two rules" |
| ct_hoffman_irb | QE6nfBBD_AI, OLsKawK3c-s | rising 20 MA, a 45%-wick inventory retracement bar, then a close above its high -> long | concept |

## Not built

Nadaraya-Watson (repaints; his non-repainting version is a BB envelope),
CHOCH, flags, triangles, ascending triangle, head and shoulders, FVG+S/R
breakout, key-level breakouts, 1m box breakout (covered by other creators'
breakout/ORB/structure families), RSI+S/R rejection (close to the pin-bar
S/R variant), UPRO/SPY SMA switch and the 47-asset rotation (daily
portfolio rules), grid/hedging/martingale (no entry signal), Harris'
second pattern (tOH6Bd_jvfA, conditions cut off in the captions read).
