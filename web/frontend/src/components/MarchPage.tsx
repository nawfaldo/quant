import { useRef, useEffect, useState } from 'react'
import {
  createChart, CandlestickSeries, ColorType, CrosshairMode,
  type UTCTimestamp
} from 'lightweight-charts'
import { useApp } from '../context/AppContext'
import { BACKEND_URL, type Bar, type TF } from '../types'

interface Tick {
  ts: number      // nanoseconds
  price: number
  size: number
  side: 'BUY' | 'SELL'
}

async function fetchMarchCandles(symbol: string, tf: TF): Promise<Bar[]> {
  const res = await fetch(`${BACKEND_URL}/api/march/candles/bin?symbol=${symbol}&tf=${tf.table}`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  const buf = await res.arrayBuffer()
  const view = new DataView(buf)
  if (view.getUint32(0, true) !== 0x45444C43) throw new Error('Bad response magic')
  const count = view.getUint32(4, true)
  const data: Bar[] = new Array(count)
  let off = 8
  for (let i = 0; i < count; i++) {
    data[i] = {
      time:  view.getUint32(off,      true) as UTCTimestamp,
      open:  view.getFloat32(off + 4,  true),
      high:  view.getFloat32(off + 8,  true),
      low:   view.getFloat32(off + 12, true),
      close: view.getFloat32(off + 16, true),
    }
    off += 20
  }
  return data
}

async function fetchMarchTicks(symbol: string, sinceNanos: number | null): Promise<Tick[]> {
  const query = sinceNanos !== null ? `&since=${sinceNanos}` : ''
  const res = await fetch(`${BACKEND_URL}/api/march/ticks?symbol=${symbol}${query}`)
  if (!res.ok) throw new Error(`Backend error: ${res.status}`)
  return res.json()
}

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

// Inserts whitespace entries for every missing TF slot so lightweight-charts
// renders real time gaps instead of compressing bars together.
function fillGaps(candles: Bar[], tfSecs: number): Array<Bar | { time: UTCTimestamp }> {
  if (candles.length === 0) return []
  const map = new Map(candles.map(c => [c.time, c]))
  const first = candles[0].time
  const last = candles[candles.length - 1].time
  const out: Array<Bar | { time: UTCTimestamp }> = []
  for (let t = first as number; t <= (last as number); t += tfSecs) {
    out.push(map.get(t as UTCTimestamp) ?? { time: t as UTCTimestamp })
  }
  return out
}

export default function MarchPage() {
  const containerRef = useRef<HTMLDivElement>(null)
  const { marchSymbol, marchTf, setMarchStreamStatus } = useApp()
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!containerRef.current) return

    let active = true
    let pollTimeoutId: any = null
    let idleTimerId: any = null

    // Create chart
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
      lastValueVisible: true,
      priceLineVisible: true,
    })

    async function initAndPoll() {
      try {
        setLoading(true)
        setError(null)
        setMarchStreamStatus('loading')

        // 1. Fetch historical candles
        const candles = await fetchMarchCandles(marchSymbol, marchTf)
        if (!active) return

        series.setData(fillGaps(candles, marchTf.seconds) as any)
        chart.timeScale().fitContent()

        // 2. Determine initial state for polling
        let lastCandle: Bar | null = candles.length > 0 ? { ...candles[candles.length - 1] } : null
        
        // Start polling from the start of the last candle to catch any missing ticks
        let lastTickNanos: number | null = lastCandle ? (lastCandle.time as number) * 1_000_000_000 : null

        setLoading(false)
        setMarchStreamStatus('idle')

        // Polling function
        async function poll() {
          if (!active) return
          try {
            const ticks = await fetchMarchTicks(marchSymbol, lastTickNanos)
            if (!active) return

            if (ticks.length > 0) {
              // Mark live and schedule revert to idle after 5s of no new data
              setMarchStreamStatus('live')
              clearTimeout(idleTimerId)
              idleTimerId = setTimeout(() => setMarchStreamStatus('idle'), 5000)
              const tfSecs = marchTf.seconds

              for (const tick of ticks) {
                // Keep track of the highest tick timestamp received
                if (lastTickNanos === null || tick.ts > lastTickNanos) {
                  lastTickNanos = tick.ts
                }

                const tickSecs = Math.floor(tick.ts / 1_000_000_000)
                const candleStartSecs = Math.floor(tickSecs / tfSecs) * tfSecs

                if (lastCandle && lastCandle.time === candleStartSecs) {
                  // Update current candle
                  lastCandle.close = tick.price
                  if (tick.price > lastCandle.high) lastCandle.high = tick.price
                  if (tick.price < lastCandle.low) lastCandle.low = tick.price
                  series.update(lastCandle)
                } else {
                  // Create new candle
                  const newCandle: Bar = {
                    time: candleStartSecs as UTCTimestamp,
                    open: tick.price,
                    high: tick.price,
                    low: tick.price,
                    close: tick.price,
                  }
                  series.update(newCandle)
                  lastCandle = newCandle
                }
              }
            }
          } catch (err) {
            console.error('Tick polling error:', err)
            setMarchStreamStatus('error')
          }

          // Schedule next poll in 150ms for low-latency real-time stream
          pollTimeoutId = setTimeout(poll, 150)
        }

        // Trigger first poll
        poll()

      } catch (err: any) {
        if (!active) return
        setError(err.message || 'Failed to initialize March data')
        setLoading(false)
      }
    }

    initAndPoll()

    return () => {
      active = false
      if (pollTimeoutId) clearTimeout(pollTimeoutId)
      if (idleTimerId) clearTimeout(idleTimerId)
      setMarchStreamStatus('idle')
      chart.remove()
    }
  }, [marchSymbol, marchTf])

  return (
    <div className="flex-1 flex flex-col bg-gray-950 min-h-0 relative">
      {loading && (
        <div className="absolute inset-0 bg-gray-950/80 backdrop-blur-sm flex items-center justify-center z-10">
          <div className="text-gray-400 text-sm flex items-center gap-2">
            <svg className="animate-spin h-4 w-4 text-blue-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Loading March Chart...
          </div>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 bg-gray-950/90 flex flex-col items-center justify-center gap-3 z-10 p-4">
          <div className="text-red-400 text-sm text-center">
            {error}
          </div>
          <button
            onClick={() => {
              setError(null)
              // This dummy state change or just calling initAndPoll again handles retry
              window.location.reload()
            }}
            className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-xs font-medium rounded-md transition-colors"
          >
            Retry
          </button>
        </div>
      )}
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  )
}
