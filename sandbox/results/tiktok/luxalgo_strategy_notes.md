# LuxAlgo TikTok (@luxalgo) — strategy extraction notes

Collected 2026-09-28 from Chrome (logged in). 607 posts on the profile; 430 have
English speech subtitles (TikTok's own auto-captions), 177 are music/text-only.
Keyword screen (>=6 rule words) left 194 candidates, read newest -> oldest.
`#N` = rank on the profile, newest = 1. Rules below are what the video SAYS;
"LuxAlgo version" = the indicator LuxAlgo built on top of the creator's idea.

## Families built (cfd_tt_families, group `luxalgo`), newest first

`luxalgo_manipulation` (#28, #40 in part) was the first family; the 49 below were added 2026-09-28.
"NY clock" = uses New York wall-clock windows, so it skips aus200/hk50/jp225.

| Family | Posts | Notes |
|---|---|---|
| `lux_structure_poc` | #6 |  |
| `lux_no_wick_retest` | #9, #122 |  |
| `lux_session_sweep_bos` | #13, #25, #33, #108, #142, #154, #374 | NY clock |
| `lux_htf_stoch_bucket` | #14 |  |
| `lux_value_area_reversion` | #15, #139 |  |
| `lux_eight_am_range` | #16, #43, #151 | NY clock |
| `lux_eight_am_roadmap` | #42 | NY clock |
| `lux_equal_levels` | #17, #18, #128, #130 |  |
| `lux_rsi_divergence` | #19, #84, #143, #371, #60 |  |
| `lux_vwap_ema` | #20 |  / own exit |
| `lux_htf_liquidity_fvg` | #22, #133, #141 |  |
| `lux_sweep_ifvg` | #50, #85, #93, #96, #360, #388, #25 |  |
| `lux_break_gap_reaction` | #26 |  |
| `lux_trend_pullback` | #27, #31, #35, #63, #73, #123, #578 |  |
| `lux_htf_manipulation` | #40, #28 |  |
| `lux_orb_retest` | #29, #135, #144 |  |
| `lux_orb_breakout` | #30, #117 |  |
| `lux_swing_sweep_mss` | #34, #396 |  |
| `lux_sweep_reclaim` | #39, #89, #124, #354, #358, #514, #577 |  |
| `lux_cisd` | #41, #55, #79, #110 |  |
| `lux_open_candle_fade` | #44 |  |
| `lux_trendline_break` | #45, #127, #69 |  |
| `lux_rubber_band` | #47, #119 |  |
| `lux_gap_fill_breakout` | #48 |  |
| `lux_three_step_trap` | #52, #71 |  |
| `lux_momentum_flip` | #53 |  |
| `lux_body_momentum` | #56 |  / own exit |
| `lux_first_hour_sweep` | #58, #137 |  |
| `lux_range_breakout_retest` | #62, #97, #346, #390, #484 |  |
| `lux_rsi_50_pullback` | #66 |  |
| `lux_fvg_sweep_wick` | #68, #91 |  |
| `lux_box_theory` | #70 |  |
| `lux_prior_poc_reaction` | #75 |  |
| `lux_alternating_sequence` | #76 |  |
| `lux_unicorn_breaker` | #81, #300, #381 |  |
| `lux_silver_bullet` | #92, #159 | NY clock |
| `lux_po3_midnight` | #99 | NY clock |
| `lux_ny_vwap_pullback` | #105, #95 |  |
| `lux_supply_demand` | #64, #112, #152 |  |
| `lux_amd_fvg` | #115, #85 |  |
| `lux_fvg_violation` | #118, #304 |  |
| `lux_htf_trend_fakeout` | #121, #152 |  |
| `lux_first_break_fvg` | #126, #149 |  |
| `lux_session_open_reaction` | #129 | NY clock |
| `lux_key_levels_orb` | #134 |  |
| `lux_london_range` | #148, #339, #410 | NY clock |
| `lux_friday_monday` | #153 |  |
| `lux_first_session_fvg` | #356, #398 |  / own exit |
| `lux_prior_day_direction` | #349, #445 |  |

## Batch 1 (#6 - #30)

- **#6 2026-09-15 structure_poc** — trend = 3 BOS then opposing CHoCH; take last
  swing low->high, volume profile over it, POC. Enter when price retraces to POC
  (continuation with the prior trend). TP/SL on ATR: 4 ATR target, 2 ATR stop (2:1).
- **#9 2026-09-09 no_wick_retest** — candle with no top wick (high == max(open,close))
  leaves a level at its high; first untapped return to it -> short; no bottom wick ->
  level at low, return -> long. ATR stop/target 2:1. 1m ES in the video. Wick tolerance.
- **#13 2026-08-27 asia_sweep_bos** — EURUSD 5m. Asia session high/low. After Asia
  closes: sweep of Asia high then bearish BOS (break of last swing low) -> short, stop
  above sweep high, target 1:1.5 (or Asia midline). Mirror for low sweep. Max bars
  between sweep and signal matters.
- **#14 2026-08-21 htf_stoch_bucket** — 1h stochastic OB/OS zone; on 5m take RSI
  reversal (leaving OB/OS) / structure change inside the zone, fade direction.
- **#15 2026-08-19 value_area_reversion** (Fabio Valentini idea) — previous day's
  value area. Price closes outside VAL with declining volume, then bullish engulfing
  back inside -> long, stop below low, target POC (or VAH). Mirror at VAH. NQ 15m.
- **#16 2026-08-17 eight_am_range** — 1h 08:00 NY candle high/low. Sweep one end,
  then reversal (key-level break / IFVG / order block) -> target the opposite end.
- **#17 2026-08-10 equal_high_breakout_fvg** — equal highs/lows; breakout leaving
  an FVG; retrace into FVG + reaction candle -> continuation; stop beyond gap; ~1.5R.
- **#18 2026-08-06 equal_high_sweep_mss** — 15m equal highs swept, 1m market structure
  shift, retrace into imbalance -> reversal trade, target the lows.
- **#19 2026-08-05 rsi_regime_divergence** — RSI regular divergence at extremes, only
  in a ranging regime; RSI crossing 50 = trend change.
- **#20 2026-08-03 vwap_ema9** — long on first close above session VWAP, short on
  close below; exit on close back through the 9 EMA; stop beyond the signal candle.
  Their backtest: 30% win, PF 1.6.
- **#22 2026-07-29 htf_liquidity_ltf_fvg** — 1h liquidity sweep; 5m structure break
  with FVG -> entry toward the next 1h liquidity; stop beyond swing.
- **#25 2026-07-27 asia_sweep_ifvg_trendline** — Asian-session low swept, price back
  in range, IFVG, trendline break -> long; stop below IFVG; target other session
  side or ATR.
- **#26 2026-07-24 break_gap_reaction** — break of a recent pivot (<=50 bars old)
  leaving an FVG; retrace to gap + reaction -> continuation; stop below low; 2R.
- **#27 2026-07-22 htf_ema_pullback_50pct** — daily 50 EMA trend on 4h (or 1h 50 EMA
  on lower TF). Counter-trend pullback inside trend; on CHoCH back with trend, mark
  50% retrace of the leg, enter at 50%; stop beyond pullback extreme; target new extreme.
- **#28 2026-07-16 manipulation_candle** — ALREADY IMPLEMENTED (luxalgo_manipulation).
- **#29 2026-07-16 orb_retest_1m** — first 15m candle after the open = opening range;
  breakout sets bias; on 1m wait for price back inside the range + rejection candle ->
  enter with bias; stop 20-25 pts (NQ); target 1:2 or 1:3.
- **#30 2026-07-15 orb30_breakout** — first 30m candle at 09:30; on 5m, close outside
  the range -> enter; target 1:3. LuxAlgo version filters on breakout volume (HV).

## Batch 2 (#33 - #52)

- **#33 2026-07-10 session_sweep_mss** — Asia/London/NY session highs & lows (YM, XAU
  5m). Session low swept -> market structure shift (break of last lower high) -> long,
  target buy-side; mirror. Video entered at OTE 61.8/78.6; LuxAlgo found the MSS itself
  a better signal and dropped the retrace. Their backtest: YM 65% win PF 2, XAU 70% PF 3.
