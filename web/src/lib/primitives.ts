import {
  type IChartApiBase,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type IPrimitivePaneView,
  type IPrimitivePaneRenderer,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { Bar, Trade } from "../types";

const MAX_VISIBLE_LINES = 1000;
const TRADE_LOOKBACK = 500;
const MAX_TEXT_LABELS = 50; // only draw pnl text when few enough trades are on screen
const BACKTEST_TRADE_COLOR = "#f59e0b"; // amber: distinct from white candles
const LIVE_TRADE_COLOR = "#22d3ee"; // cyan: separates live trades from backtests

export interface BookmapHeatmapPoint {
  time: number;
  price: number;
  size: number;
}

interface HeatmapLevel {
  price: number;
  points: BookmapHeatmapPoint[];
}

const HEATMAP_PROFILES = {
  nq: { hotPercentile: 0.999, gamma: 0.9, zoomOutFloor: 0.18 },
  es: { hotPercentile: 0.99, gamma: 0.65, zoomOutFloor: 0.12 },
} as const;

const HEATMAP_COLORS = Array.from({ length: 256 }, (_, index) => {
  const t = index / 255;
  const stops = [
    [0, 28, 52],
    [0, 120, 195],
    [0, 220, 235],
    [255, 235, 45],
    [255, 55, 20],
  ];
  const scaled = t * (stops.length - 1);
  const lo = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - lo;
  const rgb = stops[lo].map((value, channel) =>
    Math.round(value + (stops[lo + 1][channel] - value) * f),
  );
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${0.5 + t * 0.45})`;
});

class BookmapHeatmapRenderer implements IPrimitivePaneRenderer {
  private primitive: BookmapHeatmapPrimitive;
  constructor(primitive: BookmapHeatmapPrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D) {
    const series = this.primitive.getSeries();
    const chart = this.primitive.getChart();
    const levels = this.primitive.levels;
    if (!series || !chart || levels.length === 0) return;
    const range = chart.timeScale().getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const dataEndTime = this.primitive.dataEndTime;
    // range.to is clamped to the last candle, but the depth feed runs ahead of
    // the candle stream. When the last candle is on screen (live edge), extend
    // the clip to the newest depth sample so the heatmap updates in real time
    // instead of waiting for the next bar.
    const seriesData = series.data();
    const lastBarTime = seriesData.length
      ? (seriesData[seriesData.length - 1].time as number)
      : 0;
    let to = range.to as number;
    if (to >= lastBarTime) {
      // Cap at the end of the currently forming bar so the heatmap fills the
      // live column in real time without racing ahead of the price line.
      to = Math.min(
        Math.max(to, dataEndTime + 10),
        lastBarTime + this.primitive.timeStepSeconds,
      );
    }
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      ctx.save();
      const width = ctx.canvas.width;
      const height = ctx.canvas.height;
      const topPrice = series.coordinateToPrice(0);
      const bottomPrice = series.coordinateToPrice(height / vpr);
      const profile = HEATMAP_PROFILES[this.primitive.symbol as "nq" | "es"] ?? HEATMAP_PROFILES.nq;
      const cacheKey = [
        this.primitive.dataRevision,
        from,
        to,
        width,
        height,
        hpr,
        vpr,
        topPrice,
        bottomPrice,
        this.primitive.symbol,
      ].join(":");
      const cached = this.primitive.renderCache;
      if (cached && this.primitive.renderCacheKey === cacheKey) {
        ctx.drawImage(cached, 0, 0);
        ctx.restore();
        return;
      }

      const bitmap = this.primitive.getRenderCache(width, height);
      const drawCtx = bitmap.getContext("2d", { alpha: true });
      if (!drawCtx) {
        ctx.restore();
        return;
      }
      drawCtx.clearRect(0, 0, width, height);

      // Normalize against only the cells inside the current time/price
      // viewport. This makes the palette respond to pan and zoom: red means
      // unusually large liquidity for what the trader can currently see, not
      // for an unrelated outlier elsewhere in the downloaded session.
      const visibleSizeCounts = new Map<number, number>();
      let visibleSizeCount = 0;
      for (const level of levels) {
        const y = series.priceToCoordinate(level.price);
        if (y === null || y * vpr < 0 || y * vpr > height) continue;
        const points = level.points;
        const start = heatmapVisibleStart(points, from);
        for (let i = start; i < points.length; i++) {
          const point = points[i];
          if (point.time > to) break;
          const end = heatmapSegmentEnd(points, i, to, dataEndTime);
          if (point.size > 0 && end >= from) {
            const sizeBucket = Math.max(1, Math.round(point.size));
            visibleSizeCounts.set(
              sizeBucket,
              (visibleSizeCounts.get(sizeBucket) ?? 0) + 1,
            );
            visibleSizeCount++;
          }
        }
      }
      const percentileTarget = Math.max(
        1,
        Math.ceil(visibleSizeCount * profile.hotPercentile),
      );
      const sortedSizes = Array.from(visibleSizeCounts.keys()).sort((a, b) => a - b);
      let scale = 1;
      let cumulative = 0;
      for (const size of sortedSizes) {
        cumulative += visibleSizeCounts.get(size) ?? 0;
        scale = size;
        if (cumulative >= percentileTarget) break;
      }

      for (const level of levels) {
        const y = series.priceToCoordinate(level.price);
        if (y === null) continue;
        const yNext = series.priceToCoordinate(level.price + 0.25);
        // Switch rendering style with vertical price zoom. At close zoom each
        // exchange tick has enough room to be a crisp Bookmap-style cell. Once
        // ticks collapse below two CSS pixels, expand and soften them so nearby
        // levels blend into the continuous zoomed-out heatmap instead of
        // degenerating into one-pixel horizontal lines.
        const nativeRowHeight = Math.abs((yNext ?? y - 1) - y) * vpr;
        const nativeCssHeight = nativeRowHeight / vpr;
        const detailedRows = nativeCssHeight >= 2;
        const rowHeight = detailedRows
          ? Math.max(vpr, nativeRowHeight * 0.96)
          : (2.5 + Math.max(0, 1 - nativeCssHeight) * 0.75) * vpr;
        const zoomCompression = Math.max(
          0,
          Math.min(1, (2 - nativeCssHeight) / 2),
        );
        const minimumVisibleSize =
          scale * profile.zoomOutFloor * zoomCompression;
        const points = level.points;
        const start = heatmapVisibleStart(points, from);
        for (let i = start; i < points.length; i++) {
          const point = points[i];
          if (point.time > to) break;
          if (point.size <= 0) continue;
          const end = heatmapSegmentEnd(points, i, to, dataEndTime);
          if (end < from) continue;
          if (point.size < minimumVisibleSize) continue;
          const x1 = heatmapTimeCoordinate(
            chart,
            Math.max(from, point.time),
            this.primitive.timeStepSeconds,
          );
          const x2 = heatmapTimeCoordinate(
            chart,
            Math.min(to, end),
            this.primitive.timeStepSeconds,
          );
          if (x1 === null || x2 === null || x2 <= x1) continue;
          const intensity = Math.pow(
            Math.min(1, point.size / scale),
            profile.gamma,
          );
          drawCtx.fillStyle = HEATMAP_COLORS[Math.round(intensity * 255)];
          drawCtx.globalAlpha = detailedRows
            ? 1
            : Math.min(0.96, 0.86 + nativeCssHeight * 0.08);
          const top = detailedRows
            ? Math.round(y * vpr - rowHeight / 2)
            : y * vpr - rowHeight / 2;
          const height = detailedRows ? Math.max(1, Math.round(rowHeight)) : rowHeight;
          drawCtx.fillRect(
            x1 * hpr,
            top,
            Math.max(hpr, (x2 - x1) * hpr + 0.5),
            height,
          );
          // Label the still-active (live edge) levels with their size, but
          // only once they are hot enough to render orange/red. The last point
          // of a level is its current state; when scrolled into history the
          // loop breaks before reaching it, so no historical labels appear.
          if (i === points.length - 1 && intensity >= 0.85) {
            drawCtx.globalAlpha = 1;
            drawCtx.fillStyle = "#ffffff";
            drawCtx.font = `${Math.round(9 * vpr)}px ui-monospace, monospace`;
            drawCtx.textAlign = "left";
            drawCtx.textBaseline = "middle";
            drawCtx.fillText(
              String(Math.round(point.size)),
              x2 * hpr + 4 * hpr,
              y * vpr,
            );
          }
        }
      }
      drawCtx.globalAlpha = 1;
      this.primitive.renderCacheKey = cacheKey;
      ctx.drawImage(bitmap, 0, 0);
      ctx.restore();
    });
  }
}

function heatmapVisibleStart(points: BookmapHeatmapPoint[], from: number) {
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (points[mid].time < from) lo = mid + 1;
    else hi = mid;
  }
  return Math.max(0, lo - 1);
}

function heatmapSegmentEnd(
  points: BookmapHeatmapPoint[],
  index: number,
  visibleTo: number,
  dataEndTime: number,
) {
  if (index + 1 < points.length) return points[index + 1].time;
  // A level with no subsequent update remains valid only while the overall
  // depth feed is alive. The small grace covers the next 5-second sample; it
  // must not turn the final recorded value into an infinite line after the
  // collector or market-data connection has stopped.
  return Math.min(visibleTo, dataEndTime + 10);
}

function heatmapTimeCoordinate(
  chart: IChartApiBase<Time>,
  time: number,
  stepSeconds: number,
) {
  const scale = chart.timeScale();
  const bucket = Math.floor(time / stepSeconds) * stepSeconds;
  const fraction = (time - bucket) / stepSeconds;
  const x0 = scale.timeToCoordinate(bucket as UTCTimestamp);
  if (fraction === 0 && x0 !== null) return x0;
  const x1 = scale.timeToCoordinate((bucket + stepSeconds) as UTCTimestamp);
  if (x0 !== null && x1 !== null) return x0 + (x1 - x0) * fraction;

  // At the live right edge the candles may lag the depth feed by several bars
  // (the live tick stream stalls independently of the heatmap poll). Walk back
  // to the newest bucket that still maps to a bar and extrapolate forward at
  // the chart's bar spacing rather than dropping the newer depth cells.
  for (let back = 0; back <= 60; back++) {
    const t0 = (bucket - back * stepSeconds) as UTCTimestamp;
    const a = scale.timeToCoordinate(t0);
    const b = scale.timeToCoordinate((bucket - (back + 1) * stepSeconds) as UTCTimestamp);
    if (a !== null && b !== null) return a + (a - b) * (back + fraction);
  }
  return null;
}

class BookmapHeatmapView implements IPrimitivePaneView {
  private primitive: BookmapHeatmapPrimitive;
  constructor(primitive: BookmapHeatmapPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer { return new BookmapHeatmapRenderer(this.primitive); }
  zOrder() { return "bottom" as const; }
}

export class BookmapHeatmapPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new BookmapHeatmapView(this);
  private grouped = new Map<number, BookmapHeatmapPoint[]>();
  levels: HeatmapLevel[] = [];
  dataEndTime = 0;
  timeStepSeconds = 60;
  symbol: string = "nq";
  dataRevision = 0;
  renderCache: HTMLCanvasElement | null = null;
  renderCacheKey = "";

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() { this._series = null; this._chart = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  getSeries() { return this._series; }
  getChart() { return this._chart; }
  setTimeStep(seconds: number) { this.timeStepSeconds = Math.max(1, seconds); }
  setSymbol(symbol: string) {
    if (this.symbol === symbol) return;
    this.symbol = symbol;
    this.renderCacheKey = "";
  }
  getRenderCache(width: number, height: number) {
    if (!this.renderCache) this.renderCache = document.createElement("canvas");
    if (this.renderCache.width !== width || this.renderCache.height !== height) {
      this.renderCache.width = width;
      this.renderCache.height = height;
      this.renderCacheKey = "";
    }
    return this.renderCache;
  }

  setData(points: BookmapHeatmapPoint[]) {
    this.dataRevision++;
    this.renderCacheKey = "";
    this.grouped.clear();
    this.dataEndTime = 0;
    for (const point of points) {
      let level = this.grouped.get(point.price);
      if (!level) this.grouped.set(point.price, (level = []));
      level.push(point);
      this.dataEndTime = Math.max(this.dataEndTime, point.time);
    }
    this.levels = Array.from(this.grouped, ([price, levelPoints]) => ({
      price,
      points: levelPoints.sort((a, b) => a.time - b.time),
    }));
    this._requestUpdate?.();
  }

  appendData(points: BookmapHeatmapPoint[]) {
    if (points.length === 0) return;
    this.dataRevision++;
    this.renderCacheKey = "";
    const newLevels: HeatmapLevel[] = [];
    for (const point of points) {
      this.dataEndTime = Math.max(this.dataEndTime, point.time);
      let level = this.grouped.get(point.price);
      if (!level) {
        level = [];
        this.grouped.set(point.price, level);
        const entry = { price: point.price, points: level };
        this.levels.push(entry);
        newLevels.push(entry);
      }
      const previous = level[level.length - 1];
      if (previous?.time === point.time) previous.size = point.size;
      else level.push(point);
    }
    for (const level of newLevels) level.points.sort((a, b) => a.time - b.time);
    this._requestUpdate?.();
  }
}

/*
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
*/

class TradeLinesRenderer implements IPrimitivePaneRenderer {
  primitive: TradeLinesPrimitive;
  series: ISeriesApi<"Candlestick"> | null;
  chart: IChartApiBase<Time> | null;

  constructor(
    primitive: TradeLinesPrimitive,
    series: ISeriesApi<"Candlestick"> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive;
    this.series = series;
    this.chart = chart;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const et = p.et,
      xt = p.xt,
      ep = p.ep,
      xp = p.xp;
    const side = p.side,
      pnl = p.pnl,
      qty = p.qty;
    const s = this.series,
      ch = this.chart;
    if (!et || !xt || !ep || !xp || !side || !pnl || !qty || !s || !ch) return;
    const n = et.length;
    if (n === 0) return;

    const ts = ch.timeScale();
    const range = ts.getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;

    let lo = 0,
      hi = n;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (et[mid] < from) lo = mid + 1;
      else hi = mid;
    }
    const startIdx = Math.max(0, lo - TRADE_LOOKBACK);

    let lo2 = lo,
      hi2 = n;
    while (lo2 < hi2) {
      const mid = (lo2 + hi2) >>> 1;
      if (et[mid] <= to) lo2 = mid + 1;
      else hi2 = mid;
    }
    const visibleCount = lo2 - lo;
    if (visibleCount > MAX_VISIBLE_LINES) return;

    target.useBitmapCoordinateSpace(
      ({
        context: ctx,
        horizontalPixelRatio: hpr,
        verticalPixelRatio: vpr,
      }) => {
        // --- entry→exit dashed lines (single batched path) ---
        ctx.save();
        ctx.lineWidth = hpr;
        ctx.setLineDash([3 * hpr, 3 * hpr]);
        ctx.strokeStyle = "rgba(245,158,11,0.65)";
        ctx.beginPath();
        for (let i = startIdx; i < n; i++) {
          if (et[i] > to) break;
          if (xt[i] < from) continue;
          const x1 = ts.timeToCoordinate(et[i] as UTCTimestamp);
          const x2 = ts.timeToCoordinate(xt[i] as UTCTimestamp);
          const y1 = s.priceToCoordinate(ep[i]);
          const y2 = s.priceToCoordinate(xp[i]);
          if (x1 === null || x2 === null || y1 === null || y2 === null)
            continue;
          ctx.moveTo(x1 * hpr, y1 * vpr);
          ctx.lineTo(x2 * hpr, y2 * vpr);
        }
        ctx.stroke();
        ctx.restore();

        // --- entry/exit arrow markers + optional pnl text ---
        ctx.save();
        ctx.setLineDash([]);
        ctx.fillStyle = BACKTEST_TRADE_COLOR;
        ctx.strokeStyle = "rgba(15,15,15,0.9)";
        ctx.lineWidth = 1.25 * hpr;
        ctx.lineJoin = "round";
        const h = 6 * hpr;
        const drawText = visibleCount <= MAX_TEXT_LABELS;
        if (drawText) {
          ctx.font = `600 ${12 * vpr}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
        }
        for (let i = startIdx; i < n; i++) {
          if (et[i] > to) break;
          if (xt[i] < from) continue;
          const x1 = ts.timeToCoordinate(et[i] as UTCTimestamp);
          const x2 = ts.timeToCoordinate(xt[i] as UTCTimestamp);
          const y1 = s.priceToCoordinate(ep[i]);
          const y2 = s.priceToCoordinate(xp[i]);
          if (x1 === null || x2 === null || y1 === null || y2 === null)
            continue;
          const isLong = side[i] === 0;
          const ex1 = x1 * hpr,
            ey1 = y1 * vpr;
          const ex2 = x2 * hpr,
            ey2 = y2 * vpr;
          drawStandardArrow(ctx, ex1, ey1, h, isLong); // entry: up for long, down for short
          drawStandardArrow(ctx, ex2, ey2, h, !isLong); // exit: opposite
          if (drawText) {
            const v = pnl[i];
            const gain =
              v >= 0 ? "+$" + v.toFixed(2) : "-$" + Math.abs(v).toFixed(2);
            const qtyStr = Number.isInteger(qty[i])
              ? qty[i].toString()
              : qty[i].toFixed(2).replace(/\.?0+$/, "");
            const label = `${qtyStr}  ${gain}`;
            ctx.strokeText(label, ex1, ey1 - h - 2 * vpr);
            ctx.fillText(label, ex1, ey1 - h - 2 * vpr);
          }
        }
        ctx.restore();
      },
    );
  }
}

