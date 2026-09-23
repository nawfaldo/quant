# The sixth wave — 27 new families

Added to `sandbox/research/exness_families.py` and `exness_indicators.py`
on 2026-09-02. **Nothing below has been scored yet.** These are hypotheses with
their plumbing tested; the numbers that matter come from `select`, `validate`
and `why`, and none of those has been run.

---

## Part 1 — what the existing study actually found

Read off every sealed `exness_families_*.json` in `sandbox/results` (39 symbols,
mostly 30m). "Pass" = positive out-of-sample **and** above the best of its own
three coin-flip seeds on the same holdout.

### Families that worked

| family | group | passed | tried | rate | best cells |
|---|---|---|---|---|---|
| skew | regime | 4 | 4 | 100% | tsm +30%, aapl +13% |
| high_52w | swing | 4 | 5 | 80% | xznusd +16%, tsla +8% |
| vol_regime | regime | 5 | 7 | 71% | btc +34%, ethusd +30% |
| **volatility_breakout** | geometry | **14** | **20** | **70%** | btc +29%, nq +38% |
| swing_ma | swing | 9 | 13 | 69% | nq +57%, ethusd +44% |
| rvol | flow | 6 | 9 | 67% | ethusd +43% |
| mfi | flow | 2 | 3 | 67% | ethusd +40%, btc +31% |
| pullback | geometry | 6 | 10 | 60% | ethusd +49%, de40 +34% |
| day_of_week | calendar | 7 | 12 | 58% | googl +49%, ethusd +48% |
| orb | core | 7 | 12 | 58% | ethusd +62%, btc +22% |
| range_expansion | structure | 9 | 16 | 56% | de40 +12% |
| floor_pivot | geometry | 9 | 19 | 47% | **de40 +60%**, ethusd +52% |
| xma_cross | xma | 15 | 33 | 45% | — |
| level_confluence | combo | 8 | 18 | 44% | **ethusd +59%** |
| confluence | combo | 4 | 13 | 31% | **ethusd +79%, de40 +55%** |
| nested | combo | 2 | 14 | 14% | **btc +108%**, ethusd +43% |

### Families that did not

`xma_slope` 1/14 · `cross_timing` 1/12 · `nested` 2/14 · `swing_donchian` 1/6 ·
`zscore` 4/18 · `cmf` 0/4 · `rsi` 0/3 · `avwap` 0/1 · `regime_switch` 0/1 ·
`rel_break` 0/2. The whole `night`, `almanac` and `horizon` set is untested —
no holdout has been run on it.

### The two patterns worth building on

**Exit and direction are close to settled.** Among 298 passing cells:

```
exit_mode   trail_1.5 187 | rr_2 54 | days_5 18 | time_4 15 | rr_1 14 | rr_3 7
direction   breakout 142  | fade 54  | follow 46
trend       ema_20d 123   | none 102 | ema_50d 73
stop_day    0.2 117       | 0.4 109  | 0.7 72
```

**One symbol dominates.** ethusd 46/52 (88%), jp225 25/42, de40 18/30, usdjpy
20/37, btc 26/53, nq 18/44 — and `aus200`, `jpm`, `xcuusd` pass nothing at all.
Read [[exness-families-30m-sweep-is-ethusd-and-nothing-else]] before treating a
cross-symbol count as evidence.

**What loses reads a smoothed price and nothing else.** `xma_slope`,
`cross_timing`, `cmf`, `rsi`. What wins is a statement about the *state of the
process* — is it trending, is it expanding, is anybody there, is this level
real. That is the reading the new wave is built on.

---

## Part 2 — the 27 new families

### `pathstat` — what KIND of process is this (7)

| family | reading | what it fires on that nothing else does |
|---|---|---|
| `hurst` | dispersion scaling across 5 horizons | a variance ratio is **one point** on that curve; H is its slope, so they disagree when a series reverts at 2 bars and trends at 16 |
| `entropy` | Bandt-Pompe ordinal entropy | the only reading **blind to magnitude** — the exact complement of `efficiency_ratio`, which is blind to order |
| `kurtosis` | 4th moment | the study had the 1st, 2nd and 3rd. Many tiny moves + two violent ones = ordinary volatility, enormous kurtosis |
| `autocorr` | ρ at **one named lag** | a variance ratio is a weighted *sum* over lags, so it cannot tell ρ(1)=+0.2 from ρ(1)=−0.2, ρ(2)=+0.4 — opposite trades |
| `runs` | Wald-Wolfowitz runs z | `consecutive` reads the current streak; this reads the whole window, so it fires when the streak is 1 and refuses a 5-bar streak inside chop |
| `kendall` | Mann-Kendall trend test | robust where `linreg` is not: on a 300-bar grind + one −20% bar it loses 2.0% where least squares loses 6.9% |
| `tails` | quantile tail ratio | `skew` is dominated by one cubed outlier; this cannot move by more than one rank |

