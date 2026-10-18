# @chartfanatics — strategy notes (read 2026-10-04)

521 posts on the grid: podcast clips of guest traders (order flow,
liquidity, risk, prop firms), screened by caption. 10 clips whose captions
state a rule read. Module `tt_chartfanatics.py`, group `chartfanatics`,
4 families (`cf_`).

| family | clip | rule | claim |
|---|---|---|---|
| cf_runner_red_flip | "THIS 98% win rate strategy PRINTS", "THIS daily setup signals the DUMP" | after consecutive up days, mark yesterday's close: run up then flip red (close back below it) -> short; or gap down, bounce to it, fail -> short; stop above the HOD | "98% win rate" (small-cap runners) |
| cf_prevday_continuation | "If the previous day was trending long..." | a day that closed near its high (and up) -> long the next day; mirror | - |
| cf_pd_sfp | "EVERY trader MUST do THIS" | run below the previous day's low, close back above it -> long; stop below the new low; target the previous day's high | - |
| cf_consol_break_volume | "The ONLY strategy you NEED" (volume confirmation) | consolidation, breakdown on a volume spike (no spike = ignore), retest rejected -> short; 2:1 | - |

Not built: Okala's 80/20 levels (`o80_capitulation_fade`), the 5m ORB over
the prior bar with stop at LOD (`fx_orb`), 50% retrace "take the middle"
(vague), VWAP + first structure break (vague), "candle matrix" (every bar),
order-flow/delta/absorption clips (no L2 data on CFDs).
