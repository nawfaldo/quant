# IDX book

Rp1,000,000 Stockbit account, 5 sleeves (ma_trend, high_52w, donchian, dip, dividend), sizing: risk (risk 3%/trade, max position 35%, max 5 positions, heat 12%). In-sample 2016-01-04 to 2021-05-03, holdout 2021-05-04 to 2026-09-24.

Management: entry=open, stop_fill=close. Picking: rank=priority.

| run | return | max dd | trades | win % | PF | avg trade | invested |
|---|---|---|---|---|---|---|---|
| in-sample | +277.8% | 30.2% | 118 | 42% | 1.96 | +8.17% | 91% |
| holdout | +205.5% | 50.1% | 134 | 31% | 1.61 | +14.85% | 99% |
| full | +596.7% | 47.1% | 259 | 34% | 1.54 | +9.90% | 95% |

## Holdout, per sleeve

| sleeve | trades | P&L | share | win % | avg trade | closed dd | alone: return | alone: dd | in book / alone |
|---|---|---|---|---|---|---|---|---|---|
| ma_trend | 39 | Rp+716,414 | 35% | 26% | +6.97% | 29.5% | +81.1% | 29.6% | 0.88 |
| high_52w | 48 | Rp+1,711,156 | 83% | 25% | +32.65% | 45.1% | +219.0% | 29.2% | 0.78 |
| donchian | 13 | Rp+91,239 | 4% | 31% | +12.24% | 11.0% | +23.8% | 25.1% | 0.38 |
| dip | 8 | Rp-306,832 | -15% | 38% | -5.09% | 34.3% | +4.6% | 13.1% | -6.63 |
| dividend | 26 | Rp-156,693 | -8% | 46% | +1.28% | 51.6% | +22.3% | 22.7% | -0.70 |

Top stocks (holdout): wifi Rp+2,110,598, dewa Rp+782,130, indy Rp+439,701, bumi Rp+275,748, essa Rp+197,856
Exit reasons (holdout): {'end': 3, 'rule': 101, 'stop': 30}
Blocked, skipped or rotated (holdout): {'too_small_to_size': 203, 'sell_blocked_at_arb': 3}
