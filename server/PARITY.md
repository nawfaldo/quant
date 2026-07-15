# Behavioral parity results

The Zig and Rust services were run side by side against the same local QuestDB
on 2026-07-15. These checks compare behavior, not merely route names.

## Passing fixtures

| Fixture | Compared output | Result |
| --- | --- | --- |
| Night Drift, NQ forex, 2026-06-01 through 2026-06-19, $1,000 | 3 trades; timestamps, fills, quantities, P&L, report metrics, and drawdowns | Match |
| Noise Momentum, NQ forex, 2026-05-01 through 2026-06-19, $1,000 | 37 trades; timestamps, fills, partial quantities, P&L, report metrics, and drawdowns | Match |
| Stationary-block Monte Carlo for both runs | seed, path ordering, checkpoints, percentiles, drawdowns, profit probability, and ruin probability | Match |
| Noise-area endpoint, NQ, 2026-06-18 through 2026-06-19 | warm-up, 360-point boundary, timestamps, and four-decimal bounds | Match |
| Two-combination tuning grid | status contract, metrics, rankings, and summary | Match |
| March live API smoke test | inactive response, activation and warm-up, stateful bar response, and timestamp validation | Pass |

## Frontend endpoint sweep

On 2026-07-15, 50 frontend-facing checks were replayed against Zig and Rust
using copies of the same SQLite fixture and the same QuestDB. The sweep covered
market-data and saved-result binaries, settings/workspace persistence,
environment rules, MT5 account strategies, run/save, combine/save, tuning,
CORS, and representative error responses.

- Night Drift run output matched at Zig's displayed precision: three trades,
  fills, quantities, P&L, final balance, growth, and drawdowns.
- Compared candle, trade, and Monte Carlo blobs, including newly saved results,
  were byte-for-byte identical.
- Rust tuning, optional missing-tick-table responses, March symbol errors,
  trade JSON fields, and CORS were corrected to match the observed Zig
  contract, then verified by replaying the sweep.
- Combined portfolio accounting now uses Zig's candle-by-candle mark-to-market
  event ordering. Max/average and intraday drawdown percentages, dollar values,
  and dates all match (`14.8000%` and `$895.94` maximum for this fixture).
- Noise-area timestamps, count, and values match exactly.
- All deterministic response fields matched. The only comparison difference was
  the intentionally variable tuning elapsed time (`284 ms` versus `376 ms`).

The Windows-only Bookmap/MT5 EA integration was outside this backend-to-backend
sweep. MT5 status and position endpoints now run through the Rust backend.

Rust API formatting now follows Zig's field-specific two- and four-decimal
contract while retaining full precision for internal accounting and persisted
Monte Carlo data.

## Not testable in this workspace

The local QuestDB does not contain `fx_nq_ticks`, so FX repricing could not be
compared end to end. The Rust implementation has unit coverage for ordinary
time fills and same-bar level exits, but remains partial until a shared tick
fixture is available.

## Still required

Passing production-default strategy fixtures does not complete the migration.
Runtime optimizer parameter surfaces, mixed-symbol/instrument portfolio
fixtures, golden March live-decision fixtures, Windows Bookmap/MT5 integration,
tuning artifacts, CLI optimizers/validators, and the broader Zig test suite
remain on the checklist in [`PORTING.md`](PORTING.md).
