# USOIL intraday strategy families — no candidate found

Ten intraday families, 74,700 parameter cells crossed with 8 position-sizing
schemes, 2018-2024 in-sample with a 2025-2026 holdout, 30-minute bars over the
09:00-14:30 New York pit session. **Nothing was promoted.**

The headline is the last test, because it is the one that cannot be argued
with: **set the spread to zero — a free lunch no venue offers — and the seven
families that earn +70% to +608% in sample deliver a best out-of-sample gross
edge of +0.0024 points a trade, t = 0.04.** There is no cost assumption, no
regime filter and no sizing scheme that rescues that, because there is nothing
there to rescue.

Module: `sandbox/research/usoil_families_research.py`
Sealed outputs: `usoil_families_selection.json` (spread 0.20),
`..._spread0.02.json`, `..._spread0.00.json`, `..._spread0.00_sizing.json`,
`usoil_edge_scan.json`

```powershell
py -B -m sandbox.research.usoil_families_research edge     --workers 14
py -B -m sandbox.research.usoil_families_research select   --workers 14 --spread 0.0
py -B -m sandbox.research.usoil_families_research validate --spread 0.0
py -B -m sandbox.research.usoil_families_research sizing   --spread 0.0
```

## Families tested

| category | families |
| --- | --- |
| breakout | `orb` opening range, `overnight` out-of-session range, `pdr` prior-session range |
| trend following | `donchian` channel break with ATR trail, `ma_cross` fast/slow EMA |
| momentum | `momentum` intraday time-series, `gap` open against prior close |
| mean reversion | `vwap` distance from session VWAP, `zscore` rolling-mean sigmas |
| event | `eia` the Wednesday 10:30 inventory release |

## Axes searched

| axis | values |
| --- | --- |
| exit mode | 11 — targets 1/1.5/2/3 R, time stops 2/4/12 bars (1h/2h/6h), trails 1.0/1.5/2.5 ×ATR |
| stop width | 5 — 1.0/1.5/2.5/3.5/5.0 ×ATR |
| trend regime | 3 — none, price vs EMA-20d, price vs EMA-50d |
| volatility regime | 3 — none, calm, active (complements, so a lucky mask shows as a matched pair) |
| entry cutoff | 2 — 12:00, 13:30 |
| sizing | 8 — see below |

**74,700 signal/exit/regime cells × 8 sizing schemes.**

Sizing is swept in its own phase rather than inside the grid, and the reason is
structural: sizing is a monotone rescaling of an already-fixed trade sequence.
It cannot change which bar a position exits on, so gross points a trade is
*identical* across every sizing row — visible as a constant `OOS gross` column
in the sizing table below. Folding it into the search would multiply the
multiple-testing burden eightfold while adding no information about whether an
edge exists. The eight schemes are: stop-based risk at 1%/1.5%/2%, the same
throttled by a 40% volatility target, fixed-fractional notional at 1x/2x, and
`flat` (one minimum lot, never compounding — a diagnostic that shows the trade
sequence unmixed with position growth).

## Failure 1 — the 0.20 spread is larger than any edge in the grid

Cost-free scan, every cell, spread set to zero. `gross` is mean points a trade
before cost, which is also the break-even spread.

| family | cells | best gross | t | median | cells > 0.20 | same cell, OOS gross |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| orb | 6907 | 0.1506 | 3.01 | 0.0304 | **0** | -0.0570 |
| overnight | 2850 | 0.1081 | 2.05 | 0.0182 | **0** | -0.1461 |
| pdr | 2900 | 0.1720 | 2.74 | 0.0213 | **0** | -0.0871 |
| donchian | 2470 | 0.0829 | 1.73 | 0.0013 | **0** | -0.0881 |
| ma_cross | 100 | -0.0298 | -1.09 | -0.0709 | **0** | +0.0403 |
| momentum | 17108 | 0.1443 | 2.50 | 0.0096 | **0** | -0.1754 |
| gap | 2340 | 0.1545 | 2.77 | 0.0131 | **0** | -0.0042 |
| vwap | 3550 | 0.1121 | 3.17 | 0.0191 | **0** | -0.0761 |
| zscore | 8605 | 0.1893 | 3.07 | 0.0187 | **0** | -0.0614 |
| eia | 200 | 0.1697 | 2.62 | -0.0038 | **0** | -0.2423 |