### `micro` — liquidity and jumps, off an OHLCV bar (5)

| family | reading | disagreement |
|---|---|---|
| `jump` | Barndorff-Nielsen bipower jump share | ATR and `trailing_volatility` **add** jump and diffusion together; `climax` finds one big bar. This reads the regime |
| `semivariance` | Patton-Sheppard signed jump variation | a difference of two variances, not a standardised 3rd moment — the downside-premium claim, testable |
| `amihud` | \|return\| / volume | `rvol` reads volume, momentum reads return; only the **ratio** says a move was bought cheaply |
| `estimator` | Parkinson (or Garman-Klass) ÷ close-to-close | ATR *is* the range estimator and volatility *is* the close-to-close one; the module never divided them. High = whipping, low = gapping |
| `bulk_flow` | close-location volume classification | OBV gives a bar's **entire** volume to one side, so a 1-tick rise and a 2% rise count the same. This can say "heavy volume, buyers barely won" |

### `filter` — the things a moving average is not (5)

| family | reading | disagreement |
|---|---|---|
| `roofing` | Ehlers 2-pole highpass → SuperSmoother | every other filter here is a **lowpass**. Fed a pure ramp this outputs 0.0000 — no average-difference in the module does that |
| `fisher` | Ehlers Fisher transform | range position is ~uniform, so a stochastic of 90 is not rare. Gaussian-ising makes the same threshold mean the same rarity everywhere |
| `kalman` | local-level+slope state space | the only filter whose **gain adapts to observed noise**; and the slope is a *state*, not a difference of two smoothed points, so it turns when the data does |
| `fracdiff` | López de Prado fractional differencing | a z-score forgets everything past its window. Power-law weights keep it — so after a long slow drift `zscore` is silent and this is not |
| `cycle` | Hilbert dominant period | a **measured** time scale. Every other lookback here was chosen. Recovers a 16-bar sine as 16.5 and a 30-bar as 30.2 |

### `adaptive` — the rule sizes its own horizon (4)

| family | reading | disagreement |
|---|---|---|
| `half_life` | OU half-life → z-score over *that* window | uses a 12-bar window in a fast reverter and 200 in a slow one; and **refuses** when the fit is not reverting, where `zscore` happily trades a mean the price is walking away from |
| `cusum` | López de Prado symmetric filter | fires on a move made of twenty small steps, which no threshold rule can see; **resets**, so the same drift cannot fire it twice |
| `vol_of_vol` | dispersion of the vol ratio | a steady 2× vol fortnight and one alternating 0.5×/4× have the same mean — `vol_regime` and `vol_mode` cannot separate them |
| `quantile_break` | rolling quantile channel | Donchian is set by **one print**; after an unrevisited spike it refuses entries for a month while this narrows back in days |

### `fusion` — the winners, recombined (6)

Each had to fire where **neither parent** does — a gated family's trades are a
subset of its parent's, so it is bounded by it.

| family | parents | why not a filter |
|---|---|---|
| `regime_breakout` | `volatility_breakout` × `vol_regime` | scales the **trigger distance** by the vol ratio, so it triggers where the fixed rule has not reached and refuses breaks the fixed rule takes. `response=0.0` is the control, in-grid |
| `momentum_stack` | `nested` / `multi_horizon_trend` | both vote **booleanly**; this weights each horizon by its own z, so it fires when one horizon is overwhelming and two mildly disagree, and refuses three feeble agreements |
| `adaptive_pullback` | `pullback` × `hurst` | retrace depth scales with persistence — deeper in one regime, shallower in the other, so entries move both ways |
| `value_area` | new level source | `pdr` and `floor_pivot` are both **extremes**; a value area is set by volume. On a spike day they are far apart and only one has trade behind it |
| `stat_confluence` | `confluence`, new pool | `confluence`'s trend pool is five views of a smoothed price and agrees by construction. These six share **no input** |
| `pivot_exhaustion` | `floor_pivot` × `wick`/`climax` | `floor_pivot` takes feeble touches; `wick` takes rejections anywhere. This needs the rejection **at the level** |

### Rejected by the admission test

