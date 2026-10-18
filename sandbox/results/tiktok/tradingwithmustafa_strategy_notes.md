# @tradingwithmustafa — strategy notes (screened 2026-10-04)

Grid: 294 posts — mostly MarketPal Pro (trend-following tool) promos, money mindset, webinars.
One concrete day-trading rule, repeated in ~15 posts: the "school run". 8 read.

`mus_school_run` (`sandbox/research/tt_tradingwithmustafa.py`): DAX, 15m, London open 09:00 UTC+2
(= 03:00 NY). Overnight range box (Asia high/low before the open); the FIRST 15-minute candle after
the open closes above the box -> long at its close (below -> short). Stop at the candle's far end or
the box middle; target the liquidity to the left (he quotes 1:1.2-1:2.5). He says it also works on
NASDAQ and in the New York session -> `open` 570 with the 18:00-09:30 overnight box.
Grid: open 180/570, stop candle/mid, exits rr_1.5/2/1.2/days_1.
Verify: no problems; London variant fires 0 on RTH-only grids (us500/btc), ~500-700 on others.

SKIP: MarketPal Pro (paid trend-following signals), stock picks, "liquidity hack" clips.
