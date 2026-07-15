import { useMemo } from 'react'
import type { Trade } from '../../types'
import { computeStats, bucketBy, entryYear, entryWeekday, entryHour, weekdayLabel, hourLabel, type TradeStats } from '../../lib/tradeStats'

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
      axis === 'year' ? String(k) : axis === 'weekday' ? weekdayLabel(k) : hourLabel(k),
    stats: computeStats(buckets.get(k)!, initialBalance),
  }))
}

export default function Splicing({ trades, initialBalance }: Props) {
  const breakdowns = useMemo(
    () => AXES.map((axis) => ({ axis, rows: buildRows(trades, axis.id, initialBalance) })),
    [trades, initialBalance],
  )

  if (trades.length === 0) {
    return <div className="text-sm text-gray-500">No trades to break down.</div>
  }

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-5">
      {breakdowns.map(({ axis, rows }) => (
        <section key={axis.id} className="flex flex-col gap-2">
          <h2 className="text-xs font-semibold tracking-wider text-gray-300 uppercase select-none">{axis.label}</h2>
          <div className="border border-[#212124] bg-[#1A1A1E] overflow-hidden shadow-2xl shadow-black/40 w-full select-text">
            <table className="min-w-full text-right border-collapse text-xs font-mono">
              <thead>
                <tr className="bg-[#28282D] border-b border-[#212124] text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                  <th className="text-left py-3 pl-6 font-semibold">{axis.label}</th>
                  <th className="py-3 px-3 font-semibold">Net P&amp;L</th>
                  <th className="py-3 px-3 font-semibold">Net %</th>
                  <th className="py-3 px-3 font-semibold">Trades</th>
                  <th className="py-3 px-3 font-semibold">Win Rate</th>
                  <th className="py-3 px-3 font-semibold">Profit Factor</th>
                  <th className="py-3 pr-6 font-semibold">Max DD</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const s = r.stats
                  const thin = s.numTrades < LOW_CONFIDENCE
                  return (
                    <tr
                      key={r.key}
                      className={`border-b border-[#212124] last:border-0 text-gray-300 ${thin ? 'opacity-40' : 'hover:bg-[#28282D]/20'}`}
                      title={thin ? `Only ${s.numTrades} trades — too few to trust` : undefined}
                    >
                      <td className="text-left py-3.5 pl-6 text-gray-200 font-semibold">{r.label}</td>
                      <td className={`py-3.5 px-3 ${s.netPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmt$(s.netPnl)}</td>
                      <td className={`py-3.5 px-3 ${s.netPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(s.netPct)}</td>
                      <td className="py-3.5 px-3 text-gray-350">{s.numTrades}</td>
                      <td className={`py-3.5 px-3 ${s.winRate >= 50 ? 'text-emerald-400' : 'text-gray-350'}`}>{fmtPct(s.winRate)}</td>
                      <td className={`py-3.5 px-3 ${s.profitFactor >= 1 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPf(s.profitFactor)}</td>
                      <td className="py-3.5 pr-6 text-red-400">{fmtPct(s.maxDrawdownPct)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  )
}
