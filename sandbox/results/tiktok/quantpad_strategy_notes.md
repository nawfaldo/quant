# @quantpad — strategy notes (read 2026-09-28)

53 posts found, 49 with English captions. Newest first.

QuantPad is mostly a **research/debunk** channel: each post runs a popular idea on
16 years of ES 1-minute bars against a matched control. Most posts end with "no edge
after costs" or "buy and hold beat it". Only the stated, mechanical rules are kept as
families; everything that is a claim test with no trade rule is SKIP.

Their claimed ES stats are one contract, 2010-2026, after their measured costs.

## Rules kept (candidate families)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| early | 2026-09 | qp_overnight_hold | buy the close, sell the next open (overnight session only) | overnight +$141k after costs; day session worse |
| early | 2026-09 | qp_momentum_top5 | a 30-min up move in the top 5% of its history → buy, hold 60 min | top 5% held 1h nets +$949k |
| early | 2026-09 | qp_afternoon_range_break | morning range (open→noon); break of it after noon, exit at the close | +$58k after costs |
| early | 2026-09 | qp_third_touch | 3rd touch of prior-day high/low holds → fade from the level | third touch holds, +3 pts; late breakouts $30k |
| early | 2026-09 | qp_rsi_extreme_long | RSI > 80 → long (momentum), not short | RSI>70 short loses; RSI>80 long marginal |
| early | 2026-09 | qp_orb_stop_other_side | 9:30-10:00 range break, stop at the other side | used in their sizing post |
| 29 | 2026-09-10 | (merge into qp_orb_stop_other_side) | first 30 min range; break it, stop other side, out at the close; 60-min range variant | +$29k; a midday range made $27.7k (no special edge); 2022 carries it |
| 30 | 2026-09-10 | qp_gap_fade | fade the overnight gap toward yesterday's close (size buckets; gaps > 16 pts) | 59.9% fill vs 57.5% control; +$19k gross, -$32k net |
| 31 | 2026-09-08 | qp_inside_day_break | day inside the prior day's range → next day trade the break of it | +$67k, all 168 settings positive, direction ~coin flip |
| 32 | 2026-09-05 | qp_stop_hunt_fade | price pokes 1-8 ticks through prior-day HIGH and fails → short | 92% of 86 trades, +$7k in 16y; PDL/overnight levels = noise |
| 33 | 2026-09-04 | qp_volume_breakout | break of prior session high on above-average volume bar, hold 30 min | +0.47 pt vs 1.23 control; 1/3 of 320 versions positive |
| 44 | 2026-08-28 | qp_donchian / qp_connors_rsi | Donchian breakout, Connors RSI (walk-forward families) | walk-forward OOS -$605k; random params beat optimised |
| 48 | 2026-08-22 | qp_rsi2_pullback | RSI(2) pullback (buy oversold in uptrend), 1m | +$6.66/trade gross, -$125 net at 1m |
| 49 | 2026-08-21 | qp_ma_cross | fast/slow MA crossover, 12x12 grid; long-only variant | 0/129 profitable net; best long-only +$112k vs B&H $329k |

## SKIP (research, no trade rule)

28 volume dry-up (backwards; no signal) · 34 spread by hour · 35 delta divergence
(98% of highs diverge, no signal) · 36 POC revisit (= random level) · 37 short strangle
(options) · 38 delta vs ITM probability (options) · 39 TSMOM 29 futures (daily,
cross-asset) · 40 insider buying (stocks) · 41 CPI/NFP (no direction) · 42 tape
imbalance (null) · 43 optimiser luck · 45 regime labels · 46 risk-neutral density ·
47 IV surface · early posts: scaler leakage, ML split leakage, significance test,
ablation, cost vs holding period, order book size, bar-close fills, parameter-sweep
luck, FVG fills (= control), big-candle continuation (fails), decisive candle fade
(loses), volume spike (no direction), round numbers (backwards), rejection wick
(backwards), engulfing (no captions).

## Notes for the build

- Their tests are ES-only; our engine runs one trade/day, RTH, next-bar-open entry —
  the overnight hold (buy close, sell open) cannot run inside an RTH-only session
  flatten; keep it as a family only if the engine's session allows it, else SKIP.
- Many rules are 1-minute (RSI-2, MA cross). 1m is not worth running here
  (LuxAlgo 1m: 0/50 passed) — build at 5m+.
