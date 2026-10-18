# @artybryjatma (The Moving Average, "Arty") — strategy notes (read 2026-10-03/04, complete)

152 posts on the profile. 83 fetched and read on 2026-10-03 (the 82 newest,
2026-10-02 back to 2025-04-28; ~10 are reposts of the same tutorial). The
other 69 were read 2026-10-04 (posted 2025-11-28 .. 2026-05-17 per TikTok;
loaded one page at a time because background fetches hit TikTok's firewall).
Of those 69, only two new mechanical rules: `ma_ema_rsi3_signal` and
`ma_200_break_retest` (below). The rest: motivation, Q&A, his paid
"Happy Trails"/"Outback" signal indicators (undefined), the overnight-range
breakout and Fibonacci 0.5-0.618 entries already built.
Long-form (3-13 min) videos; EURUSD, DE40, US30, NAS100, BTC.

## Rules kept (families in `tt_artybryjatma`)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 2, 5, 6, 54 | 2026-06..10 | ma_tokyo_breakout | box the Tokyo session; the first candle that CLOSES above/below it -> enter; stop = 50% of the box height; 1:2. Skip the day if the breakout candle is bigger than the box (#2). #54: the box is the day's bias for London/NY | EURUSD 5m 7- and 30-day examples |
| 2, 4, 5, 9, 37, 43, 57 | 2025-06..2026-10 | ma_fib_gold_zone | an impulse that breaks structure; wait for a >=33% pullback; enter in the "gold zone" (0.5-0.618) on a rejection (or a limit at 0.618); stop at the impulse start, target the impulse end; 200 EMA confluence; only London open to NY close, never in chop | "backtested 100 times" (#43, 1m) |
| 21, 24, 59 | 2026-06..09 | (merged: ma_fib_gold_zone `confirm` hidden_div) | 0.618 retracement + HIDDEN RSI divergence (price higher low, RSI lower low) -> continuation entry, no extra trigger needed | none |
| 7, 10, 14, 18, 24, 63 | 2026-06..09 | ma_rsi_div_secret | "secret sauce" regular divergence: the first RSI extreme OUTSIDE 70/30, the second (equal or more extreme price, double top/bottom) back INSIDE; enter on the rejection candle; stop 1.5 ATR; 1:2 (#18: or hold until RSI reaches the other band); trade 02:00-12:00 UK | EURUSD 1m week: 33 trades, 17 won, PF 2 |
| 23 | 2026-09-04 | ma_ribbon_pullback | 21/50/200 smoothed MAs fanned in trend order; rejection off the 21 -> trade with the trend; stop beyond the 50 MA; 1:2; intertwined MAs = no trade | none |
| 39 | 2026-08-07 | ma_macd_sr_retest | above the smoothed 200: break of a swing high, retest of it as support while the MACD histogram is red but shrinking, engulfing -> long | none |
| 38 | 2026-08-08 | ma_three_line_strike | his example rule set: rejection off the 21, a three line strike, RSI < 50 -> sell; stop above the swing; 1:2 | none |
| 48, 61 | 2025-05-17 | ma_session_hour_break | breakout of the first hour of the London (or NY) session; scalp: if the next 3-4 candles do not go your way, get out | none |
| 49, 56, 62 | 2025-04..2026-06 | ma_htf_level_rejection | mark untapped daily/4h swing levels; on 5m wait for a tap and a rejection wick -> fade | none |

## SKIP (read)

#1, #4, #8, #13, #15, #16, #22, #27, #28, #31, #45, #50, #66, #67, #69, #70,
#73, #78 psychology / news / macro opinion · #3 footprint delta concept (no
rule) · #11 dup of #10 · #12, #20, #25, #26, #29, #30 AI/backtesting
process (#30's AI-found EURUSD strategy files not stated on video) · #17 ATR
stop placement (1.5 ATR, used above) · #19 "top 3 strategies" index (the
three above) · #32 risk calculator · #33 volume profile POC rejection (no
trigger) · #34 TradingView tools · #35 no caption · #36, #52 price action
basics · #40 Heikin Ashi for swing trading (no mechanical rule in the
caption) · #41 trendlines on RSI · #42 market structure basics · #44 "10
pips is all you need" · #46 divergence types (covered) · #51 garage lighting
· #53, #74, #75, #76 (MA crossover: don't) basics · #58 PDT rule news · #71
naked S/R + trendline (discretionary) · #72 candlestick patterns at S/R.

## TODO (resume)

Posts 84-152 (older than 2025-04-28): fetch and read, add families.

## Added 2026-10-04 (from the 69 later-read posts)

| date | family | rule as stated | claim |
|---|---|---|---|
| 2026-04-26 | ma_ema_rsi3_signal | DE40 5m, his indicator set EMA100/EMA200, RSI(3) 80/20, ATR(10)x3: buy/sell signal -> enter; stop previous swing (or the moving average if too close); RR 1:4, BE at 1R | "highest win rate strategy I have ever tested" |
| 2025-12-13, 2026-05-13 | ma_200_break_retest | 5m: close through the 200 MA, retest and rejection -> trade the break; check the 15m agrees | — |
