<script lang="ts">
	import arrowIcon from '#lib/icons/arrow-up-right.svg';
	import filterIcon from '#lib/icons/filter.svg';
	import type { Position } from '#lib/server/positions.ts';

	let { positions }: { positions: Position[] } = $props();

	const total = $derived(positions.reduce((sum, p) => sum + p.pnl, 0));

	const signedMoney = (v: number) => {
		if (Math.abs(v) < 0.005) return '+$0.00';
		return `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`;
	};
	const totalColor = $derived.by(() => {
		if (Math.abs(total) < 0.005) return 'text-white';
		return total > 0 ? 'text-blue-400' : 'text-red-400';
	});
	const price = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 5 });
	// usdjpy_qp_ma_cross_30m on USDJPY -> "qp ma cross 30m"
	const strategyName = (p: Position) =>
		p.strategy.replace(new RegExp(`^${p.symbol}_`, 'i'), '').replaceAll('_', ' ');
	const time = (unix: number) =>
		new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

	// Corner glyph, account, strategy, symbol, direction, time, price, lot, PnL, close.
	const cols = 'grid grid-cols-[1.5rem_1fr_1.6fr_1fr_1fr_0.8fr_1fr_0.7fr_0.9fr_1.5rem] items-center gap-x-3';
</script>

<div class="flex h-full flex-col px-4 pt-4 text-white">
	<header class="mb-3 flex items-center gap-3 whitespace-nowrap">
		<h2 class="text-sm text-white font-medium">Open positions</h2>
		<button type="button" aria-label="Filter" class="flex shrink-0 cursor-pointer">
			<img src={filterIcon} alt="" class="block h-4 w-4" />
		</button>
		{#if positions.length > 0}
			<span class="text-sm {totalColor}">{signedMoney(total)}</span>
		{/if}
		<a href="/history" class="ml-auto flex shrink-0 items-center text-[13px] leading-none font-light text-neutral-300 hover:text-white">
			History
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]">
				<path d="M5 4.5h6.5V11.1" />
				<path d="M4.25 11.75 11.5 4.5" />
			</svg>
		</a>
	</header>

	{#if positions.length > 0}
		<div class="{cols} border border-[#535353] bg-[#323232] py-2 text-sm font-medium text-white">
			<span></span>
			<span>Account</span>
			<span>Strategy</span>
			<span>Symbol</span>
			<span>Direction</span>
			<span>Time</span>
			<span>Price</span>
			<span>Lot</span>
			<span>PnL</span>
			<span></span>
		</div>
	{:else}
		<div class="py-1 text-[13px] font-light text-neutral-400">No positions open right now.</div>
	{/if}

	<!-- Fills the rest of the panel and scrolls; the bottom padding lives inside the
	     scroll area so the last row scrolls fully into view instead of being cut. -->
	<ul class="min-h-0 flex-1 overflow-y-auto pb-4">
		{#each positions as p (p.ticket)}
			<li class="{cols} py-1.5 text-[13px] text-neutral-200">
				<span class="flex w-4 justify-self-center">
					<span class="ml-[7.5px] block h-2 w-2 -translate-y-0.5 border-b border-l border-white"></span>
				</span>
				<span class="max-w-full truncate font-light">{p.account}</span>
				<span class="max-w-full truncate font-light" title={p.strategy}>{strategyName(p) || '—'}</span>
				<span class="max-w-full truncate font-light">{p.symbol}</span>
				<span class="flex items-center font-light">
					{p.side === 'long' ? 'Buy' : 'Sell'}
					<img src={arrowIcon} alt="" class="block h-[18px] w-[18px] {p.side === 'short' ? 'rotate-90' : ''}" />
				</span>
				<span class="font-light">{time(p.openTime)}</span>
				<span class="font-light">{price(p.price)}</span>
				<span class="font-light">{p.lot.toFixed(2)}</span>
				<span class="text-white font-light">{signedMoney(p.pnl)}</span>
				<button type="button" aria-label="Close position" class="relative h-2.5 w-2.5 cursor-pointer text-neutral-400 hover:text-white">
					<span class="absolute top-1/2 left-0 h-px w-full -translate-y-1/2 rotate-45 bg-current"></span>
					<span class="absolute top-1/2 left-0 h-px w-full -translate-y-1/2 -rotate-45 bg-current"></span>
				</button>
			</li>
		{/each}
	</ul>
</div>
