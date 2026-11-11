<script lang="ts">
	import { tick } from 'svelte';
	import { chartSymbol } from '#lib/symbols.ts';
	import { strategyName, strategyWindow, titleCase } from '#lib/strategyLabel.ts';

	// Every active sleeve, at most 3 rows. When they don't fit, the rows scroll right to left forever.
	// full: the edit page's list: every row shown, no marquee, a horizontal triple dot instead of the checker.
	type Item = { strategy: string; symbol: string; oos?: { ret: number; dd: number; pf: number | null } | null };
	// pool + onadd: the plus beside the pager lists these (strategies not in the book) to add.
	let { strategies, full = false, ondelete, label, pool = [], onadd }: { pool?: Item[]; onadd?: (strategy: string) => void; label?: string; ondelete?: (strategy: string) => void; strategies: { strategy: string; symbol: string; oos?: { ret: number; dd: number; pf: number | null } | null }[]; full?: boolean } = $props();

	// Triple-dot menu: which sleeve has it open.
	let menu = $state<string | null>(null);
	const GAP = 32;
	const ROWS = 3;
	const ROW_H = 36; // two 18px lines, and the checker's height
	const ROW_GAP = 24;
	const SECONDS_PER_ITEM = 7;

	let adding = $state(false);
	const toItem = (s: Item) => {
		const tf = s.strategy.split('-')[0].match(/_(\d+[mh])$/)?.[1] ?? '';
		return {
			key: s.strategy,
			oos: s.oos ?? null,
			title: `${chartSymbol(s.symbol)}:${titleCase(strategyName(s.strategy, s.symbol))}`,
			sub: `${titleCase(strategyWindow(s.strategy) || 'all day')}${tf ? '@' + tf : ''}`
		};
	};
	const poolItems = $derived(pool.map(toItem));
	const items = $derived(
		strategies.map((s) => {
			const tf = s.strategy.split('-')[0].match(/_(\d+[mh])$/)?.[1] ?? '';
			return {
				key: s.strategy,
				oos: s.oos ?? null,
				title: `${chartSymbol(s.symbol)}:${titleCase(strategyName(s.strategy, s.symbol))}`,
				sub: `${titleCase(strategyWindow(s.strategy) || 'all day')}${tf ? '@' + tf : ''}`
			};
		})
	);

	// Items are their natural width, so whether they fit is measured: lay them out wrapped
	// in a hidden copy and count the distinct rows.
	let width = $state(0);
	let measure = $state<HTMLDivElement>();
	let fits = $state(true);
	// full: each item's wrapped row, so pages hold PAGE_ROWS rows.
	const PAGE_ROWS = 6;
	let rowOf = $state<number[]>([]);
	let pageNo = $state(0);
	$effect(() => {
		void width, items;
		tick().then(() => {
			if (!measure) return;
			const offs = [...measure.children].map((c) => (c as HTMLElement).offsetTop);
			const tops = [...new Set(offs)].sort((a, b) => a - b);
			fits = tops.length <= ROWS;
			rowOf = offs.map((o) => tops.indexOf(o));
		});
	});
	const pages = $derived(Math.max(1, Math.ceil(((rowOf.length ? Math.max(...rowOf) : 0) + 1) / PAGE_ROWS)));
	$effect(() => {
		if (pageNo > pages - 1) pageNo = pages - 1;
	});
	const pageItems = $derived(rowOf.length === items.length ? items.filter((_, i) => Math.floor(rowOf[i] / PAGE_ROWS) === pageNo) : items);
	let blockH = $state(0);
	const slant = $derived(Math.round((blockH * 10) / 34));
	// Fade along both slanted edges: gradients run perpendicular to the slant and start
	// at the edge, which sits `d` in from the box corner the gradient begins at. Both edges are
	// parallel, so one gradient fades both ends (two layers would union, not intersect).
	const FADE = 48;
	const edgeFade = $derived.by(() => {
		const theta = Math.atan2(slant, blockH || 1);
		const d = slant * Math.cos(theta);
		const deg = (theta * 180) / Math.PI;
		return `linear-gradient(${90 + deg}deg, transparent ${d}px, black ${d + FADE}px, black calc(100% - ${d + FADE}px), transparent calc(100% - ${d}px))`;
	});
	// Overflowing: deal the sleeves into 3 rows of near-equal length.
	const rows = $derived(Array.from({ length: ROWS }, (_, r) => items.filter((_, i) => Math.floor((i * ROWS) / items.length) === r)));
</script>

