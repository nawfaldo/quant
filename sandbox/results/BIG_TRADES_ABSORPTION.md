# Big Trades Absorption — Marco Boesing's order-flow method on NQ

Port of the IQCapital interview
["Ex-Institutional Trader Exposes the ONLY Way to Follow Big Money"](https://www.youtube.com/watch?v=ODC4LhyJNcM).
Module: `sandbox/research/big_trades_absorption.py`.

**Verdict: not promotable.** The rule has a small, real-looking in-sample edge
that sits only modestly above a coin-flip control and inverts on the untouched
2026 window. Nothing here should be compiled.

## What was implemented

The tape (`dbento_nq_ticks`, 96.6M prints) is bucketed into 500ms clusters with
the aggressive buy and sell volume kept apart — the speaker's "big trades"
indicator, which bunches an algo's slices into one print. A cluster counts as
big when it clears the k-th largest single-side cluster of that session's own
first half hour, which is his stated sizing rule ("is the battle with 100
contracts or with 500?") and is causal by construction.

After the cluster, price decides which of two things happened: **accretion**
(the aggressor moved the market → trade with it) or **absorption** (it could
not → trade against it). Entry is the open of the first cluster after the
confirmation window. The stop goes one tick past the cluster's own extreme; the
target is the next session-VWAP standard-deviation band; a winning opposing
cluster closes the position early. Entries stop at 12:30 NY, flat at 15:55.

Scaling in and the macro stand-asides are explicitly discretionary in the video
and are not implemented, so this is a lower bound on the described method.

Costs are the live Exness **Pro** account: no commission, the whole cost is the
measured 0.300 bp in-session spread (0.892 points at 29,731) plus the standard
0.2-point slippage allowance. **1.092 points, or 4.4 ticks, a round trip.**

## What the phases found

`edge` (zero cost, 2025-02-12..2025-12-31, 225 sessions, 432 cells)

Zero-cost mean points a trade *is* the break-even spread. Only **7 of 432**
cells clear the 1.092-point live cost, and every cell in the top ten is
`absorption` with a `band` target — the direction the video emphasises most.

| cell | trades | gross pts | t | stop (ticks) | target (ticks) | win |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `k10\|c3000\|m4\|absorption\|vwap\|band` | 363 | 1.588 | 2.22 | 22.3 | 96.5 | 0.281 |
| `k5\|c3000\|m4\|absorption\|vwap\|band` | 200 | 1.508 | 1.54 | 23.0 | 94.0 | 0.275 |
| `k5\|c3000\|m4\|absorption\|anyside\|band` | 690 | 1.227 | 2.48 | 22.7 | 93.9 | 0.275 |

`select` (Pro cost, same window) — 5 cells passed the gates, best at +3.68%.

`validate` (2026-01-01..2026-07-16, 137 sessions, opened once)

| cell | trades | net pts | IS net pts | msharpe | IS msharpe |
| --- | ---: | ---: | ---: | ---: | ---: |
| `k10\|c3000\|m4\|absorption\|vwap\|band` | 181 | **-1.291** | +0.496 | -0.515 | +0.265 |

`null` (coin-flip direction at the same clusters, same risk, same exits, 5 draws)

The best random cell reaches **+1.073** zero-cost points a trade. The best real
cell reaches +1.588. Only three of the 432 real cells beat the random maximum.

## The three things worth keeping

**The 4R bracket makes a random rule look profitable.** The band target sits
~95 ticks out against a ~22-tick stop, so a coin toss at the same moments earns
+0.7 to +1.1 gross points a trade with a 25% win rate. Read against zero, most
of this grid "works"; read against its own null, almost none of it does. This is
the same lesson as [`coin-flip-control-beats-real-signals`], now with the
mechanism visible: it is bracket asymmetry, not direction.

**The stop the video quotes is not the stop the rule produces.** He says seven
to seventeen ticks. Hiding behind the cluster's extreme across a 3-second
confirmation window gives 22 ticks on average. His figure is only reachable by
entering *inside* the battle by hand, which is the discretionary part.

**Cost is 4.4 ticks against a 7-tick stop.** Even at his own stop distance the
Exness Pro spread is more than half the risk per trade. The method assumes a
CME futures cost base (a fraction of a tick), and it does not survive being
re-based onto a CFD spread. That is a property of the venue, not of the read.

## Reproduce

```powershell
py -B -m sandbox.research.big_trades_absorption edge     --workers 6
py -B -m sandbox.research.big_trades_absorption select   --workers 6
py -B -m sandbox.research.big_trades_absorption validate --workers 6
py -B -m sandbox.research.big_trades_absorption null     --workers 6 --draws 5
```

864 cells are charged to `trials.json` under `Big Trades Absorption` (the edge
scan and the selection grid). The 2026 holdout has now been opened once and is
spent.