Recorded rather than quietly dropped:

- **Katz / Higuchi fractal dimension** — `D = log n / (log n + log(d/L))`, and
  `d/L` *is* the efficiency ratio. At fixed window it is a monotone transform of
  a family that already runs.
- **Bollinger %B** — `zscore` with a different constant.
- **Chande momentum oscillator** — `rsi` rescaled.

---

## Part 3 — verification

Nothing here is a performance claim; it is what was checked before the wave is
worth scoring at all.

- **32 tests** in `sandbox/tests/test_sixth_wave.py`, all passing.
- **No lookahead.** The context is rebuilt from a *truncated* bar series and
  every one of the 27 families produces identical signals on the overlap. This
  catches a lookahead in a context block, which an indicator-level test cannot.
- **Indicators measure what they claim.** Hurst is monotone in return
  autocorrelation (0.294 → 0.633 as φ goes −0.4 → +0.4) and reads a pure drift
  as 0.5; entropy collapses on a repeating pattern and is invariant to a
  monotone rescaling of price; the roofing filter zeroes a pure ramp; the cycle
  estimator recovers known sine periods; `rolling_quantile(1.0)` equals the
  Donchian channel exactly.
- **No existing family moved.** All 3,262 cells of the regression fingerprint
  reproduce byte-for-byte before and after; 1,134 new cells added, 0 removed,
  0 changed. (`exness_regression verify` cannot show this on its own — its
  sealed baseline is stale relative to the working tree and already reports
  687 changed cells on the *pristine* pre-change code.)
- **Every family fires and trades** on real ETHUSD 30m bars; none is a dead grid.
- **No family is all-categorical**, so the robustness gate is not void
  ([[all-categorical-axes-void-the-robustness-gate]]).

### Two bugs found and fixed, both pinned by named tests

1. **`cusum` was out by √annual.** `trailing_volatility` returns an *annualised*
   fraction and `cusum_events` accumulates *one-bar* log returns — a factor of
   ~90 at 30m. The filter produced **23 events in seven years** instead of
   ~86,000 signals across a sampled grid. It was alive, which is what made it
   dangerous: a dead family is obvious, a family returning a handful of trades
   at a flattering profit factor is not. Now thresholds on ATR/close.
2. **`ou_half_life` clamped instead of refusing.** On a trending series the
   fitted λ is a hair below zero by chance, the implied half-life is thousands
   of bars, and clamping reported the ceiling as a *measurement* —
   indistinguishable from a genuine slow reverter.

One documented claim was also **corrected downward**: the `kendall` docstring
said the Mann-Kendall reading was "unchanged" by an outlier the regression
felt. Measured, it loses 2.0% against least squares' 6.9% — three times more
robust, not immune. The docstring was fixed to match the measurement rather
than the test loosened to match the docstring.

---

## Part 4 — cost, and how to run it

```
                 30m                    daily
sixth wave       27 families   50,112   25 families   23,616
everything      110 families  157,032  107 families   77,994
```

Context build for the whole wave: **7.9 s** on a 60,000-bar series, ~40 arrays.
Only blocks a run's families declare are built, so `--groups pathstat` pays for
six of the twenty.

```sh
python -m sandbox.research.exness_families budget  --symbols ethusd --groups sixth
python -m sandbox.research.exness_families select  --symbols ethusd --groups sixth
python -m sandbox.research.exness_families validate --symbols ethusd --groups sixth
python -m sandbox.research.exness_families why     --symbols ethusd --groups sixth
python -m sandbox.research.exness_families_report  --scope sixth --bar-minutes 30
```

Individual groups: `--groups pathstat`, `micro`, `filter`, `adaptive`, `fusion`.

### Read this before reading any result

**The wave is out-of-sample-conditioned.** It was designed *after* looking at
which families passed the 2025-01→2026-08 holdout, so that holdout is no longer
clean for it ([[exness-survivor-pool-is-oos-conditioned]]). A `select` +
`validate` pass will produce winners; that is what a 50,000-cell search does
whether or not there is an edge. A coin-flip search on this data has returned
+622% at t=4.19 ([[coin-flip-control-beats-real-signals]]) and crypto has a
strongly positive null ([[crypto-null-baseline-is-strongly-positive]]).

`why` is not optional here, and passing it is still not enough: a survivor needs
a window neither it nor its parents were chosen on before it means anything.

---

# Part 5 — RESULTS (run 2026-09-03)

Interim. The pipeline (`select` → `validate` → `why`, per symbol, priority
order) is still running; ethusd is complete through `validate`. **Read Part 5.3
before Part 5.1 — it changes what the other two mean.**