{#snippet stats(it: (typeof items)[number])}
	<dl class="flex gap-[14px] whitespace-nowrap">
		<div><dt class="text-[12px] leading-[18px] font-light text-white">Ret</dt><dd class="text-[14px] leading-[18px] font-medium {it.oos == null ? 'text-white' : it.oos.ret >= 0 ? 'text-blue-500' : 'text-red-400'}">{it.oos == null ? '—' : (it.oos.ret >= 0 ? '+' : '') + it.oos.ret.toFixed(2) + '%'}</dd></div>
		<div><dt class="text-[12px] leading-[18px] font-light text-white">DD</dt><dd class="text-[14px] leading-[18px] font-medium text-white">{it.oos == null ? '—' : it.oos.dd.toFixed(2) + '%'}</dd></div>
		<div><dt class="text-[12px] leading-[18px] font-light text-white">PF</dt><dd class="text-[14px] leading-[18px] font-medium text-white">{it.oos?.pf == null ? '—' : it.oos.pf.toFixed(2)}</dd></div>
	</dl>
{/snippet}

{#snippet sleeve(it: (typeof items)[number])}
	<div class="flex shrink-0 items-center gap-[10px]" title={it.key}>
		<div class="text-right whitespace-nowrap">
			<div class="text-[14px] leading-[18px] font-medium text-white">{it.title}</div>
			<div class="text-[14px] leading-[18px] font-light text-neutral-200">{it.sub}</div>
		</div>
		{#if full}
			<!-- 2026 out-of-sample, this sleeve alone -->
			{@render stats(it)}
			<div class="relative">
				<button type="button" aria-label="Strategy menu" onclick={() => (menu = menu === it.key ? null : it.key)} class="block cursor-pointer text-neutral-200 hover:text-white">
					<svg class="block h-[36px] w-[24px] shrink-0" viewBox="0 0 24 36" fill="currentColor"><circle cx="4" cy="18" r="2" /><circle cx="12" cy="18" r="2" /><circle cx="20" cy="18" r="2" /></svg>
				</button>
				{#if menu === it.key}
					<div class="fixed inset-0 z-10" onclick={() => (menu = null)} role="presentation"></div>
					<div class="absolute top-0 left-0 z-20 min-w-[140px] overflow-hidden rounded-2xl border border-white/20 bg-white/10 p-1.5 shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.25)] backdrop-blur-xl backdrop-saturate-150">
						<button type="button" onclick={() => ((menu = null), ondelete?.(it.key))}
							class="flex w-full cursor-pointer items-center rounded-xl px-3 py-2 text-left text-[14px] font-light text-red-400 hover:bg-white/15">Delete</button>
					</div>
				{/if}
			</div>
		{:else}
			<!-- 2x3 checker, as tall as the two lines -->
			<svg class="block h-[36px] w-[24px] shrink-0" viewBox="0 0 2 3" shape-rendering="crispEdges" fill="#e5e5e5">
				<rect x="0" y="0" width="1" height="1" /><rect x="1" y="1" width="1" height="1" /><rect x="0" y="2" width="1" height="1" />
			</svg>
		{/if}
	</div>
{/snippet}

<div bind:clientWidth={width} class="relative {full ? '' : 'overflow-hidden'}">
	<div bind:this={measure} aria-hidden="true" class="invisible absolute inset-x-0 top-0 flex flex-wrap" style="gap:24px {GAP}px">
		{#each items as it (it.key)}{@render sleeve(it)}{/each}
	</div>
	{#if full}
		<header class="mb-4 flex h-7 items-center gap-4">
			{#if label}<span class="text-[12px] leading-4 font-light text-white">{label}</span>{/if}
			{#if pages > 1}
				<nav class="flex items-center gap-0 text-[13px]">
					<span class="mr-3 font-light text-neutral-400">Showing {pageItems.length} of {items.length}</span>
					<button type="button" aria-label="Previous page" disabled={pageNo === 0} onclick={() => pageNo--}
						class="flex h-7 w-6 cursor-pointer items-center justify-center rounded-lg text-neutral-300 hover:bg-white/10 hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
						<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M13 8H3M7 4 3 8l4 4" /></svg>
					</button>
					<input aria-label="Page" inputmode="numeric" value={pageNo + 1}
						onchange={(e) => {
							const el = e.currentTarget;
							const n = Math.round(Number(el.value));
							if (Number.isFinite(n) && n >= 1) pageNo = Math.min(n, pages) - 1;
							el.value = String(pageNo + 1);
						}}
						onkeydown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
						style="width:{String(pages).length + 1.5}ch"
						class="mx-1.5 h-7 rounded-md bg-transparent text-center leading-none font-light text-neutral-300 tabular-nums outline-none hover:bg-white/10 focus:bg-white/10 focus:text-white" />
					<button type="button" aria-label="Next page" disabled={pageNo >= pages - 1} onclick={() => pageNo++}
						class="flex h-7 w-6 cursor-pointer items-center justify-center rounded-lg text-neutral-300 hover:bg-white/10 hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
						<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
					</button>
				</nav>
			{/if}
			{#if onadd}
				<div class="relative">
					<button type="button" onclick={() => (adding = !adding)} class="cursor-pointer text-[14px] font-medium text-white underline underline-offset-4 hover:text-neutral-300">Add</button>
					{#if adding}
						<div class="fixed inset-0 z-10" onclick={() => (adding = false)} role="presentation"></div>
						<!-- 2 columns, 5 rows tall, scrolls; fixed widths so every row lines up -->
						<div class="absolute top-full left-0 z-20 mt-2 grid w-max max-h-[272px] grid-cols-2 gap-x-1 overflow-y-auto rounded-2xl border border-white/20 bg-white/10 p-1.5 shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.25)] backdrop-blur-xl backdrop-saturate-150 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
							{#each poolItems as it (it.key)}
								<button type="button" onclick={() => onadd(it.key)} title={it.key}
									class="flex h-[52px] cursor-pointer items-center gap-[12px] rounded-xl px-3 text-left hover:bg-white/15">
									<div class="w-[170px] text-left whitespace-nowrap">
										<div class="truncate text-[14px] leading-[18px] font-medium text-white">{it.title}</div>
										<div class="truncate text-[14px] leading-[18px] font-light text-neutral-200">{it.sub}</div>
									</div>
									<dl class="flex gap-[14px] whitespace-nowrap">
										<div class="min-w-[64px]"><dt class="text-[12px] leading-[18px] font-light text-white">Ret</dt><dd class="text-[14px] leading-[18px] font-medium {it.oos == null ? 'text-white' : it.oos.ret >= 0 ? 'text-blue-500' : 'text-red-400'}">{it.oos == null ? '—' : (it.oos.ret >= 0 ? '+' : '') + it.oos.ret.toFixed(2) + '%'}</dd></div>
										<div class="min-w-[56px]"><dt class="text-[12px] leading-[18px] font-light text-white">DD</dt><dd class="text-[14px] leading-[18px] font-medium text-white">{it.oos == null ? '—' : it.oos.dd.toFixed(2) + '%'}</dd></div>
										<div class="min-w-[36px]"><dt class="text-[12px] leading-[18px] font-light text-white">PF</dt><dd class="text-[14px] leading-[18px] font-medium text-white">{it.oos?.pf == null ? '—' : it.oos.pf.toFixed(2)}</dd></div>
									</dl>
								</button>
							{:else}
								<div class="col-span-2 px-3 py-2 text-[13px] font-light whitespace-nowrap text-neutral-400">No strategies to add</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
			{#if onadd}
				<button type="button" class="cursor-pointer text-[14px] font-medium text-white underline underline-offset-4 hover:text-neutral-300">Editor</button>
			{/if}
		</header>
		<div class="flex flex-wrap" style="gap:24px {GAP}px">
			{#each pageItems as it (it.key)}{@render sleeve(it)}{/each}
		</div>
	{:else if fits}
		<div class="flex flex-wrap" style="gap:24px {GAP}px">
			{#each items as it (it.key)}{@render sleeve(it)}{/each}
		</div>
	{:else}
		<!-- One slanted crop over all three rows, at the tabs' angle (10px across per 34px down). -->
		<div bind:offsetHeight={blockH} class="flex flex-col gap-y-[24px]"
			style="clip-path: polygon({slant}px 0, 100% 0, calc(100% - {slant}px) 100%, 0 100%); mask-image: {edgeFade}; will-change: transform; isolation: isolate">
			{#each rows as row, r (r)}
				<!-- Each row starts where the slanted edge sits at its top, so nothing is cut on the first frame.
				     Two copies back to back; sliding by one copy's width loops seamlessly. -->
				<div class="marquee flex w-max"
					style="margin-left:{Math.max(0, Math.round(slant * (1 - ((ROW_H + ROW_GAP) * r) / (blockH || 1))))}px; animation-duration:{row.length * SECONDS_PER_ITEM}s">
					{#each [0, 1] as copy (copy)}
						<div class="flex" style="gap:{GAP}px; padding-right:{GAP}px" aria-hidden={copy === 1}>
							{#each row as it (it.key)}{@render sleeve(it)}{/each}
						</div>
					{/each}
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.marquee {
		animation: marquee linear infinite;
		will-change: transform;
		backface-visibility: hidden;
	}
	@keyframes marquee {
		from {
			transform: translate3d(0, 0, 0);
		}
		to {
			transform: translate3d(-50%, 0, 0);
		}
	}
</style>
