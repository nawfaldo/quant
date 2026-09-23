# Index CFD families — AUS200, DE40, FR40, HK50, STOXX50, UK100, JP225

Run 2026-08-10 with `sandbox/research/index_families_research.py`.

**Result: nothing is promotable.** Fifty-five (symbol, family) winners were
selected on 2020-2024 and validated on the sealed 2025 to 2026-08 holdout. One
cleared every gate. It then died on a second untouched window, where its own
coin-flip control made money and it did not.

## Protocol

| | |
|---|---|
| data | Dukascopy 1m, imported 2026-08-10, resampled to 30m |
| in-sample | 2020-01-01 to 2024-12-31 (~1,230-1,290 trading days per symbol) |
| holdout | 2025-01-01 to 2026-08-06, sealed |
| second window | 2018-03-01 to 2019-12-31, untouched by every phase |
| balance | USD 1,000 |
| sizing | 1.5% volatility-throttled stop risk, MT5 lots, 4x notional ceiling |
| cost | zero spread, 0.2 index points on UK100, **as requested** |
| families | orb, overnight, pdr, donchian, ma_cross, momentum, gap, vwap, zscore |
| grid | 7,776 cells per symbol, 54,432 total |
| null control | full re-search with every signal direction replaced by a coin flip, 5 seeds |

Contract metadata is read values from the running Exness terminal, not
convention. The broker names the German index **DE30**; volume minimums span
0.03 lots (STOXX50) to 3.00 (JP225).

Asian sessions straddle NY midnight, so AUS200/HK50/JP225 load with a +6h shift
baked into their timestamps and their windows are padded for the fact that
Tokyo, Hong Kong and Sydney do not share New York's daylight-saving dates.

## What survived, and for how long

`aus200 momentum` — fade, 30-bar lookback, signal at 02:00 shifted (20:00 NY),
rr_2 exit, 1.0 ATR stop, no filters. Robust in 3 of 4 neighbours.

| window | return | dd | n | PF | drift-adj t | own null (max of 5) |
|---|---|---|---|---|---|---|
| 2020-2024 in-sample | +187.6% | — | — | — | — | — |
| 2025 to 2026-08 holdout | **+57.2%** | 17.7% | 372 | 1.30 | **+2.41** | +5.0% |
| at a realistic 0.77bp spread | +36.4% | | | | | |
| **2018-2019, untouched** | **−8.7%** | 21.6% | 217 | 0.90 | −0.34 | **+14.4%** |

It was the only cell in the study to reach t ≥ 2 and the only one to beat its
null on the holdout while staying profitable at a real spread. On a window it
had never seen it lost money, its profit factor fell below 1, and a coin flip
wearing the same timing, filters, exits and sizing made +14.4% where it made
−8.7%. Fifty-five tests at that significance level are expected to throw roughly
one false positive; this is it.

The nine other cells that beat their own null on the first holdout all failed
the second window too — every margin negative, from −4.5 to −48.2.

## Why the in-sample numbers are worthless here

The coin-flip control manufactured in-sample returns as large as **+134%** and
holdout returns up to **+86.5%** using nothing but randomized direction. Against
that, a headline like `jp225 vwap +209.5% in-sample, +32.9% holdout` is not
evidence — its own null max was +33.6%, so it finished *behind* noise.

Counts across the 55 winners: 14 beat their null's maximum, 1 reached a
drift-adjusted t of 2, 19 stayed profitable at a realistic spread, and after the
second window, 0 remain.

## Two methodology notes worth keeping

**Bar size is not a free parameter; stop distance is.** Re-running cells at
30m/10m/5m appeared to swing results wildly (+78% → +2.9% → +41%). That was an
artefact: stop distance is `stop_atr * ATR`, and a 5m ATR is a third of a 30m
ATR, so the "same" cell silently became a three-times-tighter strategy. Holding
the stop at a constant *price* distance, sign and profit factor are stable at
every resolution. Only 2-4% of exits resolve inside a single 30m bar.

**The zero-spread assumption is safe at this horizon and nowhere finer.** Mean
absolute move by holding time, against each symbol's real spread:

