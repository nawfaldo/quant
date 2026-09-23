# Macro cross-sectional long/short — 13 instruments, 2019-2026

Run 2026-08-11. Module `sandbox/research/macro_cross_sectional_research.py`,
sealed output `sandbox/results/macro_cross_sectional.json`.

**Verdict: no candidate.** 0 of 328 cells passed in-sample selection. The
question the study set out to answer — whether ranking heterogeneous macro
instruments against each other finds an edge the single-symbol family studies
structurally cannot see — comes back the same way the stock cross-section did,
and for the same reason: **the gross edge that survives out of sample is 4-12x
smaller than the cost of the turnover required to harvest it.**

## Universe and panel

| sleeve | symbols |
| --- | --- |
| index | aus200, de40, fr40, hk50, jp225, stoxx50, uk100 |
| commodity | ukoil, xngusd, xagusd, xcuusd |
| crypto | btc, ethusd |

29,385 aligned 30-minute buckets over 1,959 sessions, 2018-03-12 to 2026-08-03,
15 buckets a session. These instruments do not share a clock — HK50 is dark
after 13:00 New York, FR40 and STOXX50 before 02:00, crypto never — so the panel
is restricted to 04:00–11:30 New York and to buckets where all thirteen quote.
XPDUSD and XPTUSD were excluded: their tables open in late 2021 and would have
truncated the panel to under five years.

2018 is warm-up for the 60-session volatility window. In-sample is
2019-01-01..2023-12-31, holdout 2024-01-01..2026-08-10. 2018-2019 was *not*
used as a second window — it is already spent on the index tables.

## The cost budget, computed before any strategy

| symbol | bp | symbol | bp | symbol | bp |
| --- | --- | --- | --- | --- | --- |
| btc | 0.05 | uk100 | 1.20 | jp225 | 2.78 |
| de40 | 0.97 | aus200 | 1.38 | stoxx50 | 3.61 |
| ethusd | 1.06 | fr40 | 1.46 | ukoil | 4.10 |
| | | hk50 | 2.06 | xcuusd | 4.94 |
| | | | | xagusd | 8.28 |
| | | | | xngusd | 20.94 |

Mean leg 4.07bp. A cross-sectional book turns over 100% of gross exposure every
rebalance, so at GROSS = 2.0x the drag is **8.13bp equal-weight / 5.94bp
inverse-vol per rebalance**, and it is invariant in `k` — more legs means
proportionally smaller ones.

Spreads are typical-Exness *estimates* converted to basis points against each
symbol's median price, not measurements. The 0–30bp sweep and `breakeven_extra_bp`
in the JSON are the decision instruments, not the headline.

## Gross edge available, in sample, zero cost

| family / direction | bp per rebalance | t | n |
| --- | --- | --- | --- |
| intraday / reversal | +1.00 | +4.27 | 13,944 |
| intraday / momentum | -1.00 | -4.27 | 13,944 |
| overnight / momentum | +1.10 | +0.31 | 1,162 |
| daily / momentum | +6.12 | +1.35 | 1,162 |

Intraday reversal is a **statistically unambiguous** signal — t = 4.27 on 13,944
rebalances — and it is roughly one eighth of what a rebalance costs.

## Selection: 0/328 cells passed

Gate: monthly Sharpe ≥ 0.5, max drawdown ≤ 20%, ≥ 100 rebalances, in sample, at
the estimated spreads.

| family | cells | dropped on trades | on drawdown | on Sharpe | passed |
| --- | --- | --- | --- | --- | --- |
| intraday | 128 | 8 | 120 | 0 | **0** |
| overnight | 8 | 0 | 8 | 0 | **0** |
| daily | 192 | 0 | 191 | 1 | **0** |

Every cell died on drawdown, not on the trade minimum, so this is a statement
about the universe rather than about the gate. The best ungated cell of each
family was carried to the holdout anyway — flagged as not a candidate, because
"the best the grid could do also failed out of sample" is evidence worth keeping:

