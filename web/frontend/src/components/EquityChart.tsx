import { useEffect, useRef } from 'react'
import { createChart, AreaSeries, ColorType, type UTCTimestamp, type IChartApiBase } from 'lightweight-charts'
import type { Trade } from '../types'

interface Props {
  trades: Trade[]
  initialBalance: number
  startDate?: string // "YYYY-MM-DD" — anchor the chart origin here if earlier than first trade
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

export default function EquityChart({ trades, initialBalance, startDate }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApiBase<UTCTimestamp> | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const chart = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
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
        // With ~2000 daily points the default minBarSpacing (0.5px) is wider than
        // the container can hold, so fitContent() clamps and only shows the most
        // recent slice. A tiny minBarSpacing lets the full history compress to fit.
        minBarSpacing: 0.001,
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
      const firstDay = Math.floor(sorted[0].et / 86400) * 86400
      let anchorDay = firstDay - 86400
      if (startDate) {
        const parsed = Date.parse(startDate + 'T00:00:00Z') / 1000
        if (!isNaN(parsed) && parsed < anchorDay) anchorDay = parsed
      }
      points.push({ time: anchorDay, value: initialBalance })
    }

    for (const [day, val] of [...dailyBalance.entries()].sort((a, b) => a[0] - b[0])) {
      points.push({ time: day, value: val })
    }

    areaSeries.setData(points.map(p => ({ time: p.time as UTCTimestamp, value: p.value })))

    chartRef.current = chart as IChartApiBase<UTCTimestamp>

    const firstTime = points.length > 0 ? points[0].time : null
    const lastTime = points.length > 0 ? points[points.length - 1].time : null

    const fitRange = () => {
      if (firstTime !== null && lastTime !== null && firstTime !== lastTime) {
        chart.timeScale().setVisibleRange({
          from: firstTime as UTCTimestamp,
          to: lastTime as UTCTimestamp,
        })
      } else {
        chart.timeScale().fitContent()
      }
    }

    // Single ResizeObserver owns both sizing and the initial range fit.
    // This avoids the race between our observer and lightweight-charts' internal
    // autoSize observer — by not using autoSize at all.
    let fitted = false
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth
      const h = el.clientHeight
      if (w === 0 || h === 0) return
      chart.resize(w, h)
      if (!fitted) {
        fitted = true
        fitRange()
      }
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [trades, initialBalance, startDate])

  return <div ref={containerRef} className="w-full h-full" />
}
