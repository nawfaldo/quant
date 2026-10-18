# @safucrypto — strategy notes (screened 2026-10-04)

Grid: 208 posts. Mostly Middle-East geopolitics; trading posts promote the paid "Safu AI"
(8 strategies, Pine scripts shared only in the community). Language: Urdu/English.

| Post | Rule | Status |
|---|---|---|
| 7690269476503309575 "one of 8 strategies" indicator | anchor time = previous-day session open, readiness score 80, ATR buffer; entry/stop/targets plotted by a private script | SKIP (paywalled script) |
| 7689153172975160584 automated ORB | ORB with ATR + relative-volume filters, "86% win rate" | SKIP (no parameters; generic ORB already covered by fx_orb etc.) |
| 7646815344362900756 Golden Zone | MSS (break of recent high/low), fib over the impulse leg, enter in 0.618-0.705 | covered by existing fib golden-zone families |
| 7625129085454306568 TPO extremes | trade the extremes of balance, skip the middle | folded into safu_cva_retest |
| 7623384954944146695 "My 2026 strategy" CVA | merge days whose value areas overlap >=50% into a composite value area; when price leaves the CVA and retests its edge -> A+ setup in the breakout direction | **safu_cva_retest** |

Family: `sandbox/research/tt_safucrypto.py` — `safu_cva_retest` (TPO = bar counts per price bin;
composite walks back up to 10 days; axes overlap 0.5/0.7, win 6/12 bars; exits rr_2/rr_1/days_1).
Verify: no problems; 55-267 events per symbol.