| family | ungated best (IS) | holdout | t | null median | null pctile |
| --- | --- | --- | --- | --- | --- |
| intraday | -51.9%, mSh -0.93 | **-33.2%** | -2.39 | -29.4% | p36 |
| overnight | -56.2%, mSh -0.67 | **-38.2%** | -1.15 | -33.1% | p36 |
| daily | +180.5%, mSh +0.87 | **-20.0%** | -0.25 | -8.2% | p36 |

All three land below their own coin-flip null's median.

## The diagnostic that explains it: the zero-cost ceiling

Rerunning the entire grid with costs switched off separates the two possible
negative results — no signal at all, versus a signal smaller than the turnover.

| family | zero-cost IS | gross bp/rebal | t | with costs | zero-cost holdout | null pctile | edge multiple needed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| intraday | +144.9%, mSh 1.88 | +0.66 | +4.02 | **-100.0%** | +9.8% | p88 | **12.3x** |
| overnight | +20.3%, mSh 0.40 | +1.98 | +0.77 | -45.6% | +16.1% | p84 | 4.1x |
| daily | +209.3%, mSh 0.94 | +121.48 | +1.86 | +180.5% | **-16.4%** | p36 | 0.07x |

This is the whole study in one table, and the rows are arranged against each
other exactly the wrong way:

* **Intraday and overnight have real gross signal.** Intraday reversal is t=4.02
  in sample and still makes money out of sample with costs off (+9.8%, above 88%
  of its nulls). It is worth 0.66bp a rebalance against a 8.13bp bill — it would
  need to be **12.3x larger** to break even.
* **The only family whose gross edge exceeds the cost is the one whose edge is
  not real.** Daily momentum at 20-session lookback / 10-session hold earns
  121bp a rebalance, twelve times the cost, precisely because holding ten
  sessions amortises the turnover. But it returns -16.4% out of sample *with
  costs already switched off*, at the 36th percentile of its own null. The
  in-sample +209% is selection, not signal.

So cost amortisation and signal persistence are available here, but not at the
same time. Holding longer pays for the turnover and destroys the edge; holding
shorter keeps the edge and cannot pay for the turnover.

## What this does and does not close

Closed: cross-sectional ranking on **trailing return** across this universe, at
intraday, overnight and daily horizons, long/short dollar-neutral. Re-running
these three families on finer bars or different `k` will not change the answer —
`k` is not a lever on cost, and the binding constraint is a ratio of 12.

Not addressed, and the only directions with a live prior:

1. **A signal other than trailing return.** Everything here ranks on past price.
   Carry, term structure, or a cross-asset lead-lag rule would be a genuinely
   new hypothesis rather than this one re-parameterised.
2. **A universe with dispersion but shared cost.** XNGUSD alone is 20.9bp — 5x
   the panel mean — and it enters half the books. A cheaper sub-universe
   (indices plus crypto, mean ~1.6bp) would cut the bill roughly in half, which
   still leaves intraday needing ~6x.

The inverse-vol long-only basket of the thirteen returned +84.8% in sample
(mSharpe 0.77) and +50.8% on the holdout (mSharpe 2.08) — better than every
long/short configuration tried, which is the same result the stock study reached.

## Protocol notes

* Signals are built from bucket t's close and enter at bucket t+1's **open**.
* Rebalances are non-overlapping; a cell holding H buckets rebalances every H.
* Every signal is divided by the symbol's trailing 60-session volatility before
  ranking, and legs are sized inverse-vol. Ranking raw returns across this panel
  would sort it by volatility — bitcoin would hold both extremes almost daily
  and the "macro cross-section" would be a long/short crypto position.
* Gross leverage is fixed at 2.0x and is never an axis. Leverage sensitivity is
  reported after selection, in `leverage_sensitivity`.
* Nulls shuffle *which* symbols go long and short, holding k, the schedule, the
  weights and the costs fixed, so they price exactly the selection performed.
