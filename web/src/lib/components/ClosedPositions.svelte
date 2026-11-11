<script lang="ts">
	import RangePicker from '#lib/components/RangePicker.svelte';
	import arrowIcon from '#lib/icons/arrow-up-right.svg';
	import type { ClosedPosition } from '#lib/server/accounts.ts';
	import { strategyName, strategyWindow, titleCase } from '#lib/strategyLabel.ts';

	// The account's closed trades, styled like the chart page's Open positions table.
	let { accountId }: { accountId: number } = $props();

	let rows = $state<ClosedPosition[]>([]);
	let loaded = $state(false);
	$effect(() => {
		let stale = false;
		fetch(`/api/accounts/${accountId}/closed`)
			.then((r) => r.json())
			.then((d: ClosedPosition[]) => {
				if (stale) return;
				rows = d;
				loaded = true;
			});
		return () => (stale = true);
	});

	// Range: counted back from the newest close, like the equity chart.
	let range = $state('all');
	// The glass needs something behind it to refract: this block, on the page background.
	let root = $state<HTMLElement>();
	const ts = (s: string) => Date.parse(s.replace(' ', 'T') + 'Z') / 1000;
	const inRange = $derived.by(() => {
		if (range === 'all' || rows.length === 0) return rows;
		const end = Math.max(...rows.map((r) => ts(r.closeTime)));
		const D = 86400;
		const y = new Date(end * 1000).getUTCFullYear();
		const start = ({ day: end - D, week: end - 7 * D, month: end - 30 * D, ytd: Date.UTC(y, 0, 1) / 1000, '1y': end - 365 * D, '3y': end - 3 * 365 * D, '5y': end - 5 * 365 * D } as Record<string, number>)[range] ?? -Infinity;
		return rows.filter((r) => ts(r.closeTime) >= start);
	});
	$effect(() => {
		void range;
		pageNo = 0;
	});

	// 6 rows a page.
	const PER = 6;
	let pageNo = $state(0);
	const pages = $derived(Math.max(1, Math.ceil(inRange.length / PER)));
	const shown = $derived(inRange.slice(pageNo * PER, pageNo * PER + PER));
	$effect(() => {
		void accountId;
		pageNo = 0;
	});

	const total = $derived(inRange.reduce((s, p) => s + p.pnl, 0));
	const signedMoney = (v: number) => (Math.abs(v) < 0.005 ? '+$0.00' : `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`);
	const pnlColor = (v: number) => (Math.abs(v) < 0.005 ? 'text-white' : v > 0 ? 'text-blue-400' : 'text-red-400');
	const price = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 5 });
	// "2026-11-04 22:16" -> "11-04 22:16"
	const stamp = (s: string) => s.slice(5, 16);

	const cols =
		'grid grid-cols-[16px_minmax(90px,1fr)_minmax(64px,90px)_minmax(52px,64px)_minmax(60px,72px)_minmax(76px,84px)_minmax(76px,84px)_minmax(52px,72px)_minmax(52px,72px)_minmax(28px,40px)_minmax(48px,64px)] items-center gap-x-2';
</script>

<div bind:this={root} class="w-[min(920px,100%)] bg-[#121212] text-white">
	<header class="mb-3 flex items-center gap-3 whitespace-nowrap">
		<h2 class="text-[14px] font-medium">Closed Positions</h2>
		{#if inRange.length > 0}<span class="text-sm {pnlColor(total)}">{signedMoney(total)}</span>{/if}
		<RangePicker bind:value={range} source={root} />
	</header>
	{#if inRange.length > 0}
		<div class="overflow-x-auto [scrollbar-width:thin]"><div class="min-w-[700px]">
		<div class="{cols} border border-[#535353] bg-[#323232] py-2 text-sm font-medium">
			<span></span><span>Strategy</span><span>Window</span><span>Symbol</span><span>Direction</span>
			<span>Open</span><span>Close</span><span>Entry</span><span>Exit</span><span>Lot</span><span>PnL</span>
		</div>
		<ul>
			{#each shown as p (p.id)}
				<li class="{cols} py-1.5 text-[13px] font-light text-neutral-200">
					<span class="flex w-4 justify-self-center"><span class="ml-[7.5px] block h-2 w-2 -translate-y-0.5 border-b border-l border-white"></span></span>
					<span class="truncate" title={p.strategy}>{titleCase(strategyName(p.strategy, p.symbol)) || '—'}</span>
					<span class="truncate">{titleCase(strategyWindow(p.strategy)) || '—'}</span>
					<span class="truncate">{p.symbol}</span>
					<span class="flex items-center">
						{p.side === 'long' ? 'Buy' : 'Sell'}
						<img src={arrowIcon} alt="" class="block h-[18px] w-[18px] {p.side === 'short' ? 'rotate-90' : ''}" />
					</span>
					<span>{stamp(p.openTime)}</span>
					<span>{stamp(p.closeTime)}</span>
					<span>{price(p.openPrice)}</span>
					<span>{price(p.closePrice)}</span>
					<span>{p.lot.toFixed(2)}</span>
					<span class={pnlColor(p.pnl)}>{signedMoney(p.pnl)}</span>
				</li>
			{/each}
		</ul>
		</div></div>
		{#if pages > 1}
			<nav class="flex items-center gap-0 pt-3 pb-8 text-[13px]">
				<span class="mr-3 font-light text-neutral-400">Showing {shown.length} of {inRange.length}</span>
				<button type="button" aria-label="Previous page" disabled={pageNo === 0} onclick={() => pageNo--}
					class="flex h-7 w-6 cursor-pointer items-center justify-center rounded-lg text-neutral-300 hover:bg-white/10 hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M13 8H3M7 4 3 8l4 4" /></svg>
				</button>
				<input
					aria-label="Page"
					inputmode="numeric"
					value={pageNo + 1}
					onchange={(e) => {
						const el = e.currentTarget;
						const n = Math.round(Number(el.value));
						if (Number.isFinite(n) && n >= 1) pageNo = Math.min(n, pages) - 1;
						el.value = String(pageNo + 1);
					}}
					onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
					style="width:{String(pages).length + 1.5}ch"
					class="mx-1.5 h-7 rounded-md bg-transparent text-center leading-none font-light text-neutral-300 tabular-nums outline-none hover:bg-white/10 focus:bg-white/10 focus:text-white"
				/>
				<button type="button" aria-label="Next page" disabled={pageNo >= pages - 1} onclick={() => pageNo++}
					class="flex h-7 w-6 cursor-pointer items-center justify-center rounded-lg text-neutral-300 hover:bg-white/10 hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
				</button>
			</nav>
		{:else}
			<div class="pb-8"></div>
		{/if}
	{:else if loaded}
		<div class="py-1 text-[13px] font-light text-neutral-400">No closed positions yet.</div>
	{/if}
</div>
