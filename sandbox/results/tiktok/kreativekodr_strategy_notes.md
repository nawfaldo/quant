# @kreativekodr — strategy notes (2026-10-04)

Grid: 120 posts — 0DTE SPY/QQQ options bots (paper), Claude Code / Cursor demos, indicator explainers.
6 bot posts read; only "VWAPR" states rules.

`kk_vwapr` (`sandbox/research/tt_kreativekodr.py`): RSI below 15 with price below VWAP; when RSI starts
climbing -> long; one trade a session, 09:36-11:11 NY; 3-minute max hold (time_1/time_3/rr_1).
His paper result: 28 trades / 10 days, 50% win, $0.76 a trade ("the edge is thin").
Rare on 15m+ bars (15-60 events); built for 1-5m.

SKIP: HFT Baby (MA cross + 4 unnamed checks; backtest 95% win, live 40%, down YTD), Penny4U, 3-candle bot,
ORB-retest / reversal / H&S options backtests (no rules in captions), scanners, education posts.
