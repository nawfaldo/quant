# Frontend

React + TypeScript + Vite + Bun + Tailwind CSS single-page app. Two routes: `/` is the live March trading chart, `/stats` is backtests analysis.

## Stack

| Layer | Technology |
|-------|-----------|
| Language | TypeScript 6 + React 19 |
| Bundler / dev server | Vite 8 |
| Package manager | Bun |
| Styling | Tailwind CSS v4 (via `@tailwindcss/vite` plugin) |
| Data fetching / caching | TanStack React Query v5 (`@tanstack/react-query`) |
| Chart | lightweight-charts v5 |
| Backend | Zig server on `http://localhost:8080` |

## Commands

```bash
bun install        # install dependencies
bun dev            # start dev server (hot reload)
bun run build      # type-check + production bundle → dist/
bun run preview    # serve the production bundle locally
```

## Project layout

```
frontend/src/
├── main.tsx                    # React root + QueryClientProvider
├── App.tsx                     # top-level state, layout, context provider
├── api.ts                      # fetch helpers (binary decoders: trades; JSON: backtests, march settings)
├── types.ts                    # shared types: Bar, Backtest, Trade, Indicators, TF, MarchLayouts, etc.
├── style.css
├── context/
│   └── AppContext.tsx          # shared React context (march settings, indicator toggles, account state)
├── components/
│   ├── ChartPanel.tsx          # main chart: live tick stream + historical candles + 24h VWAP + trade overlay
│   ├── MarchPage.tsx           # / route: multi-panel layout grid (single, split-v, split-h, 2×2, bottom-row-*)
│   ├── StatsPage.tsx           # /stats route: backtest list + analysis / equity curve / monte-carlo tabs
│   ├── Header.tsx              # per-panel date range + symbol + TF controls
│   ├── AccountsTree.tsx        # MT5 account/strategy tree with live status
│   ├── ActivePositionsTable.tsx # live open positions (polled every 2s)
│   ├── IndicatorsModal.tsx     # VWAP toggle (per panel)
│   ├── AccountModal.tsx        # add MT5 account dialog
│   ├── StrategyModal.tsx       # add/configure strategy dialog
│   ├── StrategyControls.tsx    # inline strategy on/off controls
│   ├── AccountSelect.tsx       # account picker
│   ├── EquityChart.tsx         # lightweight-charts area chart for equity curve (used in StatsPage/StatsModal)
│   ├── MonteCarloChart.tsx     # monte-carlo fan chart (SVG)
│   ├── StatsModal.tsx          # per-backtest stats popup
│   ├── Sidebar.tsx             # left nav (/ and /stats links)
│   └── icons.tsx               # SVG icon components
└── lib/
    └── primitives.ts           # ActivePositionsPrimitive, HistoricalTradesPrimitive, OpeningRangePrimitive
```

## State & data flow

Global state lives in `AppContext` (provided by `App.tsx`). Each `ChartPanel` manages its own local state: chart lifecycle, VWAP accumulators, live tick WebSocket, and per-panel query for `fetchMarchCandles`.

```
Zig backend (port 8080)
  GET /api/march/candles/bin  →  fetchMarchCandles()  →  per-panel useEffect (historical + VWAP seed)
  GET /api/march/ticks        →  WebSocket bm_nq_ticks → applyTicks() (live stream)
  GET /api/backtests          →  fetchBacktests()     →  StatsPage
  GET /api/trades/:id         →  fetchTrades(id)      →  EquityChart / StatsPage
  GET /api/march/settings     →  fetchMarchSettings() →  App.tsx (persisted per-session state)
  GET /api/march/layouts      →  fetchMarchLayouts()  →  App.tsx (per-panel configs)
```

