# XAUUSD intraday strategy families — the best candidate yet, and it still fails

**FINAL VERDICT: not promotable.** `momentum` passed every test the USOIL study
could throw at it — it cleared the gate at the real spread, held its profit
factor out of sample (1.387 -> 1.391), and beat the coin-flip null in both
windows. Then it was scored on 2006-2015, twelve untouched years carrying **948
trades against the holdout's 132**, and posted **-3.2% with a gross edge of
0.079 ATR at t = 0.98**. The much better-powered window says the edge is
indistinguishable from zero.

This is a real result and a much closer call than oil, which failed on cost
before it ever reached a statistical question. The full reasoning is below;
the summary table is:

| window | trades | return | PF | gross/ATR | t |
| --- | ---: | ---: | ---: | ---: | ---: |
| fitted 2018-2024 | 566 | +96.3% | 1.387 | 0.364 | 3.49 |
| holdout 2025-2026 | 132 | +15.0% | 1.391 | 0.282 | 1.35 |
| **pretest 2006-2015** | **948** | **-3.2%** | **0.990** | **0.079** | **0.98** |

Pooling the two untouched windows by trade count gives ~0.104 ATR gross against
a spread of ~0.06 ATR — about 0.04 ATR net a trade, with a pooled t below 2.
That is not a strategy; it is a coin toss with a small tilt that costs eat.



The USOIL protocol re-pointed at gold: nine price-only families, 71,100 cells,
2018-2024 in-sample with a sealed 2025-2026 holdout, 30-minute bars over the
08:00-16:00 New York session, **the real 0.20 spread charged at entry**.

Unlike oil, this found something. `momentum` clears the gate in sample, holds
up out of sample on a volatility-neutral edge measure, and beats the coin-flip
control both in and out of sample. It is also **not statistically significant
(t = 1.35 on 132 holdout trades)** and **cannot be traded on a $1,000 account
at all**. Both caveats are load-bearing.

Module: `sandbox/research/xauusd_families_research.py`
Outputs: `xauusd_families_selection.json`, `xauusd_edge_scan.json`,
`xauusd_why.json`

```powershell
py -B -m sandbox.research.xauusd_families_research edge     --workers 14
py -B -m sandbox.research.xauusd_families_research select   --workers 14
py -B -m sandbox.research.xauusd_families_research validate
py -B -m sandbox.research.xauusd_families_research why      --workers 14
```

## Why gold is a different problem from oil

| | USOIL | XAUUSD |
| --- | ---: | ---: |
| price | ~70 | 1,270 - 5,600 |
| 0.20 spread, in bp | **~28 bp** | **0.44 - 1.6 bp** |
| spread in ATR units | ~0.45 | **0.02 - 0.06** |
| best gross edge, ATR units | — | 0.15 - 0.36 |
| cells clearing the spread | **0 of 74,700** | **~30,000 of 71,100** |
| families surviving in sample at the real spread | **0 of 10** | **3 of 9** |

Oil failed because the toll was 300% of the edge. On gold the toll is about
20% of the edge, so a real signal has room to survive. That single ratio is the
whole difference.

## Measure edges in ATR units on this instrument, never in dollars

The mean 30-minute session bar ran **2.09 in 2018, 5.55 in 2024, 19.84 in
2026** — a 9.5x rise. A dollar-denominated edge therefore mostly measures which
era a trade happened in. The first edge scan here reported `gap` improving from
1.07 in sample to 7.18 out of sample, which looked spectacular and meant almost
nothing: normalised, it went 0.211 -> 0.409, a far more modest claim, and the
families that looked strongest on dollars were not the ones that held up.

Everything below is `gross / ATR at entry`. The spread is normalised the same
way (`spread_atr`, about 0.02-0.06).

## The benchmark, which gold badly needs

Gold rose **+114%** across the holdout, so a long-biased rule could look
brilliant for no reason. The control settles it:

| control, 2025-2026 | value |
| --- | ---: |
| buy and hold | **+60.5%** |
| always long the 08:00-16:00 session | **-4.3%** |
| that control's gross per trade | **-0.85** |

