# Uncorrelated Commodity Strategy Combinations

Run from the 13 mechanically passing family cells. Correlations and basket
selection use in-sample daily returns only; 2025-2026 is reported afterward.
Candidates require maximum absolute pairwise IS correlation <= 0.25 and at
least two distinct instruments (three distinct instruments for three-sleeve
baskets). Larger baskets permit at most two strategies per instrument. Of 520
eligible baskets, the best-IS-Sharpe and lowest-correlation
basket at each size were retained.

All baskets start at USD 1,000 and combine daily-rebalanced equal-weight sleeve
returns. This tests diversification, but it does not replay shared MT5 margin
or lot rounding across simultaneous positions.

| Basket | Weights | IS corr | OOS corr | IS return | IS DD | OOS return | OOS DD | OOS Sharpe | OOS PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XNG Donchian + XNI VWAP | 50/50 | 0.023 | 0.010 | +178.42% | 8.81% | +24.53% | 8.40% | 1.046 | 1.266 |
| XNG Z-score + XNI VWAP | 50/50 | 0.001 | 0.038 | +194.45% | 10.41% | +29.18% | 9.25% | 1.140 | 1.269 |
| XAL Z-score + XNG Donchian + XNI VWAP | 1/3 each | 0.058 | 0.072 | +122.40% | 5.09% | +29.87% | 7.36% | 1.516 | 1.373 |
| XAL MA cross + XNG Overnight + XNI VWAP | 1/3 each | 0.004 | 0.067 | +121.17% | 6.31% | +22.32% | 7.59% | 1.194 | 1.266 |
| XAL Z-score + XNG Donchian + XNI VWAP + XNI Z-score | 1/4 each | 0.058 | 0.115 | +119.01% | 3.84% | +30.92% | 6.45% | 1.780 | 1.429 |
| XAL MA cross + XNG Donchian + XNG Momentum + XNI VWAP | 1/4 each | 0.029 | 0.097 | +109.54% | 3.97% | +22.45% | 6.31% | 1.502 | 1.351 |
| XAL Z-score + XNG Donchian + XNG Momentum + XNI VWAP + XNI Z-score | 1/5 each | 0.058 | 0.115 | +116.23% | 3.40% | +29.54% | 5.53% | 1.996 | 1.474 |
| XAL ORB + XAL Overnight + XNG Donchian + XNG Momentum + XNI VWAP | 1/5 each | 0.034 | 0.097 | +100.97% | 4.20% | +37.34% | 4.46% | 2.056 | 1.502 |
| XAL Overnight + XAL Z-score + XNG Donchian + XNG Momentum + XNI VWAP + XNI Z-score | 1/6 each | 0.119 | 0.115 | +96.68% | 3.52% | +32.07% | 4.58% | 2.297 | 1.556 |
| XAL MA cross + XAL ORB + XNG Donchian + XNG Momentum + XNI VWAP + XNI Z-score | 1/6 each | 0.093 | 0.154 | +101.65% | 3.03% | +31.86% | 4.99% | 2.081 | 1.506 |

The strongest risk-adjusted result is the first six-sleeve basket: +32.07% OOS
with 4.58% maximum drawdown and 2.297 daily Sharpe. The highest-return result is
the second five-sleeve basket: +37.34% with 4.46% drawdown. The
lowest-correlation result remains XNG Z-score + XNI VWAP at 0.0006 IS
correlation; its OOS correlation remained only 0.0382.

The second three-sleeve basket includes XNG Overnight, which can react to CFD
roll gaps. Prefer the first three-sleeve basket for forward testing.
