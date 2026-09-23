# FX family study - 8 Exness Pro pairs, 30m, $1,000

Run 2026-08-17. Protocol: `exness_families.py select`, 27 families x 29,736 cells
per symbol = 237,888 cells total. In-sample from 2018. Priced on measured Exness
Pro spreads (Pro cost is the spread alone; commission measured 0.0000).
Data: Dukascopy 1m bid bars 2015-2026, New York wall-clock, imported 2026-08-16.

## IMPORTANT: these numbers are in-sample selection winners

Nothing here has faced a control yet. On this desk a coin-flip null has scored
+622% in-sample, and a random-direction crypto search earned +28% on a holdout.
The margin over the null is the only readable quantity, and buy-and-hold is the
second mandatory control - three prior survivors beat their null and still lost
badly to simply holding the instrument.

## Sizing bug fixed before this run

`money_per_point` multiplied `multiplier` by `fx_to_usd`, but MT5 reports
`trade_tick_value` already in the account currency. That double conversion was a
159x error on JPY crosses and 1.39x on USDCAD. Fixed at both sites; `margin_lot`
keeps its conversion because `price * contract_size` really is in profit currency.
EURUSD (fx=1.0) reproduced bit-identical after the fix, which is the regression check.

Results on disk for non-USD-profit symbols from BEFORE this fix are suspect:
xauaud, xagaud, xaueur, xageur, xaugbp, xaggbp, de40, fr40, stoxx50, uk100,
aus200, hk50, jp225, ethbtc. Not re-run.

## Survivors per symbol

| symbol | survivors of 27 |
|---|---|
| usdjpy | 14 |
| audjpy | 8 |
| gbpjpy | 6 |
| gbpusd | 3 |
| eurjpy | 3 |
| audusd | 2 |
| usdcad | 2 |
| eurusd | 1 |

## All 39 survivors

