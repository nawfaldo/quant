# Non-FX sweep - 38 Exness Pro symbols, 30m, $1,000

Run 2026-08-17. `exness_families.py` select -> validate -> why.
27 families x 29,736 cells x 38 symbols = **1,129,968 cells**.
In-sample from each symbol's first full year (2018-2024 typically); OOS
2025-01-01 -> 2026-08-16, untouched by selection. Priced on measured Exness Pro
spreads, sampled from MT5 tick history. Sizing uses the corrected
`money_per_point` (see the FX study for the 159x double-conversion bug).

## Universe

| class | symbols | n |
|---|---|---|
| index | de40 fr40 stoxx50 uk100 aus200 hk50 jp225 | 7 |
| commodity | xagusd xcuusd xngusd xpdusd xptusd xalusd xniusd xznusd ukoil xauaud xaueur xaugbp xagaud xageur xaggbp | 15 |
| crypto | btc ethusd ethbtc | 3 |
| stock | aapl amd amzn avgo googl jpm msft nvda orcl tsla tsm | 11 |
| future | nq (USTEC), es (US500) | 2 |

Excluded: CVX, GS, MU, PLTR have QuestDB tables but are not quoted on this
Exness account, so they cannot be priced.

## Attrition

| stage | count |
|---|---|
| cells searched | 1,129,968 |
| selection survivors (in-sample) | 236 |
| positive out-of-sample | 103 |
| beat buy-and-hold | 42 |
| beat the coin-flip null too | **9** |

## The 9 that cleared every control

| symbol | family | OOS% | B&H% | margin | PF | dd% | n | BE bp | spread bp | nulls (3 seeds) |
|---|---|---|---|---|---|---|---|---|---|---|
| ethusd | orb | +61.7 | -43.9 | +105.6 | 1.29 | 15.1 | 364 | 17.23 | 3.72 | no cell x3 |
| ethusd | momentum | +41.3 | -43.9 | +85.1 | 1.62 | 6.9 | 187 | 35.46 | 3.72 | no cell x3 |
| ethusd | key_reversal | +6.0 | -43.9 | +49.9 | 1.10 | 14.0 | 80 | 10.08 | 3.72 | no cell x3 |
| tsla | failed_break | +26.4 | -15.7 | +42.1 | 1.17 | 17.8 | 180 | 12.54 | 2.41 | +8.3 / no cell / -2.3 |
| tsla | vwap | +14.6 | -15.7 | +30.3 | 1.26 | 8.3 | 141 | 11.58 | 2.41 | no cell / no cell / +3.3 |
| tsla | high_52w | +7.9 | -15.7 | +23.6 | 1.29 | 5.5 | 35 | 85.95 | 2.41 | -8.1 / -5.4 / -4.1 |
| nq | swing_ma | +56.9 | +33.8 | +23.1 | 2.01 | 11.2 | 38 | 38.54 | 0.30 | no cell / -6.6 / no cell |
| xaugbp | turn_of_month | +72.6 | +53.3 | +19.3 | 2.65 | 12.9 | 40 | 56.12 | 0.91 | no cell x3 |
| de40 | ib | +33.4 | +32.6 | +0.8 | 1.21 | 12.8 | 243 | 4.36 | 0.19 | no cell x3 |

de40 `consecutive` (+33.0 vs +32.6 B&H, null: no cell x3) also technically
clears but its margin is +0.4 - index tracking, not edge.

## What the controls killed, and how

**BTC: wiped out by its own null.** Every candidate was matched or beaten by a
coin-flip seed. keltner real +24.1% against nulls +34.0/+30.0/+22.6; zscore real
+19.0% against +28.4/+32.7/+21.1; pdr real +23.2% against +22.2/+27.6. Nothing
on BTC is distinguishable from a random direction assignment.

**ETHUSD: 18 of 19 families were positive OOS and most are null artefacts.**
Coin flips reached +40.5%, +55.6%, +43.0%, +49.1% OOS. Real keltner (+26.5%)
is BELOW its own null (+43.0/+15.7/+49.1). Only orb, momentum and key_reversal
had no null find an in-sample cell in any seed. This is the crypto null baseline
behaving exactly as recorded.

**The buy-and-hold column is doing less work than it looks on crypto.** ETH fell
43.9% and BTC fell 32.8% over the OOS window, so a strategy that merely avoided
the drawdown clears the control. B&H only bites where drift was strongly
positive: de40 +32.6, nq +33.8, xaugbp +53.3.

**Metals and industrial commodities: near-total failure.** xniusd 17 selection
survivors -> every one negative OOS except zscore (+5.0% on 35 trades). xznusd
17 survivors -> 5 positive, the rest catastrophic (keltner -76.9%, orb -54.0%).
xagusd, xcuusd, xngusd, xpdusd, xptusd, xageur produced **zero** selection
survivors at all.

**Stocks: cost decides it, as before.** orcl (14.02bp spread) found no survivor.
Of 11 stocks only tsla clears anything, and tsla is the cheapest at 2.41bp.
nvda: 12 survivors, 9 negative OOS. amzn: 11 survivors, 9 negative.

**es: 9 survivors, 8 negative OOS.** Only range_expansion positive (+10.1%),
which fails buy-and-hold.

## Verdict

Nothing here is promotable. The three least-bad observations:

1. **ethusd momentum** - +41.3% OOS, PF 1.62, 6.9% dd, 187 trades, breakeven
   35.46bp against a 3.72bp spread (9.5x headroom), and no null seed could find
   an in-sample cell. The strongest single result in the sweep.
2. **tsla failed_break** - +26.4% OOS, 180 trades, 12.54bp breakeven on a 2.41bp
   spread, nulls clearly worse.
3. **ethusd orb** - +61.7% OOS on 364 trades, no null cell in 3 seeds.

All three are single-holdout survivors drawn from 1.13M cells. On this desk that
is exactly the population that dies on a second untouched window. Both ETH
results also sit on an instrument whose null baseline is the strongest of any
asset class here.

nq swing_ma (n=38), xaugbp turn_of_month (n=40) and tsla high_52w (n=35) are too
thin to read, and xaugbp turn_of_month is on the same gold-cross drift pattern
already documented as 77% drift.

## Next

A second untouched window. Not capital.
