# IDX book: signal picking study

Rp1,000,000 Stockbit account, 5 sleeves (ma_trend, high_52w, donchian, dip, dividend), sizing: risk (risk 3%/trade, max position 35%, max 5 positions, heat 12%). In-sample 2016-01-04 to 2021-05-03, holdout 2021-05-04 to 2026-09-24.

Default: rank=priority. 16 cells scored in-sample; 9 pass the gates with robust neighbours.

| run | window | return | max dd | trades | win % | PF | avg trade | invested |
|---|---|---|---|---|---|---|---|---|
| default | in-sample | +277.8% | 30.2% | 118 | 42% | 1.96 | +8.17% | 91% |
| default | holdout | +205.5% | 50.1% | 134 | 31% | 1.61 | +14.85% | 99% |
| chosen | in-sample | +277.4% | 31.1% | 148 | 34% | 1.92 | +7.08% | 91% |
| chosen | holdout | +55.1% | 43.1% | 146 | 36% | 1.30 | +3.69% | 98% |

Every cell (in-sample selects; holdout shown for context only):

| cell | IS return | IS dd | holdout return | holdout dd | holdout trades |
|---|---|---|---|---|---|
| rank=priority | +277.8% | 30.2% | +205.5% | 50.1% | 134 |
| rank=priority, rotate=True | +269.4% | 28.3% | +132.2% | 47.8% | 185 |
| rank=edge | +221.5% | 24.1% | +180.8% | 45.4% | 132 |
| rank=edge, rotate=True | +273.9% | 30.0% | +178.7% | 35.9% | 179 |
| min_edge=0.0, rank=priority | +142.9% | 33.6% | +25.5% | 50.3% | 122 |
| min_edge=0.0, rank=priority, rotate=True | +234.7% | 35.3% | +44.0% | 46.5% | 153 |
| min_edge=0.0, rank=edge | +225.8% | 32.0% | +51.1% | 41.6% | 116 |
| min_edge=0.0, rank=edge, rotate=True (chosen) | +277.4% | 31.1% | +55.1% | 43.1% | 146 |
| min_edge=1.0, rank=priority | +124.5% | 38.3% | +7.3% | 38.1% | 120 |
| min_edge=1.0, rank=priority, rotate=True | +131.4% | 36.1% | +114.3% | 39.8% | 151 |
| min_edge=1.0, rank=edge | +244.4% | 26.8% | +5.8% | 39.6% | 116 |
| min_edge=1.0, rank=edge, rotate=True | +138.1% | 33.0% | +99.0% | 39.6% | 135 |
| min_edge=2.0, rank=priority | +129.1% | 36.4% | +27.0% | 42.0% | 105 |
| min_edge=2.0, rank=priority, rotate=True | +15.0% | 38.9% | +50.0% | 23.9% | 118 |
| min_edge=2.0, rank=edge | +134.0% | 31.7% | +68.8% | 38.8% | 105 |
| min_edge=2.0, rank=edge, rotate=True | +13.3% | 42.9% | +86.4% | 29.6% | 121 |

0 of 16 cells beat the default on the holdout.

## Holdout per sleeve (chosen: min_edge=0.0, rank=edge, rotate=True)

| sleeve | trades | P&L | share | win % | avg trade | closed dd | alone: return | alone: dd | in book / alone |
|---|---|---|---|---|---|---|---|---|---|
| ma_trend | 57 | Rp+508,564 | 92% | 33% | +7.44% | 22.8% | +20.5% | 35.6% | 2.49 |
| high_52w | 31 | Rp-283,578 | -51% | 23% | -5.52% | 32.8% | -14.9% | 36.6% | 1.90 |
| donchian | 25 | Rp+27,920 | 5% | 28% | +7.73% | 10.1% | +25.2% | 23.1% | 0.11 |
| dip | 8 | Rp+117,579 | 21% | 50% | +2.56% | 2.8% | +6.9% | 11.2% | 1.70 |
| dividend | 25 | Rp+180,989 | 33% | 60% | +2.89% | 19.4% | +38.3% | 19.0% | 0.47 |

Top stocks (holdout): adro Rp+262,893, medc Rp+248,833, dewa Rp+220,181, hrta Rp+212,736, wifi Rp+98,468
Exit reasons (holdout): {'end': 4, 'rotate': 29, 'rule': 88, 'stop': 25}
Blocked, skipped or rotated (holdout): {'below_min_edge': 646, 'rotations': 29, 'no_room': 233, 'too_small_to_size': 28, 'sell_blocked_at_arb': 7}
