# The sixth wave — final report

27 new families, scored on 25 symbols at 30m. Run 2026-09-02/03.

**Bottom line: one family is worth keeping. The wave as a whole is not an
improvement, and two of its headline numbers are artifacts I introduced.**

Raw tables: `SIXTH_VERDICT.md`. Design and admission arguments:
`SIXTH_WAVE_FAMILIES.md`.

---

## 1. What was run

Per symbol, in priority order: `select` → `validate` → `why` (coin-flip null,
3 seeds), each stage gated on the last.

```
select    all 27 families, 50,112 cells
validate  families where select found a winner
why       families that ALSO made money on the holdout, 3 seeds
```

Protocol is byte-identical to the four earlier sealed waves — spread 3.7211 bp
on ethusd, slippage 0.2, $1,000, 1.5% risk, IS 2018-2024, holdout
2025-01-01 → 2026-08-16 — so old and new are directly comparable.

25 symbols scored. 10 not run: `ethbtc`, `eurusd`, `xagaud`, `xagusd`,
`xcuusd` selected nothing at all; `xngusd`, `xniusd`, `xpdusd`, `xptusd`,
`xznusd` were still queued when the run was stopped.

## 2. Headline result

```
scored          177 family-symbol results
PASS             66  (37%)   positive holdout AND above its own coin-flip null
fail vs null     33
fail on holdout  78
```

**PASS = 66. Corrected for duplication = 55.** Eleven of the 66 are the same
rule passing twice under different names (§5).

## 3. The one real find: `regime_breakout`

| family | scored | PASS | rate | median margin |
|---|---|---|---|---|
| **regime_breakout** | **14** | **9** | **64%** | **+25.6** |
| roofing | 16 | 7 | 44% | +19.3 |
| kalman | 8 | 3 | 38% | +21.1 |
| adaptive_pullback | 14 | 5 | 36% | +2.7 |
| half_life | 12 | 4 | 33% | −7.4 |
| quantile_break | 16 | 3 | 19% | −1.1 |
| value_area | 12 | 2 | 17% | −3.8 |
| pivot_exhaustion | 8 | 1 | 12% | +9.6 |
| fisher / tails / hurst | 2 each | 0 | 0% | — |

`regime_breakout` passes on **es, nq, de40, ethusd, jp225, usdjpy, eurjpy,
audjpy, stoxx50** — futures, indices, crypto and FX. Nothing else in the wave
travels across asset classes. Its best cell is the best result in the study:

```
es  regime_breakout   OOS +44.8%   null -12.8%   margin +57.6   dd 4.7   n=222   pf 1.50
```

It is Williams' volatility breakout with the trigger distance scaled by the
short-against-long volatility ratio, and `response=0.0` — which degenerates to
plain `volatility_breakout` — sits inside its own grid as a control. The
search did not pick that value, so the scaling is doing the work.

`vol_of_vol` (4/4), `runs` (2/2) and `cycle` (1/1) show 100% pass rates on tiny
samples; `vol_of_vol`'s four passes are all inside the collapse cluster (§5).

## 4. Ranked by margin, not return

The top of the list by holdout return is misleading. Ranked by margin over each
cell's own null:

| symbol | family | OOS % | null % | margin | dd | n | pf | bars fired |
|---|---|---|---|---|---|---|---|---|
| es | regime_breakout | +44.8 | −12.8 | **+57.6** | 4.7 | 222 | 1.50 | 4% |
| ethusd | cusum | +77.2 | +30.1 | +47.1 | 11.4 | 490 | 1.21 | 20% |
| ethusd | kalman | +67.2 | +25.4 | +41.8 | 14.6 | 465 | 1.20 | 30% |
| ethusd | roofing | +48.7 | +7.2 | +41.5 | 12.1 | 204 | 1.37 | 10% |
| nq | regime_breakout | +51.7 | +15.0 | +36.7 | 9.7 | 202 | 1.40 | — |
| gbpjpy | adaptive_pullback | +1.1 | −33.4 | +34.4 | 12.7 | 160 | 1.02 | — |
| jp225 | momentum_stack | +34.3 | +2.1 | +32.1 | 30.1 | 169 | 1.31 | — |
| ethusd | kendall | +56.9 | +26.6 | +30.4 | 16.6 | 145 | 1.33 | — |
| de40 | regime_breakout | +27.0 | +1.4 | +25.6 | 12.8 | 180 | 1.22 | — |
| jp225 | value_area | +29.9 | +6.5 | +23.4 | 21.7 | 162 | 1.26 | 72% |

