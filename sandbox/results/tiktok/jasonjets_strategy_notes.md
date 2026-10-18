# @jasonjets — strategy notes (read 2026-10-03)

26 posts on the profile (2026-02..10), 24 with English captions. Half are
dev-agency / app promos (Coding Captain, Quant IDE); the trading posts are
"I coded X and backtested it" with results, usually without the rule.
Newest first.

## Rules kept (candidate families)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 1, 2 | 2026-09-29..10-01 | jj_tsmom | time-series momentum (Moskowitz-Ooi-Pedersen style), 41 futures, sign of the trailing return -> long/short. Single instrument here | flatter than S&P but +8% / +17% in the S&P's worst quarters; passes a prop challenge 31.8% |
| 11 | 2026-07-03 | (merge into jj_tsmom, `side` long) | Antonacci dual momentum: own trailing return positive (absolute momentum) -> long, else out | +10.2% in a year, 20 trades, 80% win, Sharpe 1.4 |
| 3 | 2026-09-28 | jj_icc | "ICC" (indication, correction, continuation): a break of structure (indication), a pullback into the Fibonacci retracement of that leg (correction), then the move resumes (continuation) -> enter | poor Sharpe; mildly positive on USDJPY; ~70% win in MT5 |

## SKIP

#4, #5 volatility arbitrage on index options (options) · #6 AI day trade
(no rule) · #7 dream · #8 app promo · #9 no caption · #10, #12-#17, #23,
#26 dev agency · #18-#22, #24, #25 "coded a YouTube/TikTok strategy" results
with no rule stated (#19/#25 an ICT FVG strategy, #20 "a momentum strategy").

## Notes for the build

- TSMOM is a monthly cross-asset portfolio; here it is the single-symbol sign
  rule read at the session's first bar and held for the session (every cell is
  flat by the close), which keeps the signal but not the holding period.

## Re-scroll 2026-10-04 (75 posts on the grid; 6 more read)

The rest is agency/lifestyle and QuantIDE promos. The 6 strategy posts read
(viral YouTube strategy, trend vs reversal, RSI divergence "AI optimised",
EMA chandelier, Dow Theory swing) are AI-written code with no rule stated
on screen; nothing to build.
