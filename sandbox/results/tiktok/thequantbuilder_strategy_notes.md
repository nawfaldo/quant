# @thequantbuilder ("Quantlab") — strategy notes (read 2026-09-28)

19 posts found (profile shows 20), 9 with English captions. Newest first.

Mostly one bot sold over and over (#1, #3, #4, #6, #10, #13) plus "does X
work?" myth tests. Two families built.

## Rules kept

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 1,3,4,6,10,13 | 2026-09-15..27 | tqb_first_candle_ema | NY open, first 5-min candle: close above the 12 EMA -> long, below -> short. No fixed target; stop trails "as momentum develops", held until the exit logic fires (one trade held over a weekend) | NQ 5m 2019-2026: 1,448 trades, +982%, 57% win, PF 1.29, max DD just under 20% |
| 5 | 2026-09-23 | tqb_red_days_long | after 3 red days in a row, buy (next day green 58.4% vs 54.9% base) | not significant; vanished outside crisis periods; no effect on 55y of composite data |

Build notes: our cells flatten at the session close, so the weekend hold can't
be reproduced; the trail is the engine's `trail_X` (X daily ranges) plus
`days_1` (to the close). "First candle" = the session's first bar at every
timeframe (at 5m it is their candle exactly).

## SKIP (tests with no trade rule, or no captions)

2 200-EMA touches (= a line 1.5 ATR away) · 7 gold after ATH (no captions) ·
8 PDH/PDL on gold (no captions) · 9 3,771 order blocks on gold (no captions) ·
11 liquidity (no captions) · 12 moves before news (no captions) · 14 break &
retest on gold (no captions) · 15 COT data (no captions) · 16 Fibonacci (no
captions) · 17 round numbers (45.5% vs 45.8% control) · 18 liquidity explainer
(no captions) · 19 time of day (NQ moves 48% in NY; gold per hour a third more
in NY than Asia).
