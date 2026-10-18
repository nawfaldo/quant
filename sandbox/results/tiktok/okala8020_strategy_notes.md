# Okala (YouTube @Okala8020, "8020 Trading Pit") — strategy notes (read 2026-10-03)

1 long video (prop-firm scaling) + 10 Shorts (NQ scalp clips). Module
`tt_okala8020.py`, group `okala8020`, 1 family.

| family | clips | rule | claim |
|---|---|---|---|
| o80_capitulation_fade | 3, 6, 9 | "80/20" levels (the 20 and 80 marks of each 100 NQ points); a capitulation candle into a 20 level -> fade it; a run of a high rejected at a level -> short; "fade the cross" | clip P&L only |

Grid generalised per market (step = 10^(floor(log10 price) - 2)).

## SKIP

Prop-firm scaling (one account -> one payout -> scale), motivation clips,
clips without speech. The "H pattern" continuation and "cross-section entry"
are named but not defined in the available clips.
