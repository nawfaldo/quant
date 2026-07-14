import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { Trade } from '../types'
import { computeStats } from '../lib/tradeStats'
import { fetchVix, type VixPoint } from '../api'

// Volatility — joins each trade to the VIX close of its ET entry day, then asks
// two questions: does the edge (gain) depend on the volatility regime, and do
// the drawdowns cluster in high-VIX periods?
//
// The charts aggregate trades into VIX buckets (raw per-trade scatters were an
// unreadable blob — thousands of points pile up at low VIX). Each bar is the
// bucket average; hover for count / win rate / worst case.
//
// Join rule: day = floor(et / 86400) (both trade times and vix_1d timestamps are
// fake-UTC ET, see frontend AGENT.md "Timezone model"). Weekends/holidays fall
// back to the most recent prior VIX close (up to 7 days back).

interface Props {
  trades: Trade[]
  initialBalance: number
}

const LOW_CONFIDENCE = 20 // buckets below this trade count are dimmed

// VIX buckets: [min, max) — the last one is open-ended. Used by both the table
// and the charts so the two views line up.
const REGIMES = [
  { label: '< 15', min: 0, max: 15 },
  { label: '15 – 20', min: 15, max: 20 },
  { label: '20 – 25', min: 20, max: 25 },
  { label: '25 – 30', min: 25, max: 30 },
  { label: '30 – 40', min: 30, max: 40 },
  { label: '≥ 40', min: 40, max: Infinity },
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

// A trade annotated with its entry-day VIX and its position on the equity curve.
interface JoinedTrade {
  trade: Trade
  vix: number
  pnlPct: number // trade P&L as % of initial balance
  ddPct: number  // equity drawdown from peak (%) right after this trade closed
}

function joinTrades(trades: Trade[], vix: VixPoint[], initialBalance: number): { joined: JoinedTrade[]; unmatched: number } {
  const byDay = new Map<number, number>()
  for (const p of vix) byDay.set(Math.floor(p.t / 86400), p.c)

  const vixForDay = (day: number): number | undefined => {
    for (let d = day; d >= day - 7; d--) {
      const v = byDay.get(d)
      if (v !== undefined) return v
    }
    return undefined
  }

  const joined: JoinedTrade[] = []
  let unmatched = 0
  let balance = initialBalance
  let peak = initialBalance

  for (const t of trades) {
    balance += t.pnl
    if (balance > peak) peak = balance
    const ddPct = peak > 0 ? ((peak - balance) / peak) * 100 : 0

    const v = vixForDay(Math.floor(t.et / 86400))
    if (v === undefined) {
      unmatched++
      continue
    }
    joined.push({
      trade: t,
      vix: v,
      pnlPct: initialBalance > 0 ? (t.pnl / initialBalance) * 100 : 0,
      ddPct,
    })
  }
  return { joined, unmatched }
}

function pearson(xs: number[], ys: number[]): number {
  const n = xs.length
  if (n < 2) return NaN
  let sx = 0, sy = 0
  for (let i = 0; i < n; i++) { sx += xs[i]; sy += ys[i] }
  const mx = sx / n, my = sy / n
  let cov = 0, vx = 0, vy = 0
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx, dy = ys[i] - my
    cov += dx * dy
    vx += dx * dx
    vy += dy * dy
  }
  if (vx === 0 || vy === 0) return NaN
  return cov / Math.sqrt(vx * vy)
}

// Round-number axis ticks spanning [min, max].
function niceTicks(min: number, max: number, count = 5): number[] {
  const range = max - min
  if (!(range > 0)) return [min]
  const rawStep = range / count
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)))
  const residual = rawStep / magnitude
  const step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude
  const ticks: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 0.01; v += step) ticks.push(v)
  return ticks
}

// ── Bucket bar chart (inline SVG, matches the app's dark house style) ─────────

const W = 900
const H = 320
const PAD = { top: 22, right: 14, bottom: 44, left: 48 }

interface BarDatum {
  label: string
  n: number
  value: number   // bar height (avg for the bucket)
  color: string
  title: string   // native tooltip
  marker?: number // optional "worst case" tick (drawdown chart)
}

// Bar with only its data end rounded, anchored flat on the baseline.
function barPath(x: number, w: number, yBase: number, yEnd: number, r: number): string {
  const up = yEnd < yBase // bar grows upward (positive value)
  const h = Math.abs(yBase - yEnd)
  const rr = Math.min(r, w / 2, h)
  if (h === 0) return ''
  if (up) {
    return `M ${x} ${yBase} L ${x} ${yEnd + rr} Q ${x} ${yEnd} ${x + rr} ${yEnd} L ${x + w - rr} ${yEnd} Q ${x + w} ${yEnd} ${x + w} ${yEnd + rr} L ${x + w} ${yBase} Z`
  }
  return `M ${x} ${yBase} L ${x} ${yEnd - rr} Q ${x} ${yEnd} ${x + rr} ${yEnd} L ${x + w - rr} ${yEnd} Q ${x + w} ${yEnd} ${x + w} ${yEnd - rr} L ${x + w} ${yBase} Z`
}

