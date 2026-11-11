<script lang="ts">
	import Chart from '#lib/components/Chart.svelte';
	import RangePicker from '#lib/components/RangePicker.svelte';
	import type { Bar } from '#lib/server/bars.ts';

	// The account's equity as candles, with a glass range picker in the top-left like the chart page's timeframe pill.
	// `change`: percent from the range's first open to its last close, for the title.
	let { accountId, change = $bindable(null) }: { accountId: number; change?: number | null } = $props();
	$effect(() => {
		change = bars.length ? ((bars[bars.length - 1].close - bars[0].open) / bars[0].open) * 100 : null;
	});

	const STEP_MINUTES: Record<string, number> = { day: 15, week: 60, month: 240, ytd: 1440, '1y': 1440, '3y': 10080, '5y': 10080, all: 1440 };

	let range = $state('all');
	let bars = $state<Bar[]>([]);
	let loaded = $state(false);
	let chartEl = $state<HTMLElement>();

	$effect(() => {
		const r = range;
		const id = accountId;
		let stale = false;
		fetch(`/api/accounts/${id}/equity?range=${r}`)
			.then((res) => res.json())
			.then((b: Bar[]) => {
				if (stale) return;
				bars = b;
				loaded = true;
			});
		return () => (stale = true);
	});

</script>

<div class="relative h-full w-full">
	<div class="absolute top-3 left-3 z-10">
		<RangePicker bind:value={range} source={chartEl} />
	</div>
	<div bind:this={chartEl} class="h-full w-full">
		{#if bars.length > 0}
			{#key range}
				<Chart {bars} minutes={STEP_MINUTES[range]} fit />
			{/key}
		{:else if loaded}
			<div class="flex h-full items-center justify-center text-sm font-light text-neutral-500">No closed trades in this range</div>
		{/if}
	</div>
</div>
