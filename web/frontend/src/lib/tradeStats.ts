import type { Trade } from '../types'

// tradeStats — re-aggregation helpers for the Splicing view. Given a slice of the
// trade log it derives the same summary metrics the backend reports per backtest
// (net P&L, win rate, profit factor, max drawdown), so any subset (long/short, a
// single year, weekday, hour) can be scored on its own.
//
// Timezone note: Trade.et/xt are fake-UTC ET epoch seconds (see frontend CLAUDE.md
// "Timezone model"). The bucketing keys below therefore use getUTC* methods so a
// trade lands in the ET hour/weekday/year it actually happened in — never apply a
// named timezone here, that would double-shift it.

export interface TradeStats {
  numTrades: number
  netPnl: number
  netPct: number       // netPnl as a % of initial balance
  winRate: number      // 0–100
  profitFactor: number // gross profit / gross loss (Infinity if no losing trades)
  maxDrawdownPct: number // peak-to-trough equity decline, % of peak (>= 0)
}

export function computeStats(trades: Trade[], initialBalance: number): TradeStats {
  const numTrades = trades.length
  if (numTrades === 0) {
    return { numTrades: 0, netPnl: 0, netPct: 0, winRate: 0, profitFactor: 0, maxDrawdownPct: 0 }
  }

  let netPnl = 0
  let wins = 0
  let grossProfit = 0
  let grossLoss = 0 // accumulated as a positive magnitude

  // Equity curve starts at the initial balance; track the running peak so we can
  // measure the worst peak-to-trough decline as a percentage of that peak.
  let balance = initialBalance
  let peak = initialBalance
  let maxDrawdownPct = 0

  for (const t of trades) {
    netPnl += t.pnl
    if (t.pnl > 0) {
      wins++
      grossProfit += t.pnl
    } else if (t.pnl < 0) {
      grossLoss += -t.pnl
    }

    balance += t.pnl
    if (balance > peak) peak = balance
    if (peak > 0) {
      const dd = ((peak - balance) / peak) * 100
      if (dd > maxDrawdownPct) maxDrawdownPct = dd
    }
  }

  const winRate = (wins / numTrades) * 100
  const netPct = initialBalance > 0 ? (netPnl / initialBalance) * 100 : 0
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? Infinity : 0)

  return { numTrades, netPnl, netPct, winRate, profitFactor, maxDrawdownPct }
}

// Group trades into a Map keyed by the numeric value keyFn returns for each trade.
export function bucketBy(trades: Trade[], keyFn: (t: Trade) => number): Map<number, Trade[]> {
  const buckets = new Map<number, Trade[]>()
  for (const t of trades) {
    const k = keyFn(t)
    const arr = buckets.get(k)
    if (arr) arr.push(t)
    else buckets.set(k, [t])
  }
  return buckets
}

// Entry-time axis keys. et is fake-UTC ET seconds → read with UTC getters.
export function entryYear(t: Trade): number {
  return new Date(t.et * 1000).getUTCFullYear()
}

export function entryWeekday(t: Trade): number {
  return new Date(t.et * 1000).getUTCDay() // 0=Sun … 6=Sat
}

export function entryHour(t: Trade): number {
  return new Date(t.et * 1000).getUTCHours() // 0–23 (ET)
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export function weekdayLabel(day: number): string {
  return WEEKDAYS[day] ?? String(day)
}
