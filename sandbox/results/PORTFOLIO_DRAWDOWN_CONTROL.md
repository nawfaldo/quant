# Three sleeves, one $1,000 account: where the 37% drawdown comes from and what removes it

Book: `NQ Deep OFI Momentum` + `NQ Hourly Delta Reversal` + `BTC Maroy Ladder`,
forex, $1,000 initial, 2025-01-01 .. 2026-08-05, on the Rust engine
(`/api/combine`). 2025 in sample, 2026 out of sample.

| book | return | max DD |
| --- | --- | --- |
| all three | **+130.5%** | **37.49%** |
| OFI + Hourly Delta | +103.1% | 23.34% |
| OFI + Ladder | +91.9% | 21.69% |
| Hourly Delta + Ladder | +41.4% | 23.41% |

Modules: `research/portfolio_dd_diagnosis.py`, `portfolio_exposure.py`,
`portfolio_regime_control.py`, `portfolio_sizing_mix.py`, `portfolio_warm_oos.py`.

## Why it is 37% and not 23%

**It is not correlation.** Daily-return rho between the sleeves is -0.142
(OFI/Hourly Delta), +0.012 (OFI/Ladder) and -0.141 (Hourly Delta/Ladder). There
is no common losing day to filter out; diversification is already working.

**It is three risk budgets stacked on one balance.** Each sleeve sizes at its own
fraction of the *whole* account — 0.5% per trade for OFI, 1.0% for Hourly Delta,
a 2% ladder unit for Maroy — so adding a sleeve adds a budget rather than
splitting one. Each sleeve's own drawdown *as the shared account sized it*:

| sleeve | PnL | own max DD in book | trades |
| --- | --- | --- | --- |
| NQ Deep OFI Momentum | +$941.80 | 20.29% | 2440 |
| NQ Hourly Delta Reversal | +$242.34 | 17.61% | 299 |
| BTC Maroy Ladder | +$121.19 | 13.55% | 165 |
| **stacked** | | **51.45%** | |
| **diversification credit** | | **-13.96%** | |
| **book** | | **37.49%** | |

Diversification only ever subtracts from the stack. It cannot take the total
below the largest single sleeve, and here it removes 14 of 51.

**It is entirely a 2025 event.** At *every* exposure level the full-window
drawdown equals the 2025 drawdown exactly:

| | return | max DD |
| --- | --- | --- |
| 2025 (in sample) | +8.7% | 37.49% |
| 2026 (out of sample) | +80.9% | 15.93% |

The worst drawdown ran 2025-04-23 → 2025-06-19, taking the balance from **$1,095
to $860**. It happened early, on a balance that had not compounded, and 2025
carried essentially none of the return. Note also that 2025 is the window both NQ
sleeves were *selected* on — that drawdown is what they did on their own
training data.

**One more thing worth knowing:** 37.49% is a *mark-to-market* drawdown — the
engine marks open positions every bar. Measured on closed trades only the same
book draws 21%. The number you feel in the account is the first one.

## What fixes it

### The control: just turn the size down

Sizing is linear in equity, so a constant `k` on every sleeve moves return and
drawdown together and leaves Sharpe alone (1.79 at both k=1.0 and k=0.5). This is
the null hypothesis, and it is strong.

| | full return | full DD | 2026 return | 2026 DD |
| --- | --- | --- | --- | --- |
| k=1.0 | +130.5% | 37.49% | +112.1% | 14.22% |
| k=0.8 | +99.0% | 29.84% | +82.0% | 11.45% |
| k=0.6 | +60.5% | 25.03% | +55.3% | 9.10% |
| **k=0.5** | **+50.3%** | **20.05%** | +46.2% | 7.02% |
| k=0.4 | +34.1% | 16.04% | +32.6% | 4.65% |

Any regime rule has to beat *this line*, at matched drawdown, or it is a
complicated way to earn less.

### Regime rules that failed

Throttling off the book's own equity curve does not work. Standing down below a
moving average, or past a drawdown limit, mostly turned 2025 *negative* while
leaving the drawdown roughly where it was — the signature of a rule reacting
after the loss has happened. `ma window=40,low=0.0`: 2025 **-13.1%** at 40.51%
DD, worse than doing nothing on both axes.

(An equity-curve throttle also cannot be evaluated naively: the schedule changes
the curve that generates the schedule. Iterating to a fixed point does not
converge here — twelve rounds wander between 15% and 41% drawdown. The rules
above are therefore driven off a *reference-size shadow book* held at k=1, which
is feedback-free and is what a live system would actually run.)

