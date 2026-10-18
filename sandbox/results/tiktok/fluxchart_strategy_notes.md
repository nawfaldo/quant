# @fluxchart (Flux Charts) — strategy notes (read 2026-10-03)

44 posts on the profile (grid stops at 44 for this login), 28 with English
captions. Newest first, `#N` = rank on the profile. Flux Charts is an indicator
vendor like LuxAlgo: most posts are "rule + our indicator", so the rule is
kept and the indicator is replaced by a causal stand-in.

## Rules kept (candidate families)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 1 | 2026-10-02 | fx_htf_band_reversal | price breaks the HIGHER-TF Bollinger lower (upper) band; the lower-TF bands turn up (down) -> long (short). Kalman-smoothed bands in theirs; plain BB here | none |
| 2 | 2026-09-30 | fx_equal_sweep_mss | 15m wick-sweep of relative EQUAL highs/lows; then a body close beyond the last swing before the sweep (MSS); enter at the FVG/OB; stop at the FVG's third candle; target the nearest swing before the sweep | 80% win, +62% (their backtester) |
| 3 | 2026-09-28 | fx_unicorn | breaker block (broken OB flipped) with an FVG in the same zone, same direction -> enter; stop beyond the zone; target the opposite side of the range | none |
| 4 | 2026-09-27 | fx_sr_retest_low_volume | 5m: retest of a support level, low selling pressure (relative volume below normal), then a retest -> long; stop the recent low; 2R | none |
| 5 | 2026-09-26 | fx_cvd_zero_cross | cumulative volume delta crosses zero, then an FVG in that direction confirms -> enter (CVD proxied by signed tick volume) | none |
| 6, 9 | 2026-09-24 | fx_sweep_reclaim_bias | a swing high/low is broken and reclaimed (sweep); trend bias across timeframes agrees -> enter the reclaim direction; stop beyond the sweep; target the next opposing swing | none |
| 8 | 2026-09-22 | fx_fvg_grade | grade every FVG on displacement size, volume, range position, structure alignment; trade the top grade (retest of the gap) | grade table only |
| 10, (12) | 2026-09-19 | fx_liquidity_grab | break a recent high/low and snap back inside; only the biggest wick-to-body ratios -> enter opposite the break; stop beyond the wick; target the next opposing level (#12 Turtle Soup: no caption, same idea) | none |
| 11 | 2026-09-18 | fx_sr_poc_rejection | 30m: higher-TF S/R zone lines up with the volume-profile POC; retest, then a rejection candle closes back out -> enter; stop beyond the zone; 2R | none |
| 16 | 2026-09-13 | fx_consolidation_break | 15m: a tight ("clean coil") range; close outside it -> enter; stop the range's other side; target the typical expansion | none |
| 20, 36 | 2026-09-05 | fx_orb | first 15 (or 30) min range; close above -> long, below -> short; stop the other side; 2R. #36: wait for a retest of the broken level first | 75% win over 100+ trades |
| 22, 24 | 2026-08-31 | fx_power_of_three | accumulation (tight range) -> manipulation (false break outside) -> distribution: signal inside an order block of that side; their SL/TP. 15m | "printing since the start of 2026" |
| 23 | 2026-08-28 | fx_trend_fade | strength score from RSI + SuperTrend + EMA distance; candles shift from strong to neutral = trend fading -> reversal | none |
| 30, 32 | 2026-08-15 | fx_fvg_crossfire | a bullish and a bearish FVG overlap ("crossfire" zone); price retests it and closes back beyond -> enter with the zone's last direction; stop beyond the zone; 2-3R (#30 adds: zone on the POC, 30m) | none |
| 33 | 2026-08-08 | fx_htf_trend_demand_st | 4h uptrend (HH/HL) -> 15m bounce off a demand zone, then a SuperTrend buy -> long; stop the recent low; 2R | "crushing it in 2026" |
| 34 | 2026-08-07 | fx_breakout_delta | break of a swing high/low only when the breakout candle's volume delta agrees | none |
| 35 | 2026-08-06 | fx_fvg_osc_divergence | oscillator of FVG activity; price makes a new high, the oscillator does not -> reversal | none |
| 37, 38, 42 | 2026-08-04 | fx_tested_zone | only zones with >= 10 retests and > 60% hold rate (#42: probability > 70%); rejection candle closes back out -> enter, stop beyond the zone, target the next level. #42 short side: a decisive BREAK of a high-score level -> trade the break | 61% hold over 44 tests (example) |

## SKIP

#7, #13, #14, #18, #21, #25-#29, #39-#41 no captions (titles only) · #15 order
block vs HTF zone (concept, no trigger) · #17 backtester promo · #19 S/R indicator
promo (folded into #11) · #31 proprietary AI signal + "retracement wave" (no
causal stand-in) · #43, #44 brand promos.

## Re-scroll 2026-10-04 (690 posts on the grid; 24 more read)

The first pass saw 44 posts (zoomed-out grid). The full grid holds 690, most
of them indicator promos repeating ~20 ICT/SMC concepts. Captions were
screened; only posts on concepts with no family yet were opened.

| family | posts | rule | claim |
|---|---|---|---|
| fx_grab_breaker | Insane / CRAZY / Perfect Breaker Block strategy; "58% win rate" (BTC 30m) | liquidity grab, then the last opposite candle (order block) is broken into a breaker; enter at formation (CRAZY) or on the retest; stop beyond the zone; 1.5-3R | 58% win, "$2,000 in a few days" |
| fx_bpr_sweep | Balanced Price Range strategy (5m after 1h levels), INSANE BPR (30m) | sweep of an HTF high/low, an FVG overlapping an opposite FVG (BPR), retest -> enter with the HTF trend; stop at the sweep; 1:2 | - |
| fx_turtle_soup | ICT Turtle Soup explained | HTF liquidity swept on the LTF, then a market structure shift -> enter; stop beyond the sweep; target the opposite liquidity | - |
| fx_channel_retest | Easy / Overpowered / Insane Trend Channels | rising channel, tag of the lower line with buyers (volume) -> long; stop below; 3R | - |
| fx_trendline_sweep_fvg | Trendline Liquidity Sweeps | trendline broken then reversal leaving an FVG; retrace into it in discount -> enter; 1.5R | - |
| fx_demand_9ema | EASY S&D strategy, 65% win rate (backtested) | demand-zone retest and a close back above the 9 EMA -> long (supply mirror) | 65% win, PF 1.6 on 15m |

Skipped: Judas swing and the two VWAP posts (no captions), the EMA-cross
dashboard post (needs their "Fibonacci Golden Wave"), every "FREE indicator"
promo, AI/ML signal posts, photo explainers.
