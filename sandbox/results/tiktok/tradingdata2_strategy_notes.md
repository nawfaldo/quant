# @tradingdata2 — strategy notes (read 2026-09-28)

Profile claims 180 posts; 105 load in the grid. Long-form tutorials (1-24 min),
reposted several times each under new ids. Newest first; `=` marks a repost.

## Batch 1 (posts #1-#19 unique)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 1 (=2) | 2026-09-27 | td_trendline_break | uptrend line through the higher lows; a candle CLOSE below it -> short; stop above the last higher high; target 2R (4H chart). Mirror for downtrends | example only |
| 3 | 2026-09-26 | td_ma_cross_filtered | 20/50 MA cross; take it only if the LAST TWO crosses both followed through; 200 MA trend filter; stop at the recent swing; 2R. 15m-4H only | 200 MA filter "38% -> 48% win" |
| 4 (=5) | 2026-09-18 | td_ema_cross_level | 9/20 EMA cross taken only at a level with 2+ prior touches (S/R) or a broken-and-retested level (flip zone); with the HH/HL trend; in premium/discount half of the last leg; stop beyond the zone; 1:1.5 | 100 trades gold/BTC/EURUSD, 68 won |
| 6 (=7) | 2026-08-21 | td_ema_pullback | EMA9 > EMA15 (or 20), price above both; pullback to EMA9 / between the lines; bullish candle closes back above EMA9 -> long; stop below the pullback candle; 1:1-1.5 or exit on a close below EMA9; RSI > 50 filter; skip flat EMAs | examples BTC 5m, ETH 3m |
| 8, 13 | 2026-07-31..08-18 | td_sweep_three_confirm | sweep of a MEANINGFUL low (prior day/session low, equal lows, major swing); displacement away; a candle BODY closes above the last lower high -> long at the close (if that candle is oversized, wait for a retrace into its FVG); stop below the last swing low; 2R | examples |
| 9-11, 14-16 | 2026-07-19..08-08 | td_ema_smi | EMA 9/15 cross with clear separation; SMI(7,2,2) at the cross must be RISING and not above +60 (sweet spot +40..+60); skip if SMI > +60 and flat | gold 5m examples |
| 12 | 2026-08-05 | td_halftrend | HalfTrend (BigBeluga) flip + label on candle close -> enter; indicator stop; TP at 2R | indicator table 67% win at 1:3 |
| 17 | 2026-04-25 | (trendline bounce/breakout — same as #1) | 2+ touches; bounce with confirmation or break + close | 934k views |
| 18 | 2026-01-18 | td_first_4h_fakeout | first 4H candle of the NY day (00:00-04:00) sets the range; on 5m a candle CLOSES outside, then a later candle closes back inside -> fade (short above, long below); stop at the breakout extreme; 2R; same day only | 2.1M views; BTC/FX/gold examples |
| 19 | 2026-01-07 | td_supply_demand | DBR/RBR demand, RBD/DBD supply bases; enter on return to the zone | 511k views |

## Batch 2 (unique posts from 2026-03 to 2026-08, read after the grid finished)

| # (profile id) | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 7673641105157573909 | 2026-08-13 | td_divergence_choch | BigBeluga Market Structure Trend Matrix flip is only a candidate; need RSI divergence then a change-of-character close; ATR trail + stacked ATR targets | examples |
| 7662869869280464148 | 2026-07-15 | td_squeeze_fire | LazyBear Squeeze Momentum: dots yellow -> white (squeeze fires), trade the histogram's side | none |
| 7661402915990637844, 7660650095309884693 | 2026-07-09..11 | td_ut_bot | UT Bot twice: buys key 6 / ATR 10, sells key 7 / ATR 20 (5m), 200 EMA filter; variants with STC confirm and linear-regression-candle trend ride | 121k views |
| 7660658949254860053 (=3 reposts) | 2026-07-07..09 | td_adx_di | ADX > 20 (not 25) and rising + DI cross; 200 EMA slope as lag-free filter; entries on 200 EMA retests | gold 15m example |
| 7657808746839543061 | 2026-07-02 | td_flag_break | pole 1-2% then 3-5 candle flag; break of flag top on volume | none |
| 7657328062584884501 | 2026-06-30 | (td_ma_cross_filtered) | 200 MA filter clip of #3 | "38% -> 48% win" |
| 7655577297029778708 | 2026-06-26 | td_ema_macd | long only: close > EMA200, MACD crossed signal within 3 bars and > 0; exit MACD cross down or close < EMA200; stop 1.5 ATR | freqtrade, 8 crypto pairs 4h 2020-26: +2,311%, DD 14.3%, Sharpe 1.59 |
| 7654344674244660501 | 2026-06-22 | td_choch_bos | AlgoAlpha Smart Money Breakout: CHoCH then BOS the same way; stop at invalidation; TP1/2/3 | "$7,280 in three days" (promo) |
| 7654330701185142036 | 2026-06-22 | SKIP | BigBeluga Liquidity Spectrum: sweep of a liquidity level then close back (= td_sweep_three_confirm) | |
| 7652489549192465685 (=7633907643282623765) | 2026-04-28..06-17 | SKIP | 15m 50-EMA trend + fresh/flip zone + 1-minute double bottom entry (needs 1m) | |
| 7652004775516540181 (=7645078901273955605) | 2026-05-28..06-16 | td_ema_5_13_89 | EMA5 & 13 above EMA89; pullback = 5 dips to 13; 5 crosses back up on a green candle; stop below EMA89; ride until both under 89, or 1:1/1:2 | |
| 7648762481187360020 | 2026-06-07 | td_zero_lag_trend | AlgoAlpha Zero Lag Trend Signals on 1m with 5m/15m MTF table agreeing; 1:1.5 | |
| 7644730313876245781 | 2026-05-28 | td_ma_cross_filtered (`ma` ema9_20), td_ema_pullback (`confirm` prev_high) | 9/20 EMA: last-two-crosses filter; pullback into the EMA zone, red candle then green close above its high | |
| 7644333147420871957 | 2026-05-26 | td_ema_cci | EMA10/20 cross with CCI(20) on the same side of 0 (often crosses first); stop swing or EMA20; 1.5-2R; 15m-4H | 31k views |
| 7643962770299358484 (=7643977606890556692) | 2026-05-25 | td_vixfix_stoch | Williams VIX Fix green flash + Stochastic %K < 20 crossing up %D; stop swing low; 2R; tops version less reliable | 25k views |
| 7642252840739654933 | 2026-05-21 | td_bb_squeeze_break | Bollinger squeeze, breakout candle body outside band on volume spike; enter on close or on pullback to the middle band; 5-7R | |
| 7626497872900492565, 7623405145455430932 | 2026-03-31..04-08 | td_poc_flip, td_volume_dryup_break | POC flip: break above POC, retest holds -> long, stop below POC, target VAH. Volume dry-up in a tight range then breakout on a spike; stop other side; 2-3R | |
| 7615128325031202068 (=7592659451757268244) | 2026-01-07..03-09 | td_supply_demand | DBR/RBR/RBD/DBD base + explosive departure; first return to a FRESH zone; "smart entry" on rejection; stop past the zone; target next opposing zone, >= 2R | 511k views |

SKIP (no testable rule): 7626170981702208788 volume basics · 7621492621747555605
RSI masterclass (40/60 levels, vague) · 7620238713695538453 MACD + trendline clip ·
7616682144470928660 MACD teaser (cut off) · 7614979649914883349 glossary ·
7614016347437206805 swing-trade workflow (D/4H/1H, discretionary) · 7658051349698891029
Trendilo (cut off before the rules) · 7648625180251524372 candlestick montage (no
captions) · trendline reposts (7646554396729347349, 7646531831009004820,
7646100734093872404, 7632848093364423956, 7623020339072683284).

Posts: 105 loaded, 56 unique after reposts, 23 families built.