class TradeLinesView implements IPrimitivePaneView {
  primitive: TradeLinesPrimitive;
  constructor(primitive: TradeLinesPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new TradeLinesRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    );
  }
  zOrder() {
    return "normal" as const;
  }
}

export class TradeLinesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new TradeLinesView(this);

  et: Uint32Array | null = null;
  xt: Uint32Array | null = null;
  ep: Float32Array | null = null;
  xp: Float32Array | null = null;
  side: Uint8Array | null = null; // 0 = long, 1 = short
  pnl: Float32Array | null = null;
  qty: Float32Array | null = null;

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
  }
  updateAllViews() {}
  paneViews() {
    return [this._view];
  }

  getSeries() {
    return this._series;
  }
  getChart() {
    return this._chart;
  }

  setTrades(trades: Trade[], tfSeconds = 60) {
    const n = trades.length;
    if (n === 0) {
      this.et = this.xt = null;
      this.ep = this.xp = null;
      this.side = null;
      this.pnl = this.qty = null;
      this._requestUpdate?.();
      return;
    }
    const sorted = trades
      .slice()
      .sort((a, b) => (a.et as number) - (b.et as number));
    const et = new Uint32Array(n),
      xt = new Uint32Array(n);
    const ep = new Float32Array(n),
      xp = new Float32Array(n);
    const side = new Uint8Array(n);
    const pnl = new Float32Array(n),
      qty = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const t = sorted[i];
      // Snap timestamps to the active timeframe bucket so timeToCoordinate
      // can resolve them on any timeframe (not just 1m).
      et[i] = Math.floor((t.et as number) / tfSeconds) * tfSeconds;
      xt[i] = Math.floor((t.xt as number) / tfSeconds) * tfSeconds;
      ep[i] = t.ep;
      xp[i] = t.xp;
      side[i] = t.side === "long" ? 0 : 1;
      pnl[i] = t.pnl;
      qty[i] = t.qty;
    }
    this.et = et;
    this.xt = xt;
    this.ep = ep;
    this.xp = xp;
    this.side = side;
    this.pnl = pnl;
    this.qty = qty;
    this._requestUpdate?.();
  }
}

