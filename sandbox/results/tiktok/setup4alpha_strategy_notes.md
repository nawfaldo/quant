# setup4alpha (Substack) — strategy notes (read 2026-10-03)

~80 posts in the sitemap (RealTest backtests, Connors-style rules). Most
strategy posts paywall the rules; built from the free text only. Module
`tt_setup4alpha.py`, group `setup4alpha`, 3 families (`s4a_`), daily signals
entered at the next session's open.

| family | post | rule | their result |
|---|---|---|---|
| s4a_close_below_low | tested-5-buy-the-dip-patterns-spy | daily close < previous low -> buy; (engulf: and high > previous high); exit first close > previous close | SPY 2000-26: 61.1% win, CAGR 3.7%; engulf 66.5%, CAGR 1.5% |
| s4a_gap_below_low | the-simplest-gap-down-reversal-trading | open below previous low -> buy; exit first green day | SPY: 21% exposure, MAR ~ buy-and-hold, lower return |
| s4a_rsi3_dip | we-tried-mean-reversion-on-gold-and | RSI(3) < 30 and RSI(2) < 20 -> buy; exit RSI(3) > 80; SMA 200/252/500 filters | GLD: did not beat buy-and-hold |

## Not built

Paywalled rules: sp500-mean-reversion (73.7% win, 251 trades), high-win-rate
Bollinger, late-entry edge, small-cap breakout, all-time-high mean reversion,
simple-trading-strategies ($100k -> $1.7M), top-3 mean-reversion indicators,
low-drawdown Nasdaq mean reversion (Connors pullback + EWMA vol filter).
Gold Thursday-close to Friday-close (overnight hold, not expressible in a
session cell). MACD 12/26 on S&P stocks (covered by `tr_macd`). Cross-
sectional momentum, ETF rotation, leveraged ETF rebalancing, regime-filter
surveys, process/AI/psychology posts.
