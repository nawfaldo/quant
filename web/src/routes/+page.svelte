<script lang="ts">
	import ChartPane from '#lib/components/ChartPane.svelte';
	import LayoutIcon from '#lib/components/LayoutIcon.svelte';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import AccountView from '#lib/components/AccountView.svelte';
	import cornerIcon from '#lib/icons/corner.png';
	import cornerHoverIcon from '#lib/icons/corner-hover.png';
	import ActiveAccounts from '#lib/components/ActiveAccounts.svelte';
	import OpenPositions from '#lib/components/OpenPositions.svelte';
	import type { Account } from '#lib/server/accounts.ts';
	import type { Bar } from '#lib/server/bars.ts';
	import type { Position } from '#lib/server/positions.ts';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { scale } from 'svelte/transition';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Starts with the server-rendered values, then follows the live stream.
	type Live = { accounts: Account[]; positions: Position[]; candles: Record<string, Bar[]>; mbps: number | null };
	let live = $state<Live | null>(null);
	const accounts = $derived(live?.accounts ?? data.accounts);
	const positions = $derived(live?.positions ?? data.positions);

	// Chart layouts: charts per row, top to bottom. 1 to 6 charts.
	const LAYOUTS = [[1], [2], [3], [2, 2], [3, 2], [3, 3]];
	let layout = $state(untrack(() => data.layout));
	let layoutOpen = $state(false);
	let layoutPicker: HTMLElement;
	let chartsEl = $state<HTMLElement>(); // all charts, which the layout picker's glass refracts

	// One symbol per chart slot; slots beyond the layout keep their symbol for
	// when the layout grows again.
	let symbols = $state(untrack(() => data.slots.map((s) => s.symbol)));

	// Choices are saved to web.db so a refresh restores them.
	const save = (body: object) =>
		fetch('/api/chart-layout', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
	const shownCount = $derived(LAYOUTS[layout].reduce((n, c) => n + c, 0));

	// Poll for every symbol on screen about once a second; restarts when that set
	// changes. Polled because the Cloudflare tunnel does not pass server-sent events.
	const POLL_MS = 1000;
	const streamKey = $derived([...new Set(symbols.slice(0, shownCount))].sort().join(','));
	$effect(() => {
		const key = streamKey;
		let stopped = false;
		let timer: ReturnType<typeof setTimeout>;
		const poll = async () => {
			try {
				const res = await fetch(`/api/live?symbols=${key}`, { cache: 'no-store' });
				if (res.ok && !stopped) live = await res.json();
			} catch {
				// offline or the server restarting: try again next tick
			}
			if (!stopped) timer = setTimeout(poll, POLL_MS);
		};
		poll();
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});
</script>

<svelte:window
	onpointerdown={(e) => layoutOpen && !layoutPicker.contains(e.target as Node) && (layoutOpen = false)}
	onkeydown={(e) => e.key === 'Escape' && (layoutOpen = false)}
/>

<main class="flex h-screen flex-col">
	<!-- The charts, laid out in rows, touching each other. -->
	<section bind:this={chartsEl} class="flex min-h-0 flex-[2] flex-col">
		{#each LAYOUTS[layout] as count, r (r)}
			<div class="flex min-h-0 flex-1">
				{#each Array(count) as _, c (c)}
					{@const slot = LAYOUTS[layout].slice(0, r).reduce((n, k) => n + k, 0) + c}
					<div class="min-w-0 flex-1">
						<ChartPane
							bind:symbol={symbols[slot]}
							corner={slot !== 0}
							initialMinutes={data.slots[slot].minutes}
							initialBars={slot === 0 ? data.bars : undefined}
							onchange={(symbol, minutes) => save({ slot, symbol, minutes })}
							live={live?.candles[symbols[slot]] ?? []}
							{positions}
						/>
					</div>
				{/each}
			</div>
		{/each}
	</section>
	<section class="relative flex min-h-0 flex-[1] bg-[#181818]">
		<!-- Display only for now: none of these buttons does anything yet. -->
		<!-- Proportions from the design: text, arrow and grid are about 10, 11 and
		     16px tall, 22px apart (16px gap + the arrow image's 6px margin). -->
		<div class="absolute top-3 right-3 z-10 flex items-center gap-4">
			<!-- Internet download speed, from a speed test every 5 minutes. -->
			<span class="text-sm leading-none font-light text-white" title="Internet download speed">
				{live?.mbps != null ? `${live.mbps.toFixed(live.mbps < 10 ? 1 : 0)} Mbps` : '— Mbps'}
			</span>
			<!-- Chart layout: the button shows the current one; the picker opens above it. -->
			<div bind:this={layoutPicker} class="relative flex">
				<button
					type="button"
					aria-label="Chart layout"
					aria-expanded={layoutOpen}
					onclick={() => (layoutOpen = !layoutOpen)}
					class="flex cursor-pointer"
				>
					<LayoutIcon rows={LAYOUTS[layout]} />
				</button>
				{#if layoutOpen}
					<!-- Opens from bigger and solidifies to size; closing dissolves back out to bigger. -->
					<div transition:scale={{ start: 1.15, duration: 70 }} class="absolute right-0 bottom-full z-30 mb-3 origin-bottom-right">
						<LiquidGlass source={chartsEl} hoverable={false} class="grid w-max grid-cols-3 gap-1 rounded-[18px] p-2">
							{#each LAYOUTS as rows, i (i)}
								<button
									type="button"
									aria-label="{rows.reduce((n, k) => n + k, 0)} charts"
									onclick={() => ((layout = i), (layoutOpen = false), save({ layout: i }))}
									class="flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg hover:bg-white/10 {i === layout ? 'bg-white/15' : ''}"
								>
									<LayoutIcon {rows} />
								</button>
							{/each}
						</LiquidGlass>
					</div>
				{/if}
			</div>
		</div>
		<!-- Both images are anchored to the corner, so the larger hover one grows out of it. -->
		<button type="button" aria-label="Resize" class="group absolute right-2 bottom-0 z-10 flex cursor-pointer items-end justify-end">
			<img src={cornerIcon} alt="" class="block h-8 w-8 group-hover:hidden" />
			<img src={cornerHoverIcon} alt="" class="hidden h-[35px] w-[35px] group-hover:block" />
		</button>
		<div class="w-1/5 min-w-64">
			<ActiveAccounts {accounts} {positions} />
		</div>
		<div class="w-1/2 min-w-0">
			<OpenPositions {positions} />
		</div>
	</section>
	{#if page.state.account}
		<!-- Shallow-routed: the chart page stays mounted underneath, so nothing reloads. -->
		<AccountView {accounts} close={() => history.back()} />
	{/if}
</main>
