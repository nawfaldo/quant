# Part Time Larry (YouTube @parttimelarry) — strategy notes (read 2026-10-03)

168 videos; ~150 are API/app/tooling tutorials (IBKR, Alpaca, TD, Kalshi,
OpenAI). 14 rule videos read. Module `tt_parttimelarry.py`, group
`parttimelarry`, 6 families (`ptl_`).

| family | videos | rule | result shown |
|---|---|---|---|
| ptl_orb_candle | 15, 17 | Concretum paper: first 5-min candle up -> long, down -> short; stop at the OR's other side; 10R target | paper's results reproduced (QQQ) |
| ptl_orb_range_target | 89, 100, 94, 95 | 15-min OR; close outside -> enter; stop and target one OR away (1:1) | live paper trades, anecdotal |
| ptl_gap_down_short | 51, 53 | open 1-1.5% below yesterday's close and below the 20-day MA -> short with a bracket (QuantRocket idea) | demo |
| ptl_bb_reentry | 93 | prior close below the lower BB, this candle closes back above -> long; stop under the red candle; target 3x the candle's range | demo |
| ptl_ttm_squeeze | 113 | Bollinger inside Keltner (squeeze) turns off with momentum -> ride it | scanner |
| ptl_consolidation_breakout | 114 | last 15 closes within 2% -> breakout of that range | scanner |

## SKIP

Kalshi/Polymarket (prediction markets), breadth thrust (#54, needs NYSE
breadth), fear&greed / put-call (#109, external sentiment data), AI/LLM,
infrastructure tutorials.
