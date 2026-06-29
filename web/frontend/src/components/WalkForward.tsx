import { useMemo, useState } from 'react'
import EquityChart from './EquityChart'
import type { Trade } from '../types'
import { computeStats, type TradeStats } from '../lib/tradeStats'

// #1 — Out-of-sample / across-time consistency. Splits the realized trade log into
// N consecutive equal-time periods and reports each period's stats. The final
// period is treated as the out-of-sample (OOS) tail: a real edge holds up there,
// an overfit one decays. This is a re-slice of trades you already have — it does
// NOT re-optimize parameters (that would need a backend grid per window).

interface Props {
  trades: Trade[]
  initialBalance: number
  startDate?: string
}

const PERIOD_CHOICES = [3, 4, 5, 6]

function fmt$(n: number) {
  if (!isFinite(n)) return '—'
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(n: number, d = 1) {
  return n.toFixed(d) + '%'
}
function fmtPf(n: number) {
  return n === Infinity ? '∞' : n.toFixed(2)
}
function isoDay(sec: number) {
  return new Date(sec * 1000).toISOString().slice(0, 10)
}

interface Period {
  idx: number
  fromSec: number
  toSec: number
  stats: TradeStats
  isOos: boolean
}

function buildPeriods(trades: Trade[], n: number, initialBalance: number): Period[] {
  if (trades.length === 0) return []
  let minEt = Infinity
  let maxEt = -Infinity
  for (const t of trades) {
    if (t.et < minEt) minEt = t.et
    if (t.et > maxEt) maxEt = t.et
  }
  const span = Math.max(1, maxEt - minEt)
  const step = span / n
  const periods: Period[] = []
  for (let i = 0; i < n; i++) {
    const fromSec = minEt + step * i
    // Last period is inclusive of the final timestamp.
    const toSec = i === n - 1 ? maxEt + 1 : minEt + step * (i + 1)
    const slice = trades.filter((t) => t.et >= fromSec && t.et < toSec)
    periods.push({
      idx: i,
      fromSec,
      toSec,
      stats: computeStats(slice, initialBalance),
      isOos: i === n - 1,
    })
  }
  return periods
}

export default function WalkForward({ trades, initialBalance, startDate }: Props) {
  const [n, setN] = useState(4)
  const periods = useMemo(() => buildPeriods(trades, n, initialBalance), [trades, n, initialBalance])

  // In-sample = all but the last period; OOS = the last period.
  const { isStats, oosStats } = useMemo(() => {
    const isTrades = trades.filter((t) => {
      const last = periods[periods.length - 1]
      return last ? t.et < last.fromSec : false
    })
    const oosTrades = trades.filter((t) => {
      const last = periods[periods.length - 1]
      return last ? t.et >= last.fromSec : false
    })
    return { isStats: computeStats(isTrades, initialBalance), oosStats: computeStats(oosTrades, initialBalance) }
  }, [trades, periods, initialBalance])

  if (trades.length === 0) {
    return <div className="text-sm text-gray-500">No trades for a walk-forward split.</div>
  }

  const degraded = oosStats.numTrades > 0 && oosStats.profitFactor < 1 && isStats.profitFactor >= 1

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-5">
      {/* Period count selector */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Periods</span>
          <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80">
            {PERIOD_CHOICES.map((c) => (
              <button
                key={c}
                onClick={() => setN(c)}
                className={`px-3 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none cursor-pointer ${
                  n === c ? 'bg-gray-700 text-white shadow-sm' : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
        {degraded && (
          <span className="text-xs text-amber-400">⚠ Edge weakens out-of-sample — possible overfit / regime change.</span>
        )}
      </div>

      {/* Per-period table */}
      <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 p-4 overflow-x-auto">
        <table className="w-full border-collapse font-mono text-xs text-right">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800/40">
              <th className="text-left pb-2 font-semibold">Period</th>
              <th className="pb-2 font-semibold pr-4">Net P&amp;L</th>
              <th className="pb-2 font-semibold pr-4">Net %</th>
              <th className="pb-2 font-semibold pr-4">Trades</th>
              <th className="pb-2 font-semibold pr-4">Win Rate</th>
              <th className="pb-2 font-semibold pr-4">Profit Factor</th>
              <th className="pb-2 font-semibold">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {periods.map((p) => {
              const s = p.stats
              return (
                <tr
                  key={p.idx}
                  className={`border-b border-gray-800/20 last:border-b-0 ${p.isOos ? 'bg-blue-500/5' : 'hover:bg-gray-800/20'}`}
                >
                  <td className="text-left py-2 text-gray-300 font-medium">
                    {isoDay(p.fromSec)} → {isoDay(p.toSec - 1)}
                    {p.isOos && <span className="ml-2 text-[10px] text-blue-400 font-semibold">OOS</span>}
                  </td>
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

      {/* In-sample vs OOS headline */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 p-4">
          <div className="text-[10px] font-semibold tracking-widest uppercase text-gray-500 mb-2">In-Sample (first {n - 1})</div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Net %</span><span className={isStats.netPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{fmtPct(isStats.netPct)}</span></div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Profit Factor</span><span className={isStats.profitFactor >= 1 ? 'text-emerald-400' : 'text-red-400'}>{fmtPf(isStats.profitFactor)}</span></div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Win Rate</span><span className="text-gray-200">{fmtPct(isStats.winRate)}</span></div>
        </div>
        <div className="bg-blue-500/5 rounded-lg border border-blue-500/20 p-4">
          <div className="text-[10px] font-semibold tracking-widest uppercase text-blue-400/80 mb-2">Out-of-Sample (last)</div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Net %</span><span className={oosStats.netPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{fmtPct(oosStats.netPct)}</span></div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Profit Factor</span><span className={oosStats.profitFactor >= 1 ? 'text-emerald-400' : 'text-red-400'}>{fmtPf(oosStats.profitFactor)}</span></div>
          <div className="flex justify-between text-xs font-mono py-1"><span className="text-gray-500">Win Rate</span><span className="text-gray-200">{fmtPct(oosStats.winRate)}</span></div>
        </div>
      </div>

      {/* Stitched equity curve (full history; period boundaries are visible in the table) */}
      <div className="w-full h-[320px]">
        <EquityChart trades={trades} initialBalance={initialBalance} startDate={startDate} />
      </div>

      <p className="text-gray-500 text-xs">
        Periods are equal time spans by trade entry date. This is an out-of-sample consistency check on the existing run — it
        does not re-optimize parameters. Consistent stats across periods (especially the OOS tail) suggest a durable edge;
        a strong in-sample run that collapses in the OOS period is the classic overfit signature.
      </p>
    </div>
  )
}