Against that, the cells that made the most money and **failed**:

```
ethusd  value_area         +56.3   null +68.2   margin -11.8
de40    quantile_break     +50.5   null +62.7   margin -12.1
de40    adaptive_pullback  +29.4   null +86.8   margin -57.4
```

A coin flip on de40's `adaptive_pullback` grid made **+86.8%**. Any reading of
these returns without the null attached is worthless.

## 5. Problem one — six families are one rule

Nine of the new families share a direction engine:

```python
move = close[i] - close[i - session]
side = sign(move)            # a state test on a new statistic gates it
```

The search sets that state test permissively, because a looser gate keeps more
trades and `t ≈ edge × √n` rewards that. Signal-set agreement on (bar, side)
over the holdout, ethusd:

```
entropy <-> estimator   100.0%       bars each fires on, of ALL bars:
amihud  <-> entropy      98.4%         entropy    85.7%
entropy <-> vol_of_vol   97.8%         estimator  85.7%
entropy <-> jump         93.1%         amihud     84.4%
jump    <-> vol_of_vol   90.9%         vol_of_vol 83.8%
```

Clusters of passing cells that are one signal set:

- **btc**: amihud, entropy, estimator, vol_of_vol — 4 names
- **nq**: amihud, entropy, estimator, vol_of_vol — 4 names
- **jp225**: entropy, estimator, value_area, vol_of_vol — 4 names
- **ethusd**: amihud, jump, vol_of_vol — 3 names

**66 passes → 55 distinct signal clusters. 17% of the passes are duplicates.**

And **23 of 61 passing cells (38%) fire on ≥50% of all bars** — their own gate
is inert and they have degenerated to the fallback rule:

```
usdcad  cycle        fires on 92% of bars      de40    value_area   87%
jp225   entropy      87%                       usdjpy  kurtosis     85%
ethusd  vol_of_vol   84%                       usdjpy  kendall      82%
ethusd  amihud       84%                       ethusd  jump         80%
```

`usdcad cycle` is the clearest case: the only pass for the Hilbert dominant-cycle
family, and it fires on 92% of bars — the cycle measurement is doing nothing.

### Why the admission test missed this

Every family answered "name a bar where this fires and no existing family does",
and those arguments are **sound about the constructs**. A Hurst exponent really
is not a variance ratio. The test says nothing about the **cell the search
picks**, and the search can dissolve the distinction. A wave can pass the
admission test family by family and still be one hypothesis counted N times.

## 6. Problem two — the in-sample gain was trade count, not edge

ethusd, against all four earlier waves pooled (83 family slots, 106,920 cells):

| | winners | median t | max t | t≥3 | median IS % | median edge |
|---|---|---|---|---|---|---|
| OLD | 52/83 | 2.96 | **4.05** | 24/52 | +350% | **20.0 bp** |
| NEW | 19/27 | **3.32** | 3.94 | 16/19 | +447% | 19.4 bp |

Decomposed:

```
median edge/trade   old 20.0 bp   new 19.4 bp    0.97x   ← new is 3% WORSE
median trades       old   819     new  1,168     1.43x
sqrt(1.43)                                       1.19x
median t            old  2.96     new   3.32     1.12x
```

`t ≈ edge × √n`. The higher t is the trade count. Median grid size is identical
(1,152 cells each side), so it is not a grid artifact. btc repeats it: edge
15.8 → 16.0 bp, trades 681 → 1,048.

## 7. The cross-symbol picture

The apparent decline down the queue is **mostly my sort order**, not evidence:

