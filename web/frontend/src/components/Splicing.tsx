import { useMemo, useState } from 'react'
import type { Trade } from '../types'
import { computeStats, bucketBy, entryYear, entryWeekday, entryHour, weekdayLabel, type TradeStats } from '../lib/tradeStats'

// Splicing — re-aggregates the existing trade log along a chosen axis (long/short,
// year, weekday, hour) so you can see WHERE the edge comes from and whether it's
// broadly sourced or concentrated in one fragile bucket.
//
// Thin buckets are dimmed: a great win rate on 6 trades is noise, not signal.

interface Props {
  trades: Trade[]
  initialBalance: number
}

type Axis = 'side' | 'year' | 'weekday' | 'hour'

const LOW_CONFIDENCE = 20 // buckets below this trade count are dimmed

const AXES: { id: Axis; label: string }[] = [
  { id: 'side', label: 'Long / Short' },
  { id: 'year', label: 'By Year' },
  { id: 'weekday', label: 'By Weekday' },
  { id: 'hour', label: 'By Hour' },
]

function fmt$(n: number) {
  if (!isFinite(n)) return '—'
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(n: number, d = 1) {
  return n.toFixed(d) + '%'
}
function fmtPf(n: number) {
  if (n === Infinity) return '∞'
  return n.toFixed(2)
}

interface Row {
  key: string
  label: string
  stats: TradeStats
}

function buildRows(trades: Trade[], axis: Axis, initialBalance: number): Row[] {
  if (axis === 'side') {
    const longs = trades.filter((t) => t.side === 'long')
    const shorts = trades.filter((t) => t.side === 'short')
    return [
      { key: 'long', label: 'Long', stats: computeStats(longs, initialBalance) },
      { key: 'short', label: 'Short', stats: computeStats(shorts, initialBalance) },
    ].filter((r) => r.stats.numTrades > 0)
  }

  const keyFn =
    axis === 'year' ? entryYear : axis === 'weekday' ? entryWeekday : entryHour
  const buckets = bucketBy(trades, keyFn as (t: Trade) => number)
  const keys = [...buckets.keys()].sort((a, b) => a - b)
  return keys.map((k) => ({
    key: String(k),
    label:
      axis === 'year' ? String(k) : axis === 'weekday' ? weekdayLabel(k) : `${String(k).padStart(2, '0')}:00`,
    stats: computeStats(buckets.get(k)!, initialBalance),
  }))
}

export default function Splicing({ trades, initialBalance }: Props) {
  const [axis, setAxis] = useState<Axis>('side')
  const rows = useMemo(() => buildRows(trades, axis, initialBalance), [trades, axis, initialBalance])

  if (trades.length === 0) {
    return <div className="text-sm text-gray-500">No trades to break down.</div>
  }

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-5">
      {/* Axis selector */}
      <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 self-start">
        {AXES.map((a) => (
          <button
            key={a.id}
            onClick={() => setAxis(a.id)}
            className={`px-3 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none cursor-pointer ${
              axis === a.id ? 'bg-gray-700 text-white shadow-sm' : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>

      <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 p-4 overflow-x-auto">
        <table className="w-full border-collapse font-mono text-xs text-right">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800/40">
              <th className="text-left pb-2 font-semibold">{AXES.find((a) => a.id === axis)!.label}</th>
              <th className="pb-2 font-semibold pr-4">Net P&amp;L</th>
              <th className="pb-2 font-semibold pr-4">Net %</th>
              <th className="pb-2 font-semibold pr-4">Trades</th>
              <th className="pb-2 font-semibold pr-4">Win Rate</th>
              <th className="pb-2 font-semibold pr-4">Profit Factor</th>
              <th className="pb-2 font-semibold">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const s = r.stats
              const thin = s.numTrades < LOW_CONFIDENCE
              return (
                <tr
                  key={r.key}
                  className={`border-b border-gray-800/20 last:border-b-0 ${thin ? 'opacity-40' : 'hover:bg-gray-800/20'}`}
                  title={thin ? `Only ${s.numTrades} trades — too few to trust` : undefined}
                >
                  <td className="text-left py-2 text-gray-300 font-medium">{r.label}</td>
                  <td className={`py-2 pr-4 ${s.netPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmt$(s.netPnl)}</td>
                  <td className={`py-2 pr-4 ${s.netPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(s.netPct)}</td>
                  <td className="py-2 pr-4 text-gray-350">{s.numTrades}</td>
                  <td className={`py-2 pr-4 ${s.winRate >= 50 ? 'text-emerald-400' : 'text-gray-300'}`}>{fmtPct(s.winRate)}</td>
                  <td className={`py-2 pr-4 ${s.profitFactor >= 1 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPf(s.profitFactor)}</td>
                  <td className="py-2 text-red-400">{fmtPct(s.maxDrawdownPct)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
