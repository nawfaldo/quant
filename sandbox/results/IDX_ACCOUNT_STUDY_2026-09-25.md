# IDX one-account study, 2026-09-25

One Rp1,000,000 Stockbit account trading all 29 stocks with one setting per family. In-sample 2016-01-04 to 2021-05-03, holdout 2021-05-04 to 2026-09-24. Gates: max dd 35%, PF 1.05, half the years positive, 60% of neighbours.

| family | params | IS ret | IS dd | IS n | OOS ret | OOS dd | OOS n | OOS PF | OOS avg trade | OOS top stocks (pnl Rp) |
|---|---|---|---|---|---|---|---|---|---|---|
| dip | drop_atr=2.5 drop_days=3 exit=trail_2 regime=market slots=2 stop_atr=3.0 trend=sma200 | +224.2% | 28.0% | 25 | -27.9% | 30.3% | 35 | 0.59 | -1.24% | wifi +114,101 (3), mdka +83,084 (2), dewa +9,626 (2) |
| rsi2 | refused by profit_factor | best cell +368.7% | 51.0% | 61 | | | | | | |
| high_52w | exit=channel_20 lookback=120 near=0.02 regime=none slots=2 stop_atr=5.0 | +476.0% | 29.8% | 33 | +342.0% | 46.6% | 40 | 2.20 | +37.69% | wifi +5,641,905 (1), pgas +221,667 (3), ptba +98,924 (2) |
| donchian | channel=60 exit=trail_4 regime=market slots=3 stop_atr=5.0 | +342.8% | 29.0% | 40 | +52.1% | 46.9% | 52 | 1.27 | +4.14% | essa +393,775 (2), wifi +336,447 (4), dewa +309,049 (1) |
| ma_trend | exit=trail_4 pair=(10, 50) regime=market slots=2 stop_atr=3.0 | +614.0% | 30.3% | 33 | +44.0% | 55.3% | 43 | 1.37 | +2.92% | bumi +326,103 (4), adro +255,348 (2), mapi +134,659 (1) |
| dividend | days_before=15 exit=ex_5 regime=none slots=2 stop_atr=2.0 | +26.7% | 26.1% | 37 | +6.7% | 20.7% | 55 | 1.07 | +0.60% | akra +148,020 (8), adro +142,970 (7), excl +50,915 (3) |
