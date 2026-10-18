<script lang="ts">
	import searchIcon from '#lib/icons/search.png';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import SymbolIcon from '#lib/components/SymbolIcon.svelte';

	// `source` is the element behind the toolbar (the chart) that the glass
	// refracts.
	let {
		symbol,
		minutes,
		onSymbol,
		onTimeframe,
		source
	}: {
		symbol: string;
		minutes: number;
		onSymbol: (symbol: string) => void;
		onTimeframe: (minutes: number) => void;
		source: HTMLElement | undefined;
	} = $props();

	const TIMEFRAMES = [
		{ minutes: 1, label: '1m' },
		{ minutes: 5, label: '5m' },
		{ minutes: 15, label: '15m' },
		{ minutes: 30, label: '30m' },
		{ minutes: 60, label: '1h' },
		{ minutes: 240, label: '4h' },
		{ minutes: 1440, label: '1d' }
	];
	const label = $derived(TIMEFRAMES.find((t) => t.minutes === minutes)?.label ?? `${minutes}m`);

	// The timeframe pill grows into a tall glass panel listing every timeframe.
	const ROW = 32; // option height, px
	const PILL = 36; // closed height, px
	let open = $state(false);
	let pillWidth = $state(84); // the closed pill hugs its label
	const OPEN_WIDTH = 84;
	let picker: HTMLElement;

	// The symbol pill grows into a tall glass panel as wide as the whole
	// toolbar: a search bar on top, the matching symbols below.
	const SYMBOL_PANEL_HEIGHT = 320;
	let symbolOpen = $state(false);
	let symbolWidth = $state(100);
	let toolbarWidth = $state(200);
	let symbolPicker: HTMLElement;
	let searchInput = $state<HTMLInputElement>();
	let query = $state('');
	let symbols = $state<string[]>([]);
	const matches = $derived(symbols.filter((s) => s.includes(query.trim().toUpperCase())));

	async function toggleSymbol() {
		symbolOpen = !symbolOpen;
		if (!symbolOpen) return;
		open = false;
		query = '';
		setTimeout(() => searchInput?.focus(), 50);
		if (symbols.length === 0) symbols = await (await fetch('/api/symbols')).json();
	}

	// Our own scrollbar for the symbol list: Firefox (Zen) cannot shape or
	// move the native one. A capsule near the panel's right edge, draggable.
	let list = $state<HTMLElement>();
	let scroll = $state({ top: 0, height: 1, view: 1 });
	const measure = () => list && (scroll = { top: list.scrollTop, height: list.scrollHeight, view: list.clientHeight });
	$effect(() => {
		void matches.length;
		measure();
		if (!list) return;
		// The panel grows open, so the list's height changes after it appears.
		const ro = new ResizeObserver(measure);
		ro.observe(list);
		return () => ro.disconnect();
	});
	const THUMB_MIN = 24;
	const thumbHeight = $derived(Math.max(THUMB_MIN, (scroll.view * scroll.view) / scroll.height));
	const thumbTop = $derived(
		scroll.height > scroll.view ? (scroll.top / (scroll.height - scroll.view)) * (scroll.view - thumbHeight) : 0
	);

	function dragThumb(e: PointerEvent) {
		if (!list) return;
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const startY = e.clientY;
		const startTop = list.scrollTop;
		const perPixel = (scroll.height - scroll.view) / (scroll.view - thumbHeight);
		const move = (ev: PointerEvent) => list && (list.scrollTop = startTop + (ev.clientY - startY) * perPixel);
		const up = () => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
	}

	function pickSymbol(s: string) {
		symbolOpen = false;
		if (s !== symbol) onSymbol(s);
	}

	function choose(m: number) {
		open = false;
		if (m !== minutes) onTimeframe(m);
	}
</script>

<svelte:window
	onpointerdown={(e) => {
		const t = e.target as Node;
		if (open && !picker.contains(t)) open = false;
		if (symbolOpen && !symbolPicker.contains(t)) symbolOpen = false;
	}}
	onkeydown={(e) => e.key === 'Escape' && ((open = false), (symbolOpen = false))}
/>

