# QuantifiedStrategies.com (Oddmund Groette) — strategy notes (read 2026-10-03)

314 articles linked from "200 Trading Strategies (Free)" scanned in Chrome
(plain fetches hit a bot check). 89 print rules openly, 143 mix printed and
hidden rules ("you find the trading rules at the bottom of the article" is
shown to members only), the rest are general articles, portfolios, options,
sentiment/macro data we don't have. Module `tt_quantifiedstrategies.py`,
group `quantifiedstrategies`, 12 families (`qs_`). Daily rules: features
from completed sessions, entry on the next session's first bar.

| family | article | rule | their result |
|---|---|---|---|
| qs_connors_3day_hl | larry-connors-3-day-high-low-method | > SMA200, < SMA5, 3 days of lower high + lower low -> long; exit close > SMA5 | ETF mean reversion |
| qs_connors_r3 | larry-connors-r3-strategy | > SMA200; RSI2 down 3 days, first drop from < 60, today < 10 -> long; exit RSI2 > 70 | — |
| qs_connors_4of5_down | larry-connors-multiple-days-up-...down | > SMA200, < SMA5, 4 of last 5 days down -> long; exit close > SMA5 | 0.31%/trade |
| qs_ibs_rsi5 | nasdaq-qqq-mean-reversion | IBS < 0.25 and RSI5 < 25 -> long; QS exit (close > yesterday's high) | beat B&H since 2000 |
| qs_cci_dip | cci-trading-strategy | CCI9 < -90 -> long; exit close > prior high | PF ~1.8, DD ~23% |
| qs_vix_fix_rank | williamsvixfix | WVF percent rank > 98 over 10 days -> long; exit on an up close | 366 trades, 0.44%, PF 1.78 |
| qs_rsi2_overbought | what-happens-when-stock-markets-are-overbought | RSI2 > 95 -> BUY, hold N days | strength persists |
| qs_inside_day | inside-day-trading-strategy | inside day and yesterday's high < prior close -> long; QS exit | "untradeable" alone |
| qs_gap_ibs_rsi | gap-trading-strategies | yesterday IBS <= 0.25, RSI5 <= 45, gap-down open -> buy the open; exit up close | third condition not printed (gap assumed) |
| qs_sma_atr_band | sugar-trading-strategy | close beyond SMA350 +/- ATR7 -> follow; exit beyond the other band | sugar trend system |
| qs_open_low | ohl-trading-strategy | Open = Low over the first minutes -> long the next bar, out at the close (Open = High -> short) | intraday |
| qs_last_hour_extreme | spy-sets-low-or-high-in-the-last-hour | the day's low set in the last hour -> long next open, out by midday (high -> short, weaker) | buy weakness works |

## Not built

Already covered by other modules: RSI2 dip + QS exit (qt_rsi2_dip),
Turnaround Tuesday (qt_turnaround), IBS band, ORB, London breakout, MA/EMA
crosses (golden/death cross, 13/48, 9/20/50 EMA), Bollinger, SuperTrend,
Ichimoku, Donchian/turtle, Heikin Ashi. Rules hidden for non-members:
Double Seven, %b, Triple RSI, NR7, 5-day low, 3 days down overnight, rubber
band, Ultimate Oscillator divergence specifics, turn-of-month parameters.
Out of scope: rotations/portfolios (multi-asset), sentiment/macro/VIX/put-call
data, options, seasonality calendars (no single-market rule printed).