- **#34 2026-07-09 sweep_mss_fib50** — new high/low (sweep) into an immediate MSS, enter
  on a 50% retrace of the displacement leg. LuxAlgo added a 200 EMA filter; tested
  SL/TP 1.5/3, 2/1.5; ES 5m poor, NQ 30m "promising".
- #38 2026-07-04 promo (market-structure signals, 50/150 pt) — no rule. SKIP.
- **#39 2026-07-04 sweep_spring** — sweep below support, close back above it (spring)
  -> long, stop below the sweep low, 2:1. Mirror at resistance.
- **#40 2026-07-03 htf_manipulation_candle** — same idea as #28 on the 4h candle: HTF
  candle trades below the prior HTF low and closes higher -> long (mirror). Option:
  previous HTF bar same direction. Entry refinement on LTF after the HTF signal.
- **#41 2026-07-01 cisd_htf** — 4h/1h trend aligned; previous 4h/1h high or low swept;
  CISD (close through the open of the last opposite run) -> trade with HTF trend;
  stop beyond the sweep; targets 2, 2.5, 4 R.
- **#42 2026-06-29 eight_am_15m_roadmap** — ES. 08:00-08:15 NY candle = zone. At the
  09:30 open: above zone -> longs on retest of the zone midpoint with a reaction;
  below -> shorts; inside -> no trade. 10 pt stop, 20-40 pt target (2:1 .. 4:1).
