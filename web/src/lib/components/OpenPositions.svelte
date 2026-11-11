<script lang="ts">
	import arrowIcon from '#lib/icons/arrow-up-right.svg';
	import filterIcon from '#lib/icons/filter.svg';
	import type { Position } from '#lib/server/positions.ts';
	import { strategyLabel } from '#lib/strategyLabel.ts';

	let { positions }: { positions: Position[] } = $props();

	const total = $derived(positions.reduce((sum, p) => sum + p.pnl, 0));

	const signedMoney = (v: number) => {
		if (Math.abs(v) < 0.005) return '+$0.00';
		return `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`;
	};
	// Blue for profit, red for loss, white at zero.
	const pnlColor = (v: number) => {
		if (Math.abs(v) < 0.005) return 'text-white';
		return v > 0 ? 'text-blue-400' : 'text-red-400';
	};
	const totalColor = $derived(pnlColor(total));
	const price = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 5 });
	// Tickets with a close in flight: the X is disabled until the position leaves the table.
	let closing = $state(new Set<number>());
	async function closePosition(p: Position) {
		if (closing.has(p.ticket)) return;
		if (!confirm(`Close ${p.side === 'long' ? 'buy' : 'sell'} ${p.lot.toFixed(2)} ${p.symbol} (#${p.ticket})?`)) return;
		closing = new Set([...closing, p.ticket]);
		const r = await fetch(`/api/positions/${p.ticket}/close`, { method: 'POST' }).catch(() => null);
		if (!r?.ok) alert(`Close failed: ${r ? ((await r.json().catch(() => null))?.message ?? r.status) : 'server unreachable'}`);
		closing = new Set([...closing].filter((t) => t !== p.ticket));
	}
	const time = (unix: number) =>
		new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

	// Columns: corner glyph, account, strategy (strategy-window@timeframe), symbol, direction, time, price, lot, PnL,
	// close. Each text track is at most its compact width (the table is as wide as its contents)
	// and shrinks to its minimum when the panel is narrower. 8px gaps.
	const cols =
		'grid grid-cols-[16px_minmax(64px,80px)_minmax(140px,200px)_minmax(52px,64px)_minmax(60px,72px)_minmax(36px,48px)_minmax(48px,72px)_minmax(24px,40px)_minmax(46px,64px)_16px] items-center gap-x-2';
</script>

<div class="flex h-full flex-col px-4 pt-4 text-white">
	<!-- The table's own width. -->
	<div class="flex min-h-0 flex-1 flex-col {positions.length > 0 ? 'w-fit max-w-full' : 'w-full'}">
		<header class="mb-3 flex w-full items-center gap-3 whitespace-nowrap">
			<h2 class="text-sm text-white font-medium">Open positions</h2>
			<button type="button" aria-label="Filter" class="flex shrink-0 cursor-pointer">
				<img src={filterIcon} alt="" class="block h-4 w-4" />
			</button>
			{#if positions.length > 0}
				<span class="text-sm {totalColor}">{signedMoney(total)}</span>
			{/if}
		</header>

		<div class="flex min-h-0 flex-1 flex-col overflow-x-auto overflow-y-hidden [scrollbar-width:thin]">
		{#if positions.length > 0}
			<div class="{cols} w-full min-w-max border border-[#535353] bg-[#323232] py-2 text-sm font-medium text-white">
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
		<ul class="min-h-0 min-w-max flex-1 overflow-y-auto pb-4">
			{#each positions as p (p.ticket)}
				<li class="{cols} py-1.5 text-[13px] text-neutral-200">
					<span class="flex w-4 justify-self-center">
						<span class="ml-[7.5px] block h-2 w-2 -translate-y-0.5 border-b border-l border-white"></span>
					</span>
					<span class="max-w-full truncate font-light">{p.account}</span>
					<span class="max-w-full truncate font-light" title={p.strategy}>{strategyLabel(p.strategy, p.symbol)}</span>
					<span class="max-w-full truncate font-light">{p.symbol}</span>
					<span class="flex items-center font-light">
						{p.side === 'long' ? 'Buy' : 'Sell'}
						<img src={arrowIcon} alt="" class="block h-[18px] w-[18px] {p.side === 'short' ? 'rotate-90' : ''}" />
					</span>
					<span class="font-light">{time(p.openTime)}</span>
					<span class="font-light">{price(p.price)}</span>
					<span class="font-light">{p.lot.toFixed(2)}</span>
					<!-- font-[300], not font-light: layout.css forces .font-light to grey, which hid the P&L colour. -->
					<span class="font-[300] {pnlColor(p.pnl)}">{signedMoney(p.pnl)}</span>
					<button type="button" aria-label="Close position" disabled={closing.has(p.ticket)} onclick={() => closePosition(p)} class="relative h-2.5 w-2.5 cursor-pointer text-neutral-400 hover:text-white disabled:cursor-wait disabled:opacity-40">
						<span class="absolute top-1/2 left-0 h-px w-full -translate-y-1/2 rotate-45 bg-current"></span>
						<span class="absolute top-1/2 left-0 h-px w-full -translate-y-1/2 -rotate-45 bg-current"></span>
					</button>
				</li>
			{/each}
		</ul>
		</div>
	</div>
</div>