```
correlation of SELECTED count with:
  queue position (I ordered by prior productivity)   -0.72
  in-sample years                                    +0.30
  spread in bp                                       -0.25
```

Flat read instead: **22 seven-year symbols averaged 7.2 of 27 families
selected**, falling to ~4 surviving the holdout. Short-history symbols averaged
2.7 — and face a weaker gate anyway
([[selection-gate-scales-with-history-length]]), so their selections are the
least trustworthy in the set.

Five symbols selected nothing at all: `ethbtc`, `eurusd`, `xagaud`, `xagusd`,
`xcuusd`. Seven more selected but kept nothing through the holdout: `aus200`,
`fr40`, `gbpusd`, `ukoil`, `xaggbp`, `xalusd`, `xaugbp`.

## 8. What is and is not comparable

**Comparable:** in-sample and holdout figures, old vs new. Same protocol, same
cost, same gates, verified byte-identical.

**Not comparable:** pass rates against the null. Only 6 of 52 old ethusd
families ever had a null run, and they were chosen for it *because they already
looked good* (5/6 passed). The old study never ran a comprehensive null. So
"37% of new families pass" has no old counterpart, and constructing one would
require running `why` across the old families — which nobody has done.

**The contamination:** these families were designed *after* I read which old
families passed the 2025→2026 holdout. That window is no longer clean for them
([[exness-survivor-pool-is-oos-conditioned]]). This biases *toward* the new
wave, which makes "no more edge per trade" a weaker result than it looks.

## 9. Verification

- 32 tests in `sandbox/tests/test_sixth_wave.py`, all passing.
- **No lookahead.** Context rebuilt from a truncated bar series; all 27 families
  produce identical signals on the overlap. Catches leaks in a context block,
  which an indicator-level test cannot.
- **No existing family moved.** All 3,262 regression-fingerprint cells identical
  before and after; 1,134 added, 0 removed, 0 changed.
- Indicators verified against series with known answers: Hurst monotone in
  return autocorrelation (0.294 → 0.633) and reading a pure drift as 0.5;
  entropy invariant to monotone rescaling; roofing filter zeroing a pure ramp;
  cycle recovering 16- and 30-bar sines.

**Two bugs found and fixed, both pinned by tests:** `cusum` thresholded one-bar
returns against an *annualised* volatility (out by √annual ≈ 90 at 30m, 23
events in seven years); `ou_half_life` clamped a non-reverting series to its
ceiling instead of refusing it. One documented claim was **corrected downward** —
Mann-Kendall's outlier robustness is 3×, not the immunity the docstring implied.

## 10. Recommendation

1. **Keep `regime_breakout`.** 9/14, median margin +25.6, works across four
   asset classes, and its own degenerate control sits in-grid and lost. It is
   the only result here that survives every correction in this document.
2. **Treat `roofing` and `kalman` as candidates**, not findings — 44% and 38%
   with decent margins, and both fire on few bars (1–10%), so their signal is
   real rather than a fallback.
3. **Do not promote anything from the collapse cluster** — `entropy`,
   `estimator`, `amihud`, `jump`, `vol_of_vol`, `value_area`. Their passes are
   one rule, and that rule is session momentum with a trailing stop, which the
   study already has.
4. **Fix the design before any further wave:** give each family a distinct
   direction engine, or bound the permissive branch of every state axis so a
   gate cannot admit most bars. Run `_sixth_overlap` after every `select` and
   read the cluster count *before* the returns.
5. **Nothing here is live-ready.** Every survivor still needs a window neither
   it nor its parents were selected on.

## 11. Reproducing

```sh
python -m sandbox.research._sixth_pipeline --workers 16 --resume   # finish the 5 queued
python -m sandbox.research._sixth_verdict  --out results/SIXTH_VERDICT.md
python -m sandbox.research._sixth_compare  --symbols ethusd        # old vs new, in sample
python -m sandbox.research._sixth_overlap  --symbols ethusd        # the collapse check
```

State: `sandbox/results/SIXTH_PIPELINE_STATUS.json`.
