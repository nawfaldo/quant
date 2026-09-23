# NQ Whale Digestion — chronological 80/20

Status: **rejected**.

The first 296 sessions selected one of
three emergency stops. The final 75
sessions were evaluated once and did not influence selection.

## Frozen strategy

- Emergency stop: 240 points
- Profit target: none
- Time exit: 15 minutes
- Risk: 0.25% of live equity, Forex 0.01-lot floor

## Result

| Window | Trades | Net points | Edge | Point PF | Sized PnL | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 80% train | 166 | +352.30 | +2.122 | 1.112 | +3.52 | 4.35 |
| 20% holdout | 32 | +4.10 | +0.128 | 1.005 | +0.04 | 4.83 |

Promotion requires at least 30 holdout trades, PF 1.10, mean edge
0.60 points, and a bootstrap lower bound above zero.
