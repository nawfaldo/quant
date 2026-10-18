# Algovibes (YouTube @Algovibes, "WiseQuant") — strategy notes (read 2026-10-03)

331 videos (2020-2026; titles shown machine-translated to Indonesian for this
login, audio English). ~78 rule/test videos read (rule passages + results).
Module `tt_algovibes.py`, group `algovibes`, 14 families (`av_`).

| family | videos | rule | his result |
|---|---|---|---|
| av_morning_momentum | 2 | volatile yesterday + NQ moved >= thr x yesterday's range from the open by 10:30 -> trade that way to 15:55 | 962 trades, Sharpe 1.22, PF 1.3 in-sample+validation; FROZEN forward test lost (Sharpe -0.56) |
| av_pd_level_retest | 3 | break of the previous day high/low, retest, close beyond -> trade; 1 ATR stop / 2 ATR target | NQ OOS +3.31 pts/trade (one of 3/10 classics that held) |
| av_ema_systems | 3 | 13/48 EMA cross; 9/20 EMA pullback; the pullback traded backwards | 13/48 +2.14 pts OOS; pullback -5.47; flipped +0.48 (PF 1.02) |
| av_first_hour_fib | 163, 164 | first-hour range D; first-hour close + 0.618D entry, -0.618D stop, +1.618D target | "quite convincing" (his words) |
| av_pd_box | 52 | previous day box; cross below its low -> buy, target the middle | 57% win on 21 trades, before costs |
| av_four_hour_fade | 49 | NY day's first 4h range; breakout then close back inside -> fade; stop at breakout extreme; 2R | EURUSD 32% win, -0.04R |
| av_rayner | 159, 250, 278, 289, 184, 214, 198, 235 | Rayner Teo daily dips above SMA200: RSI10<30; close < BB(20,2.5); Stoch10 < 5; 10-day low | RSI10 ~70% win (SPX) |
| av_tradepro | 201, 202 | close > EMA8 > 14 > 50 + StochRSI K x D; SL 3 ATR, TP 2 ATR | below the claimed 76% |
| av_short_momentum | 122, 127, 140 | price up >= x over the last hour(s) -> buy; 2% SL/TP or 2% trail | crypto, mixed |
| av_dip_bounce | 70, 258 | drop >= 1% in an hour then +0.5% bounce -> buy; max 5h / trailing stop | ETH 1h |
| av_bad_day_reversal | 107 | yesterday's return in the bottom (top) 10% -> long (short) | crypto paper replication |
| av_donchian_mid | 209 | close crosses the rolling high/low channel midline -> buy, sell at the upper band | crypto 1m-ish |
| av_stoch_rsi_macd | 241 | after Stoch K,D < 20: K,D back in 20-80, RSI > 50, MACD cross up -> long | Data Trader replication |
| av_bb_rsi | 126, 194, 254 | close < lower BB and RSI < 30 -> buy; exit close > upper BB and RSI > 70 | ChatGPT / LinkedIn strategy |

## Findings (not families)

Mass studies (2025-26): 6.7M / 1M / 3.2M configurations; after costs and
deflated-Sharpe / multiple-testing correction almost nothing survives
(SPY mean reversion: best adjusted p 22.7%; BTC candlesticks: 0 of 5,679;
trend following 10 of 8,080; daily is the only timeframe with candlestick
signal). Trend-following is two-thirds of the profitable strategies; momentum
and mean reversion work on equities, trend on BTC, volume is noise. Power of
Three 95% claim: 21% win, PF < 1. TJR liquidity sweep: best of 640 versions
Sharpe 1.6, wrong 4 times in 5. Trendlines (Tori Trades): BTC PF 0.87.
ETH 30-day TSMOM (his own) decayed (covered by `jj_tsmom`).

## SKIP

Python/data tutorials, bots, dashboards, APIs, portfolio theory, options
pricing, cross-sectional momentum/pairs/cointegration (multi-asset), grid
trading, funding-rate arbitrage, triangular arbitrage, overnight S&P anomaly,
JPMorgan VIX signal (needs VIX), Renko, ML playarounds, ChatGPT reviews.
