# Quantitativo (quantitativo.com Substack) — strategy notes (read 2026-10-03)

~50 posts in the sitemap. Rules read from the free parts of each post (some
are paywalled past the intro). Module `tt_quantitativo.py`, group
`quantitativo`, 6 families (`qt_`). All are DAILY signals held for days in
the source; here the entry is the next session's first bar and the exit the
session close or their "close above yesterday's high" rule (`signal`).

| family | post | rule | their result |
|---|---|---|---|
| qt_ibs_band | a-mean-reversion-strategy-with-211 | close < 10d high - 2.5 x 25d mean(H-L) AND IBS < 0.3 -> long; exit close > yesterday's high; 300 SMA regime | QQQ Sharpe 1.83-2.25, 15-20% exposure |
| qt_rsi2_dip | the-holy-grail-still-works, trading-the-mean-reversion-curve | RSI(2) < 5 (5..30) and close > 200 SMA -> long | stocks: 64% win, +0.40%/trade |
| qt_dv2 | a-different-indicator | DV2 < 10, close > 200 SMA, +6m return -> long | S&P stocks 65.8% win, Sharpe 1.01 |
| qt_turnaround | turnaround-tuesdays-on-steroids | Tue/Wed after two down closes -> long; exit close > yesterday's high | TQQQ 71% win, Sharpe 1.74 |
| qt_gap_down | mind-the-gap | gap down below yesterday's close, open > 200 SMA -> long, out next open | 55.6% win, Sharpe 1.66 after 10 bps |
| qt_qp_rarity | a-mean-reversion-strategy-from-first | rare 3-day decline (QP < 15) above 200 SMA -> long; rare surge below -> short | 64% win, Sharpe 1.10 |

## Not built

- intraday-momentum-for-es-and-nq: the Zarattini/Aziz/Barbon Noise Area
  breakout (ES Sharpe 1.25, NQ 1.67) -- already in the repo as
  `sandbox/research/zarattini_*`.
- fast-trend-following: NQ 1m "QTI" (Kalman-filter percentile indicator,
  long on cross above 5, short below -90; OOS Sharpe 1.17, PF 1.07 before
  costs) -- the indicator's formula is not published.
- Cross-sectional / multi-asset (one symbol per cell here): statistical
  arbitrage, triangulated stat-arb, network momentum, momentum long/short
  equities, learning-to-rank, asset embeddings, trend factor, IPOs, portfolio
  posts, beta hedging, tech vs treasury spread, slope/strength (paywalled).
- volume-shocks-and-overnight-returns (overnight holding), short-term basis
  reversal (futures basis), weekly digests, meta posts.
