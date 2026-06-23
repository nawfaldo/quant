# Backend

Zig 0.16 HTTP server using the zap framework (backed by facil.io). Acts as a proxy/cache layer between the frontend and QuestDB + SQLite, adding CORS headers.

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Zig 0.16 |
| HTTP framework | [zap](https://github.com/zigzap/zap) (facil.io wrapper) |
| OHLCV data | QuestDB on `localhost:9000` (table: `nq_1min`) |
| Backtest results | SQLite at `/Users/nawfaldo/Bunker/Quant/backtest/backtests.db` |
| Allocator | `std.heap.page_allocator` (per-request, supports free) |

## Commands

```bash
zig build          # compile
zig build run      # compile and run (port 8080)
```

Always kill any existing process on port 8080 before `zig build run` (`lsof -ti :8080 | xargs kill -9`).

## Project layout

```
backend/
├── build.zig          # build script
├── build.zig.zon      # package manifest
└── src/
    ├── main.zig       # entry point: startup, cache init, listener
    ├── router.zig     # request dispatch (onRequest)
    ├── cache.zig      # startup caches for candles + VWAP (QuestDB)
    ├── db.zig         # SQLite helpers: backtests list, trades binary
    └── questdb.zig    # raw C-socket fetch + chunked-decode from QuestDB
```

## API routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | `{"status":"ok"}` liveness check |
| GET | `/api/candles/bin` | Binary OHLC, fetched on demand from QuestDB (`?tf=`, defaults to `default_timeframe` in app.db) |
| GET | `/api/vwap/bin` | Binary RTH VWAP, computed on demand from OHLCV |
| GET | `/api/backtests` | JSON list of backtests from SQLite |
| GET | `/api/trades/:id` | Binary trades for a backtest from SQLite |
| OPTIONS | `*` | CORS preflight |

## Binary wire formats

### `/api/candles/bin`
- Header: `u32 magic (0x45444C43)` | `u32 row_count`
- Row (20 B): `u32 unix_seconds, f32 open, f32 high, f32 low, f32 close`

### `/api/vwap/bin`
- Header: `u32 magic (0x50415756)` | `u32 row_count`
- Row (8 B): `u32 unix_seconds, f32 rth_vwap`
- `rth_vwap = 0.0` means the bar is outside RTH (09:30–16:00 ET)
- RTH VWAP is **computed in code** (not stored in DB): typical = `(high+low+close)/3`, `vwap = Σ(typical×volume)/Σ(volume)`, anchored at session open, reset each calendar day

### `/api/trades/:id`
- Header: `u32 magic (0x54524445)` | `u32 row_count`
- Row (25 B): `u8 side (0=long,1=short), u32 et, u32 xt, f32 ep, f32 xp, f32 pnl, u32 qty`

## Key implementation details

### Why raw C sockets instead of `std.http.Client`
Zig 0.16 redesigned the I/O model: `std.http.Client` now requires a `std.Io` instance only available from `main(init: std.process.Init)`. zap callbacks run inside facil.io's event loop where `std.Io` is inaccessible. The workaround is calling C library `socket`/`connect`/`send`/`recv` directly via `extern "c"`.

### Why chunked decoding is needed
QuestDB always responds with `Transfer-Encoding: chunked`. The proxy decodes it into a plain byte stream before parsing.

### Timezone model

All timestamps served by the backend are **New York (ET) wall-clock times stored as fake-UTC** by the importer (`--tz-hours 1` bakes ET into the nanosecond values, which QuestDB stores and returns as if they were UTC). The backend passes these epoch-second values to the frontend unchanged — no timezone conversion is applied anywhere in the backend. The frontend must treat them as UTC (i.e. display verbatim) to show correct ET times.

The VWAP RTH window (`09:30–16:00 ET`) in `cache.zig` is therefore detected by comparing the UTC hour/minute of each bar's timestamp directly against those bounds — **do not convert to `America/New_York`**.

### On-demand candle/VWAP fetch
`cache.zig` builds each candle/VWAP binary blob **per request** straight from QuestDB (`cache.fetchTf` / `cache.fetchVwap`), and the router frees it after sending. Nothing is cached: peak memory is one blob at a time and startup does no QuestDB scans at all (it used to pre-build all 7 timeframes + VWAP, which thrashed an 8 GB Mac and raced QuestDB's post-restart partition hydration → `error.IncompleteResponse`). Trade-off: the first hit for a given timeframe pays the QuestDB scan cost. `default_timeframe` (seeded to `5m`) lives in app.db `settings` and is used when `/api/candles/bin` is called without `?tf=`.

### SQLite busy timeout
Both `getBacktests` and `getTradesBin` open the DB with `SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX` and set a 3-second busy timeout (`sqlite3_busy_timeout`) to handle concurrent writes from the backtester.

### Allocator pattern (Zig 0.16)
`std.ArrayList` is unmanaged — pass allocator to every mutating method:
```zig
var list: std.ArrayList(u8) = .empty;
defer list.deinit(allocator);
try list.append(allocator, byte);
const owned = try list.toOwnedSlice(allocator);
```

### Adding a new route
Add an `if` branch in `router.zig:onRequest`:
```zig
if (std.mem.eql(u8, path, "/api/my-route")) {
    try req.sendJson("{\"hello\":\"world\"}");
    return;
}
```