| symbol | spread | 1m | 5m | 15m | 30m | 120m | shortest horizon that pays |
|---|---|---|---|---|---|---|---|
| aus200 | 0.77bp | 1.5 | 3.4 | 5.9 | 8.4 | 17.2 | 30m |
| de40 | 0.54bp | 1.4 | 3.2 | 5.6 | 7.9 | 16.1 | 15m |
| fr40 | 0.79bp | 1.9 | 4.3 | 7.6 | 11.0 | 23.2 | 30m |
| hk50 | 2.26bp | 3.2 | 7.3 | 13.0 | 18.7 | 39.7 | 120m |
| stoxx50 | 1.22bp | 2.2 | 4.8 | 8.4 | 12.0 | 25.2 | 120m |
| uk100 | 0.70bp | 1.3 | 2.9 | 5.0 | 7.1 | 14.6 | 30m |
| jp225 | 1.56bp | 2.4 | 5.6 | 9.7 | 13.9 | 29.1 | 120m |

Nothing held under 15 minutes has budget on any of the seven. The search found
this without being told: UK100 and AUS200, the cheapest, selected the quickest
exit in the grid (`rr_1`), while HK50, STOXX50 and JP225 selected `trail_1.5`
and `rr_2` almost exclusively and hold 120-480 minutes.

Only UK100's spread was read live (2026-08-10, 0.57 points); the other six are
the broker's typical quotes and HK50/JP225 should be confirmed during their own
sessions before being relied on.

## Caveat on the long-hold winners

Several STOXX50, UK100 and AUS200 cells hold 300-480 minutes on a 510-minute
session. Those are not intraday trades, they are session-long directional bets
entered by an intraday rule, and European indices rose through most of
2020-2024. `edge_vs_drift_t_stat` exists to catch exactly this, and it does:
every one of those long-hold cells lands between −1.4 and +0.8.

## Five sleeves on one $1,000 account, no per-sleeve cap

`sandbox/research/index_portfolio.py`. Sleeves: `aus200 momentum`,
`fr40 gap`, `de40 gap`, `hk50 gap`, `aus200 pdr` — the holdout's five best by
return at a realistic spread. Each sizes at the full 1.5% stop risk off the
*shared* balance, so five open positions risk 7.5% at once. Only the
account-level margin ceiling brakes it. Equity is marked to market on every bar
of every symbol; drawdown is taken from that mark, not from closed trades.

| window | return | dd | n | PF | mSharpe | +months | coin-flip book (max of 5) | margin |
|---|---|---|---|---|---|---|---|---|
| 2020-24 in-sample | +266.9% | 31.9% | 3404 | 1.10 | +0.26 | 36/60 | +47.5% | +219.4 |
| 2025-26 holdout | **+155.3%** | 21.2% | 1071 | 1.23 | +0.58 | 14/20 | +52.3% | +103.0 |
| **2018-19 untouched** | **−34.0%** | 35.8% | 559 | 0.81 | −0.61 | 2/12 | +45.5% | **−79.5** |

At the requested zero-spread model the holdout reads +225.7% / dd 15.7%, and
2018-2019 still reads −24.8%. At a $10,000 balance the holdout is +153.8% and
2018-2019 is −34.1%, so the result is not margin arithmetic.

Holdout attribution (% of the $1,000 account): aus200 momentum +48.6, hk50 gap
+39.4, fr40 gap +37.1, aus200 pdr +22.2, de40 gap +8.0.

Three things the joint loop shows that summed streams would not:

- **The account, not the design, does the capping.** 437 of ~1,500 attempted
  entries on the holdout were refused for want of free margin — 29%. `de40 gap`
  takes 129 trades in the book against 200 standalone, which is why it
  contributes +8.0 having made +26.8 alone. "No per-sleeve cap" is largely
  overridden by the margin ceiling at this balance.
- **Drawdown stacks only on the bad windows.** Holdout book drawdown (21.2%) is
  no worse than the worst single sleeve (21.0%), but in-sample it is 31.9%
  against 21.0% and on 2018-2019 it is 35.8% against 24.2%.
- **Naive summing is wrong in both directions**: it reads +140.4% where the book
  makes +155.3% (shared-equity compounding) and −28.6% where the book loses
  −34.0%.

