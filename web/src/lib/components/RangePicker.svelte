<script lang="ts">
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';

	// Glass dropdown of time ranges, like the chart page's timeframe pill.
	let { value = $bindable('all'), source }: { value?: string; source?: HTMLElement } = $props();

	export const RANGES = [
		{ id: 'day', label: 'Day' },
		{ id: 'week', label: 'Week' },
		{ id: 'month', label: 'Month' },
		{ id: 'ytd', label: 'YTD' },
		{ id: '1y', label: '1Y' },
		{ id: '3y', label: '3Y' },
		{ id: '5y', label: '5Y' },
		{ id: 'all', label: 'All time' }
	];
	const ROW = 32;
	const PILL = 36;
	const OPEN_WIDTH = 104;
	let open = $state(false);
	let pillWidth = $state(84);
	let picker: HTMLElement;
	const label = $derived(RANGES.find((r) => r.id === value)?.label ?? value);
</script>

<svelte:window
	onpointerdown={(e) => open && !picker.contains(e.target as Node) && (open = false)}
	onkeydown={(e) => e.key === 'Escape' && (open = false)}
/>

<div bind:this={picker} class="relative z-10 h-9" style="width:{pillWidth}px">
	<LiquidGlass
		{source}
		hoverable={!open}
		class="absolute top-0 left-0 overflow-hidden rounded-[18px] text-sm font-medium text-white transition-[height] duration-200 ease-out"
		style="width:{open ? Math.max(pillWidth, OPEN_WIDTH) : pillWidth}px; height:{open ? PILL + RANGES.length * ROW + 6 : PILL}px"
	>
		<button type="button" onclick={() => (open = !open)} aria-haspopup="listbox" aria-expanded={open} bind:offsetWidth={pillWidth}
			class="flex h-9 w-max cursor-pointer items-center px-4">
			{label}
		</button>
		<ul role="listbox" class="px-1 pb-1.5 transition-opacity duration-150 {open ? 'opacity-100' : 'pointer-events-none opacity-0'}">
			{#each RANGES as r (r.id)}
				<li class="flex h-8 items-center">
					<button type="button" role="option" aria-selected={r.id === value} tabindex={open ? 0 : -1} onclick={() => ((open = false), (value = r.id))}
						class="flex h-7 w-full cursor-pointer items-center rounded-lg px-3 text-left whitespace-nowrap hover:bg-white/10 {r.id === value ? 'bg-white/15' : ''}">
						{r.label}
					</button>
				</li>
			{/each}
		</ul>
	</LiquidGlass>
</div>
