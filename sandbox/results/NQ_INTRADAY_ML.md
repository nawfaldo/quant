# NQ intraday ML: can a model find directional edge the rules do not?

Run 2026-08-15, `sandbox/research/nq_intraday_ml.py`, 145,143 level-two minute
bars 2025-02-12 .. 2026-08-13. **2025 in-sample, 2026 out-of-sample**, predicted
once per configuration. 96 cells, all charged to `trials.json` under
`NQ Intraday ML`.

Costs are the live **Exness Pro** account, not the 0.2 constant:
`combined_book` prices NQ at 0.300 bp of 29,731 = **0.892 points** of spread,
plus 0.2 slippage, no commission. **1.092 points per entry**, billed once at
entry.

## Why the experiment is shaped this way

The first plan was a meta-labeler on Hourly Delta Reversal: propose every signal,
let a model choose which to take. It is not runnable, and the reason is worth
recording because it is not obvious from the strategy's results page.

| pool | trades | 2025 | 2026 | sd | min detectable edge |
| --- | ---: | ---: | ---: | ---: | ---: |
| HDR compiled | 292 | 184 | 108 | 94.3 | **11.03** |
| delta 0, no trend gate | 507 | 305 | 202 | 88.4 | 7.86 |
| every gate off | 546 | 328 | 218 | 91.2 | 7.80 |
| every gate off, k=0.05 | 546 | 328 | 218 | 36.5 | 3.12 |

546 is the structural maximum — there are only ~2,280 RTH hour boundaries in 18
months and the direction condition fires on a quarter of them. HDR earns 1.44
points a trade; the sample cannot resolve anything under ~11. 328 training rows
is not a training set, and this is exactly the regime where the microprice
meta-labeler fit AUC 0.72 and delivered 0.51.

So the sample was turned up two ways: regress the **signed forward move** on
*every* RTH boundary rather than classifying 546 win/loss labels, and run it at
four period lengths.

## What was fixed before any number was read

* Four periods (15/30/60/120m), two feature sets, two model families, no tuning.
* Bracket is HDR's own — stop `0.2 x DAILY ATR`, target `2 x stop`, time exit at
  the period length, session flatten. The stop is a fraction of the *daily* ATR
  at every period, so it does not shrink with the timeframe; otherwise a
  resolution test silently compares two different strategies.
* Headline is `|prediction| > 0` — take a position at every boundary. No choice
  to make, and it maximises power. The keep-fraction curve below it is reported
  for shape; every point is another draw.
* Two controls: a 200-draw random-direction null on identical entries and
  brackets, and buy-and-hold.

`flow` is 11 columns from the traded bars (period delta, body, range, counts,
ATR, trend distance, session position). `book` is those plus 6 level-two
snapshot columns read from the minute *before* the entry bar (spread, three
imbalance depths, microprice tilt, replenishment). **The difference between them
is the answer to "is there edge in level two data".**

## Result: nothing, at any timeframe

All 16 declared cells, 2026, ranked:

| period | set | family | pts/trade | t | z vs null | OOS IC | IS IC |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 120m | book | gradient_boost | +4.97 | +1.00 | +0.82 | +0.030 | +0.005 |
| 120m | flow | ridge | +2.85 | +0.58 | +0.28 | −0.035 | −0.018 |
| 60m | flow | ridge | +1.79 | +0.63 | +0.90 | −0.001 | +0.033 |
| 120m | book | ridge | +1.38 | +0.28 | +0.15 | −0.006 | −0.067 |
| 30m | book | ridge | +1.28 | +0.85 | +1.51 | +0.035 | −0.008 |
| 15m | book | ridge | +0.14 | +0.17 | +1.72 | +0.026 | +0.038 |
| 15m | flow | ridge | +0.06 | +0.07 | +1.65 | +0.030 | +0.034 |
| 30m | book | gradient_boost | −0.10 | −0.07 | +0.52 | +0.006 | −0.033 |
| 30m | flow | ridge | −0.49 | −0.33 | +0.24 | +0.038 | −0.004 |
| 120m | flow | gradient_boost | −0.97 | −0.20 | −0.38 | −0.061 | −0.059 |
| 60m | flow | gradient_boost | −1.59 | −0.57 | −0.45 | −0.039 | −0.043 |
| 60m | book | ridge | −1.63 | −0.58 | −0.52 | −0.024 | −0.006 |
| 60m | book | gradient_boost | −1.92 | −0.69 | −0.58 | −0.011 | −0.065 |
| 30m | flow | gradient_boost | −2.12 | −1.39 | −0.64 | −0.022 | −0.008 |
| 15m | flow | gradient_boost | −2.74 | −3.37 | −1.94 | −0.047 | −0.003 |
| 15m | book | gradient_boost | −2.91 | −3.59 | −2.13 | −0.043 | +0.000 |

