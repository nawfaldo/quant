# Commodity and Energy Family Study

Run 2026-08-09 against the commodity tables present in QuestDB. Selection used
2020-2024 where available, 2022-2024 for XPD/XPT, and 2023-2024 for
XAL/XNI/XZN. The sealed holdout is 2025-01-01 through 2026-08-06. Every result
starts with USD 1,000, risks 1.5% at the ATR stop with volatility throttling,
uses Exness MT5 lot multipliers and minimum steps, and charges 0.2 conventional
pips at entry.

The compact grid searched 7,776 cells per standard symbol and 8,352 on XNG
(which also included the Thursday 10:30 storage-report family). A candidate had
to clear profitable-year, PF, drawdown, fillability and numeric-neighbour gates
inside the selection period before its holdout was read.

## Retained candidates

| Symbol | Family | IS return | OOS return | OOS DD | PF | Trades | @10 pips | Null median | Edge vs drift | t |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| XNGUSD | Z-score continuation | +179.6% | +42.1% | 8.4% | 1.36 | 192 | +32.3% | +25.7% | 15.43 bp/trade | 1.72 |
| XNGUSD | Gap continuation | +74.1% | +39.9% | 7.0% | 1.44 | 150 | +33.9% | -4.9% | 16.88 bp/trade | 1.59 |
| XNGUSD | Opening-range breakout | +108.9% | +38.7% | 11.2% | 1.35 | 157 | +26.1% | +15.1% | 14.85 bp/trade | 1.54 |
| XNGUSD | Donchian | +152.3% | +32.4% | 11.2% | 1.42 | 142 | +22.5% | +2.6% | 14.28 bp/trade | 1.36 |
| XALUSD | Opening-range fade | +56.6% | +66.1% | 17.8% | 1.29 | 230 | +61.3% | -1.5% | 4.11 bp/trade | 0.98 |
| XALUSD | Overnight fade | +20.5% | +41.9% | 13.1% | 1.39 | 196 | +39.3% | +25.1% | 11.20 bp/trade | 1.99 |
| XALUSD | Z-score continuation | +37.8% | +37.9% | 12.9% | 1.43 | 96 | +37.1% | +9.8% | 13.99 bp/trade | 0.97 |
| XALUSD | Prior-range breakout | +31.1% | +31.2% | 10.9% | 1.32 | 103 | +30.3% | +19.0% | 12.96 bp/trade | 0.88 |
| XNIUSD | Z-score fade | +102.1% | +31.1% | 17.7% | 1.25 | 128 | +30.8% | +0.8% | 10.45 bp/trade | 1.83 |
| XNIUSD | VWAP fade | +189.6% | +13.7% | 14.6% | 1.09 | 144 | +13.5% | -23.0% | 6.02 bp/trade | 0.91 |

`Null median` is the median holdout return of three full searches in which the
signal direction was replaced by a deterministic coin flip while timing,
filters, sizing and exits remained unchanged. Missing null cells are omitted
from the median. `Edge vs drift` subtracts what the same side mix and holding
times would earn from unconditional 30-minute session drift.

## Primary cells

### XNGUSD — Donchian (cleanest conservative choice)

```json
{"channel":48,"exit_mode":"time_4","last_entry_minute":810,"stop_atr":1.0,"trend":"ema_50d","vol_mode":"none"}
```

This is a four-session Donchian breakout, filtered by the 50-day EMA, entered
no later than 13:30, stopped at one ATR and exited after two hours. It is not
dependent on the rolling-CFD overnight gap, has PF 1.42, and beats the null
median by 29.8 percentage points. The z-score cell has the higher return but a
much stronger random-search null (+25.7%).

### XALUSD — opening-range fade (highest return, provisional)

```json
{"breakout_atr":0.25,"direction":"fade","exit_mode":"trail_1.5","last_entry_minute":720,"range_bars":1,"stop_atr":1.0,"trend":"ema_50d","vol_mode":"none"}
```

Fade a move 0.25 ATR beyond the first 03:00-03:30 range, with a one-ATR initial
stop and 1.5-ATR trail, only in the direction permitted by the 50-day EMA. This
is based on only 2023-2024 selection history. It was 71.7% long during a
+25.1% always-long-session holdout, and its drift-adjusted t-stat is only 0.98,
so treat +66.1% as a forward-test candidate rather than an expected return.

### XNIUSD — z-score fade

```json
{"direction":"fade","exit_mode":"rr_2","last_entry_minute":780,"period":46,"stop_atr":1.0,"threshold_z":2.5,"trend":"none","vol_mode":"none"}
```

Fade a 2.5-sigma deviation from the two-session mean before 13:00, with a
one-ATR stop and two-ATR target. It beat the null median by 30.3 percentage
points and retained a 10.45 bp/trade drift-adjusted edge. Like aluminum, its
selection period is only 2023-2024.

## Rejected instruments and families

- XAGUSD: no family cleared the in-sample $1,000 selection and fill gates.
- XCUUSD: no family cleared selection; additionally, QuestDB uses the old
  2.8-6.6 quote scale while live Exness XCUUSD is around 14,000. Do not deploy.
- XPDUSD: the only IS survivor, momentum, lost 9.6% OOS with PF 0.93 and 22.5%
  drawdown.
- XPTUSD: momentum made only 0.7% on 48 trades; the other survivors lost money.
- XZNUSD: no credible holdout survivor. MA cross made 2.4% but had negative
  gross expectancy; the remaining families lost money or exceeded 20% DD.
- XNGUSD PDR exceeded the holdout DD limit at 20.3%; MA cross and VWAP lost.
- XALUSD Donchian exceeded 20% DD; gap was borderline PF 1.05; momentum/VWAP
  lost money.
- XNIUSD families other than z-score and VWAP lost money or had excessive DD.

## Baselines and caveats

Holdout buy-and-hold / always-long-session returns were: XNG -28.2% / -25.1%,
XAL +27.4% / +25.1%, XNI +9.1% / -28.1%. The selected XNG and XNI directions
therefore did not merely inherit the obvious long benchmark; XAL needs the
stronger drift caveat described above.

This study models entry spread only. It does not include swap, exchange/broker
commission, slippage, funding, or market impact. Gap and overnight families can
also react to synthetic CFD roll jumps; prefer session-contained families for
deployment unless roll dates are explicitly filtered. The next honest step is
to freeze the three primary cells and forward test them, not optimize them
again on 2025-2026.