- **#43 2026-06-24 eight_am_1h_cr** — 08:00-09:00 1h candle high/low. After 09:00 the
  first side hit is faded toward the other side, entered on IFVG/order-block on 1m;
  stop at the swing extreme; target the opposite end of the 8am candle.
- **#44 2026-06-22 open_candle_fade_fib** — first 15m candle of the session; if its
  range > ATR it is a "manipulation candle": fade it (enter opposite its direction),
  target 38.2% / 50% / 61.8% retrace of the candle; stop beyond the candle extreme.
- **#45 2026-06-19 trendline_three_touch** — trendline with >=3 touches, trade the break
  (1h works better).
- #46 2026-06-16 portfolio of 9 textbook strategies (supertrend, donchian, stoch, CCI,
  bollinger...) — not a single rule. SKIP.
- **#47 2026-06-15 rubber_band** — consolidation box after a strong move; sweep of the
  box's far side (fake-out), then breakout with the trend -> enter, stop beyond the
  sweep, target 1:1 or 1:2 (bull/bear flag).
- **#48 2026-06-12 gap_fill_breakout** — close through a support/resistance level into
  an unfilled gap (FVG / untraded zone) -> trade toward the gap fill; stop beyond the
  signal candle; target full gap fill.
- **#50 2026-06-10 sweep_ifvg** — liquidity sweep of a swing high that leaves a bullish
  FVG; close below that FVG (inversion) -> short, stop above the high, 2:1. Mirror.
- **#52 2026-06-08 three_step_trap** — breakout of a range, failed (closes back inside),
  price pushes in the failure direction -> enter, stop at the extreme, target the other side.

## Batch 3 (#31, #53 - #68)

- **#31 2026-07-14 trend_bos_zone_sweep** — CHoCH then the FIRST BOS sets trend and a
  zone at the break; price retraces, sweeps the last internal high/low, snaps back
  -> enter with trend, stop at the zone extreme, target by ratio. LuxAlgo's best: no
  200 EMA filter, ATR x2 stop, 1:1 target (54% win over 3 months, NQ 5m).
- **#53 2026-06-05 ltf_momentum_flip** — candle bodies shrink into support (momentum
  slowing), a candle flips colour = signal; next candle must open, trade against the
  signal, then cross back over its open -> enter with the signal; stop beyond the low;
  1:1 or 1:1.5. Shown on 1h.