| symbol | family | ret% | dd% | trades | gross bp | vs drift bp | t | mSharpe | PF | pos months |
|---|---|---|---|---|---|---|---|---|---|---|
| usdjpy | vwap | 207.4 | 10.3 | 1416 | 3.34 | 3.31 | 4.75 | 0.364 | 1.312 | 56/84 |
| usdjpy | swing_zscore | 197.8 | 16.4 | 500 | 8.41 | 7.28 | 2.27 | 0.297 | 1.436 | 50/84 |
| gbpjpy | swing_zscore | 193.5 | 16.8 | 405 | 9.74 | 9.14 | 1.99 | 0.342 | 1.405 | 51/83 |
| usdcad | momentum | 153.0 | 11.3 | 1093 | 3.46 | 3.46 | 4.10 | 0.258 | 1.227 | 38/74 |
| audjpy | swing_zscore | 130.4 | 17.9 | 351 | 9.45 | 9.10 | 1.61 | 0.244 | 1.272 | 46/84 |
| gbpjpy | zscore | 118.4 | 8.6 | 636 | 5.11 | 5.11 | 2.83 | 0.360 | 1.402 | 56/84 |
| usdjpy | swing_donchian | 114.0 | 15.8 | 336 | 7.94 | 7.09 | 2.16 | 0.254 | 1.561 | 39/84 |
| usdjpy | donchian | 110.2 | 12.2 | 820 | 3.48 | 3.30 | 3.25 | 0.339 | 1.313 | 50/84 |
| eurusd | swing_donchian | 100.7 | 17.4 | 327 | 7.86 | 7.99 | 2.06 | 0.177 | 1.302 | 45/80 |
| usdjpy | volume_thrust | 97.6 | 10.8 | 822 | 3.50 | 3.54 | 3.20 | 0.300 | 1.301 | 50/84 |
| usdjpy | swing_ma | 95.6 | 9.4 | 211 | 12.46 | 11.13 | 1.99 | 0.250 | 1.547 | 38/79 |
| usdjpy | zscore | 93.8 | 7.4 | 537 | 4.29 | 4.13 | 3.42 | 0.343 | 1.476 | 52/84 |
| usdjpy | pdr | 92.8 | 13.4 | 1096 | 2.46 | 2.26 | 2.73 | 0.218 | 1.208 | 51/84 |
| eurjpy | swing_ma | 87.2 | 19.4 | 273 | 10.98 | 10.71 | 2.20 | 0.209 | 1.294 | 51/84 |
| usdjpy | keltner | 81.5 | 18.7 | 1785 | 2.05 | 2.41 | 2.75 | 0.141 | 1.085 | 44/84 |
| usdjpy | overnight | 75.0 | 12.6 | 939 | 2.31 | 2.21 | 2.85 | 0.254 | 1.225 | 39/72 |
| audjpy | key_reversal | 73.8 | 16.1 | 459 | 3.84 | 3.76 | 2.03 | 0.205 | 1.286 | 46/83 |
| usdjpy | range_expansion | 71.1 | 12.4 | 955 | 2.52 | 2.51 | 3.05 | 0.241 | 1.202 | 48/84 |
| audjpy | donchian | 70.0 | 12.3 | 658 | 3.23 | 3.21 | 2.55 | 0.261 | 1.211 | 39/69 |
| audjpy | zscore | 66.5 | 11.3 | 886 | 2.33 | 2.23 | 1.35 | 0.201 | 1.168 | 41/84 |
| usdjpy | key_reversal | 63.6 | 11.4 | 825 | 2.56 | 2.41 | 2.68 | 0.259 | 1.249 | 49/84 |
| audjpy | squeeze | 61.5 | 15.4 | 434 | 4.35 | 4.30 | 2.60 | 0.238 | 1.335 | 47/84 |
| gbpusd | key_reversal | 56.3 | 6.8 | 269 | 6.27 | 6.27 | 2.95 | 0.240 | 1.515 | 42/78 |
| gbpjpy | donchian | 54.5 | 14.0 | 664 | 3.31 | 3.15 | 2.34 | 0.174 | 1.187 | 40/84 |
| usdjpy | climax | 44.5 | 11.7 | 643 | 2.43 | 2.45 | 2.63 | 0.232 | 1.233 | 47/84 |
| audjpy | turn_of_month | 42.0 | 12.0 | 265 | 4.51 | 4.18 | 1.81 | 0.167 | 1.248 | 35/78 |
| usdjpy | ma_cross | 41.0 | 6.8 | 283 | 5.06 | 4.96 | 2.77 | 0.292 | 1.487 | 46/81 |
| eurjpy | vwap | 36.8 | 10.8 | 614 | 2.28 | 2.19 | 2.57 | 0.207 | 1.169 | 45/72 |
| gbpusd | overnight | 36.1 | 9.3 | 566 | 3.21 | 3.24 | 2.28 | 0.198 | 1.159 | 39/69 |
| audusd | zscore | 35.5 | 8.9 | 227 | 7.02 | 6.95 | 2.75 | 0.212 | 1.366 | 48/75 |
| audusd | gap | 33.4 | 8.4 | 396 | 3.00 | 2.94 | 1.28 | 0.218 | 1.194 | 43/73 |
| gbpjpy | range_expansion | 27.8 | 10.6 | 585 | 2.08 | 2.13 | 1.33 | 0.147 | 1.153 | 45/83 |
| gbpjpy | climax | 25.6 | 8.6 | 518 | 3.09 | 3.06 | 2.07 | 0.159 | 1.184 | 43/81 |
| audjpy | volume_thrust | 23.4 | 12.5 | 397 | 1.74 | 1.78 | 0.88 | 0.132 | 1.199 | 45/81 |
| audjpy | vwap | 20.4 | 4.4 | 545 | 2.13 | 2.11 | 2.42 | 0.211 | 1.226 | 41/70 |
| gbpusd | climax | 18.7 | 3.6 | 194 | 3.40 | 3.40 | 2.11 | 0.201 | 1.408 | 34/75 |
| eurjpy | zscore | 18.6 | 10.4 | 362 | 2.51 | 2.51 | 1.43 | 0.152 | 1.176 | 53/84 |
| usdcad | squeeze | 13.9 | 6.6 | 178 | 2.77 | 2.74 | 1.67 | 0.148 | 1.274 | 35/67 |
| gbpjpy | volume_thrust | 13.2 | 7.8 | 316 | 1.09 | 1.02 | 0.46 | 0.108 | 1.155 | 40/76 |

