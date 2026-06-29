import { useMemo, useState } from 'react'
import type { TuneCombo } from '../api'

// #2 — Parameter-sensitivity surface. Tune already computes every grid cell; this
// pivots the full grid into a 2-D heatmap so you can see whether the "best" combo
// sits on a broad plateau (robust) or a lone spike (overfit noise). A spike that
// craters the moment a parameter shifts one notch will not survive live trading.

interface Props {
  grid: TuneCombo[]
  hasVol: boolean
}

type MetricId = 'score' | 'growth' | 'drawdown'

const METRICS: { id: MetricId; label: string }[] = [
  { id: 'score', label: 'Score' },
  { id: 'growth', label: 'Growth' },
  { id: 'drawdown', label: 'Max DD' },
]

const ALL_DIMS = [
  { key: 'baseLot', label: 'Base Lot', vol: false },
  { key: 'leverage', label: 'Leverage', vol: false },
  { key: 'volTarget', label: 'Vol Target', vol: true },
  { key: 'volHalflife', label: 'Halflife', vol: true },
  { key: 'volMaxMult', label: 'Max Mult', vol: true },
  { key: 'volMinDays', label: 'Min Days', vol: true },
] as const

type DimKey = (typeof ALL_DIMS)[number]['key']

const val = (c: TuneCombo, k: DimKey): number => (c[k] as number | undefined) ?? 0
const metricOf = (c: TuneCombo, m: MetricId) => (m === 'score' ? c.score : m === 'growth' ? c.growth : c.drawdown)
// Higher is better for score/growth; lower is better for drawdown.
const goodness = (v: number, m: MetricId) => (m === 'drawdown' ? -v : v)

function fmtMetric(v: number, m: MetricId) {
  if (!isFinite(v)) return '—'
  return m === 'score' ? v.toFixed(2) : v.toFixed(0) + '%'
}
function fmtDim(v: number) {
  return Number.isInteger(v) ? String(v) : v.toFixed(2)
}
// norm 0 (worst) → red, 1 (best) → green.
function colorFor(norm: number) {
  const hue = Math.max(0, Math.min(1, norm)) * 130
  return `hsl(${hue}, 60%, 38%)`
}

export default function SensitivityHeatmap({ grid, hasVol }: Props) {
  const [metric, setMetric] = useState<MetricId>('score')

  // Which dimensions were actually swept (>1 distinct value)?
  const sweptDims = useMemo(() => {
    return ALL_DIMS.filter((d) => (d.vol ? hasVol : true)).filter((d) => {
      const set = new Set(grid.map((c) => val(c, d.key)))
      return set.size > 1
    })
  }, [grid, hasVol])

  const [xKey, setXKey] = useState<DimKey | null>(null)
  const [yKey, setYKey] = useState<DimKey | null>(null)

  // Resolve effective axes: default to the first two swept dims.
  const x = (xKey && sweptDims.find((d) => d.key === xKey)) || sweptDims[0] || null
  const y = (yKey && sweptDims.find((d) => d.key === yKey && d.key !== x?.key)) || sweptDims.find((d) => d.key !== x?.key) || null

  const xVals = useMemo(() => (x ? [...new Set(grid.map((c) => val(c, x.key)))].sort((a, b) => a - b) : []), [grid, x])
  const yVals = useMemo(() => (y ? [...new Set(grid.map((c) => val(c, y.key)))].sort((a, b) => a - b) : []), [grid, y])

  // Build the cell matrix: average the metric across any non-axis dimensions.
  const { cells, gMin, gMax } = useMemo(() => {
    const rows = (y ? yVals : [0]).map((yv) =>
      xVals.map((xv) => {
        const matches = grid.filter((c) => val(c, x!.key) === xv && (!y || val(c, y.key) === yv))
        if (matches.length === 0) return null
        const avg = matches.reduce((s, c) => s + metricOf(c, metric), 0) / matches.length
        return avg
      }),
    )
    let mn = Infinity
    let mx = -Infinity
    for (const r of rows) for (const v of r) {
      if (v == null) continue
      const g = goodness(v, metric)
      if (g < mn) mn = g
      if (g > mx) mx = g
    }
    return { cells: rows, gMin: mn, gMax: mx }
  }, [grid, x, y, xVals, yVals, metric])

  if (sweptDims.length === 0) {
    return <div className="text-sm text-gray-500">Only one parameter combination was tested — nothing to compare. Sweep a range (comma-separated values) to see a sensitivity surface.</div>
  }

  const norm = (v: number) => (gMax > gMin ? (goodness(v, metric) - gMin) / (gMax - gMin) : 0.5)

  return (
    <div className="flex flex-col gap-4 w-full">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Metric</span>
          <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80">
            {METRICS.map((m) => (
              <button
                key={m.id}
                onClick={() => setMetric(m.id)}
                className={`px-3 py-1 text-xs font-medium rounded-md select-none cursor-pointer transition-all ${
                  metric === m.id ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
        {sweptDims.length > 1 && (
          <>
            <label className="flex items-center gap-2 text-xs text-gray-500">
              X
              <select
                value={x?.key}
                onChange={(e) => setXKey(e.target.value as DimKey)}
                className="bg-gray-900 border border-gray-800 rounded-md px-2 py-1 text-xs text-gray-200"
              >
                {sweptDims.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-500">
              Y
              <select
                value={y?.key}
                onChange={(e) => setYKey(e.target.value as DimKey)}
                className="bg-gray-900 border border-gray-800 rounded-md px-2 py-1 text-xs text-gray-200"
              >
                {sweptDims.filter((d) => d.key !== x?.key).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
              </select>
            </label>
          </>
        )}
      </div>

      {/* Heatmap grid */}
      <div className="overflow-x-auto">
        <table className="border-collapse font-mono text-xs">
          <thead>
            <tr>
              <th className="p-1 text-gray-600 text-right pr-2">{y ? `${y.label} ↓ / ${x!.label} →` : x!.label}</th>
              {xVals.map((xv) => (
                <th key={xv} className="p-1 text-gray-400 font-normal text-center min-w-[58px]">{fmtDim(xv)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(y ? yVals : [0]).map((yv, ri) => (
              <tr key={yv}>
                <td className="p-1 text-gray-400 text-right pr-2">{y ? fmtDim(yv) : ''}</td>
                {xVals.map((xv, ci) => {
                  const v = cells[ri]?.[ci]
                  if (v == null) return <td key={ci} className="p-1"><div className="h-9 rounded bg-gray-900/40" /></td>
                  return (
                    <td key={ci} className="p-0.5">
                      <div
                        className="h-9 rounded flex items-center justify-center text-white/90 font-medium"
                        style={{ backgroundColor: colorFor(norm(v)) }}
                        title={`${x!.label}=${fmtDim(xv)}${y ? `, ${y.label}=${fmtDim(yv)}` : ''} → ${METRICS.find((m) => m.id === metric)!.label} ${fmtMetric(v, metric)}`}
                      >
                        {fmtMetric(v, metric)}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-gray-500 text-xs max-w-3xl">
        A broad block of green = a robust plateau: small parameter changes barely move the result, so it should hold up
        live. A lone green cell among red = a fragile spike fitted to noise — avoid it even if its peak looks best. Prefer
        the center of the largest green region over the single highest cell.
      </p>
    </div>
  )
}