Context fields (AppContext):
- `modalOpen` / `setModalOpen` — BacktestsModal open state
- `visibleIds` / `loadingIds` / `allTrades` / `toggleId` — backtest trade overlay state (fetched via `useQueries` in App.tsx, displayed via `TradeLinesPrimitive` in ChartPanel)
- `indicatorsOpen` / `setIndicatorsOpen` — VWAP indicator modal
- `marchSymbol`, `marchTf`, `marchMode`, `marchFromDate`, `marchToDate` — persisted to app.db
- `marchLayout`, `marchLayouts`, `updateMarchPanel`, `activeMarchPanel` — multi-panel layout config
- `marchBottomHeight`, `isBottomOpen` — bottom panel resize state
- `selectedAccountId`, `marchAccountModalOpen`, `marchStrategyModalOpen` — account/strategy modals
- `visibleTradeStrategies`, `toggleTradeStrategy` — live trade overlay filter
- `selectedBacktestId`, `activeTab` — StatsPage selection

## ChartPanel (`ChartPanel.tsx`)

Each panel is fully self-contained. Runs its own data pipeline:

1. **Historical load**: `fetchMarchCandles` → sets candlestick series, seeds 24h VWAP accumulators
2. **Live stream**: WebSocket `ws://localhost:8765` (Bookmap addon) → `applyTicks()` → updates OHLCV + VWAP tick-by-tick
3. **VWAP**: 24h VWAP with **two re-anchors per ET day**: midnight (00:00) and RTH open (09:30). This gives two continuous VWAP sessions per day (overnight 00:00–09:30 and RTH+evening 09:30–24:00). The line breaks at each anchor so it never connects across a reset.
4. **Primitives**: `ActivePositionsPrimitive` (live positions), `HistoricalTradesPrimitive` (live trade history), `TradeLinesPrimitive` (backtest trade overlays from `allTrades`), `OpeningRangePrimitive` (09:30–10:00 OR box for orb_buy strategy)

The VWAP accumulator logic is mirrored in both the historical render and the live `applyTicks` path — they use the same midnight + 09:30 anchor rule so the line is continuous across the historical→live boundary.

## Primitives (`lib/primitives.ts`)

- **`ActivePositionsPrimitive`**: canvas overlay showing live MT5 open positions as price lines with P&L labels
- **`HistoricalTradesPrimitive`**: canvas overlay of completed march trades (entry/exit arrows + P&L)
- **`OpeningRangePrimitive`**: red box for days where the 09:55 ORB breakout triggered. Mirrors `strategies/30m_buy.zig` exactly — **do not change independently**:
  - Range bars: 09:30–09:50 (first five 5m candles). `OR_high` = max close (breakout reference)
  - Box extents: body high/low across all six bars including 09:55
  - Trigger: 09:55 bar close > OR_high
  - Timestamps are fake-UTC ET; always use `timeZone: 'UTC'`

## Binary wire formats (parsed in `api.ts`)

### Trades (`/api/trades/:id`)
- 8-byte header: `u32 magic (0x54524445)` | `u32 count`
- 25 bytes/row: `u8 side (0=long,1=short), u32 et, u32 xt, f32 ep, f32 xp, f32 pnl, u32 qty`

### March candles (`/api/march/candles/bin`)
- 8-byte header: `u32 magic (0x45444C43)` | `u32 count`
- 24 bytes/row: `u32 time, f32 open, f32 high, f32 low, f32 close, f32 volume`

## Timezone model

All timestamps from the backend are **New York (ET) wall-clock times stored as fake-UTC** by the importer. The epoch seconds values already represent ET; **never apply `America/New_York` (or any timezone) when formatting them** — doing so would subtract another 4–5 hours and shift times to ~05:00.

Always format timestamps with `timeZone: 'UTC'` or UTC date methods (`getUTCHours`, `getUTCDate`, etc.):

- `ChartPanel.tsx` — `TZ = 'UTC'` for all `Intl.DateTimeFormat` formatters
- `primitives.ts` — `OpeningRangePrimitive.setBars` uses `timeZone: 'UTC'` to detect 09:30–10:00 OR window

## Environment

Backend URL is hardcoded to `http://localhost:8080` in `src/types.ts` (`BACKEND_URL`).