Not one cell of 74,700 clears 0.20 gross; the best anywhere is 0.189 and the
typical cell earns about 0.02 a barrel. A 0.20 spread on a ~70 dollar barrel is
roughly 28 bp a round trip — not a tax on the edge here but one to two orders
of magnitude larger than it.

Selection at 0.20 accordingly returned **no cell in any of the ten families**.

Widening the grid is itself evidence. Going from 13,944 cells to 74,700 moved
the best gross edge from 0.166 to 0.189 while the median stayed near 0.02. A
maximum that grows with the number of draws while the centre does not move is
the definition of sampling noise, not of a discovery.

## Failure 2 — the in-sample winners are noise, so cheaper cost does not save it

Costs being the whole story would be recoverable — a different venue or a
larger timeframe would fix it. So the entire protocol was re-run at 0.02, a
realistic Exness Zero raw spread. Five families then cleared the in-sample
gate. All five failed the holdout:

| family | IS 2018-24 return | IS gross/trade | OOS 2025-26 return @10k | OOS gross/trade | OOS gross t |
| --- | ---: | ---: | ---: | ---: | ---: |
| orb | +173% | 0.099 | **-18.0%** | **-0.054** | -0.93 |
| pdr | +116% | 0.083 | **-25.0%** | **-0.258** | -2.75 |
| momentum | +52% | 0.069 | **-3.1%** | +0.110 | 1.63 |
| vwap | +59% | 0.065 | **-17.6%** | **-0.099** | -1.90 |
| zscore | +94% | 0.101 | **-13.5%** | **-0.153** | -1.40 |

Four of the five have **negative gross edge out of sample** — the entry rule
does not predict direction at all in the holdout, before any cost is charged.
Momentum keeps a positive gross mean but still loses money and posts a negative
monthly Sharpe.

This is the signature of selection noise, and the in-sample t-statistics agree.
A maximum |t| near 3.0 is what 1,344 draws produce from pure noise even if they
were independent, and these cells are heavily correlated, so the effective
count is lower still and the bar is met even more easily. Read against its
search budget, no cell in this study has a t-statistic distinguishable from
zero.

Also note the direction flips between the two cost regimes: at 0.20 the least
bad `orb` cells are `direction=fade`, at 0.02 the winners are
`direction=breakout`. A rule whose optimal sign depends on the spread is not
describing the market.

## Failure 3 — remove the spread entirely and there is still nothing

The decisive test. Costs set to **zero**, full 74,700-cell protocol, selection
on 2018-2024 and the holdout scored once. Seven of ten families now clear the
gate, with in-sample returns that look like a discovery:

| family | IS 2018-24 | OOS 2025-26 @10k | **OOS gross/trade** | **OOS gross t** |
| --- | ---: | ---: | ---: | ---: |
| orb | **+608.9%** | +5.9% | +0.0024 | 0.04 |
| zscore | **+433.3%** | -0.7% | +0.0013 | 0.02 |
| pdr | **+423.5%** | +4.9% | -0.0546 | -0.71 |
| vwap | **+327.6%** | -7.5% | -0.0698 | -1.61 |
| momentum | **+177.6%** | -10.5% | -0.0800 | -1.19 |
| overnight | **+119.4%** | -15.0% | -0.0808 | -0.74 |
| gap | **+70.4%** | +3.8% | -0.0531 | -0.67 |

With no transaction cost of any kind, the best out-of-sample gross edge among
seven selected strategies is **+0.0024 points a trade at t = 0.04**, and five
of the seven are negative. +608.9% in sample becomes +5.9% out of sample on a
free-lunch cost model.

The three families with a positive OOS *return* are the trap worth naming: two
of them (`pdr`, `gap`) have **negative** gross edge per trade. Their profit is
compounding and lot-rounding luck sitting on top of a losing entry, not edge.

## Sizing: eight schemes, and the column that does not move

The sealed zero-cost winners, re-run under all eight sizing schemes (56 rows;
`orb` and `pdr` shown, the rest are in `..._spread0.00_sizing.json`):

