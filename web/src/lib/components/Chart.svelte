<script lang="ts">
	import { untrack } from 'svelte';
	import { CandlestickSeries, createChart, CrosshairMode, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
	import type { Bar } from '#lib/server/bars.ts';
	import { notifyGlass } from '#lib/liquidGlass.ts';
	import { PositionLines } from '#lib/positionLines.ts';

	type OpenPosition = { chartTime: number; chartPrice: number; side: 'long' | 'short'; pnl: number };

	// `bars` draws the chart; `live` (the previous and forming 1-minute
	// candles) then keeps the right edge moving, merged into `minutes`-long
	// candles to match `bars`. `positions` are this symbol's open positions,
	// drawn as an entry arrow and a dashed line with their P&L.
	let {
		bars,
		live = [],
		minutes = 1,
		positions = []
	}: { bars: Bar[]; live?: Bar[]; minutes?: number; positions?: OpenPosition[] } = $props();

	let container: HTMLDivElement;
	let series = $state<ISeriesApi<'Candlestick'>>();
	let last: Bar | undefined; // the newest candle on the chart
	let liveKey = ''; // the stream resends unchanged candles with every message
	let lines: PositionLines | undefined;

	// Redraw the position lines against the current candles, only when
	// something they show changed (the live stream resends positions 4x/s).
	let drawnKey = '';
	function drawPositions(force = false) {
		if (!lines) return;
		const step = minutes * 60;
		const shown = positions.map((p) => ({ time: Math.floor(p.chartTime / step) * step, price: p.chartPrice, side: p.side, pnl: p.pnl }));
		const key = JSON.stringify(shown);
		if (!force && key === drawnKey) return;
		drawnKey = key;
		lines.set(shown, last && last.time !== bars.at(-1)?.time ? [...bars, last] : bars);
		repainted();
	}

	// Glass over the chart redraws only when told the chart repainted.
	const repainted = () => notifyGlass(container);

	$effect(() => {
		const chart = createChart(container, {
			autoSize: true,
			layout: { background: { color: '#121212' }, textColor: '#a3a3a3', attributionLogo: false },
			grid: { vertLines: { color: '#1f1f1f' }, horzLines: { color: '#1f1f1f' } },
			timeScale: { timeVisible: true, borderVisible: false },
			rightPriceScale: { borderVisible: false },
			// Follow the mouse freely instead of snapping to the candle's close.
			crosshair: { mode: CrosshairMode.Normal }
		});
		// Glass look: one translucent white for every candle, up or down, with a
		// brighter rim so the background shows through.
		const body = 'rgba(255, 255, 255, 0.18)';
		const rim = 'rgba(255, 255, 255, 0.4)';
		const wick = 'rgba(255, 255, 255, 0.3)';
		const s = chart.addSeries(CandlestickSeries, {
			upColor: body,
			downColor: body,
			borderUpColor: rim,
			borderDownColor: rim,
			wickUpColor: wick,
			wickDownColor: wick,
			lastValueVisible: false,
			priceLineVisible: false
		});
		// Price decimals from the data: 2 for indices, 5 for most FX pairs.
		const decimals = Math.max(
			0,
			...bars.slice(-200).map((b) => (String(b.close).split('.')[1] ?? '').length)
		);
		const precision = Math.min(decimals, 6);
		s.applyOptions({ priceFormat: { type: 'price', precision, minMove: 10 ** -precision } });
		s.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
		last = bars.at(-1) && { ...bars.at(-1)! };
		lines = new PositionLines();
		s.attachPrimitive(lines);
		drawnKey = '';
		liveKey = '';
		chart.subscribeCrosshairMove(repainted);
		chart.timeScale().subscribeVisibleLogicalRangeChange(repainted);
		series = s;
		repainted();
		return () => {
			series = undefined;
			lines = undefined;
			chart.remove();
		};
	});

	$effect(() => {
		if (!series) return;
		const key = minutes + JSON.stringify(live);
		if (key === liveKey) return;
		liveKey = key;
		const step = minutes * 60;
		const before = last?.time;
		for (const b of live) {
			const t = Math.floor(b.time / step) * step;
			// The chart only accepts updates to its newest candle or a newer one.
			if (last && t < last.time) continue;
			if (last && t === last.time) {
				// Same candle: re-applying a minute is harmless (max, min, latest close).
				last = { ...last, high: Math.max(last.high, b.high), low: Math.min(last.low, b.low), close: b.close };
				if (minutes === 1) last = { ...b };
			} else last = { ...b, time: t };
			series.update({ ...last, time: last.time as UTCTimestamp });
		}
		if (live.length) repainted();
		// A new candle moves the position lines' right end; otherwise they only
		// change with the positions, handled below. Positions stay untracked
		// here so this effect does not rerun on every stream message.
		const appended = last?.time !== before;
		untrack(() => drawPositions(appended));
	});

	$effect(() => {
		void positions;
		if (series) drawPositions();
	});
</script>

<div bind:this={container} data-glass-notify class="h-full w-full"></div>