## What stands out

- USDJPY passed 14 of 27 families. When a gate passes half the hypothesis space
  the gate is usually reading instrument drift, not 14 independent edges.
- `zscore`/`swing_zscore` passed on all three JPY crosses (+130%, +193%, +198%)
  - the one pattern with a shared mechanism rather than a per-symbol accident.
- Dollar majors passed 1-3 each; EURUSD passed exactly one.
- Several survivors are weak on their own terms: gbpjpy volume_thrust t=0.46,
  audjpy volume_thrust t=0.88, audusd gap t=1.28.

## Next

`why` with coin-flip and random-entry nulls, then buy-and-hold, then an untouched
window. Prior family studies on new instruments (index CFDs, US stock CFDs, metal
crosses, macro cross-section) all produced in-sample winners that died out of sample.


---

# CONTROLS (added after validate + why)

OOS window 2025-01-01 -> 2026-08-16 (19.5 months, untouched by selection).
Nulls: 3 coin-flip seeds per family, each re-running the full 8,640-cell search
with randomised direction. Buy-and-hold measured on the same OOS window.

## Attrition

| stage | survivors |
|---|---|
| selection (in-sample) | 39 |
| positive out-of-sample | 14 |
| beat the coin-flip null | 11 |
| beat buy-and-hold too | 5 |

## The 5 that cleared all three controls

| symbol | family | OOS% | B&H% | margin | PF | dd% | n | BE bp | spread bp |
|---|---|---|---|---|---|---|---|---|---|
| usdjpy | range_expansion | +22.8 | +1.4 | +21.4 | 1.37 | 10.1 | 196 | 3.72 | 0.44 |
| usdcad | squeeze | +1.6 | -3.5 | +5.1 | 1.19 | 4.5 | 39 | 2.42 | 0.72 |
| usdjpy | ma_cross | +5.6 | +1.4 | +4.2 | 1.23 | 6.8 | 76 | 2.98 | 0.44 |
| usdjpy | pdr | +5.6 | +1.4 | +4.2 | 1.06 | 14.7 | 251 | 1.39 | 0.44 |
| usdjpy | key_reversal | +4.7 | +1.4 | +3.3 | 1.07 | 6.6 | 213 | 1.09 | 0.44 |

Only `usdjpy range_expansion` has a margin worth anything. The other four clear
by 3-5 points over 19.5 months, and `usdcad squeeze` does it on 39 trades.

## Killed, and how

- **The three biggest in-sample numbers all inverted.** gbpjpy `swing_zscore`
  +193.5% IS -> -30.2% OOS; usdjpy `swing_zscore` +197.8% -> -20.9%; usdjpy
  `vwap` +207.4% (t=4.75, n=1416) -> -8.9%. Cross-symbol recurrence of `zscore`
  on three JPY crosses was regime, not mechanism.
- **usdjpy `volume_thrust` was matched by chance.** Real OOS +31.2%, PF 1.58.
  Coin-flip seed 2 found a cell worth +54.6% IS and **+29.2% OOS** on the same
  data. Not distinguishable from a random direction assignment.
- **usdjpy `zscore` sits inside its own null band.** Real +10.0%; flips returned
  -20.3%, +7.9%, +15.8%.
- **audusd `zscore` and eurjpy `swing_ma` beat the null and lost to buy-and-hold.**
  +13.3% against +14.6% holding AUDUSD; +12.8% against +13.3% holding EURJPY.
  The coin-flip null does not catch drift - only buy-and-hold does.

## Verdict

One candidate worth a further look: `usdjpy range_expansion`, +22.8% OOS against
+1.4% buy-and-hold, PF 1.37, 196 OOS trades, breakeven 3.72bp against a measured
0.44bp spread (8x cost headroom). Not promotable on this evidence: it is one
survivor from 237,888 cells, and the desk precedent is that survivors of a single
holdout still die on a second untouched window.

Next step would be a second untouched window, not a live allocation.