- #54 2026-06-04 session volume MA/profile — tool, no rule. SKIP.
- #55 2026-06-02 "15m FVG, liquidity sweep, CISD, FVG entry" — no parameters given; folded
  into #41/#50.
- **#56 2026-06-01 body_momentum** — signal when the mean body of the last 2 candles is
  >= 2x the average body; enter with it; exit when bodies shrink (momentum slows) or
  2:1; stop beyond the recent swing. 1h filter, 1m entries in the video.
- #57 2026-06-01 session/volatility dashboard — SKIP.
- **#58 2026-05-29 london_first_hour_sweep** — first 1h candle after the London open;
  next hour wicks through one side but closes back inside -> bias to the other side;
  entry on an FVG retrace (or at the close); target the opposite end; stop beyond the
  sweep. EURUSD, gold.
- #59 2026-05-28 Nadaraya-Watson kernel + HTF dashboard — no fixed rule. SKIP.
- #60 2026-05-27 HTF reversal candle (shooting star, engulfing) after RSI divergence —
  loose; overlaps #19/#40.
- #61 2026-05-26 indicator-combiner (supertrend + AO + SMA cross) — SKIP.
- **#62 2026-05-25 range_breakout_retest** — sideways range; break of support/resistance,
  retest of the broken level holds -> enter; stop at the other end of the range (or the
  swing); 2:1 or the next high.
- #65 2026-05-21 ICT pack (silver bullet, OTE, Judas swing, turtle soup, 2022 model) —
  names only; candidates for later: silver_bullet, turtle_soup.
- **#66 2026-05-21 rsi_50_pullback** — RSI goes overbought, then pulls back to 50 ->
  long (continuation); oversold then back to 50 -> short; 1:2, target the extreme.
- **#67 2026-05-20 msb_ifvg_to_order_block** — MSB + IFVG reversal, target the nearest
  unmitigated order block (full mitigation); stop behind the IFVG/MSB.
- **#68 2026-05-19 fvg_sweep_wick** — liquidity (swing high) swept up into a bearish FVG,
  the candle wicks into the FVG and closes back out -> short; mirror.

## Batch 4 (#69 - #91)

- #69 2026-05-19 SMT divergence + trendline break — needs a correlated second market;
  candidate `smt_trendline` (us500/ustec, eurusd/gbpusd) — not built.
- **#70 2026-05-18 box_theory** (890k views) — previous day's high/low box + midline.
  Only buy in the lower half, only sell in the upper half, nothing near the middle.
  Entry on a rejection candle (hammer/engulfing, LuxAlgo: volume >= 2x). Best days:
  open inside yesterday's range, touch the 50% line first, then pull away. Target the
  midline or 2:1.
- **#71 2026-05-18 orb_failed_both_ways** — opening-range breakout failed in both
  directions -> reversal entry on the next break, stop at the range median, 2:1.
- **#73 2026-05-14 bos_retrace_sweep_shift** — trend BOS; in the retrace, internal
  liquidity swept, then a minor structure shift back with trend -> enter, stop beyond
  the sweep, target the extreme or 1.5-2R. (Same family as #31.)
- #74 2026-05-13 MTF trend dashboard + volatility band — loose. SKIP.
- **#75 2026-05-12 prior_poc_reaction** — POCs of the previous days as zones; reaction
  candle (hammer / 3-bar reversal) at a recent POC -> trade toward today's POC.
- **#76 2026-05-12 alternating_sequence** — after N alternating candle colours (3/6/10)
  bet the next candle repeats the last colour (breaks the alternation).
- **#79 2026-05-08 crt_cisd_killzone** — 09:00-11:00 NY only; 1h/4h CRT (candle sweeps
  the prior candle's high/low and closes back inside); LTF CISD; entry on retrace into
  FVG/OB; target the HTF candle's other end; stop beyond.
- **#81 2026-05-07 unicorn_breaker** — swing high/low, run above the high, displacement
  below the low leaving an FVG; breaker = last down-close candle before the run; enter
  on the retrace into breaker/FVG; stop above the FVG; 1:2.
- #84 2026-05-05 RSI divergence tool — covered by #19.
- **#85 2026-05-04 significant_sweep_ifvg** — only significant highs/lows (left behind
  after big moves); swept, then IFVG -> trade; stop at the sweep; 2:1 (1:1 in high vol).
  Same as #50.
