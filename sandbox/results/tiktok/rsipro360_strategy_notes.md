# @rsipro.360 (RSI Pro) — strategy notes (read 2026-10-04)

Grid: 17 posts, all 8-9 minute indicator-combo lessons with explicit rules. GO.
Risk rule almost always: stop just beyond the recent swing, take profit 1:2.

Coded — `sandbox/research/tt_rsipro360.py` (stop = `swing`-bar extreme, floored; rr_2/1/1.5):

| Family | Post | Rule |
|---|---|---|
| rp_triple_macd | Triple MACD scalping (1m) | MACD(30,150) hist trend side; MACD(12,20) weakening bar; MACD(4,7) first opposite bar then a larger one |
| rp_rsi_macd_ema | RSI + MACD + EMA | EMA9 x EMA50, RSI back above 30, MACD cross with hist > 0 within `win` bars, then close above both EMAs |
| rp_double_rsi | Double RSI | RSI21 > 50; RSI5 dips below 50 then crosses back above RSI21 |
| rp_ribbon_rsi | RSI + MA ribbon (5m) | EMA34 high/close/low above SMA55; pullback to EMA34 close holding EMA34 low; RSI 50-70; close back above EMA34 close; exit RSI > 70 |
| rp_three_ema_rsi | 5-min RSI & EMA | EMA25>50>100; pullback to 25/50 not below 100, RSI held > 40, RSI crosses back > 50; stop below EMA50 (or swing), 2R |
| rp_rsi_double_macd | RSI + double MACD (1m) | MACD(34,144) hist > 0, MACD(13,21) hist < 0, RSI(9) crosses back above 30 |
| rp_rsi_mfi_candle | RSI + MFI | MFI > 80, RSI > 70, strong green candle (body >= 60%, small upper wick) |
| rp_stoch_rsi_macd | RSI + MACD + Stochastic | %K and %D oversold recently, RSI > 50, MACD cross up with hist rising |
| rp_impulse_macd_rsi | Impulse MACD + RSI | LazyBear Impulse MACD crosses its signal (not flat), RSI side of 50 |
| rp_st_rsi | RSI + Supertrend | ST(10,3) flip with RSI > 50 (and rising) |
| rp_vwap_rsi_break | RSI + VWAP fake breakouts | strong candle closes across VWAP while RSI crosses 50 |

Covered elsewhere / SKIP: RSI divergence scalping, RSI + Fibonacci 0.5/0.618 + engulfing (golden-zone
families), RSI + order blocks (OB families), RSI + linear-regression candles (ugurvu script),
BigBeluga two-pole oscillator (closed), RSI + Alligator (no transcript).
Verify: no problems; rp_rsi_macd_ema is rare (tens of events), the rest hundreds to thousands.
