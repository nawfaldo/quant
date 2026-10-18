<script lang="ts">
	// One chart in the layout, with its own symbol and timeframe. The page
	// feeds it the live candles for its symbol and all open positions.
	import Chart from '#lib/components/Chart.svelte';
	import ChartToolbar from '#lib/components/ChartToolbar.svelte';
	import type { Bar } from '#lib/server/bars.ts';
	import type { Position } from '#lib/server/positions.ts';
	import { chartSymbol } from '#lib/symbols.ts';

	let {
		symbol = $bindable('USTEC'),
		initialMinutes = 1,
		initialBars,
		live = [],
		positions = [],
		corner = true,
		onchange
	}: {
		symbol?: string;
		initialMinutes?: number;
		initialBars?: Bar[]; // candles from the page load, so the first chart needs no fetch
		live?: Bar[];
		positions?: Position[];
			corner?: boolean; // the white corner mark at the top left
		onchange?: (symbol: string, minutes: number) => void; // saves the choice
	} = $props();

	let minutes = $state(untrackedMinutes());
	let bars = $state<Bar[]>(untrackedInitial());

	function untrackedMinutes() {
		return initialMinutes;
	}
	let chartEl = $state<HTMLElement>(); // the chart, which the toolbar's glass refracts

	function untrackedInitial() {
		return initialBars ?? [];
	}

	async function load(nextSymbol: string, m: number, refresh = true) {
		const res = await fetch(`/api/bars?symbol=${nextSymbol}&tf=${m}`);
		if (!res.ok) return;
		const next = await res.json();
		const newSymbol = nextSymbol !== symbol;
		// Before the bars, so live minutes merge into the right candles.
		symbol = nextSymbol;
		minutes = m;
		bars = next;
		if (refresh) onchange?.(nextSymbol, m);
		// A symbol new to MT5's Market Watch needs a few seconds to download its
		// recent history; reload once it has, if this chart is still showing it.
		if (newSymbol && refresh)
			setTimeout(() => symbol === nextSymbol && minutes === m && load(nextSymbol, m, false), 5000);
	}

	// A pane created without the page's bars (a new chart in the layout) loads its own.
	$effect(() => {
		if (bars.length === 0) load(symbol, minutes, false);
	});
</script>

<div class="relative h-full min-h-0 min-w-0 bg-[#121212]">
	{#if corner}
		<span class="absolute top-0 left-0 z-10 block h-2 w-2 border-t border-l border-white"></span>
	{/if}
	<div class="absolute top-3 left-3 z-10">
		<ChartToolbar
			source={chartEl}
			{symbol}
			{minutes}
			onSymbol={(s) => load(s, minutes)}
			onTimeframe={(m) => load(symbol, m)}
		/>
	</div>
	<div bind:this={chartEl} class="h-full bg-[#121212]">
		{#if bars.length > 0}
			{#key symbol}
				<Chart
					{bars}
					{minutes}
					{live}
					positions={positions.filter((p) => chartSymbol(p.symbol).startsWith(symbol))}
				/>
			{/key}
		{/if}
	</div>
</div>
