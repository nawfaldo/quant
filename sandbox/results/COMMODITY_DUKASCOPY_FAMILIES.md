# Commodity families: UKOIL and the six metal crosses

Run 2026-08-10 with `commodity_families_research.py` at its standing protocol:
USD 1,000 initial balance, 0.2 pips charged wholly at entry, 1.5%
volatility-throttled stop risk, MT5 lots, 4x notional ceiling. Nine families,
7,776 cells per symbol per pass, plus a three-seed coin-flip null that re-runs
the identical search with signal direction randomized.

Two sourcings were tried. The record of the first is kept below because the
comparison between them is the most useful thing in this file.

| Group | Source | Selection | Holdout |
|---|---|---|---|
| UKOIL | Dukascopy `E_Brent` 1m, from 2011 | 2020-2024 | 2025-01-01 to 2026-08-06 |
| Six crosses | Exness M30, continuous from 2021-07 | 2022-2024 | same |

## Verdict

**Nothing is promotable.** UKOIL's best family sits inside the range its own
coin flip produced. On the crosses, re-sourcing to three real selection years
did not rescue them: five of six still fail to beat their null, one is
untestable at this account size, and the single arguable survivor rests on 23
trades.

## Why the crosses were re-sourced

Dukascopy's cross archive holds 2012-2014 and then nothing until 2024, so the
requested 2020-2024 window was empty and selection ran on roughly eight months
of 2024. HistData lists the gold crosses in its index and renders year links for
them, but serves no files: their download pages carry an empty token while
XAUUSD and XAGUSD carry a real one. No free vendor covers 2015-2023.

Exness quotes all six and its M30 history is continuous from 2021-07 (below
that: about one bar a day to 2021-02, half-days to June). That yields three full
selection years, real broker bars from the venue that would fill the orders, and
measured contract specs. The previously assumed multipliers were close --
XAUEUR 115.5 against an assumed 116, XAGGBP 6744 against 6700 -- except the AUD
pairs, which were off about 8% because the assumed AUDUSD rate was 0.65 against
a real 0.706.

## The longer window did what a longer window should

Eight months of 2024 admitted a winner in nearly every family. Three years
admits almost none:

| Symbol | Families surviving selection, 8-month window | 3-year window |
|---|---|---|
| xaueur | 9 | 1 |
| xaggbp | 8 | 3 |
| xagaud | 8 | 2 |
| xaugbp | 6 | 3 |
| xageur | 8 | **0** |
| xauaud | 2 | 3 |

The null control shows the same effect from the other side. On the eight-month
window, XAGGBP's gate admitted a random cell 17 times in 27 attempts; UKOIL's
five-year gate admitted one 6 times. Across the re-run, 106 of 162 flip attempts
found no in-sample cell at all.

## Out of sample against the null

| Symbol | Real median | Real best | Nulls | Null median | Null best | Real beating null median |
|---|---|---|---|---|---|---|
| xaueur | +11.7% | +11.7% | 21 | -0.3% | +18.3% | **1/1** |
| xaggbp | -0.7% | +19.9% | 3 | +20.9% | +22.2% | 0/3 |
| xaugbp | -3.3% | -2.4% | 8 | +1.4% | +31.9% | 0/3 |
| xagaud | -7.2% | -0.6% | 3 | +11.2% | +12.1% | 0/2 |
| xageur | — | — | 1 | — | — | nothing selected |
| xauaud | +0.0% | +0.0% | 20 | +0.0% | +0.0% | untestable |

`xaggbp overnight` is the trap worth naming: +19.9% OOS, PF 1.88, drawdown
5.3% reads like a find, and it is *below* the median its own coin flip
produced (+20.9%). Read against zero it passes; read against the null it is
nothing.

`xaueur momentum` is the only cell that clears its null: OOS +11.7%, PF 2.18,
drawdown 4.5%, break-even spread 441 pips. It rests on **23 trades**, one family
out of nine on one symbol out of six, and its null's best run (+18.3%) still
exceeds it. That is what multiple testing produces, not a candidate.

## XAUAUD is untestable at USD 1,000

A gold cross lot is 100 oz and the harness caps notional at 25% margin, so the
smallest fillable position needs `price <= 1000 / (25 * 0.01) = 4,000`. XAUAUD
trades above that for the whole holdout. Real and null alike return n=0 --
selection ran, nothing could ever fill. Compare
[[thousand-dollar-account-is-margin-capped]]. Testing it needs a larger balance,
not a different signal.

## UKOIL, full 2020-2024 protocol

| Family | IS | OOS | PF | n | t vs drift | 0 pips | 1 pip | 3 pips | 5 pips |
|---|---|---|---|---|---|---|---|---|---|
| gap | +222.1% | **+27.2%** | 1.30 | 126 | +0.89 | +28.6 | +22.0 | +15.9 | +6.3 |
| overnight | +112.8% | +17.0% | 1.15 | 164 | +0.15 | +23.1 | +10.0 | -3.2 | -13.2 |
| momentum | +105.3% | +10.5% | 1.17 | 104 | +0.94 | +10.9 | +8.1 | +1.5 | -7.0 |
| vwap | +163.6% | -0.7% | 0.99 | 243 | -0.72 | +0.7 | -7.8 | -21.7 | -34.1 |
| orb | +179.0% | -4.8% | 0.96 | 209 | -0.37 | -4.2 | -13.7 | -27.4 | -38.5 |

Four of five beat the null median (-3.1%), the best single line in this study.
But the null's best run reached +54.8%, double UKOIL's best real result, and the
winner's t vs drift is +0.89. Break-even spreads: gap survives to 4.6 pips,
overnight breaks at 0.9, orb and vwap are negative at the 0.2 they were selected
under. Real Brent spreads are 3-5 points.

## Caveats

- **0.2 pips is roughly a tenth of the real cost.** The terminal reports 25-28
  point spreads on the gold crosses, i.e. 2.5-2.8 pips. The cost sweep, not the
  headline, is the honest column. Silver crosses and UKOIL reported spread 0 at
  read time, which means unavailable, not free.
- **Cross multipliers are a snapshot.** A lot's USD value moves with the FX leg.
  It cancels between risk sizing and P&L, surviving only in lot rounding and the
  margin ceiling -- but XAUAUD above shows rounding is not always inert.
- **UKOIL is a rolling CFD** with unadjusted roll gaps, not screened for here.
  Correcting that can only lower its numbers.
- **A cache race exists in `data._cached`.** At 14 workers two pool initializers
  died on concurrent writes to the same cache file. Verified harmless here: a
  single-threaded re-select of xaueur reproduced the parallel result exactly,
  because the crash happens in the initializer before any job is claimed and the
  pool respawns the worker. It should still be fixed before a run where a silent
  gap would not be caught.
