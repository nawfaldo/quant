import { BACKEND_URL, type Backtest, type Trade, type MarchSettings, type MarchLayouts, type MonteCarloData } from './types'
import type { UTCTimestamp } from 'lightweight-charts'

const HEADER_BYTES = 8

const TRADE_MAGIC     = 0x54524445
const TRADE_ROW_BYTES = 25

export async function fetchBacktests(): Promise<Backtest[]> {
  const res = await fetch(`${BACKEND_URL}/api/backtests`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function deleteBacktest(id: number): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/backtests/${id}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
}


export async function fetchMarchSettings(): Promise<MarchSettings> {
  const res = await fetch(`${BACKEND_URL}/api/march/settings`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function saveMarchSettings(s: MarchSettings): Promise<void> {
  await fetch(`${BACKEND_URL}/api/march/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(s),
  })
}

export async function fetchMarchLayouts(): Promise<MarchLayouts> {
  const res = await fetch(`${BACKEND_URL}/api/march/layouts`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function saveMarchLayouts(m: MarchLayouts): Promise<void> {
  await fetch(`${BACKEND_URL}/api/march/layouts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(m),
  })
}

const MC_MAGIC = 0x4D435054
const MC_HEADER_BYTES = 68

export async function fetchMonteCarloData(id: number): Promise<MonteCarloData> {
  const res = await fetch(`${BACKEND_URL}/api/montecarlo/${id}`)
  if (res.status === 404) throw Object.assign(new Error('no_mc_data'), { code: 'not_found' })
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  const buf = await res.arrayBuffer()
  const view = new DataView(buf)
  if (view.getUint32(0, true) !== MC_MAGIC) throw new Error('Bad MC magic')
  const numPaths = view.getUint32(4, true)
  const steps = view.getUint32(8, true)
  const initialBalance = view.getFloat32(12, true)
  const p5   = view.getFloat32(16, true)
  const p25  = view.getFloat32(20, true)
  const p50  = view.getFloat32(24, true)
  const p75  = view.getFloat32(28, true)
  const p95  = view.getFloat32(32, true)
  const pProfit = view.getFloat32(36, true)
  const pRuin   = view.getFloat32(40, true)
  const sims = view.getUint32(44, true)
  const ddP5  = view.getFloat32(48, true)
  const ddP25 = view.getFloat32(52, true)
  const ddP50 = view.getFloat32(56, true)
  const ddP75 = view.getFloat32(60, true)
  const ddP95 = view.getFloat32(64, true)
  // Step values for path 0 (actual trade counts at each checkpoint)
  let off = MC_HEADER_BYTES
  const stepValues = new Uint32Array(steps)
  for (let s = 0; s < steps; s++) {
    stepValues[s] = view.getUint32(off, true)
    off += 4
  }
  const paths: Float32Array[] = []
  for (let i = 0; i < numPaths; i++) {
    const path = new Float32Array(steps)
    for (let s = 0; s < steps; s++) {
      path[s] = view.getFloat32(off, true)
      off += 4
    }
    paths.push(path)
  }
  return { numPaths, sims, steps, stepValues, initialBalance, p5, p25, p50, p75, p95, pProfit, pRuin, ddP5, ddP25, ddP50, ddP75, ddP95, paths }
}

// ── On-demand backtest run (Test page) ───────────────────────────────────────
// POSTs the wizard params to the Zig engine and returns a fully-computed run:
// the report (same fields as a saved Backtest), the trade log, and a Monte Carlo
// resampling — all live, no DB round-trip.

export interface RunParams {
  strategy: string
  symbol: string
  initialBalance: string
  baseLot: string
  sizing: string
  volTarget?: string
  volHalflife?: string
  volMaxMult?: string
  volMinDays?: string
  fromDate: string
  toDate: string
  spread: string
  slippage: string
}

// The report half of /api/run mirrors the saved-backtest shape, minus the DB-only
// id/run_at fields, plus initial_bal/final_bal supplied by the engine.
export type RunReport = Omit<Backtest, 'id' | 'run_at'>

export interface RunResult {
  report: RunReport
  trades: Trade[]
  monteCarlo: MonteCarloData | null
}

interface RunMonteCarloJson {
  initialBalance: number
  sims: number
  steps: number
  numPaths: number
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  pProfit: number
  pRuin: number
  ddP5: number
  ddP25: number
  ddP50: number
  ddP75: number
  ddP95: number
  stepValues: number[]
  paths: number[][]
}

export async function runBacktest(params: RunParams): Promise<RunResult> {
  const res = await fetch(`${BACKEND_URL}/api/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    let detail = `Backend error: ${res.status}`
    try {
      const j = await res.json()
      if (j?.error) detail = j.error
    } catch { /* keep default */ }
    throw new Error(detail)
  }
  const data = await res.json()

  const trades: Trade[] = (data.trades ?? []).map((t: Trade) => ({
    side: t.side,
    et: t.et as UTCTimestamp,
    xt: t.xt as UTCTimestamp,
    ep: t.ep,
    xp: t.xp,
    pnl: t.pnl,
    qty: t.qty,
  }))

  let monteCarlo: MonteCarloData | null = null
  const mc: RunMonteCarloJson | null = data.montecarlo
  if (mc) {
    monteCarlo = {
      numPaths: mc.numPaths,
      sims: mc.sims,
      steps: mc.steps,
      stepValues: Uint32Array.from(mc.stepValues),
      initialBalance: mc.initialBalance,
      p5: mc.p5,
      p25: mc.p25,
      p50: mc.p50,
      p75: mc.p75,
      p95: mc.p95,
      pProfit: mc.pProfit,
      pRuin: mc.pRuin,
      ddP5: mc.ddP5,
      ddP25: mc.ddP25,
      ddP50: mc.ddP50,
      ddP75: mc.ddP75,
      ddP95: mc.ddP95,
      paths: mc.paths.map((p) => Float32Array.from(p)),
    }
  }

  // Strip trades/montecarlo from the report object; the rest is the Backtest shape.
  const { trades: _t, montecarlo: _m, ...report } = data
  return { report: report as RunReport, trades, monteCarlo }
}

// Persist a run into app.db (re-runs server-side with a fixed Monte Carlo seed,
// so the saved result matches the preview). Returns the new backtest id.
export async function saveRun(params: RunParams): Promise<number> {
  const res = await fetch(`${BACKEND_URL}/api/run/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    let detail = `Backend error: ${res.status}`
    try {
      const j = await res.json()
      if (j?.error) detail = j.error
    } catch { /* keep default */ }
    throw new Error(detail)
  }
  const data = await res.json()
  return data.id as number
}

export interface CombineParams {
  ids: number[]
  initialBalance: string
  fromDate: string
  toDate: string
}

export async function combineBacktests(params: CombineParams): Promise<RunResult> {
  const res = await fetch(`${BACKEND_URL}/api/combine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    let detail = `Backend error: ${res.status}`
    try { const j = await res.json(); if (j?.error) detail = j.error } catch { /* keep */ }
    throw new Error(detail)
  }
  const data = await res.json()
  if (typeof data.symbol !== 'string') throw new Error('Unexpected response — restart the backend')
  const trades: Trade[] = (data.trades ?? []).map((t: Trade) => ({
    side: t.side, et: t.et as any, xt: t.xt as any,
    ep: t.ep, xp: t.xp, pnl: t.pnl, qty: t.qty,
  }))
  let monteCarlo: MonteCarloData | null = null
  const mc: RunMonteCarloJson | null = data.montecarlo
  if (mc) {
    monteCarlo = {
      numPaths: mc.numPaths, sims: mc.sims, steps: mc.steps,
      stepValues: Uint32Array.from(mc.stepValues),
      initialBalance: mc.initialBalance,
      p5: mc.p5, p25: mc.p25, p50: mc.p50, p75: mc.p75, p95: mc.p95,
      pProfit: mc.pProfit, pRuin: mc.pRuin,
      ddP5: mc.ddP5, ddP25: mc.ddP25, ddP50: mc.ddP50, ddP75: mc.ddP75, ddP95: mc.ddP95,
      paths: mc.paths.map((p) => Float32Array.from(p)),
    }
  }
  const { trades: _t, montecarlo: _m, ...report } = data
  return { report: report as RunReport, trades, monteCarlo }
}

export async function saveCombine(params: CombineParams): Promise<number> {
  const res = await fetch(`${BACKEND_URL}/api/combine/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    let detail = `Backend error: ${res.status}`
    try { const j = await res.json(); if (j?.error) detail = j.error } catch { /* keep */ }
    throw new Error(detail)
  }
  const data = await res.json()
  return data.id as number
}

// ── Tune (grid-search) ──────────────────────────────────────────────────────

export interface TuneCombo {
  growth: number
  drawdown: number
  score: number
  baseLot: number
  volTarget?: number
  volHalflife?: number
  volMaxMult?: number
  volMinDays?: number
}

export interface TuneResult {
  totalCombos: number
  bestGrowth: TuneCombo[]
  minDrawdown: TuneCombo[]
  bestOfTwo: TuneCombo[]
}

export interface TuneParams {
  strategy: string
  symbol: string
  initialBalance: string
  baseLot: string          // comma-separated for grid sweep
  sizing: string
  volTarget?: string       // comma-separated for grid sweep
  volHalflife?: string
  volMaxMult?: string
  volMinDays?: string
  fromDate: string
  toDate: string
  spread: string
  slippage: string
}

export async function runTune(params: TuneParams): Promise<TuneResult> {
  const res = await fetch(`${BACKEND_URL}/api/tune`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    let detail = `Backend error: ${res.status}`
    try {
      const j = await res.json()
      if (j?.error) detail = j.error
    } catch { /* keep default */ }
    throw new Error(detail)
  }
  return res.json()
}

export interface TuneStatus {
  status: 'running' | 'completed' | 'failed'
  progress?: number
  total?: number
  result?: TuneResult
  error?: string
}

export async function fetchTuneStatus(): Promise<TuneStatus> {
  const res = await fetch(`${BACKEND_URL}/api/tune/status`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function fetchTrades(id: number): Promise<Trade[]> {
  const res = await fetch(`${BACKEND_URL}/api/trades/${id}`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  const buf = await res.arrayBuffer()
  const view = new DataView(buf)
  if (view.getUint32(0, true) !== TRADE_MAGIC) throw new Error('Bad trades magic')
  const count = view.getUint32(4, true)
  const data: Trade[] = new Array(count)
  let off = HEADER_BYTES
  for (let i = 0; i < count; i++) {
    data[i] = {
      side: view.getUint8(off) === 0 ? 'long' : 'short',
      et:   view.getUint32(off + 1,  true) as UTCTimestamp,
      xt:   view.getUint32(off + 5,  true) as UTCTimestamp,
      ep:   view.getFloat32(off + 9,  true),
      xp:   view.getFloat32(off + 13, true),
      pnl:  view.getFloat32(off + 17, true),
      qty:  view.getFloat32(off + 21, true),
    }
    off += TRADE_ROW_BYTES
  }
  return data
}

// ── March strategy API (served on main web port 8080 via /api/march/) ────────

export interface MarchStrategy {
  name: string
  active: boolean
}

export async function fetchMarchStrategies(): Promise<MarchStrategy[]> {
  const res = await fetch(`${BACKEND_URL}/api/march/strategies`)
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
  return res.json()
}

export async function setMarchStrategyOn(name: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/march/strategies/${name}/on`, { method: 'PUT' })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

export async function setMarchStrategyOff(name: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/march/strategies/${name}/off`, { method: 'PUT' })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

// The Python MT5 execution server (port 5001). Only the live account-status
// check is read directly from the browser; everything else goes through Zig.
const MARCH_PY_URL = 'http://localhost:5001'

export type AccountStatusKind = 'ready' | 'incomplete' | 'error' | 'offline'

export interface AccountStatus {
  account_id: number
  login: string | number
  status: AccountStatusKind
  detail: string
  balance?: number
  equity?: number
  currency?: string
}

// Live MT5 connection health per account, polled by the accounts tree. Throws
// if the Python server is unreachable (callers render that as "unavailable").
export async function fetchAccountStatuses(): Promise<AccountStatus[]> {
  const res = await fetch(`${MARCH_PY_URL}/accounts/status`)
  if (!res.ok) throw new Error(`March Python API error: ${res.status}`)
  return res.json()
}

export interface ActivePosition {
  account: string | number
  account_name: string
  ticket: number
  type: 'long' | 'short'
  symbol: string
  volume: number
  profit: number
  open_price: number
  strategy?: string
  zig_entry_price?: number
  zig_entry_time?: number
}

export async function fetchActivePositions(): Promise<ActivePosition[]> {
  const res = await fetch(`${MARCH_PY_URL}/positions`)
  if (!res.ok) throw new Error(`March Python API error: ${res.status}`)
  return res.json()
}

// ── MT5 accounts (stored in march.db) ────────────────────────────────────────

export interface Mt5Account {
  id: number
  name: string
  login: string
  server: string
}

export interface AccountStrategy {
  id: number
  strategy: string
  symbol: string
  active: boolean
}

export async function fetchMt5Accounts(): Promise<Mt5Account[]> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts`)
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
  return res.json()
}

export async function addMt5Account(account: {
  name: string
  login: string
  password: string
  server: string
}): Promise<number> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(account),
  })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
  const data = await res.json()
  return data.id as number
}

export async function deleteMt5Account(id: number): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

export async function fetchAccountStrategies(accountId: number): Promise<AccountStrategy[]> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts/${accountId}/strategies`)
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
  return res.json()
}

export async function addAccountStrategy(
  accountId: number,
  data: { strategy: string; symbol: string },
): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts/${accountId}/strategies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

export async function deleteAccountStrategy(accountId: number, strategyId: number): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/march/mt5/accounts/${accountId}/strategies/${strategyId}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

export async function setAccountStrategyActive(
  accountId: number,
  strategyId: number,
  active: boolean,
): Promise<void> {
  const res = await fetch(
    `${BACKEND_URL}/api/march/mt5/accounts/${accountId}/strategies/${strategyId}/${active ? 'on' : 'off'}`,
    { method: 'PUT' },
  )
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
}

// Known strategy names that can be attached to an account. Mirrors the Zig
// registry (StrategyTag in web/backend/src/march/api.zig).
export const KNOWN_MARCH_STRATEGIES = ['rth_vwap', 'orb_buy', 'min_loop'] as const

export interface LiveTrade {
  id: number
  strategy_name: string
  side: 'long' | 'short'
  contract: number
  zig_entry_price: number
  zig_close_price: number
  mt5_entry_price: number
  mt5_close_price: number
  zig_open_time: string
  zig_close_time: string
  mt5_open_time: string
  mt5_close_time: string
}

export async function fetchLiveTradeHistory(): Promise<LiveTrade[]> {
  const res = await fetch(`${BACKEND_URL}/api/march/trades`)
  if (!res.ok) throw new Error(`March API error: ${res.status}`)
  return res.json()
}

