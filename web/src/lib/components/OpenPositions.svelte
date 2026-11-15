<script lang="ts">
	import { chartSymbol } from '#lib/symbols.ts';
	import { untrack } from 'svelte';
	import searchIcon from '#lib/icons/search.png';
	import arrowIcon from '#lib/icons/arrow-up-right.svg';
	import CapsuleScroll from '#lib/components/CapsuleScroll.svelte';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import type { Account } from '#lib/server/accounts.ts';
	import type { PositionSort } from '#lib/server/webdb.ts';
	import type { Position } from '#lib/server/positions.ts';
	import { strategyLabel } from '#lib/strategyLabel.ts';

	let { positions, accounts, desktopId, initialAccountSelections, symbols, initialSymbolSelections, initialSorts }: { positions: Position[]; accounts: Account[]; desktopId: number; initialAccountSelections: Record<number, number[]>; symbols: string[]; initialSymbolSelections: Record<number, string[]>; initialSorts: Record<number, PositionSort> } = $props();
	let accountQuery = $state('');
	let symbolQuery = $state('');
	const matchingAccounts = $derived(accounts.filter((a) => a.name.toLowerCase().includes(accountQuery.trim().toLowerCase())));
	const matchingSymbols = $derived(symbols.filter((s) => s.toLowerCase().includes(symbolQuery.trim().toLowerCase())));
	let accountSelections = $state<Record<number, number[]>>(untrack(() => ({ ...initialAccountSelections })));
	let savingAccounts = $state<Record<number, boolean>>({});
	async function chooseAccount(accountId: number | null) {
		const id = desktopId;
		if (savingAccounts[id]) return;
		const previous = accountSelections[id] ?? [];
		const next = accountId === null ? [] : previous.includes(accountId)
			? previous.filter((value) => value !== accountId) : [...previous, accountId];
		accountSelections[id] = next;
		savingAccounts[id] = true;
		try {
			const response = await fetch('/api/desktop-account-filter', {
				method: 'POST', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ desktopId: id, accountIds: next })
			});
			if (!response.ok) throw new Error('Save failed');
		} catch {
			accountSelections[id] = previous;
			alert('Pilihan akun gagal disimpan. Silakan coba lagi.');
		} finally { savingAccounts[id] = false; }
	}
	const selectedAccountIds = $derived(accountSelections[desktopId] ?? []);
	const selectedAccounts = $derived(accounts.filter((a) => selectedAccountIds.includes(a.id)));
	let symbolSelections = $state<Record<number, string[]>>(untrack(() => ({ ...initialSymbolSelections })));
	let savingSymbols = $state<Record<number, boolean>>({});
	const selectedSymbols = $derived(symbolSelections[desktopId] ?? []);
	async function chooseSymbol(symbol: string | null) {
		const id = desktopId;
		if (savingSymbols[id]) return;
		const previous = symbolSelections[id] ?? [];
		const next = symbol === null ? [] : previous.includes(symbol) ? previous.filter((s) => s !== symbol) : [...previous, symbol];
		symbolSelections[id] = next;
		savingSymbols[id] = true;
		try {
			const response = await fetch('/api/desktop-symbol-filter', {
				method: 'POST', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ desktopId: id, symbols: next })
			});
			if (!response.ok) throw new Error('Save failed');
		} catch {
			symbolSelections[id] = previous;
			alert('Pilihan simbol gagal disimpan. Silakan coba lagi.');
		} finally { savingSymbols[id] = false; }
	}
	const visiblePositions = $derived(positions.filter((p) =>
		(selectedAccountIds.length === 0 || selectedAccounts.some((a) => p.account.toLowerCase() === a.name.toLowerCase() || p.account === String(a.id))) &&
		(selectedSymbols.length === 0 || selectedSymbols.some((symbol) => chartSymbol(p.symbol).startsWith(symbol)))
	));

	type SortKey = 'account' | 'strategy' | 'symbol' | 'side' | 'openTime' | 'price' | 'lot' | 'pnl';
	const sortColumns: { key: SortKey; label: string }[] = [
		{ key: 'account', label: 'Account' }, { key: 'strategy', label: 'Strategy' },
		{ key: 'symbol', label: 'Symbol' }, { key: 'side', label: 'Direction' },
		{ key: 'openTime', label: 'Time' }, { key: 'price', label: 'Price' },
		{ key: 'lot', label: 'Lot' }, { key: 'pnl', label: 'PnL' }
	];
	let sorts = $state<Record<number, PositionSort>>(untrack(() => ({ ...initialSorts })));
	const sort = $derived(sorts[desktopId] ?? null);
	let savingSorts = $state<Record<number, boolean>>({});
	async function toggleSort(key: SortKey) {
		const id = desktopId;
		if (savingSorts[id]) return;
		const previous = sorts[id] ?? null;
		const next: PositionSort = previous?.key !== key ? { key, direction: 'desc' }
			: previous.direction === 'desc' ? { key, direction: 'asc' } : null;
		sorts[id] = next;
		savingSorts[id] = true;
		try {
			const response = await fetch('/api/desktop-position-sort', {
				method: 'POST', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ desktopId: id, sorting: next })
			});
			if (!response.ok) throw new Error('Save failed');
		} catch {
			sorts[id] = previous;
			alert('Urutan gagal disimpan. Silakan coba lagi.');
		} finally { savingSorts[id] = false; }
	}
	function sortValue(p: Position, key: SortKey): string | number {
		if (key === 'strategy') return strategyLabel(p.strategy, p.symbol);
		if (key === 'side') return p.side === 'long' ? 'Buy' : 'Sell';
		return p[key];
	}
	const sortedPositions = $derived.by(() => {
		if (!sort) return visiblePositions;
		const { key, direction } = sort;
		return [...visiblePositions].sort((a, b) => {
			const av = sortValue(a, key), bv = sortValue(b, key);
			const comparison = typeof av === 'number' && typeof bv === 'number'
				? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true });
			return direction === 'desc' ? -comparison : comparison;
		});
	});

	let filterSource = $state<HTMLDivElement>();
	let filterButton: HTMLButtonElement;
	let filterPanel: HTMLDivElement;
	let filterOpen = $state(false);
	let filterLeft = $state(0);
	let filterTop = $state(0);
	let filterOrigin = $state('top left');

	function positionFilter() {
		if (!filterButton || !filterOpen) return;
		const rect = filterButton.getBoundingClientRect();
		const width = Math.min(372, window.innerWidth - 24);
		filterLeft = rect.right + 12 + width <= window.innerWidth - 12
			? rect.right + 12
			: Math.max(12, rect.left - width - 12);
		filterTop = rect.top;
		filterOrigin = filterLeft >= rect.right ? 'top left' : 'top right';
	}

	function closeFilter() {
		filterPanel?.hidePopover();
		filterOpen = false;
	}

	function toggleFilter() {
		if (filterOpen) return closeFilter();
		filterOpen = true;
		positionFilter();
		filterPanel.showPopover();
	}

	const total = $derived(visiblePositions.reduce((sum, p) => sum + p.pnl, 0));

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
	// and shrinks to its minimum when the panel is narrower (strategy truncates first). Header and rows both
	// reserve the scrollbar gutter so their columns stay aligned. 8px gaps.
	const cols =
		'grid min-w-[752px] grid-cols-[16px_minmax(80px,96px)_minmax(96px,200px)_minmax(70px,80px)_minmax(88px,96px)_minmax(54px,64px)_minmax(64px,80px)_minmax(44px,52px)_minmax(52px,72px)_16px] items-center gap-x-2';