The book does not repair the sleeves. It levers them: the same five cells that
each failed the untouched window fail it together, with a third of the account
gone and 2 positive months in 12, while a coin-flip book made +45.5%.

## Rust port and Python validation

`live_trade/src/strategies/idk/index_book.rs` implements all four sleeves for the
engine; they are registered in the `idk` environment and active on the
`Exness-MT5Trial8` account (`mt5_account_strategies` ids 18-21).

Rust against Python, 2025-01-01 to 2026-08-06, $1,000, same fixed index-point
spread charged on both sides:

| cell | side | return | dd | trades | PF | win% |
|---|---|---|---|---|---|---|
| AUS200 Momentum | python | +42.34% | 19.52% | 372 | 1.24 | 46.2 |
| | rust | +45.42% | 18.38% | 372 | 1.25 | 46.2 |
| FR40 Gap | python | +28.39% | 10.05% | 111 | 1.37 | 39.6 |
| | rust | +28.74% | 12.47% | 111 | 1.37 | 39.6 |
| HK50 Gap | python | +29.42% | 13.23% | 351 | 1.22 | 50.1 |
| | rust | +25.43% | 16.92% | 350 | 1.20 | 49.4 |
| AUS200 PDR | python | +20.18% | 12.32% | 310 | 1.23 | 54.2 |
| | rust | +22.98% | 13.14% | 310 | 1.26 | 52.9 |

Trade counts agree exactly on three of four and by one trade on HK50. Entry
prices are identical on 372/372 AUS200 Momentum trades. Returns sit within
0.35-3.99 points, 1.2-13.9% relative.

TWO BUGS THE VALIDATION CAUGHT.

`market_spread` is an **account-currency amount**, not a price offset:
`fill_entry` divides it by `point_value` to reach price points. The first pass
passed index points, so HK50's 4.00 became 4.00/0.12745 = **31.4 points charged
per entry** and the sleeve read -27.5% against Python's +29.4%. The constants are
now points * USD-per-point.

The two registry guard tests (`idk_exposes_registered_strategies`,
`every_strategy_is_routed_to_the_market_its_name_declares`) both fired, which is
what they exist for -- the second panics on an unrouted market prefix rather than
letting it fall through to "nq", the failure that once cost 800 dollars.

WHAT THE RESIDUAL WAS, AND THE FIX (corrected 2026-08-10).

The first pass left 1.2-13.9% relative gaps that survived a zero-cost re-run, so
they were not the cost model. Broken down by Python exit reason, session-close
exits agreed on **0 of 123**. The engine's generic flattener closes on the LAST
in-session bar at that bar's close; the frozen sandbox cells exit on the FIRST
bar at or after the close, at its OPEN -- and `commodity_book.rs` / `index_book.rs`
already emit that exit themselves. Both were running, so the engine's flatten
fired first and the strategy's own exit never happened.

The fix is a `flattens_itself()` opt-in on the `Strategy` trait (default false),
set true on both book strategies and checked before the engine's flatten.
`session_end_minute` is still reported, because the LIVE runtime drives its own
session flatten from it ([[live-vs-backtest-parity]]).

It mattered most where a wide stop needs room to run. Standalone, 2025-2026:

| sleeve | before | after | Python |
|---|---|---|---|
| XALUSD PDR | -2.08% | **+22.20%** | +31.17% |
| XALUSD Overnight | +31.48% | +40.52% | +41.91% |
| XNGUSD Gap | +39.85% | **+39.85%** | +39.85% |
| XNGUSD Donchian | +36.27% | **+32.38%** | +32.38% |
| XNGUSD Z-score | +47.04% | **+42.11%** | +42.11% |
| AUS200 Momentum | +45.42% | **+42.35%** | +42.34% |
| FR40 Gap | +28.74% | **+28.39%** | +28.39% |
| HK50 Gap | +25.43% | +26.98% | +29.42% |
| AUS200 PDR | +22.98% | +22.43% | +20.18% |

Six of ten now match Python to the decimal. The bug was pre-existing and hit the
commodity sleeves harder than the index ones -- XALUSD PDR read as a losing
sleeve for as long as it was in the book.
