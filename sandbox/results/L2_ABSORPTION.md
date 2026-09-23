# Absorption: the first NQ L2 candidate to clear both of its nulls twice

Run 2026-08-15. `sandbox/research/l2_wide_screen.py` (the search) and
`sandbox/research/l2_absorption.py` (the candidate). Costs are the live
**Exness Pro** account: 0.892 spread + 0.2 slippage, no commission,
**1.092 points per entry**, charged once.

**Status: strongest L2 result this repo has produced, and NOT promotable.** The
reasons are at the bottom and they are not negotiable.

## The search

Twelve mechanisms x four aggregation windows x four forward horizons = 192
gross-edge tests on 2025, repeated on 2026. Scored as pooled,
**observation-weighted** conditional means with a session block bootstrap —
never an equal-weighted session mean, which is the estimator that manufactured
`add_ratio@5m`'s fake +15.8 points last time. No strategy, no bracket, no
threshold tuning: a family that cannot show gross edge cannot be rescued by a
bracket, and finding that out costs 192 tests instead of 39,000.

**Zero of 192 cleared the Bonferroni bar (t 3.7) in either window.** Best on 2025
was `absorption@60m/240m` at t 2.87 — about where the noise maximum should sit.

What made the difference was not a t-statistic but **cross-window sign
consistency**, which the previous screens never computed:

| family | sign agreement | 2025 mean | 2026 mean |
| --- | ---: | ---: | ---: |
| `depth_dist` | 16/16 | −14.94 | −16.09 |
| `absorption` | **15/16** | **+4.53** | **+4.18** |
| `imbalance10` | 15/16 | −4.35 | −10.12 |
| `ofi_deep` | 14/16 | −3.88 | −8.80 |
| `microprice` | 7/16 | −12.81 | −0.46 |
| `replenish` | 5/16 | +2.22 | −0.83 |

Overall 123/192 cells kept their sign (64%, against 50% for a coin flip),
correlation between the two windows' edges +0.25.

Two readings, and only one of them is a test:

* **The negative families are all book-shape measures pointing the same way.**
  `imbalance5/10`, `depth_dist`, `standing` and `microprice` are correlated views
  of displayed liquidity, so this is one mechanism seen five times, not five
  findings — and it says displayed book pressure **reverts**. But the screen
  declared `+1` for all of them before running, so reading them as a fade is
  sign-flipping after the fact. That is a new hypothesis owed its own correction,
  not a result.
* **`absorption` is the only family positive in its declared direction.** Its
  sign was fixed in the screen's header before any number was read.

Also worth recording, because it sharpens the live edge: plain `delta` extremes
**continue**, they do not revert (+7.24 pts at the 60m window, t 1.46). Hourly
Delta Reversal's edge is specifically the delta/body *disagreement*, not delta
alone.

## The candidate

`absorption = delta / (|price_change| + 1) / trade_count` — aggressive volume
that arrived and did not move the tape. **Faded**, on the exhaustion thesis.

Declared before any PnL was computed and not swept: 15m window, `|z| >= 2.0`
against a causal trailing 20 sessions, entry at the **next** bar's open, holds
120m and 240m (both horizons the screen declared), stop `0.2 x daily ATR`
inherited unchanged from the compiled strategy, no target, one position at a
time, session flatten.

5,451 firing minutes become 652 trades at a 240m hold once occupancy is applied.

| hold | window | n | net pts | median | drop best 5 | t | PF | z vs coin-flip | z vs random-entry | ret% |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 240m | 2025 | 383 | **+11.14** | −9.34 | +2.26 | 1.67 | 1.383 | **+2.37** | **+2.01** | +40.2 |
| 240m | 2026 | 254 | **+16.34** | +1.03 | +9.11 | 2.25 | 1.443 | **+2.15** | **+2.68** | +52.6 |
| 120m | 2025 | 526 | +3.66 | −2.59 | −1.21 | 0.89 | 1.138 | +1.66 | +0.95 | +13.5 |
| 120m | 2026 | 331 | +7.52 | +1.16 | +2.78 | 1.50 | 1.234 | +1.66 | +1.50 | +30.8 |

