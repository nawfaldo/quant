# @mulhamtrading (Mulham Trading) — strategy notes (read 2026-10-04)

193 posts on the grid, screened by caption: ICT/SMC lessons, a 30-day
psychology series, "comment STRATEGY" funnels to Edge Skool. 10 posts read
(the ones claiming mechanical strategies). Module `tt_mulhamtrading.py`,
group `mulhamtrading`, 1 family (`mul_`).

| family | post | rule | claim |
|---|---|---|---|
| mul_two_lines | "My 2 Lines trading strategy. Built on M15. Fully mechanical." | lines at the opens of the 17:00 and midnight NY candles; above both -> sell only, below both -> buy only, between -> nothing; windows London 02-05 and NY 07-10 (NY time); a 15m weakness candle (takes the recent high, fails to close above) then an opposite-colour close -> enter | "7 years of backtesting" |

mul_two_lines barely fires on us500/btc: their study bars start 09:30, and
his windows end 10:00 NY. It is an FX/gold rule.

## Not built

Candle continuity theory / one-candle OHLC / daily bias from one candle
(concepts: where the next HTF candle opens vs the prior body; no entry
rule stated beyond "enter inside the candle"), "1 rectangle" and "1 wick"
scalps (strength = close beyond, weakness = wick-only rejection -> the
same sweep-reclaim idea as fx_liquidity_grab / fx_sweep_reclaim_bias),
ICT liquidity strategy "backtested" (closure vs no closure at swings: same),
Silver Bullet "72% win rate" (rules only in the paid course), ICT course
parts, psychology series.
