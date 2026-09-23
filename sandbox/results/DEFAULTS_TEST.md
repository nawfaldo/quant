# Defaults test — do the tuned constants earn anything the untouched ones do not?

> **This is a historical record and re-running the script will no longer
> reproduce it.** The a-priori column won the argument: on 2026-08-05 those
> constants were compiled into `live_trade/src/strategies/idk/` and the replicas were
> synced to match. `defaults_test.py` reads whatever is registered as its
> "compiled" arm, so it now compares a-priori against a-priori. The tables below
> are what the comparison said while the two differed, which is the only time it
> could be asked.

Run once at `sandbox/research/defaults_test.py`, evaluation window pinned
2025-02-12 .. **2026-08-01**, $1,000 Forex NQ account, 0.2 spread. Raw output in
[`defaults_test.json`](defaults_test.json). One trial charged per strategy.

## Why this and not another sweep

All three L2 strategies failed their Stage 6 gate, were compiled anyway on an
operator override, and carry a negative deflated Sharpe. That verdict cannot be
appealed by searching harder: `trials.json` is cumulative and append-only, so
every additional cell makes the haircut worse. The 18 months are spent as
selection evidence.

What is left is the one experiment that *adds* information without spending any:
run each strategy once on constants nobody chose, and compare. Round over
precise, symmetric unless there is a stated reason to differ, structural filters
kept and tuned cuts dropped — each value declared with its reason in the module
before it was run. No grid, no ranking, no best-of.

**Read the two windows together.** The incumbents were selected on history from
2025-02, so the full sample is partly their own training set while 2025-08 onward
is not. An incumbent that wins on the full sample and ties after 2025-08 has an
advantage confined to the window it was fit on.

## Phase 0 — the leak, from the file rather than by assertion

```
model 1  status=paper_only
trained through   2026-07-23
bars available    2025-02-12 .. 2026-08-04
OOS window starts 2025-08-01
-> LEAK: the compiled coefficients were fit on the whole OOS window
```

`ofi_ml_model.json` is a full-sample fit exported by `research/ofi_ml.py` as a
paper artifact. `nq_ofi_momentum.rs` compiles it as the sole registered OFI
variant, live. Every month of that strategy's "out-of-sample" record is inside
its meta-labeler's training set.

## Phase 1 — full sample .. 2026-08-01

| | trades | pnl | pf | mSharpe | pos | strk | worstQ | pts/tr | t | defl_t |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Hourly Delta Reversal — compiled | 218 | +333.96 | 1.41 | 0.48 | 0.78 | 2 | −67.47 | 11.14 | 2.08 | **−2.54** |
| Hourly Delta Reversal — a-priori | 297 | +201.97 | 1.16 | 0.26 | 0.72 | 2 | −115.51 | 4.35 | 0.78 | −3.84 |
| Deep OFI Momentum — compiled | 1110 | +328.49 | 1.26 | 0.70 | 0.78 | 2 | −6.32 | 3.31 | 2.92 | −0.83 |
| Deep OFI Momentum — a-priori | 2247 | +304.57 | 1.10 | 0.40 | 0.61 | 4 | −36.26 | 1.55 | 1.65 | −2.10 |
| HDR (fixed) — control | 243 | +131.03 | 1.15 | 0.19 | 0.67 | 3 | −140.80 | 4.14 | 1.21 | −3.42 |

## Phase 1 — from 2025-08-01, the window no incumbent was fit on

| | trades | pnl | pf | mSharpe | pos | strk | worstQ | pts/tr | t |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Hourly Delta Reversal — compiled | 162 | +401.27 | 1.69 | 1.05 | 0.92 | 1 | +23.98 | 16.38 | 2.69 |
| Hourly Delta Reversal — a-priori | 214 | **+409.46** | 1.41 | 1.03 | 0.92 | 1 | +6.72 | 12.33 | 1.88 |
| Deep OFI Momentum — compiled | 796 | +211.95 | 1.24 | 0.63 | 0.75 | 2 | −6.94 | 3.15 | 2.26 |
| Deep OFI Momentum — a-priori | 1619 | **+246.07** | 1.11 | 0.48 | 0.58 | 4 | −29.62 | 1.87 | 1.64 |
| HDR (fixed) — control | 179 | +305.79 | 1.51 | 1.05 | 0.83 | 2 | −23.07 | 10.42 | 2.67 |