### The random-entry null is the one that matters, and it was wrong first

The first version sampled from *other firing minutes*, which only re-tests the
occupancy rule and assumes away the question. The signal fires 55-60% long inside
two windows where NQ rose ~3,900 and ~3,500 points, so "be in the market, mostly
long, with a wide stop" was a live explanation for the entire result.

The corrected null draws arbitrary RTH minutes at matched long/short counts with
the same stop, hold and flatten. The candidate passes it **more strongly** than it
passed the broken one: z 2.01 and 2.68 at the 240m hold. The ambient pool earns
**−0.49 and −1.44 points per trade** — arbitrary entries with this bracket lose
money to drag, which is `zero-is-the-wrong-backtest-baseline` showing up as a
measured number. Absorption minutes are genuinely better moments to hold.

### What is fragile

**2025 is 80% five trades**, against a −9.34 median. April 2025 alone contributes
+2,292 of +4,268. Drop the best five and per-trade edge falls +11.14 → +2.26.
2026 is healthier (top 5 = 45%, median +1.03, +9.11 after dropping five) but two
months carry 83% of it. This is a fat-tailed, volatility-event payoff. The
t-statistics assume a normality the trade distribution does not have, so read
1.67 and 2.25 as optimistic.

Against buy-and-hold the candidate is roughly a tie in total points (+4,268 vs
+3,908 in 2025; +4,151 vs +3,496 in 2026) while holding intraday only.

## Why this is not promotable

1. **The selection is circular.** `absorption` was chosen *because* it kept its
   sign across 2025 and 2026. Showing it profits in 2025 and 2026 is therefore
   partly self-fulfilling. Neither window is a holdout for it.
2. **The holdout cannot be spent, and this is a data fact, not caution.** Bookmap
   has captured live since 2026-07-17 and was deliberately never read during the
   search. It still cannot score this candidate:

   | | Databento | Bookmap |
   | --- | ---: | ---: |
   | raw absorption sd | 0.00812 | 0.00447 |
   | z sd | 1.068 | 0.711 |
   | fire rate at \|z\|>=2 | 3.76% | **1.35%** |

   Bookmap reports 25% less \|delta\| and 68% more `trade_count` per second, and
   `absorption` divides the first by the second, so its dispersion lands at 0.55x.
   The trailing-20-session normaliser is all Databento at the boundary, so the
   z-score compresses and `|z| >= 2.0` **stops selecting the population it was
   declared on**. It is a different rule wearing the same threshold. The tables do
   not overlap, so no rescaling factor can be fitted without consuming the very
   window it would protect. 14 sessions and 58 firings carry no power regardless.
3. **Nothing cleared the pre-registered bar.** t 3.7 was the threshold; the best
   cell anywhere reached 2.87.

## What would settle it

Source-consistent history is the whole blocker. Either accumulate **>= 40
Bookmap sessions**, so the signal can be normalised against its own collector
with a 20-session warm-up and still leave something to test on, or run both
collectors in parallel for a stretch and measure the ratio honestly. Until one of
those exists, this is a paper-tracking candidate and nothing more.

Do not tune it in the meantime. 196 trials are charged across
`NQ L2 Wide Screen` (192) and `NQ L2 Absorption` (4); a searched variant would
have to beat its own deflation on a sample that has no clean window left.

---

# Round two: ten more families, a scale-free rebuild, and what it cost the candidate

Run 2026-08-15, `sandbox/research/l2_wide_screen_v2.py`. 352 further cells
(176 per window), charged to `NQ L2 Wide Screen v2`.

## The ten new families are noise

Gross aggression, execution location, book churn, liquidity provision,
quote-to-trade ratio, book thinness, imbalance *dispersion*, spread regime, flow
acceleration, and realised impact per unit flow — twelve previously unread
columns between them. Across the two windows:

* correlation of cell edges between 2025 and 2026: **−0.176**
* sign agreement: **80/176 (45%)** — *below* the 50% a coin flip gives
* clearing Bonferroni (t 3.7): **0 of 176**, in either window