**All of gold's rally happened outside the New York session.** Being long
intraday *lost* money over the same window. So an intraday long bias is not a
free ride here, and a positive result is not explained by the trend.

## In sample (2018-2024), real 0.20 spread

| family | return | PF | max DD | gross/ATR | t | break-even spread |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **momentum** | **+96.3%** | **1.387** | **11.1%** | **0.364** | **3.49** | **1.35** (6.7x the cost) |
| vwap | +115.6% | 1.224 | 17.4% | 0.202 | 3.47 | 0.89 (4.5x) |
| orb | +93.9% | 1.166 | 15.6% | 0.206 | 2.67 | 0.63 (3.1x) |

`overnight`, `pdr`, `donchian`, `ma_cross`, `gap`, `zscore` — no cell cleared.

## Holdout (2025-2026), scored once

| family | return @10k | PF | max DD | gross/ATR | t | 2025 | 2026 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **momentum** | **+15.0%** | **1.391** | **6.9%** | **0.282** | 1.35 | +13.4% | +1.5% |
| vwap | +4.6% | 1.070 | 13.2% | 0.088 | 0.71 | +2.5% | +2.0% |
| orb | **-5.6%** | 0.951 | 22.6% | 0.086 | -0.12 | +2.5% | -7.9% |

`momentum`'s profit factor held almost exactly — **1.387 in sample, 1.391 out**
— and its edge retained 77% of its in-sample size (0.364 -> 0.282) on a measure
that is immune to the volatility explosion. Both holdout years are positive and
the drawdown *fell*. That is the profile of a rule that transferred.

## The coin-flip control

Identical selection, trade direction randomised. Compare on gross/ATR, not
return — return is dominated by sizing luck.

| family | run | IS gross/ATR | IS t | OOS gross/ATR |
| --- | --- | ---: | ---: | ---: |
| **momentum** | **real** | **0.3644** | **3.49** | **0.2819** |
| momentum | flip 1 | 0.2274 | 3.33 | 0.1172 |
| momentum | flip 2 | *no cell cleared* | | |
| momentum | flip 3 | 0.2152 | 1.96 | -0.0024 |
| orb | **real** | 0.2055 | 2.67 | 0.0864 |
| orb | flip 1 | 0.1784 | 2.59 | **0.0878** |
| orb | flip 3 | **0.2226** | **3.25** | 0.0469 |
| vwap | **real** | 0.2024 | 3.47 | 0.0877 |
| vwap | flip 3 | **0.2558** | **3.70** | 0.0390 |

**`momentum` is the only family whose real edge beats every coin flip in both
windows** (0.364 vs 0.227/0.215 in sample; 0.282 vs 0.117/-0.002 out). Its
excess over the null averages ~0.22 ATR units out of sample.

**`orb` and `vwap` are noise.** A coin flip matched or beat `orb` in both
windows and beat `vwap` in sample on both edge and significance. Neither should
be considered further, whatever their in-sample return says.

Caveat worth stating: only two of three null draws selected a cell for each
family, so the null sample is thin. It is enough to kill `orb` and `vwap`; it
is suggestive rather than conclusive for `momentum`.

Note also how badly *return* misleads here — null flip 1 on `momentum` posted
**+229% in sample and +22.9% out**, beating the real rule on both, while
carrying a much smaller edge. Rank on gross/ATR, never on return.

## The $1,000 question: it is the stop width, not margin or leverage

An earlier draft of this file said a $1,000 account "cannot trade this". That
is wrong as stated, and the reason it gave was wrong too. Measured directly at
the mean holdout bar (gold 3,870, ATR 14.4, sealed stop 3.5x ATR = $50.4, so
one ounce is a $3,870 position risking $50):

| leverage | margin leg (oz) | risk leg @1.5% (oz) | fills? |
| --- | ---: | ---: | :--- |
| repo self-imposed 4x cap | 1.03 | **0.30** | no |
| 1:200 | 51.68 | **0.30** | no |
| 1:2000 (Exness actual) | 516.83 | **0.30** | no |

**The risk leg binds in every case, so leverage is irrelevant** — Exness would
happily let a $1,000 account carry 517 ounces. The constraint is that a 1.5%
risk budget ($15) against a $50 stop asks for 0.30 ounces, and the step is 1.
Raising leverage does not move that column at all. The repo's 25% margin cap
was therefore not the cause, though it was also binding and had to be ruled out.

