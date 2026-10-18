# @jadetantrade (Jade Tan) — strategy notes (2026-10-04)

Grid: 284 posts (Bahasa) — mostly the team's paid "Altrade" auto-trading product, AI-manager promos,
a $500 "Cacing-Cacing Naga-Naga" challenge diary. 3 read.

`jt_hull_suite` (`sandbox/research/tt_jadetantrade.py`): Hull Suite Strategy by DashTrader, Hull
Variation THMA, default length 55; Hull line vs its 2-bar lag flips -> stop and reverse. His BTCUSDT 1h
backtest (2024-01-01 to 2026-09-26): +94.51% before commission, -23.01% at 0.07% commission.
Grid len 55/80, thma 1/0 (HMA), exits signal / rr_2 / trail_0.5.

SKIP: volume-profile post (LuxAlgo VP, levels used like S/R, no rule), Altrade features, copy-trading.
Verify: no problems.
