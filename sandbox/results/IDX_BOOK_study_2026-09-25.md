# IDX book: management study

Rp1,000,000 Stockbit account, 5 sleeves (ma_trend, high_52w, donchian, dip, dividend), sizing: risk (risk 3%/trade, max position 35%, max 5 positions, heat 12%). In-sample 2016-01-04 to 2021-05-03, holdout 2021-05-04 to 2026-09-24.

216 management cells scored in-sample; 134 pass the gates with robust neighbours.

Top in-sample management cells:

- entry=limit, stop_fill=close, trail_atr=5.0: +371.0%, dd 28.8%, n=111, neighbours 3/3
- entry=limit, stop_fill=close: +356.0%, dd 28.7%, n=111, neighbours 3/3
- entry=open, chase_atr=1.0, stop_fill=close: +355.4%, dd 28.7%, n=117, neighbours 2/3
- entry=limit, stop_fill=close, breakeven_r=2.0, trail_atr=3.0, max_hold=60: +256.1%, dd 25.2%, n=148, neighbours 4/4
- entry=limit, stop_fill=close, max_hold=60: +319.2%, dd 28.1%, n=136, neighbours 3/3

| run | window | return | max dd | trades | win % | PF | avg trade | invested |
|---|---|---|---|---|---|---|---|---|
| default | in-sample | +277.8% | 30.2% | 118 | 42% | 1.96 | +8.17% | 91% |
| default | holdout | +205.5% | 50.1% | 134 | 31% | 1.61 | +14.85% | 99% |
| chosen | in-sample | +371.0% | 28.8% | 111 | 50% | 2.34 | +10.63% | 92% |
| chosen | holdout | +56.2% | 41.8% | 130 | 33% | 1.28 | +4.15% | 99% |
| equal sizing | in-sample | +216.2% | 26.3% | 106 | 44% | 2.01 | +7.97% | 91% |
| equal sizing | holdout | +316.5% | 45.9% | 125 | 33% | 1.74 | +16.02% | 99% |

## Holdout per sleeve (chosen: entry=limit, stop_fill=close, trail_atr=5.0)

| sleeve | trades | P&L | share | win % | avg trade | closed dd | alone: return | alone: dd | in book / alone |
|---|---|---|---|---|---|---|---|---|---|
| ma_trend | 49 | Rp+55,796 | 10% | 29% | +3.03% | 35.0% | +70.9% | 36.5% | 0.08 |
| high_52w | 45 | Rp+496,096 | 88% | 29% | +7.97% | 15.4% | +128.8% | 30.6% | 0.39 |
| donchian | 12 | Rp+10,946 | 2% | 33% | +2.37% | 5.4% | +68.3% | 29.0% | 0.02 |
| dip | 5 | Rp+8,541 | 2% | 60% | +0.57% | 4.6% | +7.2% | 10.5% | 0.12 |
| dividend | 19 | Rp-9,811 | -2% | 47% | +0.06% | 15.4% | +23.6% | 20.9% | -0.04 |

Top stocks (holdout): wifi Rp+355,861, dewa Rp+243,224, adro Rp+212,210, hrta Rp+178,749, bumi Rp+166,459
Exit reasons (holdout): {'end': 4, 'rule': 94, 'stop': 27, 'trail': 5}
Blocked or skipped orders (holdout): {'too_small_to_size': 194, 'limit_unfilled': 29, 'sell_blocked_at_arb': 4}