That reframes it from a hard block into a design constraint, and it has a clean
fix: **a tighter stop.** At `stop_atr = 1.0` the stop is $14.4, so $15 of risk
buys 1.04 ounces and the trade fills.

Holdout at $1,000 with Exness leverage, sweeping the two things a trader
actually controls:

| risk/trade | stop_atr | fill | return | max DD | PF |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1.5% | **1.0** | 77% | **+30.4%** | **12.9%** | **1.391** |
| 1.5% | 1.5 | 64% | +21.9% | 9.4% | 1.477 |
| 3.0% | 1.5 | 98% | +62.1% | 18.8% | 1.339 |
| 5.0% | 2.5 | 98% | +83.4% | 24.0% | 1.376 |
| 1.5% | **3.5 (sealed)** | **0%** | — | — | — |
| 8.0% | 1.0 | 100% | -21.3% | **79.3%** | 0.974 |
| 12.0% | 1.0 | 100% | -71.5% | **92.5%** | 0.924 |

Two readings, and the second one matters more than the first.

**Mechanically, $1,000 works.** A 1x ATR stop at the same 1.5% risk fills 77% of
signals and posts +30.4% with a 12.9% drawdown and the same 1.391 profit factor
as the sealed cell. Note also that raising *risk* rather than tightening the
*stop* is the wrong lever — the 8% and 12% rows reach full fill and 79-92%
drawdowns, which is ruin, not sizing.

**Statistically, those returns are not evidence.** `stop_atr` was a searched
axis; the sealed cell chose 3.5. Every other row above is a *different cell*
whose performance I am reading off the holdout. Picking 1.0 because it scored
+30.4% in 2025-2026 is exactly the selection-on-the-holdout error this whole
study exists to detect. The table proves **feasibility**, not performance.

The correct next step is to re-run selection with fillability at $1,000 as a
*constraint applied in sample* — restrict the grid to cells a $1,000 account can
actually trade, select on 2018-2024 only, and score the holdout once more.

## The original blocker, at the sealed cell's stop width

XAUUSD is 100 troy ounces a lot with a 0.01 lot minimum, so the step is **1
ounce** — and at 2025-2026 prices one ounce is a **$4,000-$5,600 position**.
Risking 1.5% of $1,000 is $15 against a 3.5x ATR stop of roughly $35-70, which
rounds to zero.

| balance | momentum trades | fill rate | return | max DD |
| ---: | ---: | ---: | ---: | ---: |
| $1,000 | **0** | **0%** | **0.00%** | — |
| $2,500 | 82 | 62% | +9.23% | 5.7% |
| $5,000 | 121 | 92% | +12.83% | 6.4% |
| $10,000 | 132 | 100% | +15.04% | 6.9% |
| $25,000 | 132 | 100% | +14.51% | 7.3% |
| $100,000 | 132 | 100% | +14.76% | 7.4% |

The rule wants 132 trades and a $1,000 account takes **none of them**. Roughly
**$5,000 is the practical floor** and $10,000 is where it fills completely.
Above $10,000 the result is stable, which is the reassuring part — the edge is
not a lot-rounding artefact once the step stops binding.

The same table also destroys `orb`'s apparent $1,000 result: **+13.9% on a 54%
fill rate**, versus **-3% to -5.6%** at every balance where the signal actually
fills. That profit is rounding luck, exactly
[[lot-granularity-fakes-low-drawdown]].

## The candidate rule

`momentum`, sealed cell: at **09:00 New York**, if gold has moved more than
**0.5x ATR** over the previous **17 buckets** (one session), enter **with** the
move at the next bar's open, provided price is on the correct side of its
**20-day EMA** and short-horizon volatility is below its long-horizon level.
Stop **3.5x ATR**, **2.5x ATR trailing stop**, flatten at 16:00.

It is economically legible — session-opening continuation, filtered by trend
and calm conditions — rather than a shape fitted to a curve, which is a point
in its favour but not evidence.