</script>

<svelte:window onresize={positionFilter} onscroll={positionFilter} />

<div
	bind:this={filterPanel}
	id="open-positions-filter"
	popover="auto"
	onbeforetoggle={(event) => { filterOpen = event.newState === 'open'; }}
	role="dialog"
	aria-label="Position filters"
	class="filter-popover"
	style:left="{filterLeft}px"
	style:top="{filterTop}px"
	style:transform-origin={filterOrigin}
>
	<LiquidGlass source={filterSource} hoverable={false} class="h-full w-full overflow-hidden rounded-[24px] text-white">
		<div class="grid h-full min-h-0 grid-cols-[170px_minmax(0,1fr)] grid-rows-[minmax(0,1fr)] gap-4 overflow-hidden p-4">
		<div class="flex min-h-0 flex-col">
			<div class="flex h-7 shrink-0 items-center gap-2">
			<h3 class="shrink-0 text-[12px] font-light leading-none">Account</h3>
			<label class="relative flex h-7 min-w-0 flex-1 items-center rounded-lg bg-white/15">
				<input bind:value={accountQuery} aria-label="Search accounts" placeholder="Search" spellcheck="false" class="h-full min-w-0 w-full bg-transparent pr-8 pl-2 text-[12px] font-normal text-white outline-none placeholder:text-white/40" />
				<img src={searchIcon} alt="" class="pointer-events-none absolute right-2 h-3.5 w-3.5" />
			</label>
			</div>
			<CapsuleScroll>
			<ul class="space-y-1 px-2 text-[12px] font-normal leading-4 text-white">
				<li><button type="button" aria-pressed={selectedAccountIds.length === 0} disabled={savingAccounts[desktopId]} onclick={() => chooseAccount(null)} class="account-option" class:selected={selectedAccountIds.length === 0}>All account</button></li>
				{#each matchingAccounts as account (account.id)}
					<li><button type="button" aria-pressed={selectedAccountIds.includes(account.id)} disabled={savingAccounts[desktopId]} onclick={() => chooseAccount(account.id)} class="account-option" class:selected={selectedAccountIds.includes(account.id)}>{account.name}</button></li>
				{/each}
			</ul>
			</CapsuleScroll>
		</div>
		<div class="flex min-h-0 flex-col">
			<div class="flex h-7 shrink-0 items-center gap-2">
			<h3 class="shrink-0 text-[12px] font-light leading-none">Symbols</h3>
			<label class="relative flex h-7 min-w-0 flex-1 items-center rounded-lg bg-white/15">
				<input bind:value={symbolQuery} aria-label="Search symbols" placeholder="Search" spellcheck="false" class="h-full min-w-0 w-full bg-transparent pr-8 pl-2 text-[12px] font-normal text-white outline-none placeholder:text-white/40" />
				<img src={searchIcon} alt="" class="pointer-events-none absolute right-2 h-3.5 w-3.5" />
			</label>
			</div>
			<CapsuleScroll>
			<ul class="space-y-1 px-2 text-[12px] font-normal leading-4 text-white">
				<li><button type="button" aria-pressed={selectedSymbols.length === 0} disabled={savingSymbols[desktopId]} onclick={() => chooseSymbol(null)} class="account-option" class:selected={selectedSymbols.length === 0}>All symbols</button></li>
				{#each matchingSymbols as symbol (symbol)}
					<li><button type="button" aria-pressed={selectedSymbols.includes(symbol)} disabled={savingSymbols[desktopId]} onclick={() => chooseSymbol(symbol)} class="account-option" class:selected={selectedSymbols.includes(symbol)}>{symbol}</button></li>
				{/each}
			</ul>
			</CapsuleScroll>
		</div>
		</div>
	</LiquidGlass>
</div>

<div bind:this={filterSource} class="flex h-full flex-col px-4 pt-4 text-white">
	<!-- The table's own width. -->
	<div class="flex min-h-0 flex-1 flex-col {visiblePositions.length > 0 ? 'w-fit max-w-full' : 'w-full'}">
		<header class="mb-3 flex w-full items-center gap-3 whitespace-nowrap">
			<h2 class="text-sm text-white font-medium">Open positions</h2>
			<button
				bind:this={filterButton}
				type="button"
				aria-label="Filter"
				aria-haspopup="dialog"
				aria-expanded={filterOpen}
				aria-controls="open-positions-filter"
				onclick={toggleFilter}
				class="flex shrink-0 cursor-pointer items-center justify-center rounded-sm outline-none transition-colors duration-150 text-white {filterOpen ? '' : 'hover:text-neutral-600'} focus-visible:ring-2 focus-visible:ring-white/50"
			>
				<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" class="block h-4 w-4"><path d="M2 2H14Q15 2 15 3Q15 4 14.3 4.7L10 9V12.2L6 15.2V9L1.7 4.7Q1 4 1 3Q1 2 2 2Z" /></svg>
			</button>
			{#if visiblePositions.length > 0}
				<span class="text-sm {totalColor}">{signedMoney(total)}</span>
			{/if}
		</header>

		<div class="flex min-h-0 flex-1 flex-col overflow-x-auto overflow-y-hidden [scrollbar-width:thin]">
		{#if visiblePositions.length > 0}
			<div class="{cols} w-full overflow-hidden [scrollbar-gutter:stable] [scrollbar-width:thin] border border-[#535353] bg-[#323232] py-2 text-sm font-medium text-white">
				<span></span>
				{#each sortColumns as column (column.key)}
					<button type="button" disabled={savingSorts[desktopId]} onclick={() => toggleSort(column.key)}
						aria-label={`${column.label}: ${sort?.key === column.key ? (sort.direction === 'desc' ? 'descending' : 'ascending') : 'default order'}. Click to sort.`}
						class="flex min-w-0 cursor-pointer items-center gap-1.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-white/50">
						<span class="shrink-0 whitespace-nowrap">{column.label}</span>
						{#if sort?.key === column.key}
						<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="h-3 w-3 shrink-0 self-center">
							<path d={sort.direction === 'asc' ? 'm3 7.5 3-3 3 3' : 'm3 4.5 3 3 3-3'} />
						</svg>
						{/if}
					</button>
				{/each}
				<span></span>
			</div>
		{:else}
			<div class="py-1 text-[13px] font-light text-neutral-400">No positions open right now.</div>
		{/if}

		<!-- Fills the rest of the panel and scrolls; the bottom padding lives inside the
		     scroll area so the last row scrolls fully into view instead of being cut. -->
		<ul class="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable] [scrollbar-width:thin] px-px pb-4">
			{#each sortedPositions as p (p.ticket)}
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

<style>
	.account-option {
		display: block;
		width: 170px;
		margin-left: -8px;
		padding: 6px 8px;
		white-space: nowrap;
		border-radius: 8px;
		text-align: left;
		cursor: pointer;
		transition: background-color 150ms;
	}
	.account-option:hover, .account-option:focus-visible { background: #ffffff1a; outline: none; }
	.account-option.selected { background: #ffffff26; }
	.account-option.selected:hover { background: #ffffff30; }

	.filter-popover {
		opacity: 0;
		transform: scale(1.15);
		transition: opacity 100ms ease-out, transform 100ms ease-out, display 100ms allow-discrete, overlay 100ms allow-discrete;
		position: fixed;
		margin: 0;
		padding: 0;
		border: 0;
		width: min(372px, calc(100vw - 24px));
		height: 200px;
		background: transparent;
		overflow: visible;
		color: inherit;
		border-radius: 24px;
	}
	.filter-popover:popover-open {
		opacity: 1;
		transform: scale(1);
	}
	@starting-style {
		.filter-popover:popover-open {
			opacity: 0;
			transform: scale(1.15);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.filter-popover { transition: none; }
	}
</style>