function drawStandardArrow(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  h: number,
  up: boolean,
) {
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
  ctx.stroke();
}

class ActivePositionsRenderer implements IPrimitivePaneRenderer {
  primitive: ActivePositionsPrimitive;
  series: ISeriesApi<"Candlestick"> | null;
  chart: IChartApiBase<Time> | null;

  constructor(
    primitive: ActivePositionsPrimitive,
    series: ISeriesApi<"Candlestick"> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive;
    this.series = series;
    this.chart = chart;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const s = this.series,
      ch = this.chart;
    if (!s || !ch || !p.positions || p.positions.length === 0) return;

    const ts = ch.timeScale();
    const range = ts.getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;

    target.useBitmapCoordinateSpace(
      ({
        context: ctx,
        horizontalPixelRatio: hpr,
        verticalPixelRatio: vpr,
      }) => {
        ctx.save();
        ctx.setLineDash([]);
        const h = 6 * hpr;

        ctx.font = `600 ${12 * vpr}px sans-serif`;
        ctx.textAlign = "center";
        ctx.strokeStyle = "rgba(15,15,15,0.9)";
        ctx.lineWidth = 1.25 * hpr;
        ctx.lineJoin = "round";

        for (const pos of p.positions) {
          if (!pos.time || pos.time < from || pos.time > to) continue;

          const x = ts.timeToCoordinate(pos.time as UTCTimestamp);
          const y = s.priceToCoordinate(pos.price);
          if (x === null || y === null) continue;

          const cx = x * hpr,
            cy = y * vpr;

          // Draw the standard arrow pointing to the exact price
          ctx.fillStyle = LIVE_TRADE_COLOR;
          drawStandardArrow(ctx, cx, cy, h, pos.isLong);

          // Draw the text (e.g. "Buy 0.01 +$10") at the exact price
          const sideWord = pos.isLong ? "Buy" : "Sell";
          const gainSign = pos.profit >= 0 ? "+" : "-";
          const absProfitStr = Math.abs(pos.profit)
            .toFixed(2)
            .replace(/\.00$/, "");
          const formattedProfit = `${gainSign}$${absProfitStr}`;
          const text = `${sideWord} ${pos.volume.toFixed(2)} ${formattedProfit}`;

          // Draw text slightly above/below the triangle depending on type
          ctx.fillStyle = LIVE_TRADE_COLOR;
          if (pos.isLong) {
            ctx.textBaseline = "bottom";
            ctx.strokeText(text, cx, cy - h - 2 * vpr);
            ctx.fillText(text, cx, cy - h - 2 * vpr);
          } else {
            ctx.textBaseline = "top";
            ctx.strokeText(text, cx, cy + h + 2 * vpr);
            ctx.fillText(text, cx, cy + h + 2 * vpr);
          }
        }
        ctx.restore();
      },
    );
  }
}

