import {
  type IChartApiBase, type ISeriesApi, type Time, type UTCTimestamp,
  type ISeriesPrimitive, type SeriesAttachedParameter,
  type IPrimitivePaneView, type IPrimitivePaneRenderer,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'
import type { Trade, Bar } from '../types'

const MAX_VISIBLE_LINES = 1000
const TRADE_LOOKBACK    = 500
const MAX_TEXT_LABELS   = 50   // only draw pnl text when few enough trades are on screen

function triangle(ctx: CanvasRenderingContext2D, cx: number, cy: number, h: number, up: boolean) {
  ctx.beginPath()
  if (up) {
    ctx.moveTo(cx, cy - h)
    ctx.lineTo(cx - h, cy + h)
    ctx.lineTo(cx + h, cy + h)
  } else {
    ctx.moveTo(cx, cy + h)
    ctx.lineTo(cx - h, cy - h)
    ctx.lineTo(cx + h, cy - h)
  }
  ctx.closePath()
  ctx.fill()
}

class TradeLinesRenderer implements IPrimitivePaneRenderer {
  primitive: TradeLinesPrimitive
  series: ISeriesApi<'Candlestick'> | null
  chart: IChartApiBase<Time> | null

  constructor(
    primitive: TradeLinesPrimitive,
    series: ISeriesApi<'Candlestick'> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive
    this.series    = series
    this.chart     = chart
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive
    const et = p.et, xt = p.xt, ep = p.ep, xp = p.xp
    const side = p.side, pnl = p.pnl, qty = p.qty
    const s = this.series, ch = this.chart
    if (!et || !xt || !ep || !xp || !side || !pnl || !qty || !s || !ch) return
    const n = et.length
    if (n === 0) return

    const ts = ch.timeScale()
    const range = ts.getVisibleRange()
    if (!range) return
    const from = range.from as number
    const to   = range.to   as number

    let lo = 0, hi = n
    while (lo < hi) {
      const mid = (lo + hi) >>> 1
      if (et[mid] < from) lo = mid + 1
      else hi = mid
    }
    const startIdx = Math.max(0, lo - TRADE_LOOKBACK)

    let lo2 = lo, hi2 = n
    while (lo2 < hi2) {
      const mid = (lo2 + hi2) >>> 1
      if (et[mid] <= to) lo2 = mid + 1
      else hi2 = mid
    }
    const visibleCount = lo2 - lo
    if (visibleCount > MAX_VISIBLE_LINES) return

    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      // --- entry→exit dashed lines (single batched path) ---
      ctx.save()
      ctx.lineWidth    = hpr
      ctx.setLineDash([3 * hpr, 3 * hpr])
      ctx.strokeStyle  = 'rgba(255,255,255,0.45)'
      ctx.beginPath()
      for (let i = startIdx; i < n; i++) {
        if (et[i] > to) break
        if (xt[i] < from) continue
        const x1 = ts.timeToCoordinate(et[i] as UTCTimestamp)
        const x2 = ts.timeToCoordinate(xt[i] as UTCTimestamp)
        const y1 = s.priceToCoordinate(ep[i])
        const y2 = s.priceToCoordinate(xp[i])
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        ctx.moveTo(x1 * hpr, y1 * vpr)
        ctx.lineTo(x2 * hpr, y2 * vpr)
      }
      ctx.stroke()
      ctx.restore()

      // --- entry/exit arrow markers + optional pnl text ---
      ctx.save()
      ctx.setLineDash([])
      ctx.fillStyle = '#ffffff'
      const h = 4 * hpr
      const drawText = visibleCount <= MAX_TEXT_LABELS
      if (drawText) {
        ctx.font = `${10 * vpr}px sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'bottom'
      }
      for (let i = startIdx; i < n; i++) {
        if (et[i] > to) break
        if (xt[i] < from) continue
        const x1 = ts.timeToCoordinate(et[i] as UTCTimestamp)
        const x2 = ts.timeToCoordinate(xt[i] as UTCTimestamp)
        const y1 = s.priceToCoordinate(ep[i])
        const y2 = s.priceToCoordinate(xp[i])
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        const isLong = side[i] === 0
        const ex1 = x1 * hpr, ey1 = y1 * vpr
        const ex2 = x2 * hpr, ey2 = y2 * vpr
        drawStandardArrow(ctx, ex1, ey1, h, isLong)   // entry: up for long, down for short
        drawStandardArrow(ctx, ex2, ey2, h, !isLong)  // exit: opposite
        if (drawText) {
          const v = pnl[i]
          const gain = v >= 0 ? '+$' + v.toFixed(0) : '-$' + Math.abs(v).toFixed(0)
          const qtyStr = Number.isInteger(qty[i]) ? qty[i].toString() : qty[i].toFixed(2).replace(/\.?0+$/, '')
          ctx.fillText(`${qtyStr}  ${gain}`, ex1, ey1 - h - 2 * vpr)
        }
      }
      ctx.restore()
    })
  }
}

class TradeLinesView implements IPrimitivePaneView {
  primitive: TradeLinesPrimitive
  constructor(primitive: TradeLinesPrimitive) { this.primitive = primitive }
  renderer(): IPrimitivePaneRenderer {
    return new TradeLinesRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    )
  }
  zOrder() { return 'normal' as const }
}

export class TradeLinesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<'Candlestick'> | null = null
  private _chart: IChartApiBase<Time> | null = null
  private _requestUpdate: (() => void) | null = null
  private _view = new TradeLinesView(this)

  et: Uint32Array  | null = null
  xt: Uint32Array  | null = null
  ep: Float32Array | null = null
  xp: Float32Array | null = null
  side: Uint8Array  | null = null  // 0 = long, 1 = short
  pnl:  Float32Array | null = null
  qty:  Float32Array | null = null

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<'Candlestick'>
    this._chart  = p.chart
    this._requestUpdate = p.requestUpdate
  }
  detached() { this._series = null; this._chart = null }
  updateAllViews() {}
  paneViews() { return [this._view] }

  getSeries() { return this._series }
  getChart()  { return this._chart }

  setTrades(trades: Trade[], tfSeconds = 60) {
    const n = trades.length
    if (n === 0) {
      this.et = this.xt = null
      this.ep = this.xp = null
      this.side = null
      this.pnl = this.qty = null
      this._requestUpdate?.()
      return
    }
    const sorted = trades.slice().sort((a, b) => (a.et as number) - (b.et as number))
    const et = new Uint32Array(n),  xt = new Uint32Array(n)
    const ep = new Float32Array(n), xp = new Float32Array(n)
    const side = new Uint8Array(n)
    const pnl = new Float32Array(n), qty = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      const t = sorted[i]
      // Snap timestamps to the active timeframe bucket so timeToCoordinate
      // can resolve them on any timeframe (not just 1m).
      et[i] = Math.floor((t.et as number) / tfSeconds) * tfSeconds
      xt[i] = Math.floor((t.xt as number) / tfSeconds) * tfSeconds
      ep[i] = t.ep;           xp[i] = t.xp
      side[i] = t.side === 'long' ? 0 : 1
      pnl[i] = t.pnl;         qty[i] = t.qty
    }
    this.et = et; this.xt = xt; this.ep = ep; this.xp = xp
    this.side = side; this.pnl = pnl; this.qty = qty
    this._requestUpdate?.()
  }
}

interface ORArea {
  startTime: UTCTimestamp
  endTime: UTCTimestamp
  highBody: number
  lowBody: number
}

interface ORBreakout {
  time: UTCTimestamp
  price: number
}

class OpeningRangeRenderer implements IPrimitivePaneRenderer {
  primitive: OpeningRangePrimitive
  series: ISeriesApi<'Candlestick'> | null
  chart: IChartApiBase<Time> | null

  constructor(primitive: OpeningRangePrimitive, series: ISeriesApi<'Candlestick'> | null, chart: IChartApiBase<Time> | null) {
    this.primitive = primitive
    this.series = series
    this.chart = chart
  }

  draw(target: CanvasRenderingTarget2D) {
    if (!this.primitive.visible) return
    const s = this.series, ch = this.chart
    if (!s || !ch) return
    const ts = ch.timeScale()
    const ranges = this.primitive.ranges
    if (ranges.length === 0) return
    
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      ctx.save()
      for (const r of ranges) {
        const x1 = ts.timeToCoordinate(r.startTime)
        const x2 = ts.timeToCoordinate(r.endTime)
        const y1 = s.priceToCoordinate(r.highBody)
        const y2 = s.priceToCoordinate(r.lowBody)
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        
        let left = Math.min(x1, x2) * hpr
        let right = Math.max(x1, x2) * hpr
        // Add padding so it covers the bars and is visible even if x1 === x2
        const padding = 5 * hpr
        left -= padding
        right += padding
        
        const top = Math.min(y1, y2) * vpr
        const bottom = Math.max(y1, y2) * vpr
        
        ctx.fillStyle = 'rgba(239, 68, 68, 0.35)'
        ctx.fillRect(left, top, right - left, Math.max(1 * vpr, bottom - top))
      }
      
      const range = ts.getVisibleRange()
      if (!range) { ctx.restore(); return }
      
      const breakouts = this.primitive.breakouts
      if (breakouts.length > 0) {
        let lo = 0, hi = breakouts.length
        while (lo < hi) {
          const mid = (lo + hi) >>> 1
          if (breakouts[mid].time < (range.from as number)) lo = mid + 1
          else hi = mid
        }
        const startIdx = Math.max(0, lo - 100)
        
        let lo2 = lo, hi2 = breakouts.length
        while (lo2 < hi2) {
          const mid = (lo2 + hi2) >>> 1
          if (breakouts[mid].time <= (range.to as number)) lo2 = mid + 1
          else hi2 = mid
        }
        const endIdx = lo2
        
        if (endIdx - startIdx < 5000) {
          ctx.fillStyle = '#22c55e'
          const h = 4 * hpr
          for (let i = startIdx; i < endIdx; i++) {
            const b = breakouts[i]
            const x = ts.timeToCoordinate(b.time)
            const y = s.priceToCoordinate(b.price)
            if (x === null || y === null) continue
            
            const cx = x * hpr
            const cy = y * vpr - h - 2 * vpr // Position slightly above the close
            ctx.beginPath()
            ctx.moveTo(cx, cy + h)
            ctx.lineTo(cx - h, cy - h)
            ctx.lineTo(cx + h, cy - h)
            ctx.fill()
          }
        }
      }
      
      ctx.restore()
    })
  }
}

class OpeningRangeView implements IPrimitivePaneView {
  primitive: OpeningRangePrimitive
  constructor(primitive: OpeningRangePrimitive) { this.primitive = primitive }
  renderer() { return new OpeningRangeRenderer(this.primitive, this.primitive.getSeries(), this.primitive.getChart()) }
  zOrder() { return 'bottom' as const }
}

export class OpeningRangePrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<'Candlestick'> | null = null
  private _chart: IChartApiBase<Time> | null = null
  private _requestUpdate: (() => void) | null = null
  private _view = new OpeningRangeView(this)
  
  ranges: ORArea[] = []
  breakouts: ORBreakout[] = []
  visible: boolean = false

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<'Candlestick'>
    this._chart = p.chart
    this._requestUpdate = p.requestUpdate
  }
  detached() { this._series = null; this._chart = null }
  updateAllViews() { this._requestUpdate?.() }
  paneViews() { return [this._view] }

  getSeries() { return this._series }
  getChart() { return this._chart }

  setBars(bars: Bar[]) {
    // Timestamps are stored as ET wall-clock baked into fake-UTC by the importer.
    // Read them as UTC so we don't double-shift the already-ET values.
    const formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: 'UTC',
      hour: '2-digit', minute: '2-digit', hour12: false
    })

    this.ranges = []
    this.breakouts = []

    // Mirror the backtest strategy (strategies/30m_buy.zig) exactly so this
    // indicator can verify it:
    //   • Opening range is built from the FIRST FIVE bars (09:30–09:50), using
    //     CLOSE prices: OR_high = max close, OR_low = min close. No wicks/bodies.
    //   • The 09:55 bar is NOT part of the range — it is the single breakout
    //     trigger. If close(09:55) > OR_high, the strategy enters at the OPEN of
    //     the 10:00 bar. So the range box ends at 09:55; the entry sits on the
    //     next bar (10:00), OUTSIDE the box.
    let currentRange: Partial<ORArea> | null = null
    let orHigh = -Infinity   // breakout reference: max CLOSE of first five bars
    let bodyHigh = -Infinity // box top: highest body (no wick) of the range
    let bodyLow = Infinity   // box bottom: lowest body (no wick) of the range

    const reset = () => { currentRange = null; orHigh = -Infinity; bodyHigh = -Infinity; bodyLow = Infinity }

    for (const b of bars) {
      const timeStr = formatter.format(new Date((b.time as number) * 1000))

      if (timeStr >= '09:30' && timeStr <= '09:50') {
        // Range-defining bar (first five). Breakout level uses CLOSE; the red
        // box spans the candle bodies (open/close extents, wicks excluded).
        if (!currentRange) {
          currentRange = { startTime: b.time, endTime: b.time, highBody: 0, lowBody: 0 }
          orHigh = -Infinity; bodyHigh = -Infinity; bodyLow = Infinity
        } else {
          currentRange.endTime = b.time
        }
        orHigh = Math.max(orHigh, b.close)
        bodyHigh = Math.max(bodyHigh, b.open, b.close)
        bodyLow = Math.min(bodyLow, b.open, b.close)
      } else if (timeStr === '09:55') {
        // Single breakout trigger: close(09:55) > OR_high (max close of the first
        // five bars). The box spans all six bodies (09:30–09:55), so the breakout
        // candle's body high closes flush with the top of the box.
        if (currentRange && orHigh !== -Infinity) {
          bodyHigh = Math.max(bodyHigh, b.open, b.close)
          bodyLow = Math.min(bodyLow, b.open, b.close)
          currentRange.endTime = b.time
          currentRange.highBody = bodyHigh
          currentRange.lowBody = bodyLow
          if (b.close > orHigh) {
            this.ranges.push(currentRange as ORArea)
            this.breakouts.push({ time: b.time as UTCTimestamp, price: b.close })
          }
        }
        reset()
      } else {
        // Any other bar ends/discards an in-progress (incomplete) window.
        reset()
      }
    }

    this._requestUpdate?.()
  }
}

function drawStandardArrow(ctx: CanvasRenderingContext2D, cx: number, cy: number, h: number, up: boolean) {
  const hSize = h * 1.2;
  const headH = h * 1.0;
  const len = h * 2.2;
  const stemW = h * 0.4;

  ctx.beginPath();
  if (up) {
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx - hSize, cy + headH);
    ctx.lineTo(cx - stemW / 2, cy + headH);
    ctx.lineTo(cx - stemW / 2, cy + len);
    ctx.lineTo(cx + stemW / 2, cy + len);
    ctx.lineTo(cx + stemW / 2, cy + headH);
    ctx.lineTo(cx + hSize, cy + headH);
  } else {
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx - hSize, cy - headH);
    ctx.lineTo(cx - stemW / 2, cy - headH);
    ctx.lineTo(cx - stemW / 2, cy - len);
    ctx.lineTo(cx + stemW / 2, cy - len);
    ctx.lineTo(cx + stemW / 2, cy - headH);
    ctx.lineTo(cx + hSize, cy - headH);
  }
  ctx.closePath();
  ctx.fill();
}

class ActivePositionsRenderer implements IPrimitivePaneRenderer {
  primitive: ActivePositionsPrimitive
  series: ISeriesApi<'Candlestick'> | null
  chart: IChartApiBase<Time> | null

  constructor(
    primitive: ActivePositionsPrimitive,
    series: ISeriesApi<'Candlestick'> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive
    this.series    = series
    this.chart     = chart
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive
    const s = this.series, ch = this.chart
    if (!s || !ch || !p.positions || p.positions.length === 0) return

    const ts = ch.timeScale()
    const range = ts.getVisibleRange()
    if (!range) return
    const from = range.from as number
    const to   = range.to   as number

    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      ctx.save()
      ctx.setLineDash([])
      const h = 4 * hpr

      ctx.font = `${10 * vpr}px sans-serif`
      ctx.textAlign = 'center'

      for (const pos of p.positions) {
        if (!pos.time || pos.time < from || pos.time > to) continue

        const x = ts.timeToCoordinate(pos.time as UTCTimestamp)
        const y = s.priceToCoordinate(pos.price)
        if (x === null || y === null) continue

        const cx = x * hpr, cy = y * vpr
        
        // Draw the standard arrow pointing to the exact price
        ctx.fillStyle = '#ffffff'
        drawStandardArrow(ctx, cx, cy, h, pos.isLong)

        // Draw the text (e.g. "Buy 0.01 +$10") at the exact price
        const sideWord = pos.isLong ? 'Buy' : 'Sell'
        const gainSign = pos.profit >= 0 ? '+' : '-'
        const absProfitStr = Math.abs(pos.profit).toFixed(2).replace(/\.00$/, '')
        const formattedProfit = `${gainSign}$${absProfitStr}`
        const text = `${sideWord} ${pos.volume.toFixed(2)} ${formattedProfit}`
        
        // Draw text slightly above/below the triangle depending on type
        ctx.fillStyle = '#d1d5db'
        if (pos.isLong) {
          ctx.textBaseline = 'bottom'
          ctx.fillText(text, cx, cy - h - 2 * vpr)
        } else {
          ctx.textBaseline = 'top'
          ctx.fillText(text, cx, cy + h + 2 * vpr)
        }
      }
      ctx.restore()
    })
  }
}

class ActivePositionsView implements IPrimitivePaneView {
  primitive: ActivePositionsPrimitive
  constructor(primitive: ActivePositionsPrimitive) { this.primitive = primitive }
  renderer(): IPrimitivePaneRenderer {
    return new ActivePositionsRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    )
  }
  zOrder() { return 'normal' as const }
}

export interface ActivePosInfo {
  time: number
  price: number
  volume: number
  profit: number
  isLong: boolean
  strategy: string
}

export class ActivePositionsPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<'Candlestick'> | null = null
  private _chart: IChartApiBase<Time> | null = null
  private _requestUpdate: (() => void) | null = null
  private _view = new ActivePositionsView(this)

  positions: ActivePosInfo[] = []

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<'Candlestick'>
    this._chart  = p.chart
    this._requestUpdate = p.requestUpdate
  }
  detached() { this._series = null; this._chart = null }
  updateAllViews() {}
  paneViews() { return [this._view] }

  getSeries() { return this._series }
  getChart()  { return this._chart }

  setPositions(positions: ActivePosInfo[]) {
    this.positions = positions
    this._requestUpdate?.()
  }
}

class HistoricalTradesRenderer implements IPrimitivePaneRenderer {
  primitive: HistoricalTradesPrimitive
  series: ISeriesApi<'Candlestick'> | null
  chart: IChartApiBase<Time> | null

  constructor(
    primitive: HistoricalTradesPrimitive,
    series: ISeriesApi<'Candlestick'> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive
    this.series    = series
    this.chart     = chart
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive
    const s = this.series, ch = this.chart
    if (!s || !ch || !p.trades || p.trades.length === 0) return

    const ts = ch.timeScale()
    const range = ts.getVisibleRange()
    if (!range) return
    const from = range.from as number
    const to   = range.to   as number

    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      // 1. Draw dashed connection lines
      ctx.save()
      ctx.lineWidth = 1.5 * hpr
      ctx.setLineDash([4 * hpr, 4 * hpr])
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)'
      ctx.beginPath()
      for (const t of p.trades) {
        if (t.et > to || t.xt < from) continue
        const x1 = ts.timeToCoordinate(t.et as UTCTimestamp)
        const x2 = ts.timeToCoordinate(t.xt as UTCTimestamp)
        const y1 = s.priceToCoordinate(t.ep)
        const y2 = s.priceToCoordinate(t.xp)
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        ctx.moveTo(x1 * hpr, y1 * vpr)
        ctx.lineTo(x2 * hpr, y2 * vpr)
      }
      ctx.stroke()
      ctx.restore()

      // 2. Draw standard entry and exit arrows (white)
      ctx.save()
      ctx.setLineDash([])
      ctx.fillStyle = '#ffffff'
      const h = 4 * hpr
      for (const t of p.trades) {
        if (t.et > to || t.xt < from) continue
        const x1 = ts.timeToCoordinate(t.et as UTCTimestamp)
        const x2 = ts.timeToCoordinate(t.xt as UTCTimestamp)
        const y1 = s.priceToCoordinate(t.ep)
        const y2 = s.priceToCoordinate(t.xp)
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue

        // Entry arrow (up for long, down for short)
        drawStandardArrow(ctx, x1 * hpr, y1 * vpr, h, t.isLong)

        // Exit arrow (down for long, up for short)
        drawStandardArrow(ctx, x2 * hpr, y2 * vpr, h, !t.isLong)
      }
      ctx.restore()
    })
  }
}

class HistoricalTradesView implements IPrimitivePaneView {
  primitive: HistoricalTradesPrimitive
  constructor(primitive: HistoricalTradesPrimitive) { this.primitive = primitive }
  renderer(): IPrimitivePaneRenderer {
    return new HistoricalTradesRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    )
  }
  zOrder() { return 'normal' as const }
}

export interface HistoricalTradeInfo {
  et: number
  xt: number
  ep: number
  xp: number
  isLong: boolean
  strategy: string
}

export class HistoricalTradesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<'Candlestick'> | null = null
  private _chart: IChartApiBase<Time> | null = null
  private _requestUpdate: (() => void) | null = null
  private _view = new HistoricalTradesView(this)

  trades: HistoricalTradeInfo[] = []

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<'Candlestick'>
    this._chart  = p.chart
    this._requestUpdate = p.requestUpdate
  }
  detached() { this._series = null; this._chart = null }
  updateAllViews() {}
  paneViews() { return [this._view] }

  getSeries() { return this._series }
  getChart()  { return this._chart }

  setTrades(trades: HistoricalTradeInfo[]) {
    this.trades = trades
    this._requestUpdate?.()
  }
}