## 5.1 In sample — the new families find no more edge per trade

ethusd 30m. "Old" pools all four sealed waves (83 family slots, 106,920 cells);
protocol verified byte-identical across all five files — spread 3.7211 bp,
slippage 0.2, $1,000, 1.5% risk, IS 2018-2024.

| | winners | median t | max t | t≥3 | median IS % | median edge |
|---|---|---|---|---|---|---|
| OLD | 52/83 | 2.96 | **4.05** | 24/52 (46%) | +350% | **20.0 bp** |
| NEW | 19/27 | **3.32** | 3.94 | **16/19 (84%)** | +447% | 19.4 bp |

The higher t is **not** more edge:

```
median edge/trade   old 20.0 bp   new 19.4 bp    0.97x
median trades       old   819     new  1,168     1.43x
sqrt(1.43)                                       1.19x
median t            old  2.96     new   3.32     1.12x
```

`t ≈ edge × √n`. The new families trade 1.43× more often at 3% *worse* edge per
trade, and the t-gain is smaller than the trade count alone predicts. Median
grid size is identical (1,152 cells each side), so it is not a grid artifact.
btc repeats it: edge 15.8 → 16.0 bp (+1%), trades 681 → 1,048 (1.54×).

## 5.2 Holdout — before the null

| | families | positive OOS | median OOS | max OOS | median dd | median pf |
|---|---|---|---|---|---|---|
| OLD | 52 | 47/52 | +36.9% | **+119.0%** | 11.9 | 1.29 |
| NEW | 19 | 18/19 | **+42.7%** | +77.2% | 11.4 | 1.26 |

Best new: `cusum` +77.2%, `kalman` +67.2%, `vol_of_vol` +57.4%,
`kendall` +56.9%, `value_area` +56.3%. Only `stat_confluence` lost (−3.1%).

**No null has run yet, so none of this is a result.**

## 5.3 Six of the nineteen are one rule

`entropy` and `estimator` returned *identical* holdout stats — +30.22%, dd 8.36,
274 trades. That is not coincidence, and chasing it found the wave's real
problem.

Nine of the new families share a direction engine:

```python
move = close[i] - close[i - session]
side = sign(move)                      # then a state test gates it
```

The search sets the state test permissively, because a looser gate keeps more
trades and `t ≈ edge × √n` rewards that. Signal-set agreement on (bar, side)
over the holdout:

```
entropy <-> estimator   100.0%      share of ALL bars each fires on:
amihud  <-> entropy      98.4%        entropy    85.7%
entropy <-> vol_of_vol   97.8%        estimator  85.7%
amihud  <-> vol_of_vol   96.5%        amihud     84.4%
entropy <-> jump         93.1%        vol_of_vol 83.8%
jump    <-> vol_of_vol   90.9%        jump       79.8%
```

A gate that admits six bars in seven is not a filter. All six collapse to
*session momentum breakout, ema_20d, calm, trail_1.5* — and they carry several
of the best holdout returns (+57%, +56%, +54%, +45%).

**19 winners → 14 distinct signal clusters, one holding six families.**

The most independent pairs agree on 3–5% of bars (`kendall`↔`stat_confluence`
2.8%, `roofing`↔`stat_confluence` 3.4%), so the wave is not uniformly
degenerate — but the cluster is where much of the apparent success sits.

### What this means for the admission test

Every family answered "name a bar where this fires and nothing else does" — and
those arguments are sound *about the constructs*. A Hurst exponent really is not
a variance ratio. The test says nothing about the **cell the search picks**, and
the search can dissolve the distinction. A wave can pass the admission test
family by family and still be one hypothesis counted N times.

`_sixth_overlap.py` measures this directly and should be run after any `select`,
before the returns are read. Two diagnostics matter:

- **cluster count** at 70% Jaccard agreement, vs. family count;
- **signal share of all bars** per winner — anything over ~50% means the gate is
  inert and the family is its own fallback rule.

### The fix, not yet applied

Either give each family a distinct direction engine, or bound the permissive
branch of every state axis so a gate cannot admit most bars. Both change what is
being measured, so neither was applied mid-run.

## 5.4 Verdict so far

On ethusd, in sample and on the holdout, the sixth wave is **not better**:

- no more edge per trade (−3%), just more trades;
- lower maximum on both t and holdout return than the old families;
- and six of its nineteen winners are one rule wearing six names.

The nulls are still running and can only lower these numbers further.
