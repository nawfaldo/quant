import { BACKEND_URL, type Bar, type Backtest, type Trade, type VwapPoint, type TF, type Settings, type MarchSettings, type MarchLayouts, type MonteCarloData } from './types'
import type { UTCTimestamp } from 'lightweight-charts'

const MAGIC       = 0x45444C43
const ROW_BYTES   = 20
const HEADER_BYTES = 8

const VWAP_MAGIC    = 0x50415756
const VWAP_ROW_BYTES = 8

const TRADE_MAGIC     = 0x54524445
const TRADE_ROW_BYTES = 25

export async function fetchCandles(tf: TF, symbol: string, from?: string, to?: string): Promise<Bar[]> {
  const range = from && to ? `&from=${from}&to=${to}` : ''
  const res = await fetch(`${BACKEND_URL}/api/candles/bin?tf=${tf.table}&symbol=${symbol}${range}`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  const buf = await res.arrayBuffer()
  const view = new DataView(buf)
  if (view.getUint32(0, true) !== MAGIC) throw new Error('Bad response magic')
  const count = view.getUint32(4, true)
  const data: Bar[] = new Array(count)
  let off = HEADER_BYTES
  for (let i = 0; i < count; i++) {
    data[i] = {
      time:  view.getUint32(off,      true) as UTCTimestamp,
      open:  view.getFloat32(off + 4,  true),
      high:  view.getFloat32(off + 8,  true),
      low:   view.getFloat32(off + 12, true),
      close: view.getFloat32(off + 16, true),
    }
    off += ROW_BYTES
  }
  return data
}

export async function fetchVwap(): Promise<VwapPoint[]> {
  const res = await fetch(`${BACKEND_URL}/api/vwap/bin`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  const buf = await res.arrayBuffer()
  const view = new DataView(buf)
  if (view.getUint32(0, true) !== VWAP_MAGIC) throw new Error('Bad VWAP magic')
  const count = view.getUint32(4, true)
  const data: VwapPoint[] = new Array(count)
  let off = HEADER_BYTES
  for (let i = 0; i < count; i++) {
    data[i] = {
      time:  view.getUint32(off, true) as UTCTimestamp,
      value: view.getFloat32(off + 4, true),
    }
    off += VWAP_ROW_BYTES
  }
  return data
}

export async function fetchBacktests(): Promise<Backtest[]> {
  const res = await fetch(`${BACKEND_URL}/api/backtests`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function fetchSettings(): Promise<Settings> {
  const res = await fetch(`${BACKEND_URL}/api/settings`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

export async function saveSettings(from_date: string, to_date: string): Promise<void> {
  await fetch(`${BACKEND_URL}/api/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_date, to_date }),
  })
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
const MC_HEADER_BYTES = 48

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
  return { numPaths, sims, steps, stepValues, initialBalance, p5, p25, p50, p75, p95, pProfit, pRuin, paths }
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

