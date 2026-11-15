<script lang="ts">
	import ChartPane from '#lib/components/ChartPane.svelte';
	import LayoutIcon from '#lib/components/LayoutIcon.svelte';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import AccountView from '#lib/components/AccountView.svelte';
	import UserView from '#lib/components/UserView.svelte';
	import { circleOpen } from '#lib/circleNav.ts';
	import AgentPhone from '#lib/components/AgentPhone.svelte';
	import PcCase from '#lib/components/PcCase.svelte';
	import cornerIcon from '#lib/icons/corner.png';
	import cornerHoverIcon from '#lib/icons/corner-hover.png';
	import { me, defaultAvatar } from '#lib/me.svelte.ts';
	import ActiveAccounts from '#lib/components/ActiveAccounts.svelte';
	import OpenPositions from '#lib/components/OpenPositions.svelte';
	import type { Account } from '#lib/server/accounts.ts';
	import type { Bar } from '#lib/server/bars.ts';
	import type { Position } from '#lib/server/positions.ts';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { pushState } from '$app/navigation';
	import { scale } from 'svelte/transition';
	import { logout } from '#lib/slide.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	// Who is logged in; the /user page edits these in place.
	me.name = untrack(() => data.user);
	me.avatar = untrack(() => data.avatar);

	// Starts with the server-rendered values, then follows the live polls: every poll brings
	// positions, candles and each account's balance/equity; every FULL_EVERY-th the whole accounts.
	type Tick = { id: number; balance: number; equity: number | null };
	type Live = { accounts?: Account[]; ticks?: Tick[]; positions: Position[]; candles: Record<string, Bar[]>; mbps: number | null };
	let live = $state<Live | null>(null);
	let fullAccounts = $state<Account[]>(untrack(() => data.accounts));
	let ticks = $state<Tick[] | null>(null);
	const accounts = $derived.by(() => {
		if (!ticks) return fullAccounts;
		const byId = new Map(ticks.map((t) => [t.id, t]));
		return fullAccounts.map((a) => {
			const t = byId.get(a.id);
			return t && (t.balance !== a.balance || t.equity !== a.equity) ? { ...a, balance: t.balance, equity: t.equity } : a;
		});
	});
	const positions = $derived(live?.positions ?? data.positions);

	// Chart layouts: charts per row, top to bottom. 1 to 6 charts.
	const LAYOUTS = [[1], [2], [3], [2, 2], [3, 2], [3, 3]];
	let layout = $state(untrack(() => data.layout));
	let layoutOpen = $state(false);
	// Bottom panel height: the PC case shrinks on short screens so the speed line, avatar and door below it still fit.
	let infoH = $state(360);
	// The speed line, avatar and door need ~140px; the case gets what is left (full size at 310px+), and is
	// hidden when less than 60px is left (a very short window) rather than overlapping them.
	const caseRoom = $derived(infoH - 140);
	const caseK = $derived(caseRoom < 60 ? 0 : Math.min(1, caseRoom / 170));
	const caseBottom = $derived(48 + 170 * caseK);
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

	// Poll for every symbol on screen every POLL_MS (the next poll starts when the last one
	// answers, so a slow tunnel never stacks requests); restarts when that set changes.
	// Polled because the Cloudflare tunnel does not pass server-sent events.
	const POLL_MS = 400;
	const FULL_EVERY = 25; // whole accounts about every 10 s
	const streamKey = $derived([...new Set(symbols.slice(0, shownCount))].sort().join(','));
	$effect(() => {
		const key = streamKey;
		let stopped = false;
		let timer: ReturnType<typeof setTimeout>;
		let n = 0;
		let lastPosKey = '';
		let lastTickKey = '';
		const poll = async () => {
			try {
				const full = n++ % FULL_EVERY === FULL_EVERY - 1;
				const res = await fetch(`/api/live?symbols=${key}${full ? '&full=1' : ''}`, { cache: 'no-store' });
				if (res.ok && !stopped) {
					const next: Live = await res.json();
					// Unchanged positions / balances keep their old objects, so the tables and
					// charts only re-render when something actually moved.
					const posKey = JSON.stringify(next.positions);
					if (live && posKey === lastPosKey) next.positions = live.positions;
					lastPosKey = posKey;
					if (next.accounts) {
						fullAccounts = next.accounts;
						ticks = null;
						lastTickKey = '';
					} else if (next.ticks) {
						const tickKey = JSON.stringify(next.ticks);
						if (tickKey !== lastTickKey) ticks = next.ticks;
						lastTickKey = tickKey;
					}
					live = next;
				}
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
	<section bind:clientHeight={infoH} class="relative flex min-h-0 flex-[1] bg-[#181818]">
		<!-- Display only for now: none of these buttons does anything yet. -->
		<!-- Proportions from the design: text, arrow and grid are about 10, 11 and
		     16px tall, 22px apart (16px gap + the arrow image's 6px margin). -->
		<div class="absolute top-[15px] right-4 z-10 flex items-center gap-6">
			<!-- Agent iPhone, sized to its neighbours (22px tall); opens the same phone as /account. -->
			<div class="relative h-[22px] w-[14.1px]">
				{#if !page.state.account}
					<!-- the account overlay brings its own phone -->
					<AgentPhone login={accounts[0]?.login ?? ""} phoneClass="fixed top-[30px] right-[38px]" iconClass="absolute inset-0" />
				{/if}
			</div>
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
			<!-- Display only: a white square and a plus, neither does anything yet. -->
			<div class="flex items-center gap-2 border border-[#5a5a5a] bg-[#5a5a5a] px-1.5 py-1">
				<span class="flex h-3 w-3 items-center justify-center rounded-[2px] bg-white text-[9px] leading-none font-semibold text-black" aria-hidden="true">1</span>
				<svg class="h-3 w-3 text-white" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M8 2.5v11M2.5 8h11" /></svg>
			</div>
		</div>
		<!-- ASUS Prime AP201 (black mesh) under the toolbar; drag to rotate. -->
		<!-- box ends at the window edge; at the start angle the case's right edge then lines up with the layout icon (16px) -->
		{#if caseK > 0}
			<div class="absolute top-[48px] right-0 z-10 h-[170px] w-[166px] origin-top-right" style="transform: scale({caseK})">
				<PcCase class="h-full w-full" />
			</div>
		{/if}
		<!-- Under the case, right-aligned: which machine this is, then its internet download speed
		     (from a speed test every 5 minutes). -->
		<div style="top: {caseBottom - 4}px" class="absolute right-4 z-10 flex items-center gap-2 text-[12px] leading-none font-light text-white">
			<span title="Internet download speed">Bandung, {live?.mbps != null ? `${live.mbps.toFixed(live.mbps < 10 ? 1 : 0)} Mbps` : '— Mbps'}</span>
		</div>
		<!-- Centred in the gap between the speed line and the door, right-aligned: who is logged in, then their Arisu Tachibana profile picture on blue.
		     Clicking it opens /user through the same black circle as Settings, grown out of the picture. -->
		<div style="top: {caseBottom + 8}px" class="pointer-events-none absolute right-4 bottom-[28px] z-10 flex items-center">
			<a
				href="/user"
				class="pointer-events-auto flex items-center gap-2 text-[12px] leading-none font-light text-white"
				onclick={(e) => {
					if (e.metaKey || e.ctrlKey || e.shiftKey) return;
					e.preventDefault();
					const r = (e.currentTarget as HTMLElement).querySelector('img')!.getBoundingClientRect();
					circleOpen(() => pushState('/user', { user: true }), r.left + r.width / 2, r.top + r.height / 2);
				}}
			>
				<span>{me.name}</span>
				<img src={me.avatar ?? defaultAvatar} alt="" class="h-[30px] w-[30px] rounded-[8px] object-cover" />
			</a>
		</div>
		<!-- The door in the corner logs out and goes back to the shop. Both images are anchored
		     to the corner, so the larger hover one grows out of it. -->
		<button
			type="button"
			aria-label="Log out"
			class="group absolute right-[12px] bottom-0 z-10 flex cursor-pointer items-end justify-end"
			onclick={async () => {
				await fetch('/api/logout', { method: 'POST' }).catch(() => {});
				await logout();
			}}
		>
			<img src={cornerIcon} alt="" class="block h-7 w-7 group-hover:hidden" />
			<img src={cornerHoverIcon} alt="" class="hidden h-[31px] w-[31px] group-hover:block" />
		</button>
		<div class="w-1/5 min-w-56 shrink-0">
			<ActiveAccounts {accounts} {positions} />
		</div>
		<!-- Below 1140px the table can reach the right-hand column (toolbar, case, avatar), so it keeps clear of it. -->
		<div class="min-w-0 flex-1 pr-[176px] min-[1140px]:max-w-[calc(50%+150px)] min-[1140px]:pr-0">
			<OpenPositions {positions} />
		</div>
	</section>
	{#if page.state.account}
		<!-- Shallow-routed: the chart page stays mounted underneath, so nothing reloads. -->
		<AccountView
			{accounts}
			path={page.state.accountPath ?? '/account'}
			nav={(p: string) => pushState(p, { account: true, accountPath: p })}
			close={() => pushState('/chart', {})}
		/>
	{/if}
	{#if page.state.user}
		<UserView close={() => pushState('/chart', {})} />
	{/if}
</main>