- **#87 2026-05-01 sr_break_volume** — LuxAlgo "S/R with breaks": pivot S/R lines, break
  on high volume -> trade the break, 1.5R. (19 trades 57% PF 2; gold 1h PF 1.1.)
- **#89 2026-04-30 daily_crt** — prior day's candle; price takes out its high (low) and
  closes back inside -> target the opposite end of that candle.
- **#91 2026-04-29 liquidity_fvg_confluence** — sweep of buy/sell-side liquidity where an
  untapped FVG sits inside the liquidity zone; reaction close out of the FVG -> trade.

## Batch 5 (#92 - #115)

- **#92 2026-04-29 silver_bullet** — 09:30-11:00 NY window; run above/below a high/low,
  sharp rejection, FVG forms; enter on the retrace into the FVG; ~5 handle stop; target
  the opposing liquidity.
- **#93 2026-04-28 sweep_fvg_retrace** — sweep of a high, reversal FVG forms, price
  retraces into it and rejects -> enter; stop above the zone; target the recent low
  (~4.5R in the example).
- #95 2026-04-28 session VWAPs (Asia/London/NY open anchors) — levels, no rule. SKIP.
- **#96 2026-04-27 sweep_ifvg_swing_target** (581k views) — sweep then IFVG, target the
  next swing point; best at the market open. Same family as #50/#85.
- #97 2026-04-27 range break & retest — same as #62.
- **#99 2026-04-24 po3_midnight_open** — bias from the prior day; bullish -> only buy
  below the 00:00 NY open after a sell-side sweep + bullish structure shift + FVG
  retrace; bearish -> sell above it. Stop at the swing, 2:1.
- #100 five-pattern detector, #101 MACD>0 background filter, #104 FVG at POC,
  #111 VSA bars, #114 Monte Carlo S/R — no tradeable rule. SKIP.
- **#105 2026-04-21 ny_vwap_pullback** — after the 30m opening range (then a 90-minute
  setup window): a new low of day after the open, retrace to the session VWAP, rejection
  candle closing back below VWAP -> short (directional); stop 1.5 ATR; target the next
  low. Mirror for longs.
- **#108 2026-04-15 asia_london_ny_reversal** (222k views) — tight Asian range; London
  sweeps the Asian low (not the high) -> buy in New York, target the Asian high. Mirror.
- **#110 2026-04-14 liquidity_cisd_ote** — liquidity taken, CISD, pullback >= 50% into a
  breaker/FVG (OTE) -> enter. Same family as #41/#79.
- **#112 2026-04-13 supply_demand_liquidity** (158k) — supply = base before a strong drop
  that breaks structure; enter at the swing high (liquidity) just below the supply zone,
  stop above the zone, 3R; fib 50/61.8 confluence helps.
- **#115 2026-04-09 amd_fvg** (991k views) — accumulation = tight 30-bar range (<= 0.2x
  scale), manipulation = break of one side within 10 bars, then an FVG back the other way
  = entry; ATR stop, 1:2; New York session only.

## Batch 6 (#116 - #133)

- #116 dominant cycle oscillator, #117 ORB probability tool, #131 SMC all-in-one,
  #132 zero-lag scalp signals — no fixed rule. SKIP.
- **#118 2026-04-06 fvg_violation_continuation** — a bearish FVG gets closed above
  (violated) -> long continuation (mirror); FVG size must clear a volatility threshold;
  1.5-2R targets.
- **#119 2026-04-06 compression_break_volume** — tight range above support; breakdown
  candle on exploding volume; retest of the broken level, close at the retest = entry;
  only if the next candle closes outside the level.