**7 of 16 positive, 9 negative.** The largest |z| against the random-direction
null across all 96 cells is **2.22, and it is negative**. The best positive z
anywhere is 1.96, on a 75-trade cell. Bonferroni across 16 declared cells is
t ≈ 2.9; across 96, t ≈ 3.3. Nothing is close from either direction.

Information coefficient out of sample spans −0.061 to +0.038. Its standard error
is `1/sqrt(n)`, which is 0.018 at 15m and 0.052 at 120m — **every IC in the table
is inside one standard error of zero.** The models do not predict.

### Level two adds nothing over the flow columns

The `book` set differs from `flow` by six order-book columns. Comparing OOS IC
within each period and family:

| period | ridge flow → book | boost flow → book |
| ---: | --- | --- |
| 15m | +0.030 → +0.026 | −0.047 → −0.043 |
| 30m | +0.038 → +0.035 | −0.022 → +0.006 |
| 60m | −0.001 → −0.024 | −0.039 → −0.011 |
| 120m | −0.035 → −0.006 | −0.061 → +0.030 |

Book helps in 5 of 8 comparisons and hurts in 3, by amounts smaller than the
standard error in every case. Adding the order book to a flow model is
indistinguishable from adding noise.

### Gradient boost overfits; ridge does not fit at all

All eight gradient-boost in-sample walk-forward cells are negative, and its worst
OOS cells are its most significant ones (15m, t −3.4 and −3.6). It is
reliably learning 2025 noise and inverting on 2026. Ridge sits at zero in both
windows, which is the correct behaviour when there is nothing to learn.

### Cost is not what kills this

Unlike the sub-tick L2 finding, the spread is **not** the binding constraint at
these horizons:

| period | mean \|forward move\| | entry cost as % of it |
| ---: | ---: | ---: |
| 15m | 31.3 pts | 3.5% |
| 30m | 42.9 pts | 2.5% |
| 60m | 60.7 pts | 1.8% |
| 120m | 86.6 pts | 1.3% |

At 1.3–3.5% of the move being predicted, a real edge would clear the Pro spread
comfortably. The models have no edge to clear it with. This is a different
failure from `l2-edge-is-subtick` and should not be conflated with it — there the
signal was real and too small to trade; here the signal is absent.

### Both controls beat everything

* **Random direction.** Best real configuration is +4.97 pts/trade at z = 0.82,
  i.e. inside its own coin-flip band.
* **Buy-and-hold.** +4,203 points on one unit over the 2026 window. The best ML
  cell totals +1,863 points (120m book boost, 375 trades). Every configuration
  loses to holding the instrument.
* **The rule the ML was meant to improve.** HDR itself made +5.28 pts/trade over
  107 OOS trades — a higher per-trade edge than all 16 ML cells, on a hundredth
  of the trade count.

## What this closes and what it does not

Closed: forward-return regression on NQ intraday boundaries, 15m to 120m, with
flow and order-book features, on 2025→2026. Do not re-run this family; the 96
trials are charged and a later result on this sample cannot survive its own
deflation.

Not addressed: passive/maker fills, which is the one direction where the
arithmetic changes rather than the signal. That needs a queue-position model the
repo does not have, and it is an execution-infrastructure problem before it is a
machine-learning one.
