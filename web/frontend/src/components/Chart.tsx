import { useRef, useEffect } from 'react'
import {
  createChart, CandlestickSeries, LineSeries, ColorType, CrosshairMode,
  type UTCTimestamp, type IChartApiBase, type ISeriesApi,
} from 'lightweight-charts'
import { TradeLinesPrimitive, OpeningRangePrimitive } from '../lib/primitives'
import { barsForChart } from '../utils/candles'
import type { Bar, TF, Trade, VwapPoint, Indicators } from '../types'

interface Props {
  bars: Bar[]
  activeTf: TF
  allTrades: Trade[]
  vwapData: VwapPoint[]
  indicators: Indicators
  fromTs: number | null
  toTs: number | null
}


// Stored timestamps are already New York wall-clock baked in as fake-UTC by the
// importer (--tz-hours 1). Format them verbatim as UTC; re-converting through
// 'America/New_York' would subtract the offset a second time (10:00 ET → 05:00).
const TZ = 'UTC'

const timeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  year: 'numeric', month: 'short', day: 'numeric',
  hour: '2-digit', minute: '2-digit', hour12: false
})

const tickTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  hour: '2-digit', minute: '2-digit', hour12: false
})

const tickDateFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ,
  year: 'numeric', month: 'short', day: 'numeric'
})

export default function Chart({ bars, activeTf, allTrades, vwapData, indicators, fromTs, toTs }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApiBase<UTCTimestamp> | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const linesPlugin = useRef(new TradeLinesPrimitive())
  const linesAttached = useRef(false)
  const orPlugin = useRef(new OpeningRangePrimitive())
  const orAttached = useRef(false)

  useEffect(() => {
    if (!containerRef.current) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const chart = createChart(containerRef.current, {
      autoSize: true,
      ...(({ attributionLogo: false }) as any),
      layout: {
        background: { type: ColorType.Solid, color: '#030712' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#111827' },
        horzLines: { color: '#111827' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#374151' },
        horzLine: { color: '#374151' },
      },
      localization: {
        timeFormatter: (time: number) => timeFormatter.format(new Date(time * 1000))
      },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number, tickMarkType: number) => {
          const date = new Date(time * 1000)
          return tickMarkType >= 3 ? tickTimeFormatter.format(date) : tickDateFormatter.format(date)
        }
      },
      rightPriceScale: { borderColor: '#1f2937' },
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const vwapSeries = chart.addSeries(LineSeries, {
      color: '#60a5fa',
      lineWidth: 1,
      visible: false,
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    })

    chartRef.current = chart as IChartApiBase<UTCTimestamp>
    seriesRef.current = series
    vwapSeriesRef.current = vwapSeries

    return () => { chart.remove() }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current || bars.length === 0) return
    const chartedBars = barsForChart(bars, activeTf).filter(b => {
      if (fromTs !== null && b.time < fromTs) return false
      if (toTs   !== null && b.time > toTs)   return false
      return true
    })
    seriesRef.current.setData(chartedBars)
    orPlugin.current.setBars(chartedBars)
    if (!orAttached.current) {
      seriesRef.current.attachPrimitive(orPlugin.current)
      orAttached.current = true
    }
    chartRef.current.timeScale().fitContent()
  }, [bars, activeTf, fromTs, toTs])

  useEffect(() => {
    if (!vwapSeriesRef.current || vwapData.length === 0) return
    const seconds = activeTf.seconds
    const combined = vwapData
      // Keep VWAP on the same time window as the candles. Otherwise the full
      // 2008→present VWAP range extends the shared time scale far past the
      // (date-filtered) candle range, and the fitted logical range ends up
      // pointing at years with no candles — making the chart appear blank.
      .filter(p => {
        if (p.value === 0) return false
        if (fromTs !== null && p.time < fromTs) return false
        if (toTs   !== null && p.time > toTs)   return false
        return true
      })
      .map(p => ({ time: p.time as number, value: p.value }))

    if (seconds === 60) {
      vwapSeriesRef.current.setData(combined.map(p => ({ time: p.time as UTCTimestamp, value: p.value })))
      return
    }

    // Resample to match candle bucket timestamps — last value per bucket wins
    const buckets = new Map<number, number>()
    for (const { time, value } of combined) {
      buckets.set(Math.floor(time / seconds) * seconds, value)
    }
    vwapSeriesRef.current.setData(
      [...buckets.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([t, value]) => ({ time: t as UTCTimestamp, value }))
    )
  }, [vwapData, activeTf, fromTs, toTs])

  useEffect(() => {
    vwapSeriesRef.current?.applyOptions({ visible: indicators.vwap })
    if (orPlugin.current) {
      orPlugin.current.visible = indicators.openingRange
      orPlugin.current.updateAllViews()
    }
  }, [indicators])

  useEffect(() => {
    const series = seriesRef.current
    const chart = chartRef.current
    if (!series || !chart) return

    // Only show trades that happen within the selected date window (by entry
    // time). The full trade history stays cached upstream for the equity curve.
    const winLo = fromTs ?? -Infinity
    const winHi = toTs   ?? Infinity
    const trades = allTrades.filter(t => t.et >= winLo && t.et <= winHi)

    if (trades.length === 0) {
      if (linesAttached.current) {
        series.detachPrimitive(linesPlugin.current)
        linesAttached.current = false
      }
      linesPlugin.current.setTrades([])
      return
    }

    if (!linesAttached.current) {
      series.attachPrimitive(linesPlugin.current)
      linesAttached.current = true
    }

    // Lines AND arrow/text markers are now drawn by the viewport-culled
    // primitive; createSeriesMarkers is gone (it redrew all ~25k markers every frame).
    linesPlugin.current.setTrades(trades, activeTf.seconds)

    // Fit the visible range to the shown trades so they land in view (the candle
    // effect fits to the whole candle window, which is usually wider).
    let lo = Infinity, hi = -Infinity
    for (const t of trades) {
      if (t.et < lo) lo = t.et
      if (t.xt > hi) hi = t.xt
    }
    if (lo < hi) {
      chart.timeScale().setVisibleRange({ from: lo as UTCTimestamp, to: hi as UTCTimestamp })
    }
  }, [allTrades, activeTf, fromTs, toTs])

  return <div ref={containerRef} className="flex-1 min-w-0" />
}
