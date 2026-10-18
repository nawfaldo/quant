# @marlon_quantlab — strategy notes (2026-10-04)

Grid: 41 posts (English + German), each tests one idea on 6-7 years of NASDAQ. 10 read.
Mostly negative findings, worth knowing: silver bullet window lifts FVG win rate 41.4% -> 44.4% but
PF 0.80; best of 8 FVG setups (HTF + LTF FVG) +0.072R gross -> -0.20R after costs, OOS negative;
fading big moves is worse than random; ATR compression predicts a larger move, not its direction.

Coded (`sandbox/research/tt_marlon_quantlab.py`):
- `mql_open_candle_ema`: first 5m candle after 09:30 closes above EMA12 -> long, below -> short; held
  for the day (his +1,600%, 54.9% win, 33% DD over 1,700 trades) or stop + trail once in profit
  (946% at 1% risk, 19% DD). Exits days_1 / trail_0.25 / rr_2.
- `mql_big_move_cont`: bar move > 1.5 ATR (and volume > 2x avg) -> follow it for ~30 min (his +0.34R
  in-sample, weaker out of sample). Exits time_6 / time_2 / rr_1.
Verify: no problems.

Cross-check (@algovex, 2026-10-04): the same first-5m-candle / EMA12 rule with a trailing stop,
rebuilt by algovex: EURUSD 5m 9 years -7%, 32% win, PF 0.79 (1,200 trades); S&P 500 9 years +16%, PF 1.13.
Cross-check (@bot2profit part 11): the same rule on 19 markets, 32,985 trades: -0.22R per trade,
0 of 13 years positive ("946%" claim not reproduced). Treat mql_open_candle_ema as a debunked control.
