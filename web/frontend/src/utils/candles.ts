import type { UTCTimestamp } from 'lightweight-charts'
import { type Bar, type TF } from '../types'

const MAX_DISPLAY_BARS = 100_000

export function resample(bars: Bar[], tf: TF): Bar[] {
  if (tf.seconds === 60) return bars
  const out: Bar[] = []
  let bucket: Bar | null = null
  for (const b of bars) {
    const t = (Math.floor((b.time as number) / tf.seconds) * tf.seconds) as UTCTimestamp
    if (!bucket || bucket.time !== t) {
      if (bucket) out.push(bucket)
      bucket = { time: t, open: b.open, high: b.high, low: b.low, close: b.close }
    } else {
      if (b.high > bucket.high) bucket.high = b.high
      if (b.low  < bucket.low)  bucket.low  = b.low
      bucket.close = b.close
    }
  }
  if (bucket) out.push(bucket)
  return out
}

export function barsForChart(bars: Bar[], tf: TF): Bar[] {
  const resampled = resample(bars, tf)
  return resampled.length > MAX_DISPLAY_BARS ? resampled.slice(-MAX_DISPLAY_BARS) : resampled
}
