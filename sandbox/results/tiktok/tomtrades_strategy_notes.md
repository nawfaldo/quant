# tomtrades (YouTube @itstomtrades) — strategy notes (read 2026-10-03)

59 videos (2024-2026, gold/USDJPY, Asia->London). 20 strategy/backtest videos
read; the rest are psychology, prop-firm and journaling content. One entry
model throughout -- a lower-timeframe market-structure shift (break of a low
then of a high, entry ~50% of the shift) -- placed by condition and timed by
the hourly candle. Module `tt_tomtrades.py`, group `tomtrades`, 3 families
(`tom_`). Results are his manual journal (2,400 trades with students).

| family | videos | rule | his claim |
|---|---|---|---|
| tom_hour_extension_fade | 21, 27, 30, 1 | new hourly candle overextends beyond the previous hour's high/low with momentum (~20 min); at 30-45 min into the hour a 1m shift against it -> reversal; Asia 2nd hour to London 3rd-4th hour | 77% win, ~1:2; win rate by 10-min bucket rises from 51% (min 0-10) toward the half hour |
| tom_london_tokyo_fade | 43, 44 | London 3rd hour; bias from position in the Tokyo range (upper half -> sells, lower -> buys); shift entry | 84% win |
| tom_range_reversal | 18, 5 | middle-timeframe (4-5h) range by regression slope; lower-timeframe reversals at its edges after a sweep | "$1M", >70% win |

## SKIP

Fractal-shift refinement (#35: a shift within the shift; 70% -> 88% claimed,
not separately mechanisable from the base shift), live backtest sessions (#3,
#15, #20 discretionary walkthroughs), prop-firm maths, journaling,
psychology, "how to become profitable" talks.
