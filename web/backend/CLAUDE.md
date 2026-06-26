# Backend

Zig 0.16 HTTP server, **cross-platform (Windows / macOS / Linux), built on `std.Io.net`** — no zap/facil.io (which is POSIX-only and forced a WSL build on Windows). Acts as a proxy/cache layer between the frontend and QuestDB + SQLite, adding CORS headers.

**The march live-trading engine is integrated into this process** (`src/march/`). All march routes (`/api/march/strategies`, `/api/march/bar`, `/api/march/trades`, `/api/march/mt5/accounts/**`) are served on the **same port 8080** as the web routes — there is no separate port-4000 server. On Windows, `main()` spawns a thread that calls `march.init(io)`: this re-arms active strategies and starts the Bookmap WebSocket client (ws.zig / ws2_32 / Python on :5001). The Bookmap WS client is Windows-only; on macOS/Linux the march routes still compile and respond but live tick data does not flow. Outgoing HTTP calls (Python :5001, QuestDB :9000) use `std.Io.net` so they compile cross-platform.

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Zig 0.16 |
| HTTP server | Hand-rolled HTTP/1.1 on `std.Io.net` (`src/http.zig`) — cross-platform, no external dependency |
| SQLite | **bundled** (`src/sqlite3.c` amalgamation, compiled by `build.zig`) — not system-linked, so it builds the same on every OS |
| OHLCV data | QuestDB on `localhost:9000` over `std.Io.net` (chunked-CSV `/exp`) |
| Backtest results | SQLite at the per-OS path in `db.zig` (`DB_PATH`) |
| Allocator | `std.heap.page_allocator` (per-request, supports free) |

## Commands

```bash
zig build          # compile (native: Windows, macOS, or Linux — no WSL needed)
zig build run      # compile and run (port 8080)
PORT=8090 zig build run   # run on a different port (env override)
```

The accept loop is single-threaded and binds `127.0.0.1`. `std.Io.net`'s Windows backend needs Winsock, which `build.zig` links (`ws2_32`) only on Windows. Kill any existing listener on the port first (Windows: `netstat -ano | findstr :8080` then `taskkill /PID <pid> /F`; POSIX: `lsof -ti :8080 | xargs kill -9`).

## Project layout

```
backend/
├── build.zig          # build script (bundles sqlite3.c; links ws2_32 on Windows)
├── build.zig.zon      # package manifest (no dependencies)
└── src/
    ├── main.zig       # entry: web accept loop (PORT env) + spawns march.serve on Windows
    ├── http.zig       # cross-platform HTTP/1.1 server + Ctx (request/response adapter)
    ├── router.zig     # web request dispatch (onRequest takes *http.Ctx)
    ├── cache.zig      # on-demand candle/VWAP blob builders (QuestDB); takes std.Io
    ├── db.zig         # SQLite helpers: backtests list, trades/montecarlo binary
    ├── settings.zig   # app.db key/value settings (incl. march settings + layouts)
    ├── questdb.zig    # std.Io.net fetch + chunked-decode from QuestDB
    ├── sqlite3.c/.h   # bundled SQLite amalgamation
    └── march/         # integrated march live-trading server (Windows-only thread)
        ├── api.zig         # serve(io): port-4000 server + WS engine (was march/zig api)
        ├── db.zig          # march.db (mt5_accounts, strategies, trades) — at web/backend/march.db
        ├── ws.zig          # Bookmap live-push WebSocket client
        ├── engine.zig / data.zig
        ├── signal_runner.zig   # stdin/stdout strategy bridge → built as a separate exe
        ├── strategies/ , sizings/
```

`march.db` lives at `web/backend/march.db` (absolute path in `march/db.zig`); `march/db.py` reads it from `../web/backend/march.db`. The Python side (`march/`, port 5001) is unchanged otherwise — it still receives `/execute` from the march server on 4000.

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

### HTTP server: `std.Io.net`, not zap
The server is hand-rolled on `std.Io.net` (`http.zig`), which works natively on Windows, macOS, and Linux. zap/facil.io was dropped because it does not compile on Windows (forced a WSL build). `main(init: std.process.Init)` supplies the `std.Io` instance; it is threaded through `http.Ctx` → `router.onRequest` → `cache.*` → `questdb.open`, so the QuestDB client uses the same cross-platform `std.Io.net` networking (no `extern "c"` sockets — `std.posix` no longer exposes `socket`/`connect`/`recv`/`send` in Zig 0.16, so `std.Io.net` is the only portable path). `http.Ctx` mirrors the small slice of zap's `Request` surface the router relied on (`path`/`query`/`method`/`body`, `setHeader`/`setStatusNumeric`/`setContentType`/`sendBody`/`sendJson`), so routing code was unchanged apart from the request type.

> **macOS note:** the old `extern "c"` QuestDB socket set `SO_RCVBUF`/`TCP_NODELAY` to work around a macOS localhost stall. `std.Io.net` doesn't expose those options, so they were dropped — correctness is unaffected; if large-result QuestDB fetches feel slow on macOS, that tuning is the thing to reintroduce.

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