class ActivePositionsView implements IPrimitivePaneView {
  primitive: ActivePositionsPrimitive;
  constructor(primitive: ActivePositionsPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new ActivePositionsRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    );
  }
  zOrder() {
    return "normal" as const;
  }
}

export interface ActivePosInfo {
  time: number;
  price: number;
  volume: number;
  profit: number;
  isLong: boolean;
  strategy: string;
}

export class ActivePositionsPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new ActivePositionsView(this);

  positions: ActivePosInfo[] = [];

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
  }
  updateAllViews() {}
  paneViews() {
    return [this._view];
  }

  getSeries() {
    return this._series;
  }
  getChart() {
    return this._chart;
  }

  setPositions(positions: ActivePosInfo[]) {
    this.positions = positions;
    this._requestUpdate?.();
  }
}

class HistoricalTradesRenderer implements IPrimitivePaneRenderer {
  primitive: HistoricalTradesPrimitive;
  series: ISeriesApi<"Candlestick"> | null;
  chart: IChartApiBase<Time> | null;

  constructor(
    primitive: HistoricalTradesPrimitive,
    series: ISeriesApi<"Candlestick"> | null,
    chart: IChartApiBase<Time> | null,
  ) {
    this.primitive = primitive;
    this.series = series;
    this.chart = chart;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const s = this.series,
      ch = this.chart;
    if (!s || !ch || !p.trades || p.trades.length === 0) return;

    const ts = ch.timeScale();
    const range = ts.getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;

    target.useBitmapCoordinateSpace(
      ({
        context: ctx,
        horizontalPixelRatio: hpr,
        verticalPixelRatio: vpr,
      }) => {
        // 1. Draw dashed connection lines
        ctx.save();
        ctx.lineWidth = 1.5 * hpr;
        ctx.setLineDash([4 * hpr, 4 * hpr]);
        ctx.strokeStyle = "rgba(34,211,238,0.65)";
        ctx.beginPath();
        for (const t of p.trades) {
          if (t.et > to || t.xt < from) continue;
          const x1 = ts.timeToCoordinate(t.et as UTCTimestamp);
          const x2 = ts.timeToCoordinate(t.xt as UTCTimestamp);
          const y1 = s.priceToCoordinate(t.ep);
          const y2 = s.priceToCoordinate(t.xp);
          if (x1 === null || x2 === null || y1 === null || y2 === null)
            continue;
          ctx.moveTo(x1 * hpr, y1 * vpr);
          ctx.lineTo(x2 * hpr, y2 * vpr);
        }
        ctx.stroke();
        ctx.restore();

        // 2. Draw standard entry and exit arrows in the live-trade accent.
        ctx.save();
        ctx.setLineDash([]);
        ctx.fillStyle = LIVE_TRADE_COLOR;
        ctx.strokeStyle = "rgba(15,15,15,0.9)";
        ctx.lineWidth = 1.25 * hpr;
        ctx.lineJoin = "round";
        const h = 6 * hpr;
        for (const t of p.trades) {
          if (t.et > to || t.xt < from) continue;
          const x1 = ts.timeToCoordinate(t.et as UTCTimestamp);
          const x2 = ts.timeToCoordinate(t.xt as UTCTimestamp);
          const y1 = s.priceToCoordinate(t.ep);
          const y2 = s.priceToCoordinate(t.xp);
          if (x1 === null || x2 === null || y1 === null || y2 === null)
            continue;

          // Entry arrow (up for long, down for short)
          drawStandardArrow(ctx, x1 * hpr, y1 * vpr, h, t.isLong);

          // Exit arrow (down for long, up for short)
          drawStandardArrow(ctx, x2 * hpr, y2 * vpr, h, !t.isLong);
        }
        ctx.restore();
      },
    );
  }
}

