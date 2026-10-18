# @nomanskii ("Evans") — strategy notes (read 2026-10-04)

157 posts on the grid (most are travel vlogs: Bali, Brazil, Hawaii); the 11
trading-rule posts read. He trades QQQ / SPY in the NY session, backtests
each rule and publishes the filters that fixed it -- kept as `filt` axes
(0 = his raw rule, 1 = filtered). Module `tt_nomanskii.py`, group
`nomanskii`, 7 families (`nmk_`).

| family | post | rule | his result |
|---|---|---|---|
| nmk_value_area_80 | "I backtested the 80% rule using the FRVP on SPY and QQQ over 239 trades" | open outside yesterday's value area, a candle closes back inside -> enter toward the other side; stop beyond the HOD/LOD. Filters: inside by 11:00; target closer than stop; aim 1/4 VA past it | raw 62% win, lost in 2026; filtered 116 trades, 76%, +29.3R |
| nmk_orb30_ema_pullback | "30 minute ORB with the 20 EMA on SPY and QQQ" | close beyond the 30m range, pullback closes through the 20 EMA, close back -> enter; stop beyond the pullback; target HOD. Filters: pullback stays well beyond the range; HOD <= 2R away | raw 35% win; filtered 75% (half off at 0.5R, +7.1R) or 47% to HOD |
| nmk_orb15_body | "15 minute ORB on QQQ over almost two years" | first 5m close outside 09:30-09:45 with breakout-side wick <= 25%; stop past the candle; 1:1. Filters: before 10:00, RVOL-at-time >= 1.5, gap in direction | raw 432 trades 51% |
| nmk_fvg_first2h | "Backtesting the FVG strategy on QQQ" | first 2h, retest of an FVG, candle closes back -> enter; stop behind the gap's first candle; 1:1. Filters: gaps before 10:30, no sweep of yesterday's H/L | raw 42%; filtered 63% (50% prior year) |
| nmk_trendsetting_pullback | "Momentum pullback strategy turned $100 of risk into $400" | big-body candle closing near its extreme on rising volume; enter at its 50% retracement (200 SMA confluence optional); stop beyond the candle; 4R | one trade |
| nmk_orb_body_50 | "Trading SPY as a beginner. The whole strategy", "QQQ trended all day" | nothing before 09:50; full-body breakout of the 15m range on rising volume; enter its 50% retracement; stop below the candle/range; half at 1:1, runner until a close back through the 9 EMA | 8.6 pts off 1.3 risk (one day) |
| nmk_three_tf_9ema | "Day trading so simple your grandma could do it" | daily, 1h, 15m all trending the same way; 5m pullback to the 9 EMA and a close back -> enter; stop past the pullback; 2:1 | - |

The filtered ORB/FVG variants can't fire on 30m bars (their time filters end
before a 30m bar can close after the range): they are 5m/15m rules.

Not built: TJR ICT 60s summary, "top 5 backtested strategies" list (no
rules), the 9:45 candle stat (an avoid-rule, folded into nmk_orb_body_50's
09:50 start).