function BucketBars({ data, yUnit, valueDecimals = 2 }: { data: BarDatum[]; yUnit: string; valueDecimals?: number }) {
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom

  let yMin = 0, yMax = 0
  for (const d of data) {
    if (d.value < yMin) yMin = d.value
    if (d.value > yMax) yMax = d.value
    if (d.marker !== undefined) {
      if (d.marker < yMin) yMin = d.marker
      if (d.marker > yMax) yMax = d.marker
    }
  }
  if (yMin === 0 && yMax === 0) yMax = 1
  const yPad = (yMax - yMin) * 0.12
  if (yMin < 0) yMin -= yPad
  yMax += yPad

  const yS = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * plotH
  const yTicks = niceTicks(yMin, yMax)

  const slotW = plotW / data.length
  const barW = Math.min(slotW * 0.55, 72)
  const y0 = yS(0)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto select-none">
      {/* Grid */}
      {yTicks.map((v) => (
        <line key={`gy${v}`} x1={PAD.left} x2={W - PAD.right} y1={yS(v)} y2={yS(v)} stroke="#1f2937" strokeWidth="1" />
      ))}

      {/* Bars */}
      {data.map((d, i) => {
        const cx = PAD.left + slotW * i + slotW / 2
        const x = cx - barW / 2
        const yEnd = yS(d.value)
        const thin = d.n < LOW_CONFIDENCE
        const valLabel = `${d.value >= 0 && yUnit === '%' && yMin < 0 ? '+' : ''}${d.value.toFixed(valueDecimals)}${yUnit}`
        const labelY = d.value >= 0 ? yEnd - 6 : yEnd + 13
        return (
          <g key={d.label} opacity={thin ? 0.4 : 1} className="hover:opacity-100">
            <path d={barPath(x, barW, y0, yEnd, 4)} fill={d.color} fillOpacity="0.85" />
            {/* Worst-case tick */}
            {d.marker !== undefined && (
              <line x1={cx - barW * 0.42} x2={cx + barW * 0.42} y1={yS(d.marker)} y2={yS(d.marker)} stroke="#fca5a5" strokeWidth="2" />
            )}
            {/* Value label at the data end */}
            <text x={cx} y={labelY} textAnchor="middle" fill="#d1d5db" fontSize="10" fontFamily="monospace">
              {valLabel}
            </text>
            {/* Bucket label + trade count */}
            <text x={cx} y={H - PAD.bottom + 15} textAnchor="middle" fill="#9ca3af" fontSize="10" fontFamily="monospace">
              {d.label}
            </text>
            <text x={cx} y={H - PAD.bottom + 28} textAnchor="middle" fill="#4b5563" fontSize="9" fontFamily="monospace">
              {d.n.toLocaleString()} trades
            </text>
            {/* Invisible hover target covering the whole slot */}
            <rect x={PAD.left + slotW * i} y={PAD.top} width={slotW} height={plotH} fill="transparent">
              <title>{d.title}</title>
            </rect>
          </g>
        )
      })}

      {/* Zero baseline */}
      <line x1={PAD.left} x2={W - PAD.right} y1={y0} y2={y0} stroke="#4b5563" strokeWidth="1" />

      {/* Y tick labels */}
      {yTicks.map((v) => (
        <text key={`ty${v}`} x={PAD.left - 6} y={yS(v) + 3} textAnchor="end" fill="#6b7280" fontSize="10" fontFamily="monospace">
          {v.toFixed(Math.abs(v) < 10 && v !== 0 ? 1 : 0)}{yUnit}
        </text>
      ))}
      <text x={PAD.left + plotW / 2} y={H - 4} textAnchor="middle" fill="#4b5563" fontSize="10" fontFamily="monospace">
        VIX at entry (daily close)
      </text>
    </svg>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Volatility({ trades, initialBalance }: Props) {
  const vixQuery = useQuery({
    queryKey: ['vix'],
    queryFn: () => fetchVix(),
    staleTime: 60 * 60 * 1000, // daily data — an hour of staleness is fine
  })

  const { joined, unmatched } = useMemo(
    () => joinTrades(trades, vixQuery.data ?? [], initialBalance),
    [trades, vixQuery.data, initialBalance],
  )

  // One pass over the shared REGIMES buckets feeds the table AND both charts.
  const buckets = useMemo(() => {
    return REGIMES.map((r) => {
      const inBucket = joined.filter((j) => j.vix >= r.min && j.vix < r.max)
      const n = inBucket.length
      const stats = computeStats(inBucket.map((j) => j.trade), initialBalance)
      const avgGain = n > 0 ? inBucket.reduce((a, j) => a + j.pnlPct, 0) / n : 0
      const avgDd = n > 0 ? inBucket.reduce((a, j) => a + j.ddPct, 0) / n : 0
      const maxDd = n > 0 ? Math.max(...inBucket.map((j) => j.ddPct)) : 0
      return { label: r.label, n, stats, avgGain, avgDd, maxDd }
    }).filter((b) => b.n > 0)
  }, [joined, initialBalance])

  const rGain = useMemo(() => pearson(joined.map((j) => j.vix), joined.map((j) => j.pnlPct)), [joined])
  const rDd = useMemo(() => pearson(joined.map((j) => j.vix), joined.map((j) => j.ddPct)), [joined])

  if (trades.length === 0) {
    return <div className="text-sm text-gray-500">No trades to analyze.</div>
  }
  if (vixQuery.isLoading) {
    return <div className="text-sm text-gray-500">Loading VIX data…</div>
  }
  if (vixQuery.isError) {
    return <div className="text-sm text-red-400">Failed to load VIX data: {(vixQuery.error as Error).message}</div>
  }
  if ((vixQuery.data ?? []).length === 0) {
    return (
      <div className="text-sm text-gray-500">
        No VIX data in QuestDB — import it with <span className="font-mono text-gray-400">data_collection/fetch_vix.py</span>.
      </div>
    )
  }
  if (joined.length === 0) {
    return <div className="text-sm text-gray-500">No VIX data overlaps this backtest period.</div>
  }

  const gainBars: BarDatum[] = buckets.map((b) => ({
    label: b.label,
    n: b.n,
    value: b.avgGain,
    color: b.avgGain >= 0 ? '#34d399' : '#f87171',
    title: `VIX ${b.label} — ${b.n.toLocaleString()} trades\navg P&L ${b.avgGain >= 0 ? '+' : ''}${b.avgGain.toFixed(3)}% per trade\nwin rate ${fmtPct(b.stats.winRate)} · net ${fmt$(b.stats.netPnl)}`,
  }))

  const ddBars: BarDatum[] = buckets.map((b) => ({
    label: b.label,
    n: b.n,
    value: b.avgDd,
    color: '#f87171',
    marker: b.maxDd,
    title: `VIX ${b.label} — ${b.n.toLocaleString()} trades\navg drawdown ${b.avgDd.toFixed(2)}%\nworst drawdown ${b.maxDd.toFixed(2)}%`,
  }))

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-5">
      {/* Regime breakdown — same shape as the Splicing table */}
      <div className="border border-[#212124] bg-[#1A1A1E] overflow-hidden shadow-2xl shadow-black/40 w-full select-text">
        <table className="min-w-full text-right border-collapse text-xs font-mono">
          <thead>
            <tr className="bg-[#28282D] border-b border-[#212124] text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
              <th className="text-left py-3 pl-6 font-semibold">VIX Regime</th>
              <th className="py-3 px-3 font-semibold">Net P&amp;L</th>
              <th className="py-3 px-3 font-semibold">Net %</th>
              <th className="py-3 px-3 font-semibold">Trades</th>
              <th className="py-3 px-3 font-semibold">Win Rate</th>
              <th className="py-3 px-3 font-semibold">Profit Factor</th>
              <th className="py-3 pr-6 font-semibold">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => {
              const s = b.stats
              const thin = s.numTrades < LOW_CONFIDENCE
              return (
                <tr
                  key={b.label}
                  className={`border-b border-[#212124] last:border-0 text-gray-300 ${thin ? 'opacity-40' : 'hover:bg-[#28282D]/20'}`}
                  title={thin ? `Only ${s.numTrades} trades — too few to trust` : undefined}
                >
                  <td className="text-left py-3.5 pl-6 text-gray-200 font-semibold">{b.label}</td>
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

      {/* Bucket charts */}
      <div className="flex flex-col gap-5">
        <div className="bg-[#1A1A1E] border border-[#212124] p-4 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-[10px] font-semibold tracking-widest uppercase text-gray-500 select-none">
              Gain by VIX Regime
            </h3>
            <span className="text-[10px] font-mono text-gray-400" title="Pearson correlation between per-trade VIX and P&L">
              r = {isNaN(rGain) ? '—' : rGain.toFixed(2)}
            </span>
          </div>
          <BucketBars data={gainBars} yUnit="%" valueDecimals={3} />
        </div>

        <div className="bg-[#1A1A1E] border border-[#212124] p-4 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-[10px] font-semibold tracking-widest uppercase text-gray-500 select-none">
              Drawdown by VIX Regime
            </h3>
            <div className="flex items-center gap-4 text-[10px] font-mono">
              <span className="flex items-center gap-1.5 text-gray-400">
                <span className="w-2 h-2 rounded-sm bg-red-400/80 inline-block" /> avg
              </span>
              <span className="flex items-center gap-1.5 text-gray-400">
                <span className="w-3 h-0.5 bg-red-300 inline-block" /> worst
              </span>
              <span className="text-gray-400" title="Pearson correlation between per-trade VIX and drawdown depth">
                r = {isNaN(rDd) ? '—' : rDd.toFixed(2)}
              </span>
            </div>
          </div>
          <BucketBars data={ddBars} yUnit="%" />
        </div>
      </div>

      {unmatched > 0 && (
        <p className="text-[10px] text-gray-500">
          {unmatched} trade{unmatched === 1 ? '' : 's'} had no VIX data within 7 days of entry and {unmatched === 1 ? 'was' : 'were'} excluded.
        </p>
      )}
    </div>
  )
}
