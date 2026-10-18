# @yo.forex3 — strategy notes (screened 2026-10-04)

Grid: 250 posts. ~20-second music clips of third-party TradingView indicators on a chart
(LuxAlgo, ChartPrime, AlgoAlpha, BigBeluga, Flux Charts...), plus broker / "comment 101 / Mentor"
promos and booked-profit screenshots. No backtests despite the "tested" titles. 7 read.

Coded (public scripts, traded on their own buy/sell signals) — `sandbox/research/tt_yoforex3.py`:

| Family | Source post | Rule |
|---|---|---|
| yf_ott_signal | "Master Optimized Trend Tracker" | OTT period 4, percent 2 (his settings; 2 and 1.4 default also gridded); VAR MA crosses OTT[2]; stop at the line; his 1:2.5-1:3 |
| yf_range_filter | "Range Filter Buy Sell on 5 Min Chart" | DonovanWall range filter (per 50/100, mult 2/3), first long/short condition after the opposite |
| yf_alphatrend | "AlphaTrend Indicator Test" | Kivanc AlphaTrend (coeff 1/1.5, MFI or RSI), cross of its 2-bar lag |
| yf_qqe_mod | "Testing Smart QQE Mod" | QQE MOD 6/5/3 + 6/5/1.61 + BB 50x0.35, first blue/red bar |
| yf_double_st_adx | "Double Supertrend with ADX" | ST(10,1 or 2) and ST(10,3) agree, ADX(14) >= 20/25 |

Already covered: UT Bot, HalfTrend, zero-lag MA (`tt_tradingdata2`), Hull Suite (`tt_codetrading`),
golden-zone fib, ORB/FVG/OB/SFP/breaker (luxalgo/fluxchart families).
SKIP: proprietary/closed indicators (EneX, Kyle Algo, SFX, Viprasol, Alpha Signal Engine, AI-trend
scripts), single-EMA "backtest" (no rule shown, 22 s clip), time-of-high/low post (no rule).
Verify: no problems; 600-6,300 events per symbol.