| size_mode | orb IS% | orb OOS% | orb OOS gross | pdr IS% | pdr OOS% | pdr OOS gross |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| risk_1pct | +361.2 | +8.0 | +0.0024 | +227.9 | +3.7 | -0.0546 |
| riskvol_1pct | +292.9 | +4.8 | +0.0024 | +211.4 | +5.2 | -0.0546 |
| riskvol_1.5pct | +608.9 | +5.9 | +0.0024 | +423.5 | +4.9 | -0.0546 |
| risk_2pct | +1432.9 | +5.6 | +0.0024 | +806.5 | -1.1 | -0.0546 |
| riskvol_2pct | +1025.7 | +0.6 | +0.0024 | +720.7 | +2.2 | -0.0546 |
| notional_1x | +282.5 | -2.1 | +0.0024 | +113.6 | -7.7 | -0.0546 |
| notional_2x | +1122.6 | -6.9 | +0.0024 | +324.7 | -16.8 | -0.0546 |
| flat | +8.2 | +0.1 | +0.0024 | +6.7 | -0.7 | -0.0546 |

**The `OOS gross` column is constant.** That is the point, and it holds in
every family: sizing changes compounding, drawdown and lot-step truncation, and
changes nothing whatsoever about whether the entry predicts direction. The
in-sample column spanning +8% to +1433% while the edge column does not move by
a single digit is the clearest available picture of what these numbers are.

The best row in the whole sweep is `orb` / `risk_1pct` at +8.0% out of sample —
on a cell that returned +361% in sample and whose edge is statistically
indistinguishable from zero. That is the ceiling, at zero cost, after searching
597,600 configurations.

## Why the holdout looks so bad — it doesn't, the fit looks too good

Three candidate explanations, all tested (`... usoil_families_research why`):

### A. The holdout is underpowered — partly true, and it cuts both ways

| family | IS gross | IS se | OOS gross | OOS se | OOS n | t(drop) | significant? |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| vwap | 0.0751 | 0.0214 | -0.0698 | 0.0434 | 290 | 3.00 | yes |
| momentum | 0.1151 | 0.0393 | -0.0800 | 0.0672 | 106 | 2.51 | yes |
| pdr | 0.1184 | 0.0432 | -0.0546 | 0.0769 | 130 | 1.96 | yes |
| zscore | 0.1296 | 0.0358 | 0.0013 | 0.0650 | 125 | 1.73 | **no** |
| gap | 0.0843 | 0.0354 | -0.0531 | 0.0793 | 145 | 1.58 | **no** |
| overnight | 0.0770 | 0.0412 | -0.0808 | 0.1092 | 90 | 1.35 | **no** |
| orb | 0.0732 | 0.0250 | 0.0024 | 0.0600 | 261 | 1.09 | **no** |

For four of seven, the collapse is *inside sampling error* — with 90-260 trades
the holdout standard error is 0.06-0.11 a barrel, which cannot resolve an edge
of 0.07-0.13 either way. So the holdout does not by itself prove the edge died.
It also cannot confirm it. On its own this section says only "no information",
which is why it is not the answer.

### B. The regime changed — no, and this is the cleanest exoneration

| year | mean \|ret\| bp | ann vol % | mean ATR | ATR/price % |
| --- | ---: | ---: | ---: | ---: |
| 2018 | 32.94 | 28.7 | 0.386 | 0.595 |
| 2019 | 35.47 | 32.1 | 0.372 | 0.653 |
| 2020 | 76.27 | 103.3 | 0.441 | 1.112 |
| 2021 | 36.68 | 33.0 | 0.459 | 0.674 |
| 2022 | 55.58 | 49.2 | 0.967 | 1.025 |
| 2023 | 39.35 | 33.3 | 0.559 | 0.721 |
| 2024 | 32.90 | 28.6 | 0.452 | 0.597 |
| **2025** | **33.35** | **32.7** | **0.395** | **0.611** |
| 2026 (part) | 62.09 | 69.1 | 0.954 | 1.168 |

**2025 is the most ordinary year in the entire sample.** Its volatility (32.7%),
bar size (33 bp) and ATR-to-price (0.611%) sit right on top of 2018, 2021 and
2024 — all training years. The strategies did not meet a strange market; they
met an average one and stopped working. Regime change is not the explanation.
(2026 is a partial, high-volatility year and carries less weight.)

### C. The search manufactured the fit — this is the answer

The identical selection was re-run with each trade's **direction replaced by a
coin flip**. When the rule fires, the stop width, the exit, the session, the
gates and the ranking are all unchanged — only *which way it bets* is random.
Any in-sample return that survives is produced by the search and nothing else.