- **#121 2026-04-02 htf_trend_ny_fakeout_fvg** — daily trend (HH/HL = buys only); at the
  New York open, a fake-out against that trend, then a 5m FVG with the trend -> enter
  on the pullback into it; stop at invalidation; target the previous day's levels.
- **#122 2026-04-01 no_wick_trend_retest** — 15m; flat-top (no upper wick) candle inside a
  downtrend -> level; price retraces to it -> sell with the trend; stop at the recent
  swing; TP 1:1. USDJPY 69% win in their dashboard. Options: bearish-candle-only,
  MA/supertrend trend filter. (Same construct as #9, with a trend filter.)
- #123 2026-03-31 — same video idea as #73.
- **#124 2026-03-30 pdh_pdl_sweep** (340k views) — previous day's high/low; price sweeps
  through, then rejects -> enter the other way; stop beyond the sweep; target the other
  side of yesterday's range. Entry immediate or ATR-based.
- **#126 2026-03-27 first10_break_fvg** — high/low of the first 10 minutes after 09:30;
  a strong breakout candle through one side; an FVG on/after the breakout (within 10
  bars); pullback into the FVG + confirmation candle -> enter with the break; 1:2.
- #127 2026-03-26 trendline break — same as #45.
- **#128/#130 2026-03-25 equal_zone_resweep** — equal highs/lows zones; a zone is swept
  AND a new equal zone forms right there -> reversal trade; stop beyond the sweep;
  target the next zone or 2:1.
- **#129 2026-03-25 session_open_reaction** — 1h highs/lows of Asia/London/NY and the 15m
  session opens; trade rejections/bounces off a session open toward the nearest session
  high/low (~1:1).
- #133 2026-03-23 SMC chain (1h/4h/session sweep -> 5m BOS/IFVG/SMT -> 5m FVG -> 1m BOS) —
  same family as #22.

## Batch 7 (#134 - #151)

- **#134 2026-03-21 key_levels_orb** (153k) — PDH/PDL, overnight H/L, ORB H/L. Bias from
  a market-structure shift; take the ORB break with the bias, target the nearest resting
  liquidity (overnight / prior-day level); ~2:1.
- **#135 2026-03-20 orb15_retest_confirm** (330k) — first 15m of the NY open (ES); 1m
  breakout; retest of the range; indecision then an engulfing (or 2-3 candles in a row)
  -> enter; stop under the recent swing (option: other side of the range); 1:2.
- #136 MTF momentum dashboard, #140 HTF volume-profile candles, #145 RR-vs-winrate demo,
  #147 MTF trend/AOI/session dashboard, #150 delta — SKIP.
- **#137 2026-03-19 second_hour_sweep** — mark the first hour of the Asian session; the
  second hour takes out its high/low; in the second half of that hour, LTF CHoCH ->
  enter at 50% of the breaking move, 2:1. Generalises to "first session hour range,
  second hour sweeps it, reversal" for any session open (see #58 London).
- **#139 2026-03-18 prior_day_value_levels** — previous day's POC/VAH/VAL; trade the
  reaction at those levels. Observation: opening near PD POC drifts to the nearer VA edge.
- #141 2026-03-17 HTF FVG + PD array + sweep + 1m IFVG — chain; covered by #50 family.
- #142 2026-03-16 Asia accumulation / London manipulation / NY distribution — same as #108.
- **#143 2026-03-16 divergence_fvg_confirm** — counter-trend RSI divergence (bullish div in a
  bear trend -> buy) confirmed only if a strong FVG prints within 5 bars.
- #144 2026-03-16 first-15m ORB, breakout, high-volume retest, 2:1 — same as #135.
- **#148 2026-03-12 london_range_sweep_ob** — London session high/low; later price sweeps
  one side, closes back; order block formed -> enter on the bounce off the OB; stop
  beyond the OB; target 2:1 or the other London extreme.
