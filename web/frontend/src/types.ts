import type { CandlestickData, UTCTimestamp } from 'lightweight-charts'

export const BACKEND_URL = 'http://localhost:8080'

export type Bar = CandlestickData<UTCTimestamp> & { volume?: number }

export interface Backtest {
  id: number
  strategy: string
  run_at: string
  first_ts: string
  last_ts: string
  total_days: number
  initial_bal: number
  final_bal: number
  net_growth: number
  max_drawdown: number
  num_trades: number
  symbol: string
  avg_drawdown: number
  sharpe: number
  total_win: number
  total_loss: number
  win_rate: number
  win_count: number
  profit_factor: number
  expectancy: number
  max_lose_streak: number
  avg_size: number
  min_size: number
  max_size: number
  max_drawdown_dollars: number
  max_drawdown_peak_date: string
  max_drawdown_trough_date: string
  avg_drawdown_dollars: number
  max_intraday_drawdown: number
  max_intraday_drawdown_dollars: number
  max_intraday_drawdown_date: string
  avg_intraday_drawdown: number
  avg_intraday_drawdown_dollars: number
  max_daily_loss: number
  max_daily_loss_date: string
  avg_daily_loss: number
  avg_weekly: number
  avg_monthly: number
  avg_weekly_pct: number
  avg_monthly_pct: number
  instrument: string
}

export const SYMBOLS = [
  { id: 'nq',     label: 'NQ' },
  { id: 'gbpusd', label: 'GBPUSD' },
  { id: 'eurusd', label: 'EURUSD' },
] as const

export type SymbolId = typeof SYMBOLS[number]['id']

export interface Trade {
  side: 'long' | 'short'
  et: UTCTimestamp
  xt: UTCTimestamp
  ep: number
  xp: number
  pnl: number
  qty: number
}

export interface MonteCarloData {
  numPaths: number
  sims: number
  steps: number
  stepValues: Uint32Array  // actual trade count at each checkpoint (from montecarlo_paths.step)
  initialBalance: number
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  pProfit: number
  pRuin: number
  paths: Float32Array[]
}

export interface VwapPoint {
  time: UTCTimestamp
  value: number
}

export interface Indicators {
  vwap: boolean
  openingRange: boolean
}

export interface Settings {
  from_date: string
  to_date: string
  default_timeframe: string
}

export interface MarchSettings {
  symbol: 'nq' | 'es'
  tf: string
  from: string
  to: string
  mode: 'latest' | 'range'
  bottomOpen: string | boolean
  layout?: string
  bottomHeight?: string | number
}

// One chart panel's persisted config. Stored per layout (see MarchLayouts) so
// each layout remembers its own panels' symbol / timeframe / date / indicator.
export interface LayoutPanelConfig {
  symbol: 'nq' | 'es'
  tf: string            // matches a TIMEFRAMES[].table
  mode: 'latest' | 'range'
  from: string
  to: string
  indicators: Indicators
}

// Keyed by layout id (e.g. 'single', 'split-v'); the array is that layout's panels.
export type MarchLayouts = Record<string, LayoutPanelConfig[]>

export function makeDefaultPanelConfig(): LayoutPanelConfig {
  const today = new Date().toISOString().slice(0, 10)
  const d = new Date()
  d.setDate(d.getDate() - 7)
  const recentFrom = d.toISOString().slice(0, 10)
  return {
    symbol: 'nq',
    tf: '1m',
    mode: 'latest',
    from: recentFrom,
    to: today,
    indicators: { vwap: false, openingRange: false },
  }
}

export const TIMEFRAMES = [
  { label: '1m',  seconds: 60,    table: '1m'  },
  { label: '5m',  seconds: 300,   table: '5m'  },
  { label: '15m', seconds: 900,   table: '15m' },
  { label: '30m', seconds: 1800,  table: '30m' },
  { label: '1h',  seconds: 3600,  table: '1h'  },
  { label: '4h',  seconds: 14400, table: '4h'  },
  { label: '1D',  seconds: 86400, table: '1d'  },
] as const

export type TF = typeof TIMEFRAMES[number]