## The pretest: 2006-2015, twelve untouched years

The main study warmed up in 2016, so everything before 2018 is untouched by the
search in exactly the way 2025-2026 is — and it is a very different gold, at
500-1,800 an ounce with bar sizes a fraction of today's, containing the 2011
blow-off and the 2013 crash. It also carries **seven times the holdout's trade
count**, so it resolves an edge the holdout could only hint at.

| family | fitted 2018-24 | holdout 2025-26 | **pretest 2006-15** | pretest g/ATR | t | n |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| momentum | +96.3% | +15.0% | **-3.2%** | **0.079** | 0.98 | 948 |
| orb | +93.9% | -5.6% | +50.1% | 0.139 | 2.16 | 1240 |
| vwap | +115.6% | +4.6% | **-28.4%** | 0.043 | 0.86 | 1480 |

`momentum`'s edge falls to **22% of its fitted value and 28% of its holdout
value**, is not significant, and loses money net of the spread. Ten pretest
years split five up, five down.

`orb` is the mirror image and just as damning: it *passes* the pretest (+50.1%,
t = 2.16) having *failed* the holdout (-5.6%) — and a coin flip matched it in
both. A rule that works in one untouched window and fails the other, while
randomness keeps pace, is noise showing two different faces.

`vwap` fails both untouched windows.

**No family is positive in both untouched windows.** That single sentence is the
result.

## Why the $1,000 constraint could not be designed around

Re-running selection with a $1,000-fillability gate applied **in sample**
(`select --micro`) returned the *same* cells — because in 2018-2024 they filled
fine. Gold was 1,270-2,400 with an ATR of 2-5.5, so a 3.5x ATR stop was $10-19
and a $15 risk budget bought a full ounce. `momentum` fills **90.3%** of its
signals on $1,000 across the fitted window and returns +66.9% there.

The account only breaks in 2025-2026, when gold tripled to 3,900-5,600 and ATR
went to 14-20, pushing the same stop to $50.

So **fillability is not a property of the strategy, it is a property of the
price level**, and an in-sample gate cannot capture it. As long as gold trades
near $4,000+, a $1,000 account with a 1-ounce minimum cannot risk 1.5% on any
stop wider than about 1x ATR. That is arithmetic, not strategy design, and no
selection constraint fixes it.

## Verdict

Not promotable — and now for a stronger reason than "not yet". What it has:
in-sample and holdout profit factors that match, both holdout years positive, a
drawdown under 7%, a break-even spread 6.7x the real cost, and it beats the
coin-flip control in both windows. What it lacks: the pretest. **0.079 ATR at
t = 0.98 over 948 trades** is the best-powered estimate available, and it says
zero.

The right reading of the holdout is not "it worked" but "132 trades could not
tell". The pretest had the sample size to answer, and did.

One caveat, stated so it is not overlooked: 2006-2015 predates a good deal of
market structure change, so a rule fitted on 2018-2024 failing there is weaker
evidence than failing on 2025-2026 would have been. It is not nothing, though —
the measure is volatility-normalised precisely so the eras are comparable, and
948 trades against 132 is not a close contest.

What would still be worth doing, in order:

1. **A proper anchored walk-forward across 2006-2026** rather than three fixed
   windows. If the edge is regime-dependent rather than absent, that is what
   would show it, and this study cannot distinguish those two.
2. **Give up on $1,000 for gold.** The instrument needs roughly $5,000 at
   current prices for the arithmetic to work at all. This is not negotiable by
   strategy design.
3. **Point the coin-flip and pretest machinery at the live NQ and BTC sleeves**,
   which is where the money actually is.

## Data facts established

- `xauusd_1m` is **New York wall-clock** — empty 17:00 maintenance hour, volume
  peaking 08:00-11:00 at the COMEX open.
- Session used is **08:00-16:00 New York**, wider than oil's pit session because
  gold's liquidity is; 17 thirty-minute buckets.
- Coverage is complete: **2004-2026**, 309-315 sessions a year, ~354k minutes a
  year. Nine years of history before the 2016 warm-up start remain unused.
- Price ran 1,269 (2018) to 4,587 (2026); 30-minute bar range 2.09 to 19.84.
