// Open positions drawn on the price chart, as a lightweight-charts series
// primitive: a white arrow at the entry candle (up under it for a buy, down
// over it for a sell) and a dashed line at the entry price from that candle
// to just past the newest one, ending in the position's P&L.

import type {
	IChartApi,
	ISeriesApi,
	ISeriesPrimitive,
	IPrimitivePaneRenderer,
	IPrimitivePaneView,
	SeriesAttachedParameter,
	Time,
	UTCTimestamp
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

export type ChartPosition = {
	time: number; // entry candle's time on the chart
	price: number;
	side: 'long' | 'short';
	pnl: number;
};

type Bar = { time: number; high: number; low: number };

const LINE = 'rgba(255, 255, 255, 0.75)';
const TEXT = 'rgba(255, 255, 255, 0.9)';
const ARROW_GAP = 6; // between the candle and the arrow, px
const ARROW = 9; // arrow length, px
const PAST_LAST = 26; // how far the line runs past the newest candle, px

const money = (v: number) => `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`;

class Renderer implements IPrimitivePaneRenderer {
	constructor(private source: PositionLines) {}

	draw(target: CanvasRenderingTarget2D) {
		const { chart, series, positions, bars } = this.source;
		if (!chart || !series || positions.length === 0) return;
		const timeScale = chart.timeScale();
		const last = bars.at(-1);
		const lastX = last ? timeScale.timeToCoordinate(last.time as UTCTimestamp) : null;
		if (lastX === null) return;

		target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
			ctx.save();
			ctx.font = '11px system-ui, sans-serif';
			ctx.textBaseline = 'middle';
			for (const p of positions) {
				const y = series.priceToCoordinate(p.price);
				if (y === null) continue;
				const entryX = timeScale.timeToCoordinate(p.time as UTCTimestamp);
				const startX = entryX ?? 0; // entry before the loaded history: start at the left edge
				// Stop early enough that the P&L label still fits inside the pane.
				const label = money(p.pnl);
				const endX = Math.min(lastX + PAST_LAST, mediaSize.width - ctx.measureText(label).width - 10);

				// Dashed line at the entry price.
				ctx.strokeStyle = LINE;
				ctx.lineWidth = 1;
				ctx.setLineDash([4, 3]);
				ctx.beginPath();
				ctx.moveTo(startX, Math.round(y) + 0.5);
				ctx.lineTo(endX, Math.round(y) + 0.5);
				ctx.stroke();
				ctx.setLineDash([]);

				// P&L at the end of the line.
				ctx.fillStyle = TEXT;
				ctx.fillText(label, endX + 5, y);

				// Arrow at the entry candle.
				if (entryX === null) continue;
				const bar = bars.find((b) => b.time === p.time);
				const long = p.side === 'long';
				const anchor = bar ? series.priceToCoordinate(long ? bar.low : bar.high) : y;
				if (anchor === null) continue;
				const tip = long ? anchor + ARROW_GAP : anchor - ARROW_GAP;
				const tail = long ? tip + ARROW : tip - ARROW;
				const head = long ? 3.5 : -3.5;
				ctx.strokeStyle = TEXT;
				ctx.lineWidth = 1.5;
				ctx.lineCap = 'round';
				ctx.lineJoin = 'round';
				ctx.beginPath();
				ctx.moveTo(entryX, tail);
				ctx.lineTo(entryX, tip);
				ctx.moveTo(entryX - 3.5, tip + head);
				ctx.lineTo(entryX, tip);
				ctx.lineTo(entryX + 3.5, tip + head);
				ctx.stroke();
			}
			ctx.restore();
		});
	}
}

class View implements IPrimitivePaneView {
	private r: Renderer;
	constructor(source: PositionLines) {
		this.r = new Renderer(source);
	}
	zOrder() {
		return 'top' as const;
	}
	renderer() {
		return this.r;
	}
}

export class PositionLines implements ISeriesPrimitive<Time> {
	chart: IChartApi | undefined;
	series: ISeriesApi<'Candlestick'> | undefined;
	positions: ChartPosition[] = [];
	bars: Bar[] = [];
	private requestUpdate: (() => void) | undefined;
	private views = [new View(this)];

	attached(param: SeriesAttachedParameter<Time>) {
		this.chart = param.chart;
		this.series = param.series as ISeriesApi<'Candlestick'>;
		this.requestUpdate = param.requestUpdate;
	}

	detached() {
		this.chart = undefined;
		this.series = undefined;
	}

	paneViews() {
		return this.views;
	}

	set(positions: ChartPosition[], bars: Bar[]) {
		this.positions = positions;
		this.bars = bars;
		this.requestUpdate?.();
	}
}
