import { useEffect, useRef } from 'react'
import { createChart, AreaSeries, ColorType, type UTCTimestamp, type IChartApiBase } from 'lightweight-charts'
import type { Trade } from '../types'

interface Props {
  trades: Trade[]
  initialBalance: number
}

const TZ = 'UTC'
const dateFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export default function EquityChart({ trades, initialBalance }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApiBase<UTCTimestamp> | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const chart = createChart(el, {
      autoSize: true,
      ...(({ attributionLogo: false }) as any),
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#111827' },
        horzLines: { color: '#111827' },
      },
      localization: {
        timeFormatter: (time: number) => dateFormatter.format(new Date(time * 1000)),
        priceFormatter: (price: number) =>
          '$' + price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      },
      handleScroll: false,
      handleScale: false,
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#1f2937',
        autoScale: true,
      },
    })

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: '#10b981',
      topColor: 'rgba(16, 185, 129, 0.25)',
      bottomColor: 'rgba(16, 185, 129, 0.0)',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })

    // Build equity curve aggregated to daily resolution.
    // With 33k+ trades, per-trade resolution exceeds lightweight-charts' minBarSpacing
    // and fitContent can only show the last ~7% of the data — the initial balance
    // at the start of history is unreachable. Daily resolution (~1 point/day) fits
    // the entire history on screen.
    const sorted = [...trades].sort((a, b) => a.xt - b.xt)

    let balance = initialBalance
    const dailyBalance = new Map<number, number>()

    for (const trade of sorted) {
      balance += trade.pnl
      const dayTs = Math.floor(trade.xt / 86400) * 86400
      dailyBalance.set(dayTs, balance)
    }

    const points: { time: number; value: number }[] = []

    if (sorted.length > 0) {
      // Anchor: initial balance on the day before the first trade.
      const firstDay = Math.floor(sorted[0].et / 86400) * 86400
      points.push({ time: firstDay - 86400, value: initialBalance })
    }

    for (const [day, val] of [...dailyBalance.entries()].sort((a, b) => a[0] - b[0])) {
      points.push({ time: day, value: val })
    }

    areaSeries.setData(points.map(p => ({ time: p.time as UTCTimestamp, value: p.value })))
    chart.timeScale().fitContent()

    chartRef.current = chart as IChartApiBase<UTCTimestamp>

    return () => {
      chart.remove()
    }
  }, [trades, initialBalance])

  return <div ref={containerRef} className="w-full h-full" />
}
