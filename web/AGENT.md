# Web frontend

React 19 and TypeScript single-page application built with Vite 8 and Bun.
Tailwind CSS v4 provides styling, TanStack Query handles server state, TanStack
Router handles navigation, and lightweight-charts renders market data.

## Commands

Run from `web/`:

```bash
bun install
bun dev
bun run build
bun run preview
```

Use `bun run build` for the required TypeScript and production-build check. The
generated `dist/` directory and `node_modules/` are ignored artifacts.

## Routes

Routes use TanStack Router's file-based routing under `src/routes/`. The Vite
plugin generates `src/routeTree.gen.ts`; do not edit that generated file.

- `/` — home
- `/march` — persistent live chart workspace
- `/database` — market database summary
- `/test` — backtest and tuning workspace
- `/environment` — execution/backtest environments and rules
- `/code` — code workspace placeholder

The March workspace is intentionally kept mounted after its first visit so navigation
does not recreate charts, reconnect streams, or redownload candle history.
Preserve that lifecycle behavior when changing routing or layout.

## Layout

```text
web/src/
├── main.tsx                    # React root and QueryClientProvider
├── App.tsx                     # global state and AppContext provider
├── router.ts                   # generated route tree registration
├── routes/                     # route definitions and their screen implementations
│   ├── __root.tsx              # app shell and persistent March mount
│   ├── index.tsx               # home
│   ├── march.tsx               # multi-panel live chart workspace
│   ├── test.tsx                # backtest, tuning, and results workflow
│   ├── database.tsx            # QuestDB data summary
│   ├── environment.tsx         # environment route layout
│   ├── environment.index.tsx   # environments and execution rules
│   ├── environment.$environmentId.tsx # dedicated environment detail route
│   └── code.tsx                # code workspace placeholder
├── api.ts                      # HTTP helpers and binary decoders
├── types.ts                    # API/domain types and BACKEND_URL
├── style.css                   # Tailwind import and application styles
├── context/AppContext.tsx      # shared application state contract
├── components/
│   ├── accounts/              # account, position, and strategy controls
│   ├── backtests/             # backtest analysis and result views
│   ├── buttons/               # reusable button-like controls
│   ├── charts/                # chart and heatmap components
│   ├── layout/                # shared route-level layout wrappers
│   ├── navigation/            # sidebar, headers, and tabs
│   └── ui/                    # shared modal chrome, primitives, and icons
└── lib/
    ├── primitives.ts           # lightweight-charts canvas primitives
    └── tradeStats.ts           # client-side trade statistics
```

## State and data flow

- TanStack Query owns fetched server state and caching.
- `AppContext` owns cross-page UI state, March layouts, selected environments,
  trade overlays, test results, and tuning progress.
- `ChartPanel` owns each chart instance, historical candle load, indicator
  rendering, and the direct Bookmap live stream.
- `api.ts` is the single boundary for server requests and binary decoding.
- `types.ts` defines shared frontend contracts. Update it together with `api.ts`
  and the Rust route whenever an API shape changes.

The Rust server is expected at `http://localhost:8080` via `BACKEND_URL` in
`src/types.ts`. The chart may also connect directly to Bookmap at
`ws://localhost:8765` for low-latency ticks.

## Chart behavior

- Historical candles seed each panel before live updates are applied.
- VWAP resets at midnight and 09:30 ET. Historical and live accumulator paths
  must use the same anchors so the line remains continuous at the handoff.
- Chart overlays include active MT5 positions, completed March trades, and
  selected backtest trades.
- Layout and panel settings are persisted through the server; do not make panel
  state global unless it genuinely applies across panels.

## API and time contracts

Binary responses are little-endian. At minimum, preserve these formats:

- Trades: 8-byte header (`u32` magic `0x54524445`, `u32` count), followed by
  25-byte rows (`u8` side, two `u32` timestamps, then `f32` entry price, exit
  price, P&L, and quantity).
- March candles: 8-byte header (`u32` magic `0x45444C43`, `u32` count), followed
  by 24-byte OHLCV rows (`u32` time and five `f32` values).

All market timestamps are New York wall-clock values encoded as fake UTC. Use
UTC date methods or `timeZone: 'UTC'` when displaying them. Never apply an
`America/New_York` conversion, which would shift them by another four or five
hours.

## Change guidelines

- Preserve query keys and cache invalidation when changing data flows.
- Clean up chart series, primitives, subscriptions, timers, and WebSockets in
  effects to avoid duplicate work under React Strict Mode.
- Keep large binary payloads out of logs and avoid copying them unnecessarily.
- Coordinate API changes with `../server`; frontend-only fallbacks must not hide
  broken server contracts.