class HistoricalTradesView implements IPrimitivePaneView {
  primitive: HistoricalTradesPrimitive;
  constructor(primitive: HistoricalTradesPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new HistoricalTradesRenderer(
      this.primitive,
      this.primitive.getSeries(),
      this.primitive.getChart(),
    );
  }
  zOrder() {
    return "normal" as const;
  }
}

export interface HistoricalTradeInfo {
  et: number;
  xt: number;
  ep: number;
  xp: number;
  isLong: boolean;
  strategy: string;
}

export class HistoricalTradesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new HistoricalTradesView(this);

  trades: HistoricalTradeInfo[] = [];

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
  }
  updateAllViews() {}
  paneViews() {
    return [this._view];
  }

  getSeries() {
    return this._series;
  }
  getChart() {
    return this._chart;
  }

  setTrades(trades: HistoricalTradeInfo[]) {
    this.trades = trades;
    this._requestUpdate?.();
  }
}

export interface SessionVolumeProfileRow {
  price: number;
  volume: number;
  inValueArea: boolean;
}

export interface SessionVolumeProfile {
  startTime: number;
  endTime: number;
  rows: SessionVolumeProfileRow[];
  pocPrice: number;
  maxVolume: number;
}

const PROFILE_VALUE_AREA_FRACTION = 0.4;

interface ProfileAccumulator {
  startTime: number;
  endTime: number;
  volumes: Map<number, number>;
}

function profileDayKey(time: number) {
  // March timestamps are ET wall-clock values stored as fake UTC, so an epoch
  // day boundary here is the displayed market-day boundary. Keep one profile
  // for that entire day; do not split it at RTH open or close.
  return Math.floor(time / 86400);
}

export function buildSessionVolumeProfiles(
  candles: Bar[],
  tickSize: number,
  _tfSeconds: number,
): SessionVolumeProfile[] {
  if (candles.length === 0 || tickSize <= 0) return [];

  const sessions = new Map<number, ProfileAccumulator>();
  for (const candle of candles) {
    const time = candle.time as number;
    const volume = candle.volume ?? 0;
    if (!Number.isFinite(time) || !Number.isFinite(volume) || volume <= 0)
      continue;

    const key = profileDayKey(time);
    let session = sessions.get(key);
    if (!session) {
      session = { startTime: time, endTime: time, volumes: new Map() };
      sessions.set(key, session);
    }
    session.endTime = time;

    // OHLCV bars do not contain trade-at-price detail. Distribute each bar's
    // volume uniformly over every tick row it touched. This produces the dense,
    // continuous session silhouette expected from a volume profile while
    // remaining a frontend-only approximation.
    const lowBin = Math.round(candle.low / tickSize);
    const highBin = Math.round(candle.high / tickSize);
    if (!Number.isFinite(lowBin) || !Number.isFinite(highBin)) continue;
    const firstBin = Math.min(lowBin, highBin);
    const lastBin = Math.max(lowBin, highBin);
    const volumePerBin = volume / (lastBin - firstBin + 1);
    for (let bin = firstBin; bin <= lastBin; bin++) {
      session.volumes.set(bin, (session.volumes.get(bin) ?? 0) + volumePerBin);
    }
  }

  const profiles: SessionVolumeProfile[] = [];
  for (const session of sessions.values()) {
    const entries = [...session.volumes.entries()].sort((a, b) => a[0] - b[0]);
    if (entries.length === 0) continue;
    const totalVolume = entries.reduce((sum, [, volume]) => sum + volume, 0);
    const ranked = entries.slice().sort((a, b) => b[1] - a[1]);
    const [pocBin, maxVolume] = ranked[0];
    const pocIndex = entries.findIndex(([bin]) => bin === pocBin);
    const valueAreaBins = new Set<number>([pocBin]);
    let valueAreaVolume = maxVolume;
    let lowerIndex = pocIndex - 1;
    let upperIndex = pocIndex + 1;
    // Build a contiguous 40% value area outwards from POC, always taking the
    // higher-volume neighboring row first.
    while (
      valueAreaVolume < totalVolume * PROFILE_VALUE_AREA_FRACTION &&
      (lowerIndex >= 0 || upperIndex < entries.length)
    ) {
      const lowerVolume = lowerIndex >= 0 ? entries[lowerIndex][1] : -1;
      const upperVolume =
        upperIndex < entries.length ? entries[upperIndex][1] : -1;
      const nextIndex = upperVolume > lowerVolume ? upperIndex++ : lowerIndex--;
      const [bin, volume] = entries[nextIndex];
      valueAreaBins.add(bin);
      valueAreaVolume += volume;
    }

    profiles.push({
      startTime: session.startTime,
      endTime: session.endTime,
      pocPrice: pocBin * tickSize,
      maxVolume,
      rows: entries.map(([bin, volume]) => ({
        price: bin * tickSize,
        volume,
        inValueArea: valueAreaBins.has(bin),
      })),
    });
  }
  return profiles.sort((a, b) => a.startTime - b.startTime);
}

