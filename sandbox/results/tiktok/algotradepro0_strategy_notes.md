# @algotradepro0 (AlgoTrade Pro / "ATP") — strategy notes (screened 2026-10-04)

Grid: 37 posts, full-length cuts of his YouTube "testing indicators / strategies" series. GO.
Standard risk model: stop on the ATR(14) band (2x, sometimes 3x) from entry, TP 1:1.5; some
episodes take half at 1:1 then trail on the ATR band. 15 read.

Coded — `sandbox/research/tt_algotradepro0.py`:

| Family | Post | Rule |
|---|---|---|
| atp_st_macd_adx | "SuperTrend + MACD + ADX combo" (5m crypto) | ST(10,3) flip, MACD(34,52,21) hist same side of 0, ADX(14) > 25 |
| atp_alligator_ema200 | "Williams Alligator EXPOSED" | lips cross above teeth & jaw, close above all three, close > EMA200 |
| atp_zl_macd_ema200 | "Zero Lag MACD vs Standard MACD" | ZL-MACD(34,52,21) histogram turns green/red with EMA200 side; zl=0 is his standard-MACD leg |
| atp_gaussian_kijun_vap | "+156% gold scalping" series | Gaussian Channel (4 poles, 144, 1.414) long state + Kijun(120) side + Volume Accumulation % > 10; 3 ATR stop; 30% at 0.75R + trail |

SKIP: Purple Cloud 2.1, Super Trend Fusion + Market Manipulation Index (his own closed builds),
Super Ichi (LuxAlgo) + MACD 21/50/25, Range Identifier, Gold Scalper (formulas not published),
Liquidity Trend Line (BigBeluga) + SMA100 (trendline-breakout families exist), stock picks,
MT5 bot setup, Renko/Chandelier posts (no transcript).
Verify: no problems; 137-2,129 events per symbol.
