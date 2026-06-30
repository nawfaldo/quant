# Quant — Windows Setup & Run Guide

A personal quantitative futures-trading stack (NQ / Nasdaq-100 + forex). Five components:

| Folder | Language | Purpose |
|---|---|---|
| `questdb_csv_importer/` | Zig 0.16 | Streams a 1-min OHLCV CSV into QuestDB, auto-aggregating to 7 timeframes. |
| `data_collection/` | Python 3.10+ | yfinance fetch, Bookmap tick scraper, QuestDB sync. |
| `march/` | Python | Live trading engine (MetaTrader5 + Bookmap). Needs a broker account + Bookmap license. |
| `web/` | Zig backend + Bun/React frontend | Candlestick chart UI **+ the backtester itself** (strategies, vol-target sizing, Monte Carlo, grid tuning run inside `web/backend` via `bt_run.zig`), VWAP, equity curves. |

> The old standalone `backtest/` TUI was merged into `web/backend` — there is no separate backtester component or `backtester.exe` anymore.

## Toolchain (installed)

| Tool | Version | Location |
|---|---|---|
| Zig | 0.16.0 | `C:\Users\andra\zig\zig.exe` (on PATH) |
| Bun | 1.3.14 | `C:\Users\andra\.bun\bin\bun.exe` (on PATH) |
| Node | 24.x | system |
| Python | 3.10 (Laragon) | system |

Both Zig and Bun are on the **user PATH** — open a fresh terminal so `zig` and `bun` resolve.

## Runtime dependency: QuestDB (INSTALLED ✅)

Every data-driven component reads market data from **QuestDB** on `localhost` (PGWire `:8812`, HTTP/ILP `:9000`/`:9009`).

- **Installed at:** `C:\questdb` (QuestDB 9.4.3, bundled JRE)
- **Data root:** `C:\Users\andra\Desktop\quant\qdbroot` (gitignored)
- **Start it:** `run-questdb.bat` → web console at http://localhost:9000
- **Data loaded:** NQ 1-min bars 2008→2026 (`nq-1m_bk.csv`, 5,864,337 rows) imported and aggregated to all 7 timeframes (`nq_1m`…`nq_1d`).

To load a different/updated dataset later, use `import-data.bat` (the importer needs a CSV **with a header row**; `import-data.bat`'s defaults match the NQ `date;time;O;H;L;C;V` semicolon format).

## One-click scripts (repo root)

| Script | What it does |
|---|---|
| `build-all.bat` | Builds all 3 buildable components (questdb_csv_importer, web/backend, web/frontend) in ReleaseFast. |
| `run-all.bat` | **One window, tmux-style.** Opens Windows Terminal split into 3 panes: QuestDB (left), backend :8080 (top-right), frontend :5173 (bottom-right). Falls back to separate windows if `wt.exe` is missing. |
| `run-questdb.bat` | Starts QuestDB in the **foreground, no admin needed** (auto-detects `C:\questdb` or `%QUESTDB_HOME%`; prints install steps if missing). |
| `import-data.bat "<csv>" [table]` | Imports a 1-min OHLCV CSV into QuestDB (NQ format defaults; auto-aggregates 7 timeframes). |
| `run-web.bat` | Launches backend (:8080) + frontend dev server in two windows. |
| `run-backend.bat [port]` | Backend only. |
| `run-frontend.bat` | Frontend dev server only. |
| `run-backtester.bat` | *Deprecated* — the standalone TUI was merged into the web app; this just prints where to go (use `run-all.bat` + web UI). |
| `run-march.bat` | Live trading engine. Auto-creates `march\.venv` + installs deps on first run, then `python main.py` (MT5 receiver on :5001). |
| `run-data.bat [yfinance\|sync\|bookmap]` | Data collectors. Auto-creates `data_collection\.venv` + installs deps; menu if no arg. Extra args pass through to the script. |

Everyday use: just double-click **`run-all.bat`** (QuestDB + backend + frontend in one split window), then open the frontend URL (http://localhost:5173).
Loading a fresh dataset: `run-questdb.bat` → `import-data.bat "C:\data\nq-1m.csv"` → `run-all.bat`.

> The two Python launchers create their own `.venv` and `pip install -r requirements.txt` on first run, so they are true one-click. Note: `MetaTrader5` ships wheels for Python 3.10–3.12 only; this machine's system Python is 3.13, so `run-march.bat` needs a 3.12 interpreter (install Python 3.12 and re-point the `python -m venv` line). `run-data.bat` works on 3.13.

## Build & run each component

### backtesting (now in the web app)
The standalone TUI backtester was removed. Backtests — strategies, vol-target sizing,
Monte Carlo, grid tuning — now run **inside `web/backend`** (`bt_run.zig`, `bt_tune.zig`,
`bt_combine.zig`, `bt/`) and are driven/visualized from the React frontend. Just run
`run-all.bat` (or `run-web.bat`) with QuestDB up and use the web UI.

### questdb_csv_importer
```bash
cd questdb_csv_importer
zig build              # → zig-out/bin/questdb_csv_importer.exe
# Example (NQ 1-min, Chicago time → ET):
./zig-out/bin/questdb_csv_importer your_nq_1m.csv --table nq --delim ';' \
  --ts-col date --ts-col2 time --tz-hours 1 --aggregate
```

### web/backend (port 8080, also embeds the march engine)
```bash
cd web/backend
zig build              # → zig-out/bin/backend.exe (+ signal_runner.exe)
zig build run          # serve on :8080
PORT=8090 zig build run
```

### web/frontend (Vite dev server / Bun)
```bash
cd web/frontend
bun install
bun dev                # dev server with hot reload (talks to backend :8080)
bun run build          # type-check + production bundle → dist/
```

### Python components
```bash
cd march            # or data_collection
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
`march/` needs MetaTrader5 (Windows-only, OK) + a broker account, and Bookmap — these are not auto-installable.

## Windows port notes (changes made for this machine)

The repo was authored on macOS (`/Users/nawfaldo/Bunker/Quant`) and partially ported. Changes applied so it builds & runs here:

- **`backtest/build.zig`** — was linking macOS Homebrew SQLite (`/opt/homebrew/...`). Now bundles the SQLite amalgamation (`src/sqlite3.c`, copied from `web/backend`) and links `ws2_32` on Windows, matching the web backend's self-contained pattern.
- **`backtest/src/term.zig`** (new) — cross-platform terminal layer. The TUI's POSIX raw-mode/`poll`/`ioctl` calls are now behind a Win32 Console API backend (`SetConsoleMode` + `ReadConsoleInputW` + `WaitForSingleObject` + VT output). POSIX path unchanged.
- **`backtest/src/clock.zig`** (new) — cross-platform wall clock. `std.c.clock_gettime` (POSIX-only) is kept for macOS/Linux; Windows uses `GetSystemTimeAsFileTime`. Used by `db.zig` (run timestamp) and `montecarlo.zig` (PRNG seed).
- **DB path constants** — the hardcoded Windows paths pointed at a different machine (`C:/Users/JawirGaming66/...`). Repointed to `C:/Users/andra/Desktop/quant/...` in `backtest/src/db.zig`, `web/backend/src/db.zig`, `web/backend/src/settings.zig`, `web/backend/src/march/db.zig`.

> Note: some `CLAUDE.md` files still describe the old macOS SQLite linking / paths; the code above is the source of truth.
