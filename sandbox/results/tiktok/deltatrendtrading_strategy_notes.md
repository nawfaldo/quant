# @deltatrendtrading (Thomas Skinner, QuantPad founder) — strategy notes (read 2026-10-03)

29 posts on the profile (2025-07..2026-10), 28 with English captions. Same
person as @quantpad (already extracted). This account is commentary: QuantPad
launch/promos, guru call-outs, options theory, prop-firm Monte Carlo. The
"ML version of ORB" post the search surfaced is not on the grid any more.

## Rules kept (candidate families)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 24 | 2026-07-26 | dt_rsi_dip_vol | "basic RSI mean-reversion" long; their feature study found it works better with deeper stretch (lower RSI, further below the 20-day mean) and in elevated implied/realized volatility (VIX), and worse when price is extended above its long trend -> VIX-based sizing | sizing on VIX: PnL/contract +36.5%, PF +9%, per-trade Sharpe +22% out of sample |

## SKIP

#1 LLMs on raw numbers · #2, #15, #23, #27 QuantPad launch / promos · #3 startup
pricing · #4 phone camera · #5, #6, #10, #16, #20 guru call-outs · #7, #8 Fed
probability from ZQ futures (forecast, no trade rule) · #9 AI risk · #11, #19
research process / contextual features (no rule) · #12, #13 theta decay · #14
"AI wrapper" · #17, #21, #22 prop-firm Monte Carlo · #18 indicators as ML
features · #25 Trump-post trading system (no rule) · #26 gamma exposure ·
#28 Kalshi South Park latency trade · #29 SPX options vs Kalshi range arb
(options + event contracts, not tradeable here).

## Notes for the build

- No VIX on most of the universe: "elevated volatility" is the symbol's own
  20-bar realized volatility above its 100-bar median; their VIX SIZING becomes
  a filter (`vol` "high"), since lots are fixed-risk here.

## Re-scroll 2026-10-04 (419 posts on the grid; 11 more read)

The grid is mostly options/vol education, guru takedowns, Monte Carlo and
prop-firm maths, politics. The rule-bearing posts are his on-camera codings:

| family | post | rule | his result |
|---|---|---|---|
| dt_tjr_session_bias | "I coded TJR's daily bias methodology" | London MANIPULATION-REVERSAL (takes Asia's low, closes above Asia's high; mirror) -> New York continuation that way | 162/227 transitions right on ES; ~50% for the reversal forecasts |
| dt_powell_rb_fvg | "Powell trades' strategy, coded and tested" | tap of the prior day's rejection block sets bias; 5m FVG beyond the leg's 0.5 fib, first tap -> enter; stop beyond the FVG; target near swing; low RR (1:1.5) best | ~22% TopStep pass chance, no statistically positive per-trade EV |
| dt_vp_trough_break | "I coded Alorah Ziva's strategy ... optimized it" | levels at the previous session's volume-profile troughs; breakouts only, with an ATR conviction bar and N confirmation bars | Sharpe 0.15 original; ~5x Sharpe, 4x returns OOS after his tuning |

His ICT PD-array series (order blocks, FVGs, IFVGs vs matched baselines)
found no edge; not built as families. TJR's full strategy, the Forever Model
(photo post, no rule text read) and the ML-ORB were not built.