**`exec_side` is the cautionary tale.** It scored t **+3.38** on 2025 — the
highest single-cell t any screen here has produced. The identical cell on 2026:
t **−0.24**. Sign agreement 3/16, family mean flipping +6.10 → −2.87. It was
precisely the noise maximum the 3.7 threshold exists to reject, and reporting it
on 2025 alone would have looked like a discovery.

## Absorption survives rank normalisation — 16/16

| absorption (trailing rank) | 2025 | 2026 |
| --- | --- | --- |
| 60m window, 240m horizon | +13.58 (t 2.86) | +14.39 (t 1.95) |
| 30m window, 240m horizon | +8.47 (t 2.16) | +12.17 (t 1.43) |
| 15m window, 240m horizon | +7.25 (t 2.23) | +7.79 (t 1.28) |

Every one of 16 cells positive in both windows, better than the z-form's 15/16.

## The scale-free rebuild failed, and then explained the candidate

The plan was to make the signal collector-agnostic so the sealed Bookmap window
could score it. Three constructions, measured on fire-rate parity across the
2026-07-17 boundary (Databento → Bookmap):

| construction | 15m | 30m | 60m |
| --- | ---: | ---: | ---: |
| trailing z | 0.36 | 0.47 | 0.73 |
| trailing rank | 0.63 | 0.52 | 0.56 |
| **session-local rank** | **0.87** | **0.89** | **0.96** |

**Trailing rank did not fix it.** Rank is scale-free only under a *monotone*
transformation; Bookmap changes the distribution's shape, not just its scale
(68% more trades hits `absorption`'s denominator differently from its numerator),
so ranking against foreign history stays biased. The hypothesis was wrong and the
parity test says so.

**Session-local rank achieves parity** — ranking each minute against only its own
session's earlier minutes, which cannot span collectors because a session never
does. And it kills the edge:

| session-local rank | 2025 | 2026 |
| --- | --- | --- |
| 60m window, 240m horizon | +5.13 (t 1.20) | **−7.82** (t −1.56) |
| 30m window, 240m horizon | +3.86 (t 1.21) | **−9.73** (t −2.20) |
| 15m window, 240m horizon | +4.80 (t 1.95) | **−5.66** (t −1.75) |

All twelve 2026 cells negative.

**This is the most informative result of the round.** The edge lives in the
*cross-session* comparison — "extreme relative to the last 20 sessions" — not in
the book state itself. Remove the regime reference and it inverts. Together with
the fat tail (April 2025 carrying half that year), the honest reading is that
absorption is substantially **volatility-regime timing**, not a pure
microstructure edge. And the only collector-agnostic construction is the one with
no edge, so the holdout still cannot test the version that works.

## The candidate is not new

`trials.json` already carries **`Absorption Reversal`: 5,898 trials** — a
compiled Rust strategy with a replica in `sandbox/strategies/`, fading
"heavy one-sided `trade_delta` that fails to move price while the opposite book
refills." Same mechanism. It was found only when trials were recorded.

At Pro cost, its compiled defaults:

| window | n | pts/trade | t | win |
| --- | ---: | ---: | ---: | ---: |
| 2025 | 90 | +2.84 | 1.33 | 0.411 |
| 2026 | 127 | +0.11 | 0.06 | 0.244 |

The existing version uses a **3-minute** window; this one uses 15-60 minutes with
a 240-minute hold. That the short-window form is flat and the long-window form is
not fits the repo's one durable finding — flow edge grows with horizon
(`trade_delta` −0.03 at 1s → −0.56 at 300s; HDR works at 60m). But a
hypothesis-consistent story is not evidence, and the practical consequence is
blunt: **this candidate must be deflated against ~5,900 prior trials on its own
mechanism, not against the 30 spent here.**

## Where this leaves it

Unchanged in substance from round one, with two additions: the edge is probably
regime timing rather than book reading, and the mechanism has a five-thousand-trial
search history that no result on this sample can outrun. Further searching on
2025/2026 has reached the point of negative expected value. What would move it is
data, not cells: ≥40 Bookmap sessions so a same-collector trailing normaliser
becomes possible, or forward paper-tracking.