## What the three rows say

**Hourly Delta Reversal — the tuning is fitted, the rule is not.** Compiled beats
a-priori by $132 on the full sample and *loses to it by $8* on the window it was
never fit on. Seven tuned constants — the 50/300 delta asymmetry, `rr` 1.25, the
Thursday exclusion, `short_trend_days` 35 — are worth nothing outside the fit
region. The counter-trend short gate, which has an a-priori economic rationale,
is kept in both and is not what is being questioned here.

**Deep OFI Momentum — the gate and the hour cut are worse than nothing.** Turning
off the leaked meta-labeler *and* reverting the post-hoc 11:00 entry cut improves
out-of-sample PnL by 16%. Phase 0's fix is therefore free: removing the leak costs
no performance because the leaked model was never contributing any.

## OFI ablation — which removal earned the improvement?

The a-priori OFI configuration changes two things at once, so its +16% cannot be
attributed to either. The 2×2, same window, 2 trials charged:

| | trades | pnl | pf | mSharpe | pts/tr | t | maxDD | pnl/DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ml on, 11:00 (compiled) | 796 | +211.95 | 1.24 | 0.63 | 3.15 | 2.26 | 52.90 | 4.01 |
| ml off, 11:00 | 1208 | +208.60 | 1.15 | 0.45 | 2.06 | 1.78 | 76.43 | 2.73 |
| ml on, 09:30 | 983 | +128.25 | 1.10 | 0.40 | 1.65 | 1.17 | 80.80 | 1.59 |
| ml off, 09:30 (a-priori) | 1619 | +246.07 | 1.11 | 0.48 | 1.87 | 1.64 | 72.65 | 3.39 |

**The meta-labeler is worth $3.35.** Inside the entry window it was trained on,
removing it moves twelve months of out-of-sample PnL from 211.95 to 208.60. That
is what a 12-feature logistic, a full-sample training leak and a second artifact
to keep in sync with the Rust file buy.

**Outside that window it is harmful.** `ml on, 09:30` is the worst cell in the
table. `research/ofi_ml.py` trains on the candidate pool generated from the
strategy's compiled defaults, which carry `entry_from = 660`; scoring 09:30
candidates with it applies the model to a population it never saw. The gate is
silently coupled to a parameter nothing documents it as depending on.

**The effects do not decompose.** ml off alone −3.35, hour cut off alone −83.70,
both off +34.12. An interaction that flips the sign of both main effects, on
t-stats between 1.17 and 2.26, is not a finding to build on — the only part with
a stated mechanism is the out-of-distribution effect above.

Recommendation: delete the ML gate rather than retrain it. It cannot be evaluated
as compiled, it earns nothing where it is valid, and it damages the strategy
where it is not.

## The number that decides all three

`defl_t` is negative in every row and both windows. Charging the a-priori
configurations **nothing at all** — the most generous defensible reading, since
nothing selected them — the best out-of-sample t is 1.88. Nothing here reaches 2.

| strategy | a-priori t, 2025-08 on | vs cumulative trials |
| --- | ---: | ---: |
| Hourly Delta Reversal | +1.88 | −2.75 (43,704) |
| Deep OFI Momentum | +1.64 | −2.11 (1,131) |

No configuration on this sample, tuned or untouched, can distinguish itself from
what searching returns on a strategy with no edge. That is a statement about the
sample, not a further indictment of the strategies: HDR needs roughly 400 trades
for t=2 at its per-trade dispersion and has 162 since 2025-08.

## Reproducibility

The window is pinned because `nq_l2_features_1s` is written continuously by the
live Bookmap capture — it gains rows every few seconds, so `_table_fingerprint`
changes between runs, the feature cache rebuilds every time, and an unpinned
"full sample" is a moving target. `PINNED_TO` should move only in whole months,
and never to make a number look better.

## What this does not settle

The defaults test says where the profit lives. It does not say the rules work —
nothing on this sample can. The only unspent evidence is forward: Bookmap has
been capturing since 2026-07-17. Compare live monthly PnL against the
out-of-sample distributions above, and write the kill rule down before the data
arrives rather than after.
