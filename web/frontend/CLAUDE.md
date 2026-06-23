# Frontend

React + TypeScript + Vite + Bun + Tailwind CSS single-page app. Full-screen dark-themed candlestick chart of NQ futures (1-minute bars) with backtest trade overlays, VWAP indicator, and equity curve analysis.

## Stack

| Layer | Technology |
|-------|-----------|
| Language | TypeScript 6 + React 19 |
| Bundler / dev server | Vite 8 |
| Package manager | Bun |
| Styling | Tailwind CSS v4 (via `@tailwindcss/vite` plugin) |
| Data fetching / caching | TanStack React Query v5 (`@tanstack/react-query`) |
| Chart | lightweight-charts v5 |
| Backend | Zig zap server on `http://localhost:8080` |

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
├── App.tsx                     # top-level state, layout, query orchestration
├── api.ts                      # fetch helpers (binary decoders: candles, vwap, trades)
├── types.ts                    # shared types: Bar, Backtest, Trade, VwapPoint, Indicators, TF
├── style.css
├── components/
│   ├── Chart.tsx               # lightweight-charts candlestick + VWAP line + trade overlay
│   ├── TimeframeBar.tsx        # TF toggle buttons (1m / 15m / 1h / 1D)
│   ├── BacktestsModal.tsx      # backtest list: eye-toggle (chart overlay) + equity curve button
│   ├── EquityCurveModal.tsx    # full-screen SVG equity curve with crosshair + DD shading
│   ├── IndicatorsModal.tsx     # indicators toggles (VWAP)
│   └── icons.tsx               # EyeIcon, EyeSlashIcon, LineChartIcon, SpinnerIcon
├── lib/
│   └── primitives.ts           # TradeLinesPrimitive — custom canvas renderer for trade lines
└── utils/
    └── candles.ts              # resample() and barsForChart() — client-side OHLC resampling
```

## State & data flow

```
Zig backend (port 8080)
  GET /api/candles/bin  →  fetchCandles()   →  useQuery(['candles'])   →  bars[]
  GET /api/vwap/bin     →  fetchVwap()      →  useQuery(['vwap'])      →  vwapData[]
  GET /api/backtests    →  fetchBacktests() →  useQuery(['backtests']) (enabled when modal open)
  GET /api/trades/:id   →  fetchTrades(id)  →  useQueries(['trades',id]) per visible backtest
```

All query state lives in `App.tsx`. Components receive data as props.

- `activeTf` — currently selected timeframe
- `visibleIds` — Set of backtest IDs whose trades are overlaid on the chart
- `allTrades` — flat list of Trade objects from all visible backtests
- `loadingIds` — Set of backtest IDs currently fetching
- `indicators` — `{ vwap: boolean, openingRange: boolean }` toggle state

## Chart (`Chart.tsx`)

Uses lightweight-charts with `CrosshairMode.Normal` (no snap to series values).

- **Init effect** (`[]`): creates chart + candlestick series + VWAP line series
- **Candle effect** (`[bars, activeTf]`): resamples and sets candle data
- **VWAP effect** (`[vwapData, activeTf]`): filters zero values (non-RTH bars), resamples to active TF bucket, sets line data
- **Indicators effect** (`[indicators]`): toggles VWAP series visibility
- **Trades effect** (`[allTrades]`): attaches/detaches `TradeLinesPrimitive`

The chart container uses `flex-1 min-w-0` — do NOT add `h-full`, the flex parent controls height.

## Binary wire formats (parsed in `api.ts`)

### Candles (`/api/candles/bin`)
- 8-byte header: `u32 magic (0x45444C43)` | `u32 count`
- 20 bytes/row: `u32 time, f32 open, f32 high, f32 low, f32 close`

### VWAP (`/api/vwap/bin`)
- 8-byte header: `u32 magic (0x50415756)` | `u32 count`
- 8 bytes/row: `u32 time, f32 value` — `value=0` means non-RTH bar (skip when rendering)
- Only RTH VWAP; ETH removed. Computed in backend from OHLCV, not stored in DB.

### Trades (`/api/trades/:id`)
- 8-byte header: `u32 magic (0x54524445)` | `u32 count`
- 25 bytes/row: `u8 side (0=long,1=short), u32 et, u32 xt, f32 ep, f32 xp, f32 pnl, u32 qty`

## Trade overlay & indicators (`lib/primitives.ts`)

`TradeLinesPrimitive` is a custom lightweight-charts plugin that renders on a canvas overlay:
- Draws entry→exit dashed lines + arrow markers + PnL text for every trade
- Packs trades into typed arrays sorted by entry time
- On each redraw, binary-searches the visible time range and culls to `MAX_VISIBLE_LINES`
- `MAX_TEXT_LABELS` gates PnL text rendering for performance

`OpeningRangePrimitive` draws a red box for days where the 09:55 breakout triggered. Its logic mirrors the Zig strategy exactly — **do not change this independently of `strategies/30m_buy.zig`**:
- **Range bars**: 09:30–09:50 (first five 5m candles). `OR_high` = max close (breakout reference).
- **Box extents**: body high/low (`max/min(open, close)`, no wicks) across all **six** bars including 09:55, so the breakout candle's body closes flush with the box top/bottom.
- **Trigger**: 09:55 bar. Box is shown only when `close(09:55) > OR_high`.
- **Breakout marker**: placed on the 09:55 bar. The entry candle (10:00 open) sits one bar to the right, **outside** the box.
- Timestamps are fake-UTC ET; always use `timeZone: 'UTC'` when formatting.

## Equity curve modal (`EquityCurveModal.tsx`)

Full-screen modal with a pure SVG chart (no lightweight-charts). Opened from the line-chart icon in `BacktestsModal`.

- **Data**: reuses `fetchTrades(id)` (already cached by React Query); computes running balance from `initial_bal + Σpnl` sorted by exit time
- **SVG**: renders at actual container pixel size via `ResizeObserver` — no viewBox scaling, so full-screen = more data resolution
- **X axis**: date labels (`Jan '23`, etc.) evenly spaced across the time range
- **Y axis**: balance with auto-scaled nice round tick steps
- **Crosshair**: snaps to nearest point on mouse move; shows date + balance labels pinned to axes
- **Drawdown shading**: all periods where balance dropped ≥ 10% from peak are shaded red with `-%` label at top

## Modals

- **BacktestsModal** (`z-50`) — lists backtests; each row has an equity-curve button (line chart icon) and eye-toggle for trade overlay
- **EquityCurveModal** (`z-[60]`) — full-screen, renders on top of BacktestsModal
- **IndicatorsModal** — VWAP toggle

## Timezone model

All timestamps from the backend are **New York (ET) wall-clock times stored as fake-UTC** by the importer. The epoch seconds values already represent ET; **never apply `America/New_York` (or any timezone) when formatting them** — doing so would subtract another 4–5 hours and shift times to ~05:00.

Always format timestamps with `timeZone: 'UTC'` or UTC date methods (`getUTCHours`, `getUTCDate`, etc.):

- `Chart.tsx` — `TZ = 'UTC'` for all `Intl.DateTimeFormat` formatters
- `primitives.ts` — `OpeningRangePrimitive.setBars` uses `timeZone: 'UTC'` to detect 09:30–10:00 OR window
- `EquityCurveModal.tsx` — already uses `getUTCMonth` / `getUTCDate` / `getUTCFullYear` ✓

## Environment

Backend URL is hardcoded to `http://localhost:8080` in `src/types.ts` (`BACKEND_URL`).
