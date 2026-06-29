# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

Quantitative trading system for NQ futures (and forex pairs). Four subsystems:

| Directory | Role |
|---|---|
| `web/backend/` | Zig 0.16 HTTP server (port 8080) — data proxy + backtest engine/API + live march engine |
| `web/frontend/` | React + TypeScript SPA — chart dashboard, backtest stats, live trading UI |
| `march/` | Python FastAPI server (port 5001) — MetaTrader 5 execution bridge |
| `data_collection/` | Python scripts — fetch data from yfinance / Bookmap into QuestDB |
| `questdb_csv_importer/` | Zig CLI — stream CSV into QuestDB via ILP; builds all 7 timeframes in one pass |

The backtest engine (formerly a standalone CLI in `backtest/`) now lives inside the web backend at `web/backend/src/bt/` and is driven over HTTP by the Test page — see "Backtest engine" below.

Submodules have their own `CLAUDE.md` files with detailed context.

## Commands

### Web backend (`web/backend/`)
```bash
zig build          # compile
zig build run      # compile and run (port 8080)
PORT=8090 zig build run   # custom port
```

### Web frontend (`web/frontend/`)
```bash
bun install        # install dependencies
bun dev            # start dev server with hot reload
bun run build      # type-check + production bundle → dist/
```

### QuestDB CSV importer (`questdb_csv_importer/`)
```bash
zig build                       # produces zig-out/bin/questdb_csv_importer
zig build run -- <args>         # build + run
```

### March Python bridge (`march/`)
```bash
pip install -r requirements.txt
python main.py     # starts FastAPI on port 5001
```

## Architecture

### Data flow

```
QuestDB (localhost:9000 HTTP, 8812 PGWire) ← CSV importer / yfinance scripts
       ↓ HTTP chunked-CSV via questdb.zig (candles/VWAP) + PGWire via bt/data.zig (backtest engine)
web backend (port 8080)
       ↓ binary blobs + JSON
web frontend (port 5173 dev)
       ↓ WebSocket (live ticks from Bookmap addon at ws://localhost:8765)
```

### Live trading path (Windows-only)
```
Bookmap addon → WS :8765 → ws.zig (web backend thread)
                                    ↓
                            strategy signal → /execute HTTP → march/api_server.py (:5001)
                                                                        ↓
                                                                   MT5 order
```
The Bookmap WS client and march engine thread are spawned only on Windows (`builtin.os.tag == .windows`). March API routes (`/api/march/*`) still compile and respond on macOS/Linux, but live tick data doesn't flow.

### Timezone model (critical — applies everywhere)
All timestamps are **New York (ET) wall-clock times stored as fake-UTC**. The CSV importer uses `--tz-hours 1` to shift Chicago CT → ET and writes those values as nanosecond UTC. **Never apply `America/New_York` or any tz offset downstream.** Always format with `timeZone: 'UTC'` in JS; use plain UTC epoch arithmetic in Zig. The RTH window 09:30–16:00 is detected by comparing UTC hours directly against those bounds.

### Binary wire formats
Binary responses avoid JSON overhead for large datasets. All formats are little-endian with a magic header:

| Route | Magic | Row size | Fields |
|---|---|---|---|
| `/api/candles/bin` | `0x45444C43` | 20 B | `u32 unix_s, f32 open, high, low, close` |
| `/api/vwap/bin` | `0x50415756` | 8 B | `u32 unix_s, f32 rth_vwap` (0.0 = outside RTH) |
| `/api/trades/:id` | `0x54524445` | 25 B | `u8 side, u32 et, u32 xt, f32 ep, xp, pnl, u32 qty` |

### Web backend internals
- HTTP server is hand-rolled on `std.Io.net` (`http.zig`) — no zap/facil.io (they're POSIX-only).
- SQLite is bundled as `src/sqlite3.c` amalgamation — no system dependency.
- `cache.zig` fetches candle/VWAP blobs **on demand** from QuestDB per request (no startup pre-cache); QuestDB always responds with `Transfer-Encoding: chunked`, decoded in `questdb.zig`.
- Two SQLite databases: `app.db` (backtest results, march settings) and `march.db` (MT5 accounts, live strategies, trades).
- Route dispatch is a flat `if` chain in `router.zig:onRequest`. To add a route, add an `if` branch there.

### Zig 0.16 patterns used throughout
`std.ArrayList` is unmanaged — pass allocator to every mutating call:
```zig
var list: std.ArrayList(u8) = .empty;
defer list.deinit(allocator);
try list.append(allocator, byte);
```
Use `std.Io` writers (`io.file.stdout().writer(io, &buf)`) and flush explicitly. Do not use `std.debug.print` for response output, and do not use `std.io` (pre-0.16 namespace). Current time: `std.c.clock_gettime(std.c.CLOCK.REALTIME, &ts)`.

### Backtest engine (`web/backend/src/bt/`)
The engine, data client, Monte Carlo, sizing, and strategies live under `src/bt/`; the HTTP handlers `bt_run.zig`, `bt_tune.zig`, `bt_combine.zig` drive them and are dispatched from `router.zig`. The frontend Test page (`TestPage.tsx`) is the only UI: Run, Tune, and Combine. Saved runs and their trades/montecarlo go into `app.db`.

Run/Tune apply `contracts = base_lot × leverage` (× the vol-target multiplier when sizing is on); Tune grid-sweeps `base_lot × leverage × vol_params`. Combine merges each picked backtest's **saved trades** (deterministic engine ⇒ same as re-running) and marks the pooled book to market for a real portfolio drawdown.

Strategy contract — `engine.run` takes `strat: anytype` (compile-time duck typing):
```zig
pub const timeframe: []const u8 = "5m";
pub const columns = .{ .open=bool, .high=bool, .low=bool, .close=bool, .volume=bool };
pub fn update(self: *@This(), bar: engine.Bar, ts: engine.Ts) engine.Signal;
```
Signals: `.long`, `.short`, `.flat`, `.close`. Default fill: next bar's open (1-bar delay). For intrabar stops/targets, expose `exit_fill: ?f64` on the strategy struct — the engine closes at that exact price when non-null (see `engine.closeFill`). Current strategies: `5m_orb`, `30m_buy`, `rth_vwap`.

To add a strategy: create `src/bt/strategies/<name>.zig` with the three decls, then add a dispatch branch (display name → type) in `bt_run.zig`, `bt_tune.zig`, and `bt_combine.zig`, and add it to the `strategies` list in `TestPage.tsx`.

## Runtime dependencies

- **QuestDB** on `localhost:9000` (HTTP) and `localhost:8812` (PGWire). Tables: `{symbol}_{timeframe}` — symbols: `nq`, `gbpusd`, `eurusd`; timeframes: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`.
- **Bookmap** WebSocket addon on `ws://localhost:8765` (Windows only, live ticks).
- **MetaTrader 5** terminal (Windows only, accessed via Python `MetaTrader5` package).
- **SQLite** — bundled in `web/backend` (`src/sqlite3.c` amalgamation); no system dependency.