<div bind:offsetWidth={toolbarWidth} class="flex items-start gap-2">
	<!-- Holds the closed pill's place; opening overlays the timeframe pill and the chart. -->
	<div bind:this={symbolPicker} class="relative z-20 h-9" style="width:{symbolWidth}px">
		<LiquidGlass
			{source}
			hoverable={!symbolOpen}
			class="absolute top-0 left-0 overflow-hidden rounded-[18px] text-sm font-medium text-white transition-[width,height] duration-200 ease-out"
			style="width:{symbolOpen ? toolbarWidth : symbolWidth}px; height:{symbolOpen ? SYMBOL_PANEL_HEIGHT : PILL}px"
		>
			<button
				type="button"
				onclick={toggleSymbol}
				aria-expanded={symbolOpen}
				bind:offsetWidth={symbolWidth}
				class="flex h-9 w-max cursor-pointer items-center gap-2 px-4 {symbolOpen ? 'pointer-events-none absolute opacity-0' : 'transition-opacity duration-150'}"
			>
				<SymbolIcon {symbol} />
				{symbol}
			</button>
			{#if symbolOpen}
				<div class="relative flex h-full flex-col p-2 transition-opacity duration-150 starting:opacity-0">
					<!-- Search bar: plain grey, magnifying glass on the right. -->
					<label class="relative flex h-8 shrink-0 items-center rounded-lg bg-white/10">
						<input
							bind:this={searchInput}
							bind:value={query}
							onkeydown={(e) => e.key === 'Enter' && matches[0] && pickSymbol(matches[0])}
							placeholder="Search"
							spellcheck="false"
							class="h-full w-full bg-transparent pr-9 pl-3 text-sm font-normal outline-none placeholder:text-white/40"
						/>
						<img src={searchIcon} alt="" class="pointer-events-none absolute right-3 block h-4 w-4" />
					</label>
					<ul
						id="symbol-list"
						bind:this={list}
						onscroll={measure}
						role="listbox"
						class="mt-2 min-h-0 flex-1 overflow-y-auto pr-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
					>
						{#each matches as s (s)}
							<li class="flex h-8 items-center">
								<button
									type="button"
									role="option"
									aria-selected={s === symbol}
									onclick={() => pickSymbol(s)}
									class="flex h-7 w-full cursor-pointer items-center gap-2 rounded-lg px-3 text-left hover:bg-white/10 {s === symbol ? 'bg-white/15' : ''}"
								>
									<SymbolIcon symbol={s} />
									{s}
								</button>
							</li>
						{:else}
							<li class="px-3 py-1.5 text-white/50">{symbols.length ? 'No match' : 'Loading…'}</li>
						{/each}
					</ul>
					{#if scroll.height > scroll.view}
						<!-- Scrollbar track spans the list's height, 3px in from the panel's right edge. -->
						<div class="absolute right-[3px] bottom-2 w-[5px]" style="top:{8 + 32 + 8}px">
							<div
								role="scrollbar"
								aria-controls="symbol-list"
								aria-valuenow={Math.round(scroll.top)}
								tabindex="-1"
								onpointerdown={dragThumb}
								class="absolute w-full cursor-pointer rounded-full bg-white/45 hover:bg-white/70"
								style="height:{thumbHeight}px; top:{thumbTop}px"
							></div>
						</div>
					{/if}
				</div>
			{/if}
		</LiquidGlass>
	</div>

	<!-- Holds the closed pill's place so opening overlays the chart instead of pushing anything. -->
	<!-- Fades away while the symbol panel is open, and back when it closes. -->
	<div
		bind:this={picker}
		class="relative h-9 transition-opacity duration-200 ease-out {symbolOpen ? 'pointer-events-none opacity-0' : ''}"
		style="width:{pillWidth}px"
	>
		<LiquidGlass
			{source}
			hoverable={!open}
			class="absolute top-0 left-0 overflow-hidden rounded-[18px] text-sm font-medium text-white transition-[height] duration-200 ease-out"
			style="width:{open ? Math.max(pillWidth, OPEN_WIDTH) : pillWidth}px; height:{open ? PILL + TIMEFRAMES.length * ROW + 6 : PILL}px"
		>
			<button
				type="button"
				onclick={() => ((open = !open), (symbolOpen = false))}
				aria-haspopup="listbox"
				aria-expanded={open}
				bind:offsetWidth={pillWidth}
				class="flex h-9 w-max cursor-pointer items-center px-4"
			>
				{label}
			</button>
			<ul role="listbox" class="px-1 pb-1.5 transition-opacity duration-150 {open ? 'opacity-100' : 'pointer-events-none opacity-0'}">
				{#each TIMEFRAMES as t (t.minutes)}
					<li class="flex h-8 items-center">
						<button
							type="button"
							role="option"
							aria-selected={t.minutes === minutes}
							tabindex={open ? 0 : -1}
							onclick={() => choose(t.minutes)}
							class="flex h-7 w-full cursor-pointer items-center rounded-lg px-3 text-left hover:bg-white/10 {t.minutes === minutes ? 'bg-white/15' : ''}"
						>
							{t.label}
						</button>
					</li>
				{/each}
			</ul>
		</LiquidGlass>
	</div>
</div>