- **#149 2026-03-12 orb5_fvg** — first 5m candle after the open; 1m FVG through one side
  (within 20 bars of the breakout); enter next candle / on retest; stop at the first
  candle that closed outside; 2:1. LuxAlgo's own 1-year GBPJPY test: does not work.
- #151 2026-03-11 8am 1h range sweep -> opposite end — same as #16/#43.

## Batch 8 (#152 - #363; from here the posts are 30-50s 2024 promos)

- **#152 2026-03-10 htf_trend_supply_demand** — 4h trend; 30m supply/demand zones; at the NY
  open, in a bull trend buy a bounce off demand or a break-and-retest of supply; stop at
  the swing low; target the prior day's high.
- **#154 2026-03-09 daily_profiles** — (a) London manipulation / NY continuation (Asia range,
  London sweeps one side, NY delivers to the other); (b) NY manipulation: Asia+London
  range together, NY sweeps one side and delivers to the other; (c) every session low
  taken, no attempt at highs = trend day.
- #159 silver bullet (sweep, MSS, FVG in 10-11) — same as #92. #160 1m divergence inside
  an HTF reversal candle — same as #60. #191 AI chat trade, #222/#333/#345/#348/#357/#363
  indicator promos — SKIP.
- #300 2024-10-02 ICT unicorn in the London session with session trend — same as #81.
- #304 2024-09-21 failed-FVG (instant mitigation) signal + trailing stop — same as #118.
- #336/#346 range / break-and-retest detectors — same as #62.
- **#339 2024-07-01 london_low_break_fvg** — previous London session low; price breaks
  below it, retraces up into a large 15m FVG, short from the FVG (continuation).
- **#349 2024-06-03 prior_day_direction** — assume today's candle follows yesterday's colour;
  on the LTF take only reversal signals in that direction; target the high/low.
- #354 2024-05-17 sell-side liquidity trap, close back above -> buy — same as #39.
- #355 2024-05-15 rising wedge + IFVG — pattern-dependent. SKIP.
- **#356 2024-05-13 first_session_fvg** — only the first FVG of the session; bearish FVG:
  a candle closes above it, the next candle rejects and closes below that candle's
  open -> sell; exit on MACD histogram turn.
- **#358 2024-05-08 swing_failure_pattern** — 30m; price takes a swing high/low and closes
  back inside (SFP) -> reversal; confirm with opposing intrabar volume; stop beyond the
  candle.
- #359 2024-05-06 sweep + liquidation estimates (crypto) — sweep/reversal part same as #39.
- **#360 2024-05-03 ifvg_trend_ma** (485k) — IFVG signal, taken only with a 100-length
  moving-average trend; 1:3.

## Batch 9 (#365 - #508; 2023-2024 promos, mostly proprietary-indicator combos)

- **#371 2024-04-05 divergence_break_confirm** — bullish divergence; price must not close
  below the divergence low; enter when price closes above the most recent swing high;
  stop below the swing low; target the (HTF) FVG above.
- **#374 2024-03-27 sydney_range_london_sweep** — GBPJPY/USDJPY 5m: Sydney session high/low;
  the low is swept DURING London and price closes back above (on rising volume) -> long,
  stop below the low, target the Sydney high. Mirror.
- **#390 2024-02-16 range_break_trend_fvg** — trend (HH/HL = buys); a range forms; breakout
  only counts in the trend direction; enter on a >80% retrace into an FVG inside the
  range; target the recent extreme.
- #396 2024-02-05 liquidity grab + CHoCH + oscillator > 50, stop below the order block —
  same family as #34 with a momentum filter.
- #388/#393/#398/#410/#445/#381 — repeats of #360/#354/#356/#339/#349/#81.
- #365 #369 #372 #373 #375 #377 #378 #379 #382 #383 #384 #386 #395 #397 #399 #401 #405
  #420 #422 #439 #443 #444 #447 #449 #450 #454 #455 #459 #466 #467 #474 #477 #478 #480
  #484 #508 — promotions of LuxAlgo's paid/free indicators (signals & overlays, oscillator
  matrix, AI clustering, smart trail...) with no reproducible rule. SKIP.
