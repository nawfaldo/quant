# Full-Sleeve Commodity Consistency Search

Every constituent keeps 100% of its standalone return stream; returns are
summed rather than divided by the number of strategies. Low-correlation baskets
were first filtered to 12-18% IS drawdown. The retained consistency candidate
below was then chosen from the explicitly post-hoc screen that also landed in
the 12-18% OOS DD band.

## Consistency candidate

- XALUSD MA cross — 100% sleeve
- XNGUSD Donchian — 100% sleeve
- XNIUSD Z-score — 100% sleeve
- Total gross sleeve overlay: 3.0x
- Maximum absolute IS correlation: 0.0138

| Window | Return | Max DD | Positive months | Worst month | Monthly Sharpe | Positive years |
|---|---:|---:|---:|---:|---:|---:|
| IS 2020-2024 | +500.95% | 14.58% | 41/60 (68.3%) | -7.52% | 1.858 | 5/5 |
| OOS 2025-2026 | +98.64% | 16.35% | 17/20 (85.0%) | -10.11% | 2.191 | 2/2 |

Yearly returns: 2020 +47.61%, 2021 +21.95%, 2022 +9.26%, 2023 +60.22%,
2024 +90.70%, 2025 +77.25%, and 2026 through August 6 +12.06%.

This is a research overlay built from independent full-account strategy return
streams. It does not replay a single shared MT5 equity/margin ledger, so
simultaneous minimum-lot fills and aggregate margin must be simulated before
deployment. The OOS-near-15% choice is post-hoc and needs a new forward test.
