# neurotrader (YouTube @neurotrader888) — strategy notes (read 2026-10-03)

23 videos (2022-2025), all with English captions. Python research on hourly
BTC; most videos are indicators/methods, a few end in a trading rule. All his
numbers are BTC 1h, before costs. Module `tt_neurotrader.py`, group
`neurotrader`, 7 families (`nt_`).

| family | videos | rule | his result |
|---|---|---|---|
| nt_donchian_close | 22, 1, 4 | close above the highest CLOSE of the last n (lagged) -> long, below lowest -> short, stop-and-reverse; #4 Turtle rule: only after a losing signal | PF ~1.05-1.08 at nearly every look-back; loss filter better (runs-test z=2.7) |
| nt_trendline_break | 5 | trendlines refit each bar on the previous 72 closes; close above resistance -> long; 3 ATR stop/target, 12-bar hold (then an ML filter) | always-in PF 1.035; bracket version 50% win, PF 1.02 before the filter |
| nt_hawkes_vol | 11 | Hawkes process of normalised range crosses its 95% quantile after being below the 5% one -> trade the direction since; exit below 5% | PF 1.07, 58% long win rate |
| nt_profile_levels | 12 | market-profile (KDE) peaks of 168 closes = levels; crossing up -> long, down -> short, always in | positive (stated as a demo) |
| nt_ma_bounce | 20 | MA 72 +- 0.5 ATR bands; leaving through the far side = bounce (traded here) | study: bounce vs penetration rates |
| nt_flag | 16, 17 | bull/bear flag & pennant via pivots; breakout of the flag; fixed hold | per-pattern hold-period returns |
| nt_head_shoulders | 15 | H&S with balance + symmetry rules; early confirmation at the right shoulder/armpit midpoint; fixed hold | per-pattern returns |

## SKIP

#1 permutation-test method (Donchian is the example, built) · #2 hierarchical
market structure (indicator) · #3 ETH-minus-BTC intramarket differencing
(needs two markets in one cell; this engine trades one symbol) · #6
reversibility, #7 visibility graphs, #8 VSA, #13 permutation entropy
(indicators, no rule) · #9 books · #10 harmonic patterns (found weak) ·
#14 data-mined PIP patterns, #19 GA candlestick patterns (mining procedures,
not a fixed rule) · #18 trendline algorithm (used in #5) · #21 ETH TVL
(on-chain data) · #23 volume seasonality bots.