Per-sleeve weights also failed. The weight vector with the best 2025
return-per-drawdown (Ladder only) **lost to the constant out of sample**, and
layering volatility targeting on top of it made things worse, not better. The
sleeves' relative efficiency does not persist.

### What worked best: volatility targeting off *price*, not off P&L

The engine already contains this idea — `live_trade/src/sizing/volatility_target.rs`,
an EWMA of daily returns with `multiplier = target / annualised vol`. It is dead
code on every path: `RunRequest::position_sizing()` returns `Ok(None)`
unconditionally, so `cfg.sizing` is always `None`, `VolTarget::new` is never
reached outside its unit tests, and `volTarget` / `volHalflife` / `volMaxMult` /
`volMinDays` are parsed off the request and discarded. (Verified: `BTC Maroy
Ladder` is +18.21% / 8.56% DD / 0.0148 average size with and without `volTarget`,
identical to the last decimal.) `/api/combine` also hard-sets it to `None`, and
even reachable it would *replace* each strategy's risk-fraction quantity with
`base_lot * leverage * multiplier` rather than scale it.

But its **signal** is the better one, and that is the main finding here.
`portfolio_price_vol.py` ports the construction and drives it through the
exposure schedule. Two different things were being measured:

- **price volatility** — EWMA of NQ's daily price returns: how violent the market is;
- **P&L volatility** — stdev of the book's own daily returns: how violent the results are.

The price signal is exogenous, needs no shadow book, cannot be contaminated by
the overlay it drives, and is measurable before the book has any track record.
It wins decisively:

| rule family | full window | warm 2026 |
| --- | --- | --- |
| **price volatility** | **18/18** | **18/18** |
| P&L volatility | 15/24 | 19/24 |

Full continuous window, capped at 1.0 (de-lever only):

| candidate | return | max DD | matched constant | edge |
| --- | --- | --- | --- | --- |
| price vol t=8, hl=40 | +49.5% | 13.75% | +22.3% | **+27.1** |
| price vol t=10, hl=20 | +77.4% | 17.63% | +34.1% | **+43.3** |
| **price vol t=12, hl=40** | **+89.3%** | **18.50%** | +34.1% | **+55.2** |
| price vol t=15, hl=20 | +134.7% | 23.72% | +55.5% | **+79.2** |
| price vol t=20, hl=40 | +157.7% | 26.88% | +73.5% | **+84.2** |

`price vol t=15, hl=20` returns **more than the untouched book** (+134.7% vs
+130.5%) with **14 points less drawdown**.

Routing each sleeve to its own market's volatility (NQ vol for the NQ sleeves,
BTC vol for the Ladder) is the more defensible construction — the Rust module
uses symbol 0 for everything, which sizes a BTC sleeve by NQ — but it measured
*worse* at matched drawdown here (+65.1% at 18.60% vs +89.3% at 18.50%), so the
shared NQ signal stands. That is likely sample-specific and not a principle.

### The weaker version: volatility targeting off P&L

Scaling exposure inversely with the reference book's own trailing realised
volatility beats the constant at matched drawdown. But the size of the win
depends heavily on how it is measured, and the first measurement was wrong:

| measurement | cells beating matched constant |
| --- | --- |
| 2026 run standalone (cold start) | 38/48 |
| 2026 segment of a continuous run (**warm**) | 19/24 |
| full continuous window | 15/24 |

The cold-start figure is inflated and should be ignored. A 60-day volatility
window has no history on 1 January, so `rule_volatility` returns 1.0 and the book
trades at **full size through the two largest winning months in the sample**
(+$414 and +$311), only throttling afterwards. `portfolio_warm_oos.py` exists to
remove that artifact by running continuously from 2025-01-01 and slicing.

Broken down by window length, only the slow one survives both honest views:

| vol window | warm 2026 | full window |
| --- | --- | --- |
| 20 | 2/6 | 4/6 |
| 40 | 5/6 | 2/6 |
| **60** | **6/6** | **6/6** |
| 90 | 6/6 | 3/6 |

Full continuous window, against the constant that reaches the same drawdown:

| candidate | return | max DD | matched constant | edge |
| --- | --- | --- | --- | --- |
| vol w=60, t=8 | +27.2% | 11.65% | k=0.25 → +17.5% | **+9.7** |
| vol w=60, t=12 | +52.9% | 18.11% | k=0.40 → +34.1% | **+18.7** |
| vol w=60, t=15 | +59.4% | 21.98% | k=0.50 → +50.3% | **+9.2** |
| vol w=60, t=20 | +97.8% | 28.48% | k=0.75 → +92.3% | **+5.5** |
| vol w=60, t=25 | +116.9% | 30.51% | k=0.80 → +99.0% | **+17.9** |

