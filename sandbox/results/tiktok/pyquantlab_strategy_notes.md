# @pyquantlab (PyQuantLab) — strategy notes (2026-10-04)

Grid: 212 posts, each one strategy from his 250-strategy Backtrader/VectorBT library (code packs and
books sold; the articles at pyquantlab.com/article.php carry full code free — 250 articles).
Screened by caption; 8 rules coded from the posts + their articles.

`sandbox/research/tt_pyquantlab.py` (initial stop `stop_atr` x ATR, his ATR(7)x3 trailing exits -> trail_):

| Family | Article / post | Rule |
|---|---|---|
| pql_ao_twin_msb | AO Twin Peaks + Market Structure Break | AO(7,30) twin-peak divergence arms; close through the last confirmed swing (k bars) |
| pql_squeeze_atr_wake | Beating the False Breakout | stddev(30) at its 30-bar low + ATR(7) crosses its SMA(30); direction ROC(30) or close vs SMA(50) |
| pql_intraday_vol_break | An Intraday Volatility Breakout Strategy (5m) | 3h high/low channel; prior bar ATR(14) < 0.8 x SMA12(ATR); close beyond the channel |
| pql_bb_mr_adx_rsi | Enhancing BB mean-reversion with ADX and RSI | ADX < 25; close < lower BB(20,2) and RSI < 30 (mirror); exit at the mid band |
| pql_rsi_mr_adx | Enhancing RSI mean-reversion with ATR and ADX | RSI crosses 30/70; skipped while ADX high AND ATR/close above median; exit at opposite extreme |
| pql_momentum_ignition | Momentum Ignition (breakouts from consolidation) | low stddev/close, ROC(7) beyond its mean +- 1 sd, SMA(30) side |
| pql_rvol_spike | Relative Volume Spike Momentum | prior bar volume >= 2x SMA7 and ROC(7) beyond k; trailing stop |
| pql_sma_slope_flip | SMA slope flip (post) | SMA(20) 5-bar slope turns positive -> long, exit when negative; 1% trail |

Rescaled (daily-crypto constants -> relative): stddev 5% of price -> below its 500-bar median;
ROC 1% -> k x ATR/close; 2% / 1% trails -> trail_ exits.
Not coded (ML / exotic, or generic duplicates of existing families): HMM, Kalman, Hilbert, rough-path,
copula, KNN, deep-learning posts; Ichimoku/Vortex/SuperTrend/BB-squeeze/Donchian variants (covered).
Verify: no problems; 95-10,355 events per symbol.
