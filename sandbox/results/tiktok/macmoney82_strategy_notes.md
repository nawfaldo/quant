# @macmoney82 (MacMoney) — strategy notes (2026-10-04)

Grid: 37 posts — day-trading education (SMC top-down, order flow, VWAP scalping, 80/20 reversals,
drift-VWAP pullback, risk scorecards). 3 read.

`mac_ema9_vwap_bounce` (`sandbox/research/tt_macmoney82.py`): 5m; 9 EMA crosses the session VWAP
(alert); pullback candle touches the 9 EMA and closes back on the trend side -> enter at the close;
stop just outside that candle; target 1:2. Axes win 12/24 bars; exits rr_2 / rr_1.5 / rr_1.
Verify: no problems; 2,369-7,001 events per symbol.

Covered: drift VWAP pullback, 80/20 reversal (o80_), POC pullbacks. "4-to-6 swing rule" had no transcript.