class SessionVolumeProfileRenderer implements IPrimitivePaneRenderer {
  private primitive: SessionVolumeProfilePrimitive;
  constructor(primitive: SessionVolumeProfilePrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const series = p.getSeries();
    const chart = p.getChart();
    if (!series || !chart || p.profiles.length === 0) return;

    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;
    const barSpacing = timeScale.options().barSpacing;

    target.useBitmapCoordinateSpace(
      ({
        context: ctx,
        horizontalPixelRatio: hpr,
        verticalPixelRatio: vpr,
      }) => {
        ctx.save();
        ctx.setLineDash([]);

        for (const profile of p.profiles) {
          if (profile.endTime < from || profile.startTime > to) continue;
          const startX = timeScale.timeToCoordinate(
            profile.startTime as UTCTimestamp,
          );
          const endX = timeScale.timeToCoordinate(
            profile.endTime as UTCTimestamp,
          );
          if (startX === null || endX === null || profile.maxVolume <= 0)
            continue;

          const firstRowY = series.priceToCoordinate(profile.rows[0].price);
          const lastRowY = series.priceToCoordinate(
            profile.rows[profile.rows.length - 1].price,
          );
          if (firstRowY === null || lastRowY === null) continue;
          // Tie width to the visible price-range height so the complete profile
          // has the compact, near-square footprint of a conventional SVP. Cap it
          // at the session's own on-screen width so compressed charts never let a
          // profile spill into the next session.
          const profileHeight = Math.abs(lastRowY - firstRowY) + barSpacing;
          const sessionWidth = Math.max(barSpacing, endX - startX + barSpacing);
          const profileWidth = Math.max(
            3,
            Math.min(320, profileHeight * 0.9, sessionWidth),
          ) * 0.75;
          const left = (startX - barSpacing * 0.45) * hpr;
          const sessionKey = Math.floor(profile.startTime / 86400);
          const sessionMap = p.sessionDeltas.get(sessionKey);

          for (const row of profile.rows) {
            const centerY = series.priceToCoordinate(row.price);
            const nextY = series.priceToCoordinate(row.price + p.tickSize);
            if (centerY === null || nextY === null) continue;
            const rowHeight = Math.max(1, Math.abs(nextY - centerY) * vpr);
            const width = Math.max(
              1,
              profileWidth * (row.volume / profile.maxVolume) * hpr,
            );
            const right = left + width;
            const top = centerY * vpr - rowHeight / 2;

            ctx.fillStyle = row.inValueArea
              ? "rgba(255,255,255,0.32)"
              : "rgba(255,255,255,0.10)";
            ctx.fillRect(left, top, width, Math.max(1, rowHeight - vpr * 0.5));

            const bin = Math.round(row.price / p.tickSize);
            const delta = sessionMap ? (sessionMap.get(bin) ?? 0) : 0;
            if (delta !== 0) {
              const deltaWidth = Math.max(
                1,
                profileWidth * (Math.abs(delta) / profile.maxVolume) * hpr * 5.0,
              );
              ctx.fillStyle =
                delta > 0 ? "rgba(8, 153, 129, 0.35)" : "rgba(242, 54, 69, 0.35)";
              ctx.fillRect(
                left - deltaWidth,
                top,
                deltaWidth,
                Math.max(1, rowHeight - vpr * 0.5),
              );
            }

            if (row.price === profile.pocPrice) {
              const inset = Math.min(2 * hpr, width * 0.08);
              ctx.beginPath();
              ctx.moveTo(left + inset, centerY * vpr);
              ctx.lineTo(right - inset, centerY * vpr);
              ctx.strokeStyle = "rgba(255,255,255,0.95)";
              ctx.lineWidth = Math.max(1, vpr);
              ctx.stroke();
            }
          }
        }
        ctx.restore();
      },
    );
  }
}

class SessionVolumeProfileView implements IPrimitivePaneView {
  private primitive: SessionVolumeProfilePrimitive;
  constructor(primitive: SessionVolumeProfilePrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new SessionVolumeProfileRenderer(this.primitive);
  }
  zOrder() {
    return "top" as const;
  }
}

export class SessionVolumeProfilePrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new SessionVolumeProfileView(this);
  profiles: SessionVolumeProfile[] = [];
  tickSize = 0.25;
  symbol = "";
  sessionDeltas = new Map<number, Map<number, number>>();
  loadedHistoryDays = new Set<number>();

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
    this.sessionDeltas.clear();
    this.loadedHistoryDays.clear();
  }
  updateAllViews() {}
  paneViews() {
    return [this._view];
  }
  getSeries() {
    return this._series;
  }
  getChart() {
    return this._chart;
  }

  setData(profiles: SessionVolumeProfile[]) {
    this.profiles = profiles;
    if (profiles.length === 0) {
      this.sessionDeltas.clear();
      this.loadedHistoryDays.clear();
    }
    this._requestUpdate?.();
  }

  setCandles(
    candles: Bar[],
    tickSize: number,
    tfSeconds: number,
    symbol?: string,
  ) {
    const sym = symbol || "";
    if (sym !== this.symbol || tickSize !== this.tickSize) {
      this.sessionDeltas.clear();
      this.loadedHistoryDays.clear();
    }
    this.tickSize = tickSize;
    this.symbol = sym;
    this.setData(buildSessionVolumeProfiles(candles, tickSize, tfSeconds));
  }

  setHistoricalDeltas(bins: { day: number; price_bin: number; delta: number }[]) {
    this.loadedHistoryDays.clear();
    for (const bin of bins) {
      const sessionKey = Math.floor(bin.day / 86400);
      if (!this.loadedHistoryDays.has(sessionKey)) {
        this.sessionDeltas.delete(sessionKey);
        this.loadedHistoryDays.add(sessionKey);
      }
      let sessionMap = this.sessionDeltas.get(sessionKey);
      if (!sessionMap) {
        sessionMap = new Map<number, number>();
        this.sessionDeltas.set(sessionKey, sessionMap);
      }
      const priceBinKey = Math.round(bin.price_bin / this.tickSize);
      sessionMap.set(priceBinKey, (sessionMap.get(priceBinKey) ?? 0) + bin.delta);
    }
    this._requestUpdate?.();
  }

  addLiveTick(tsSecs: number, price: number, size: number, side: string) {
    const sideUpper = side.toUpperCase();
    const signedSize =
      sideUpper === "BUY" ? size : sideUpper === "SELL" ? -size : 0;
    if (signedSize === 0) return;

    const sessionKey = Math.floor(tsSecs / 86400);
    const bin = Math.round(price / this.tickSize);

    let sessionMap = this.sessionDeltas.get(sessionKey);
    if (!sessionMap) {
      sessionMap = new Map<number, number>();
      this.sessionDeltas.set(sessionKey, sessionMap);
    }
    sessionMap.set(bin, (sessionMap.get(bin) ?? 0) + signedSize);
    this._requestUpdate?.();
  }
}

