# OPEN: FundedNext commission in the backtest does not match the terminal

Measured 2026-10-14 on the FundedNext demo (35188708, FundedNext-Server 3, USD),
0.10-lot round trips placed through `mt5/hand.py`. Commission is booked on the
CLOSING deal only; the opening deal shows 0.

| Symbol | Terminal | Backtest model (`commission_price`, `costs.rs`) | Gap |
|---|---|---|---|
| EURUSD (forex) | **$3.00 per lot** | $7 per lot, once at the open | model 2.3x too HIGH (conservative) |
| USOUSD (oil) | **$3.00 per lot** (~3.2 bp of notional at $95) | 0.0016% of notional (~$0.15 per lot) | model ~20x too LOW (optimistic) |
| SPX500 (index) | $0 | $0 | none |

At 0.01 lot every forex/oil/gold round trip also showed -$0.03, so it looks like a
flat per-lot fee, not a notional percentage. XAUUSD was not re-measured at 0.10.

Matches the terminal (no action): contract size, tick value, P&L per point, forex
spread, index commission. SPX500 spread matched only loosely (test ran off-hours).

## Why it matters
- Live trading is unaffected: the broker charges the real fee and the bridge
  reports P&L from the actual deals.
- Backtest results are off: oil sleeves (`usoil_roofing_60m_barlb_off`) look
  better than they will trade (about 3 bp per trade on top of a 7.5 bp spread);
  forex looks worse. `compare_live_backtest.py --account fundednext` will show a
  small dollar gap on forex/oil trades until fixed; it is NOT an execution fault.

## Caveats
- Demo account only; a challenge/funded account may charge differently.
- Figures are cent-rounded (0.03 at 0.01 lot); the 0.10-lot test gives $0.30.
- Not checked: whether the live runtime uses modelled costs for sizing/risk limits.
- Not measured: stocks, crypto, other metals (none are in the FundedNext book).

## To fix
Set FundedNext commission to $3 per lot, charged at the close, for forex and
commodities (indices stay 0) in `live_trade/src/backtest/engine/costs.rs`
(`Book::FundedNext`) AND in `sandbox/research/cfd_families.py` (`commission_price`,
`BROKERS`) so Python and Rust stay identical, update `sandbox/results/fundednext_specs.json`
`cost_model` text, then rerun the FundedNext book and report per-sleeve change.
The old $7 / 0.0016% figures came from the FundedNext help centre (2026-09-24),
never measured.

Related: `mt5/hand.py` (how these trades were placed).