| family | run | IS return | IS gross | IS t | OOS return | OOS gross |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| orb | **real** | **+608.9%** | **0.0732** | **2.93** | +5.9% | +0.0024 |
| orb | coin flip #1 | +369.6% | 0.0667 | 3.27 | -3.8% | -0.0854 |
| orb | coin flip #2 | **+622.4%** | 0.0716 | **4.19** | +8.7% | -0.0324 |
| orb | coin flip #3 | +615.1% | 0.0743 | 3.94 | -25.8% | -0.0500 |
| zscore | **real** | **+433.3%** | **0.1296** | **3.62** | -0.7% | +0.0013 |
| zscore | coin flip #1 | +138.8% | 0.0740 | 2.80 | -14.8% | -0.0743 |
| zscore | coin flip #2 | +142.8% | 0.0836 | 3.52 | -5.8% | -0.0715 |
| zscore | coin flip #3 | +224.7% | 0.0953 | 2.55 | -4.2% | -0.1280 |
| pdr | **real** | **+423.5%** | **0.1184** | **2.74** | +4.9% | -0.0546 |
| pdr | coin flip #1 | +241.0% | 0.0984 | **3.93** | +4.9% | -0.0264 |
| pdr | coin flip #2 | +231.6% | 0.0928 | 3.31 | -1.7% | -0.0677 |
| pdr | coin flip #3 | +254.5% | 0.0655 | 2.89 | +0.8% | -0.0504 |

**Random trade directions produce +139% to +622% in sample, with t-statistics
of 2.55 to 4.19.** Coin flip #2 on `orb` beats the real strategy on both return
(+622% vs +609%) and significance (t = 4.19 vs 2.93). Every one of the nine
null runs has negative out-of-sample gross edge — exactly like the real ones.

The arithmetic closes cleanly on `orb`: real in-sample gross 0.0732, null
in-sample gross averaging 0.0709. The genuine signal is worth **0.0023 a barrel
above a coin flip** — and the holdout independently measured +0.0024. Two
completely different methods agree that the edge is about two thousandths of a
barrel, against a spread of 0.20.

So the holdout is not anomalously bad. **+608% was never a real number**, and
+5.9% is what the strategy always was. The in-sample figure is the artefact,
produced by picking the best of 7,200 correlated cells — which is precisely
what a search with no signal to find is guaranteed to produce.

## Data facts established (not assumed)

- **`usoil_1m` is New York wall-clock.** The daily maintenance break is the
  empty 17:00 hour — ES, which is Chicago, breaks at 16:00. The top volume
  minutes of 2021-2023 are 09:00, 10:30 and 14:27-14:29: the NYMEX open, the
  EIA release and the floor close, all in New York. No shift is applied.
- **The session is 09:00-14:30, not 09:30-16:00.** Hourly volume runs
  1650/2850/2810/2490 across 08:00-11:00 and collapses from 1799 in hour 14 to
  690 in hour 15.
- **Coverage is complete**, 309-313 sessions a year from 2012 to 2026-08-06,
  4.93M minutes, no gap. This is *not* the HistData WTIUSD series with the
  2023-12 to 2026-06 hole.
- **No negative prints.** The April 2020 low is 6.495; the CFD had already
  rolled to June, so log returns are safe throughout.

## The $1,000 account cannot express these signals

Exness USOIL is 1000 barrels a lot with a 0.01 lot minimum, so the size step is
**10 barrels**. At $1,000 with a 1.5% risk budget, a stop wider than $1.50 a
barrel cannot round up to a single step and the trade is simply not taken. Two
measurements of the same cell:

- `donchian`: 582 trades at $10,000, **257** at $1,000
- `momentum`: 806 trades at $10,000, **304** at $1,000

Roughly half the signals are unfillable, and the ones that do fill trade at
whatever risk the rounding leaves rather than the intended 1.5%. Ranking cells
under that regime ranks lot arithmetic — the failure recorded in
`lot-granularity-fakes-low-drawdown`. Selection therefore ran at $10,000 and
every result is reported at both balances. This constraint is independent of
everything above and would apply to any USOIL strategy on this account size.

## What is not ruled out

The scope here was 30-minute bars, RTH, one trade a session, bracketed exits,
and price-only inputs. Untested, and not implied by this result: holding
overnight or multi-day (where a 0.20 spread is amortised over a much larger
move), the 02:00-08:00 European window, spread/calendar structure against
Brent, inventory *surprise* versus the consensus forecast rather than the price
reaction to it, and anything at the tick or book level.

The finding is specifically that **intraday directional rules on USOIL RTH have
no out-of-sample edge even at zero cost, and separately could not have paid a
0.20 spread if they did.**
