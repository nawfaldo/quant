# @jsfinancials (Jackson Semenas) — strategy notes (read 2026-10-03)

32 posts on the profile: 18 videos (17 with English captions, read in full)
and 14 photo carousels. Photo posts carry only hashtags as text; the first
slide of 5 of them was read (risk-of-ruin math, prop-challenge odds, "DM me
mentor") and the rest skipped as the same kind of content.

## Rules kept

None. The account is research-hygiene education and mentorship marketing; no
post states an entry rule.

## Ideas worth knowing (not families)

- #3 (2026-09-30): measure a stop in "days of normal movement". Under pure
  noise a stop 0.5 day-ranges away is hit 62% of the time within one day (82%
  in a week), 1.0 range 32% (65%), 2.0 ranges 5%. Their rule of thumb: if the
  noise-hit chance over the holding time is under 20% or over 50%, rethink
  the stop; size from the distance. (Matches this repo's `stop_day` axis.)
- #8 (2026-09-26): prop challenge = a race between +8% and -10%. With no
  edge you pass 56% at any size; with a 55% edge, 2% risk passes 76%, 0.5%
  passes 98%; after costs a 48% system passes 47% at 2%, 23% at 0.5%.
- #12: write a kill condition before trading; size from the 1-in-20
  drawdown of 10,000 trade reshuffles.
- #17: look-ahead demo (fill at the same bar's open) -- the reason this repo
  enters at the next bar's open.

## SKIP

#1, #6, #11 clips · #2, #5, #7, #9, #13, #14 motivation/opinion · #4 rant
at an indicator seller · #10 survivorship bias · #15 their gold cross-asset
model (no rule) · #16 "M&M market" modelling framework · photo carousels.

## Re-scroll 2026-10-04 (496 posts on the grid; 8 more read)

Mostly quant-theory talk and "comment 'model'" funnels. Read: his "every
quant model" series (momentum, stat arb, trend following -- portfolio-level
descriptions, no single-market rule), his ATR stop optimiser (k x ATR14,
pick k by Sharpe - lambda x DD across rolling windows -- a sizing/exit
method, not an entry), and "full quant strategy in 7 days": XAUUSD next-day
bias from a state ID = daily moves of XAU, SPX, DXY, US10Y and VIX each
binned 0-4 (base-5 code), trade the historical expected next-day return of
the state ("x11.7 over 2015-2025, fees included", his claim). Not built:
needs DXY, US10Y and VIX daily series alongside gold, which the single-
symbol engine does not carry. Still 0 families.