export interface VolumeDeltaBubble {
  time: number;
  price: number;
  delta: number;
  below: boolean;
}

function deltaLabel(delta: number) {
  const value = Math.abs(delta);
  if (value >= 1_000_000)
    return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 1_000)
    return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return Number.isInteger(value)
    ? value.toLocaleString("en-US")
    : value.toFixed(1).replace(/\.0$/, "");
}

class VolumeDeltaBubblesRenderer implements IPrimitivePaneRenderer {
  private primitive: VolumeDeltaBubblesPrimitive;
  constructor(primitive: VolumeDeltaBubblesPrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const series = p.getSeries();
    const chart = p.getChart();
    if (!series || !chart || p.points.length === 0) return;

    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;
    const visiblePoints = p.points.filter(
      (point) => point.time >= from && point.time <= to,
    );
    if (visiblePoints.length === 0) return;
    const magnitudes = visiblePoints
      .map((point) => Math.abs(point.delta))
      .sort((a, b) => a - b);
    // Cap scaling at the visible 90th percentile so one extreme print does not
    // make every other bubble microscopic.
    const scaleMax = magnitudes[Math.floor((magnitudes.length - 1) * 0.9)] || 1;

    target.useBitmapCoordinateSpace(
      ({
        context: ctx,
        horizontalPixelRatio: hpr,
        verticalPixelRatio: vpr,
      }) => {
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";

        for (const point of visiblePoints) {
          const x = timeScale.timeToCoordinate(point.time as UTCTimestamp);
          const candleY = series.priceToCoordinate(point.price);
          if (x === null || candleY === null) continue;

          const text = deltaLabel(point.delta);
          const strength = Math.min(
            1,
            Math.sqrt(Math.abs(point.delta) / scaleMax),
          );
          const pixelRatio = Math.max(hpr, vpr);
          const radius = (7 + 17 * strength) * pixelRatio;
          const cx = x * hpr;
          const candleEdge = candleY * vpr;
          const cy =
            candleEdge + (point.below ? radius * 0.72 : -radius * 0.72);
          const rgb = point.delta > 0 ? "8,153,129" : "242,54,69";
          const fillOpacity = 0.08 + 0.42 * strength;
          const strokeOpacity = 0.25 + 0.55 * strength;

          ctx.beginPath();
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${rgb},${fillOpacity})`;
          ctx.fill();
          ctx.strokeStyle = `rgba(${rgb},${strokeOpacity})`;
          ctx.lineWidth = 1.5 * Math.max(hpr, vpr);
          ctx.stroke();

          ctx.font = `600 ${12 * vpr}px sans-serif`;
          ctx.fillStyle = "#ffffff";
          ctx.textBaseline = point.delta > 0 ? "top" : "bottom";
          const labelY =
            point.delta > 0 ? cy + radius + 4 * vpr : cy - radius - 4 * vpr;
          ctx.fillText(text, cx, labelY);
        }
        ctx.restore();
      },
    );
  }
}

class VolumeDeltaBubblesView implements IPrimitivePaneView {
  private primitive: VolumeDeltaBubblesPrimitive;
  constructor(primitive: VolumeDeltaBubblesPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new VolumeDeltaBubblesRenderer(this.primitive);
  }
  zOrder() {
    return "top" as const;
  }
}

export class VolumeDeltaBubblesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new VolumeDeltaBubblesView(this);
  points: VolumeDeltaBubble[] = [];

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as ISeriesApi<"Candlestick">;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
  }
  updateAllViews() {}
  paneViews() {
    return [this._view];
  }
  getSeries() {
    return this._series;
  }
  getChart() {
    return this._chart;
  }

  setData(points: VolumeDeltaBubble[]) {
    this.points = points.slice().sort((a, b) => a.time - b.time);
    this._requestUpdate?.();
  }

  setCandle(
    time: number,
    open: number,
    high: number,
    low: number,
    close: number,
    delta: number,
  ) {
    const index = this.points.findIndex((point) => point.time === time);
    const positiveDivergence = close < open && delta > 0;
    const negativeDivergence = close > open && delta < 0;
    if (!positiveDivergence && !negativeDivergence) {
      if (index >= 0) this.points.splice(index, 1);
      this._requestUpdate?.();
      return;
    }

    const point: VolumeDeltaBubble = {
      time,
      price: positiveDivergence ? low : high,
      delta,
      below: positiveDivergence,
    };
    if (index >= 0) this.points[index] = point;
    else {
      this.points.push(point);
      this.points.sort((a, b) => a.time - b.time);
    }
    this._requestUpdate?.();
  }
}

// ---------------------------------------------------------------------------
// Tick Bubbles — Bookmap-style aggregated volume bubbles
// ---------------------------------------------------------------------------

export interface TickBubbleRaw {
  time: number;   // seconds
  price: number;
  size: number;
  isBuy: boolean;
}

class TickBubblesRenderer implements IPrimitivePaneRenderer {
  private primitive: TickBubblesPrimitive;
  constructor(primitive: TickBubblesPrimitive) {
    this.primitive = primitive;
  }

  draw(target: CanvasRenderingTarget2D) {
    const p = this.primitive;
    const series = p.getSeries();
    const chart = p.getChart();
    const ticks = p.ticks;
    if (!series || !chart || ticks.length === 0) return;

    const range = chart.timeScale().getVisibleRange();
    if (!range) return;
    const from = range.from as number;
    const to = range.to as number;

    target.useBitmapCoordinateSpace(({
      context: ctx,
      horizontalPixelRatio: hpr,
      verticalPixelRatio: vpr,
    }) => {
      const canvasWidth = ctx.canvas.width / hpr;
      const visibleSeconds = Math.max(1, to - from);

      // Time bucket: each bucket gets ~20 CSS px of horizontal space.
      // Allow time buckets down to 0.01 seconds for high zoom resolution.
      const bucketsAcross = Math.max(1, Math.floor(canvasWidth / 20));
      const timeBucketSize = Math.max(0.01, visibleSeconds / bucketsAcross);

      // Price bucket: use the symbol tick size (0.25 for NQ/ES).

      // Binary search for the start of visible ticks.
      let lo = 0, hi = ticks.length;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (ticks[mid].time < from) lo = mid + 1;
        else hi = mid;
      }

      // Aggregate ticks into time buckets.
      const buckets = new Map<string, { buyVol: number; sellVol: number; time: number; sumPriceVol: number; totalVol: number }>();
      for (let i = lo; i < ticks.length; i++) {
        const tick = ticks[i];
        if (tick.time > to) break;
        const tb = Math.floor(tick.time / timeBucketSize) * timeBucketSize;
        const key = `${tb}`;
        let bucket = buckets.get(key);
        if (!bucket) {
          bucket = { buyVol: 0, sellVol: 0, time: tb + timeBucketSize / 2, sumPriceVol: 0, totalVol: 0 };
          buckets.set(key, bucket);
        }
        if (tick.isBuy) bucket.buyVol += tick.size;
        else bucket.sellVol += tick.size;
        bucket.sumPriceVol += tick.price * tick.size;
        bucket.totalVol += tick.size;
      }

      if (buckets.size === 0) return;

      // Find the max volume across visible buckets for radius scaling.
      let maxVol = 0;
      for (const b of buckets.values()) {
        if (b.totalVol > maxVol) maxVol = b.totalVol;
      }
      if (maxVol <= 0) return;

      const ts = chart.timeScale();
      const pixelRatio = Math.max(hpr, vpr);
      // Radius range: smallest bubble 3px, largest 25px (CSS) to allow bigger bubbles as requested.
      const minR = 3 * pixelRatio;
      const maxR = 25 * pixelRatio;

      ctx.save();
      for (const b of buckets.values()) {
        if (b.totalVol <= 0) continue;

        // Sub-second coordinate interpolation
        const tFloat = b.time;
        const t1 = Math.floor(tFloat);
        const t2 = t1 + 1;
        const x1 = ts.timeToCoordinate(t1 as UTCTimestamp);
        const x2 = ts.timeToCoordinate(t2 as UTCTimestamp);
        
        let x: number | null = null;
        if (x1 !== null && x2 !== null) {
          x = x1 + (x2 - x1) * (tFloat - t1);
        } else if (x1 !== null) {
          x = x1;
        } else if (x2 !== null) {
          x = x2;
        }

        // Volume-weighted average price for y-coordinate
        const avgPrice = b.sumPriceVol / b.totalVol;
        const y = series.priceToCoordinate(avgPrice);
        if (x === null || y === null) continue;

        const strength = Math.sqrt(b.totalVol / maxVol);
        const radius = minR + (maxR - minR) * strength;
        const isBuyWinner = b.buyVol >= b.sellVol;

        const cx = x * hpr;
        const cy = y * vpr;

        // 3D Spherical Radial Gradient
        // Light source is offset slightly up and left (cx - r*0.25, cy - r*0.25)
        const gradient = ctx.createRadialGradient(
          cx - radius * 0.22,
          cy - radius * 0.22,
          0,
          cx,
          cy,
          radius
        );

        if (isBuyWinner) {
          gradient.addColorStop(0, "rgba(220, 255, 235, 0.95)"); // SPECULAR HIGHLIGHT
          gradient.addColorStop(0.18, "rgba(0, 230, 115, 0.85)"); // BRIGHT SHIELD
          gradient.addColorStop(0.7, "rgba(0, 130, 65, 0.75)");   // BASE COLOR
          gradient.addColorStop(1, "rgba(0, 60, 25, 0.85)");      // SHADOW BOUNDARY
        } else {
          gradient.addColorStop(0, "rgba(255, 220, 220, 0.95)"); // SPECULAR HIGHLIGHT
          gradient.addColorStop(0.18, "rgba(235, 55, 55, 0.85)"); // BRIGHT SHIELD
          gradient.addColorStop(0.7, "rgba(140, 20, 20, 0.75)");   // BASE COLOR
          gradient.addColorStop(1, "rgba(70, 10, 10, 0.85)");      // SHADOW BOUNDARY
        }

        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();

        // Thin dark border to visually separate overlapping spheres
        ctx.strokeStyle = "rgba(10, 10, 10, 0.35)";
        ctx.lineWidth = 0.5 * pixelRatio;
        ctx.stroke();
      }
      ctx.restore();
    });
  }
}

class TickBubblesView implements IPrimitivePaneView {
  private primitive: TickBubblesPrimitive;
  constructor(primitive: TickBubblesPrimitive) {
    this.primitive = primitive;
  }
  renderer(): IPrimitivePaneRenderer {
    return new TickBubblesRenderer(this.primitive);
  }
  zOrder() { return "top" as const; }
}

export class TickBubblesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null = null;
  private _chart: IChartApiBase<Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _view = new TickBubblesView(this);
  ticks: TickBubbleRaw[] = [];
  priceStep = 0.25;

  attached(p: SeriesAttachedParameter) {
    this._series = p.series as any;
    this._chart = p.chart;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this._series = null;
    this._chart = null;
  }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  getSeries() { return this._series as ISeriesApi<"Candlestick"> | null; }
  getChart() { return this._chart; }

  setPriceStep(step: number) {
    this.priceStep = Math.max(0.01, step);
  }

  setData(ticks: TickBubbleRaw[]) {
    this.ticks = ticks.slice().sort((a, b) => a.time - b.time);
    this._requestUpdate?.();
  }

  addTick(time: number, price: number, size: number, isBuy: boolean) {
    // Append in order (ticks arrive chronologically from WS).
    this.ticks.push({ time, price, size, isBuy });
    this._requestUpdate?.();
  }

  clear() {
    this.ticks = [];
    this._requestUpdate?.();
  }
}
