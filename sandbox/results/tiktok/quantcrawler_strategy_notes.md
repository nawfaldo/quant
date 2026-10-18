# @quantcrawler — strategy notes (read 2026-10-03)

26 posts on the profile (2026-09-21..10-03; the older ORB-stats posts the
2026-10-03 search surfaced are no longer on the grid), 15 with English captions;
the rest are captioned in the description. Almost all are product promos (Ghost
order routing, Strategy Architect code generator) or a daily "$100 a day"
stock-pick series. Newest first.

## Rules kept (candidate families)

| # | date | family | rule as stated | their claim |
|---|------|--------|----------------|-------------|
| 4, 9, 10, 14, 21 | 2026-09-24..10-01 | qc_ema_cross | 9 EMA crosses above 21 -> long (mirror short); 20-tick stop, 40-tick target (= 2R); #21 restricts it to the session | example only, no stats |
| 2, 6, 8, 13, 17, 20, 22, 25, 26 | 2026-09-21..10-02 | qc_momentum_pick | daily ranker over US stocks $1-15: up over 20 AND 60 sessions, yesterday's volume >= 1.2-10x its 20-session median, close 1-4% above the 20-session average, RSI(14) ~57-60 -> buy at the next open. Single-instrument version: the same filters on yesterday's daily bar -> long at the open, held to the close | live experiment, no results yet |

## SKIP

#1, #3, #5, #11, #18 Ghost / NinjaTrader promos · #7 prop-firm rant · #12
TradingView invite-only rant · #15, #19, #24 "how to judge a backtest" (70/30
walk-forward; their M2K/MGC/XAUUSD settings shown on screen, not stated) · #16
discipline (stop + open target, walk away) · #23 indicator-cloning walkthrough.

## Notes for the build

- The stock picker is CROSS-SECTIONAL (rank 500 stocks, buy #1). This engine
  trades one symbol at a time, so only the filter is kept: it fires when the
  symbol itself would have qualified. Daily filters, intraday hold.

## Re-scroll 2026-10-04 (400 posts on the grid; 13 more read)

The rest of the grid is AI-prompt tips, Veo/Gemini videos, a "$100 a day
penny stock" challenge and promos (trade copier, Strategy Architect, QC
Radar). 13 trading posts read:

| family | posts | rule | claim |
|---|---|---|---|
| qc_session_level_fade | Asia High Rejection (10 APEX evals), QC Radar London High short, live-account rejection trades | resting limit at an UNTESTED Asia / London session high (sell) or low (buy), first touch only, fixed 1:2 ($500 / $1,000) | "$30,897 day P&L across eval accounts" |

Not built: the 15-minute 9:30 candle ORB with a 5m body close beyond the zone,
stop at the far side, 1:2 (= `fx_orb`); "AI optimised" MGC/ORB settings (not
disclosed); ChatGPT-bot calls (no rule).
