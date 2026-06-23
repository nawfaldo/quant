import { BACKEND_URL, type Bar, type Backtest, type Trade, type VwapPoint, type TF, type Settings, type MonteCarloData } from './types'
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