## Recommendation

For a ~20% drawdown budget: **price volatility targeting, 12% annual target,
40-day halflife, capped at 1.0**, applied equally to all three sleeves.

| | return | max DD | 2026 return | 2026 DD |
| --- | --- | --- | --- | --- |
| untouched book | +130.5% | 37.49% | +112.1% | 14.22% |
| flat k=0.5 (control) | +50.3% | 20.05% | +46.2% | 7.02% |
| P&L vol w=60, t=12 | +52.9% | 18.11% | +41.1% | 4.85% |
| **price vol t=12, hl=40** | **+89.3%** | **18.50%** | **+74.0%** | **8.59%** |

That is **+39 points of return over the constant at the same drawdown**, and +36
over the P&L-volatility rule. If a little more risk is acceptable, `t=15, hl=20`
gives +134.7% at 23.72% — the untouched book's return with two thirds of its
drawdown.

Unlike the P&L-volatility version, this is not a marginal improvement on
de-levering: the constant reaches 18.5% drawdown only by giving up two thirds of
the return, and the price-vol rule keeps it. The reason is that the April–June
2025 drawdown coincided with a high-NQ-volatility regime, which an exogenous
price signal sees *while it is happening* and an equity-curve signal only sees
afterwards.

## Shipped

`sizing/book_exposure.rs` compiles the policy in. It activates only when all
three sleeves run together — any other set, including any two of them, is
untouched — and both the backtest engine and the live runtime read the same
module, so the live book and its backtest cannot size differently.

| | return | max DD | Sharpe | 2025 | 2026 |
| --- | --- | --- | --- | --- | --- |
| before | +130.53% | 37.49% | 1.79 | +8.67% | +112.14% |
| **shipped** | **+143.09%** | **18.36%** | **2.32** | **+29.91%** | **+87.11%** |

Better than the research figure (+129.10% / 19.26%) for a reason worth recording:
the research schedule was built from level-two NQ closes, which begin 2025-02-12,
so the multiplier defaulted to 1.0 through January 2025 — and with it the Ladder
sat at 1.0 rather than 2.0. The engine feeds its own NQ stream, whose 90-day
preroll is strictly *before* the window, so warm-up finishes before the run
starts and the policy is live from the first bar. Causal either way; the shipped
version is simply not blind for its first two months.

Verified on the shipped build: ladder peak notional 3.20x equity against the 4x
margin ceiling; `OFI + Hourly Delta` still +103.06% / 23.34% and `OFI + Ladder`
still +91.87% / 21.69%; the standalone Ladder 2017-2026 still +670.03% / 18.81%
over 1402 trades. `SLEEVE_EXPOSURE_SCHEDULE` remains as a research override that
takes precedence when set.

## Caveats

- **The sample is one drawdown episode.** 19 months, and the entire drawdown is a
  single April–June 2025 stretch. Every conclusion about drawdown control rests
  on that one event.
- **2025 is not a clean training window.** Both NQ sleeves were selected on
  roughly that span, so fitting an overlay there fits on top of an already fitted
  book.
- **The price-vol result is the robust one; the P&L-vol result is not.** 18/18 on
  both views, monotone in both parameters, and it is a construction that was
  already in the codebase rather than one invented to fit this sample. The
  P&L-volatility rule's `w=60` cell was a 1-of-4 selection and should be treated
  as suggestive at best.
- **The NQ warm-up covers the drawdown, but only just.** NQ level-two closes
  start 2025-02-12 and `min_days=30` holds the multiplier at 1.0 until roughly
  2025-03-26. The April–June drawdown is after that, so the signal was live for
  it — but there is no margin, and a rule that needed 60 days of warm-up would
  have missed part of it.
- Several hundred cells were read here. Deflate any significance claim
  accordingly.

## Mechanism

`SLEEVE_EXPOSURE_SCHEDULE` names a JSON file of per-sleeve, per-day exposure
multipliers (`backtest/exposure.rs`). The engine applies the factor to the
*equity a slot is shown*, not to the quantity it returns — sizing is linear in
equity through both the risk leg and the margin cap, so this scales the position
correctly while keeping the cap and every internal equity-derived quantity
consistent with the size actually taken. The file is re-read once per run, so one
warm server sweeps candidates. Unset in production; verified to be an exact no-op
at 1.0 (+130.53% / 37.49%, identical to no schedule at all).

Combined-run trades now also carry `s`, the sleeve that opened them, which is what
makes the per-sleeve split of a shared-account run exact.
