<script lang="ts">
	import { circleClose } from '#lib/circleNav.ts';

	import AgentPhone from '#lib/components/AgentPhone.svelte';
	import eyeIcon from '#lib/icons/eye.png';
	import eyeSlashIcon from '#lib/icons/eye-slash.png';
	import type { Account } from '#lib/server/accounts.ts';
	import { fade } from 'svelte/transition';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import { invalidateAll } from '$app/navigation';
	import EquityChart from '#lib/components/EquityChart.svelte';
	import ClosedPositions from '#lib/components/ClosedPositions.svelte';
	import RangePicker from '#lib/components/RangePicker.svelte';
	import PnlCalendar from '#lib/components/PnlCalendar.svelte';
	import StrategyList from '#lib/components/StrategyList.svelte';
	import BookBacktest from '#lib/components/BookBacktest.svelte';

	// Shows the account view over a blank page; `close` hides it. The X shrinks the circle back into the Settings button.
	// path: /account (card list) or /account?login=<login>&tab=<tab> (detail). nav moves between them; close leaves to /.
	let { accounts, books = [], path, nav, close }: { accounts: Account[]; books?: string[]; path: string; nav: (p: string) => unknown; close: () => unknown } = $props();

	const TABS = ['history', 'strategy'] as const;
	const query = $derived(new URL(path, 'http://x').searchParams);
	const login = $derived(query.get('login'));
	const selected = $derived(accounts.find((a) => a.login === login));
	const tab = $derived(query.get('tab') ?? 'history');
	const detail = $derived(login != null);
	// /account/edit?login=&tab=strategy: the edit page pushes over the account like the chat's list -> chat.
	const editing = $derived(new URL(path, 'http://x').pathname.startsWith('/account/edit'));
	// Edit page name: local only for now (not saved anywhere).
	let nameOverride = $state<Record<number, string>>({});
	let renaming = $state(false);
	let draft = $state('');
	let bookOverride = $state<Record<number, string>>({});
	const saved = $derived(selected?.edits ?? { name: null, book: null, risk: null, removed: [], added: [] });
	const baseBook = $derived(saved.book ?? selected?.book ?? null);
	const currentBook = $derived(selected ? (bookOverride[selected.id] ?? baseBook) : null);
	const displayName = $derived(selected ? (nameOverride[selected.id] ?? (selected.id in bookOverride ? bookName(currentBook) : (saved.name ?? bookName(currentBook)))) : '—');
	// Risk: edited like the name; local only for now.
	let riskOverride = $state<Record<number, number>>({});
	let riskEditing = $state(false);
	let riskDraft = $state('');
	const displayRisk = $derived(selected ? (riskOverride[selected.id] ?? saved.risk ?? selected.risk) : null);
	function riskKey(e: KeyboardEvent) {
		if (e.key === 'Escape') riskEditing = false;
		else if (e.key === 'Enter') {
			const v = Number(riskDraft);
			if (selected && riskDraft.trim() && Number.isFinite(v) && v > 0) riskOverride[selected.id] = v;
			riskEditing = false;
		}
	}
	// Editable text in place: focus it and select its text.
	function focusSelect(el: HTMLElement) {
		el.focus();
		const range = document.createRange();
		range.selectNodeContents(el);
		const sel = getSelection();
		sel?.removeAllRanges();
		sel?.addRange(range);
	}
	function nameKey(e: KeyboardEvent) {
		if (e.key === 'Escape') renaming = false;
		else if (e.key === 'Enter') { if (selected && draft.trim()) nameOverride[selected.id] = draft.trim(); renaming = false; }
	}
	// Change: picks a book from a glass dropdown. Local only for now (nothing is written to the live tables).
	let bookOpen = $state(false);
	function pickBook(b: string) {
		if (selected) { bookOverride[selected.id] = b; delete nameOverride[selected.id]; }
		bookOpen = false;
	}
	// Edit page: sleeves deleted from the list (local only for now).
	let removed = $state<Record<number, string[]>>({});
	// Strategies added from the live runtime's pool (GET /api/strategies), kept per account like removed.
	let added = $state<Record<number, string[]>>({});
	let pool = $state<{ strategy: string; symbol: string; oos: { ret: number; dd: number; pf: number | null } | null }[]>([]);
	$effect(() => {
		if (!editing || pool.length) return;
		fetch('/api/strategies').then((r) => r.json()).then((p) => (pool = p));
	});
	const currentAdded = $derived(selected ? (added[selected.id] ?? saved.added ?? []) : []);
	const currentRemoved = $derived(selected ? (removed[selected.id] ?? saved.removed) : []);
	const editStrategies = $derived(selected ? [...selected.strategies.filter((x) => !currentRemoved.includes(x.strategy)), ...currentAdded.map((k) => pool.find((x) => x.strategy === k) ?? { strategy: k, symbol: k.split('_')[1]?.toUpperCase() ?? '', oos: null })] : []);
	const available = $derived(pool.filter((x) => !editStrategies.some((e) => e.strategy === x.strategy)));
	// Re-adding a deleted book sleeve just undoes the delete.
	function addStrategy(k: string) {
		if (!selected) return;
		if (currentRemoved.includes(k)) removed[selected.id] = currentRemoved.filter((x) => x !== k);
		else added[selected.id] = [...currentAdded, k];
	}
	function deleteStrategy(k: string) {
		if (!selected) return;
		if (currentAdded.includes(k)) added[selected.id] = currentAdded.filter((x) => x !== k);
		else removed[selected.id] = [...currentRemoved, k];
	}
	// Update shows once anything differs from what's saved; nothing is written until it's clicked.
	const dirty = $derived(
		!!selected &&
			(currentBook !== baseBook ||
				displayName !== (saved.name ?? bookName(baseBook)) ||
				displayRisk !== (saved.risk ?? selected.risk) ||
				[...currentRemoved].sort().join() !== [...saved.removed].sort().join() ||
				[...currentAdded].sort().join() !== [...(saved.added ?? [])].sort().join())
	);
	// Run: a snapshot of the edits, so the backtest below only changes when Run is clicked again.
	let runParams = $state<{ book: string | null; removed: string[]; risk: number | null } | null>(null);
	$effect(() => {
		void selected?.id;
		runParams = null;
	});
	let saving = $state(false);
	async function update() {
		if (!selected || saving) return;
		saving = true;
		const id = selected.id;
		try {
			const res = await fetch(`/api/accounts/${id}/edits`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					name: displayName === bookName(currentBook) ? null : displayName,
					book: currentBook === selected.book ? null : currentBook,
					risk: displayRisk === selected.risk ? null : displayRisk,
					removed: currentRemoved,
					added: currentAdded
				})
			});
			if (res.ok) {
				await invalidateAll();
				delete nameOverride[id]; delete bookOverride[id]; delete riskOverride[id]; delete removed[id]; delete added[id];
			}
		} finally {
			saving = false;
		}
	}
	const PUSH = 'transform 480ms cubic-bezier(0.32, 0.72, 0, 1)';
	let equityChange = $state<number | null>(null);

	// General tab: login / password / broker / server edits, local until Update writes them.
	// Server is stored as "<broker>-<rest>"; Broker edits the part before the first '-', Server the rest.
	type Field = 'login' | 'password' | 'broker' | 'server';
	let fieldEdits = $state<Record<number, Partial<Record<Field, string>>>>({});
	let fieldEditing = $state<Field | null>(null);
	let fieldDraft = $state('');
	const splitServer = (s: string) => (s.includes('-') ? [s.slice(0, s.indexOf('-')), s.slice(s.indexOf('-') + 1)] : ['', s]);
	function fieldValue(a: Account, f: Field): string {
		const e = fieldEdits[a.id]?.[f];
		if (e != null) return e;
		return f === 'login' ? a.login : f === 'broker' ? splitServer(a.server)[0] : f === 'server' ? splitServer(a.server)[1] : '';
	}
	function fieldKey(e: KeyboardEvent) {
		if (e.key === 'Enter' && selected && fieldEditing && fieldDraft.trim()) fieldEdits[selected.id] = { ...fieldEdits[selected.id], [fieldEditing]: fieldDraft.trim() };
		fieldEditing = null;
	}
	const fieldsDirty = $derived(
		!!selected && Object.entries(fieldEdits[selected.id] ?? {}).some(([f, v]) => (f === 'password' ? v !== (shown[selected!.id] ?? '') : v !== fieldValue({ ...selected!, id: -1 } as Account, f as Field)))
	);
	async function updateFields() {
		if (!selected || saving) return;
		const a = selected, e = fieldEdits[a.id] ?? {};
		saving = true;
		try {
			const r = await fetch(`/api/accounts/${a.id}/details`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					login: e.login,
					password: e.password,
					server: e.broker != null || e.server != null ? `${fieldValue(a, 'broker')}-${fieldValue(a, 'server')}` : undefined
				})
			});
			if (!r.ok) return alert(`Update failed: ${(await r.json().catch(() => null))?.message ?? r.status}`);
			if (e.password != null) shown[a.id] = e.password;
			delete fieldEdits[a.id];
			await invalidateAll();
			nav(`/account?login=${e.login ?? a.login}&tab=history`);
		} finally {
			saving = false;
		}
	}
	let toggling = $state(false);
	// Delete the account on the live server (after the confirm popup), then back to the list.
	async function deleteAccount(a: Account) {
		if (!confirm(`Delete account ${a.login}? This removes it and all its strategies. Open positions are not closed.`)) return;
		toggling = true;
		const r = await fetch(`/api/accounts/${a.id}/delete`, { method: 'POST' });
		toggling = false;
		if (!r.ok) return alert(`Delete failed: ${(await r.json().catch(() => null))?.message ?? r.status}`);
		await invalidateAll();
		nav('/account');
	}
	// Activate/Deactivate the whole account (every strategy), after the browser's confirm popup.
	async function toggleActive(a: Account) {
		const verb = a.active ? 'Deactivate' : 'Activate';
		if (!confirm(`${verb} account ${a.login}?` + (a.active ? ' All its strategies stop opening trades.' : ' Its strategies start trading again.'))) return;
		toggling = true;
		const r = await fetch(`/api/accounts/${a.id}/active`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ active: !a.active }) });
		if (!r.ok) alert(`${verb} failed: ${(await r.json().catch(() => null))?.message ?? r.status}`);
		await invalidateAll();
		toggling = false;
	}
	let bigH = $state<Record<number, number>>({});
	// Detail list height from the cards' target sizes, not a live measurement: measuring while the cards
	// shrank kept moving the box's height target, so it went thinner first and only then shorter.
	const smallListH = $derived(accounts.length * (CARD_H + FRAME + BAND) + GAP * Math.max(accounts.length - 1, 0));
	// No animation on first paint; only for moves after it.
	let ready = $state(false);
	$effect(() => {
		const t = setTimeout(() => (ready = true), 50);
		return () => clearTimeout(t);
	});
	const EASE = 'cubic-bezier(0.65, 0, 0.35, 1)';

	// Responsive layout (small monitors, iPad; phones are not supported).
	// The open phone docks at the right (content keeps clear of it) only when the window is wide enough;
	// narrower, it floats over the page. The Accounts column beside the tabs shows only when the
	// content still has room for it; otherwise the content starts at the left margin.
	let winW = $state(1920);
	let pageEl = $state<HTMLElement>(); // backdrop the icon glass refracts
	let equityRange = $state('all');
	// The open phone floats over the page; nothing moves or resizes when it opens.
	const padR = 46;
	// Detail list: cards are parallelograms leaning like the tabs (/), stacked along that slant, so each lower
	// card sits further left; a SELECT chevron fills the left edge and points at them. Card i's x-offset puts the
	// bottom-left corner of the lowest card at the list's left edge and every card on one slanted line.
	const SLANT = 12; // degrees
	const TAN = Math.tan((SLANT * Math.PI) / 180);
	const LIST_L = 144; // clear of the SELECT chevron's tip
	// Tall portrait cards: an 8px frame with a 40px bottom band (profit, drawdown, state), 10px apart.
	const CARD_W = 186, CARD_H = 176, FRAME = 8, BAND = 40, GAP = 10;
	const FRAME_W = CARD_W + 2 * FRAME, FRAME_H = CARD_H + FRAME + BAND;
	const pnlOf = (a: Account) => (a.initialBalance ? ((a.balance - a.initialBalance) / a.initialBalance) * 100 : null);
	const cardX = $derived(accounts.map((_, i) => (FRAME_H / 2) * TAN + (accounts.length - 1 - i) * (FRAME_H + GAP) * TAN));
	const listW = $derived(FRAME_W + Math.max(0, ...cardX) + (FRAME_H / 2) * TAN);
	// SELECT chevron: SEL_TOP wide at the very top and bottom, widest (its tip) at mid-height, at most 96px.
	const SEL_TOP = 2; // practically nothing at the very top and bottom
	let winH = $state(900);
	const selTip = $derived(Math.min(96, SEL_TOP + (winH / 2) * Math.tan((11 * Math.PI) / 180)));
	const sideW = $derived(Math.ceil(LIST_L + listW + 34));
	const sidePanel = $derived(winW - sideW - padR >= 640);
	const padL = $derived(sidePanel ? sideW : 46);
	const tabsL = $derived(sidePanel ? sideW : 180);
	const updateR = 90;

	// The wheel over the accounts column (or any empty margin) scrolls the open tab's column, unless something
	// under the mouse can still scroll that way.
	function wheelToTab(e: WheelEvent) {
		const t = e.target as Element;
		const root = e.currentTarget as HTMLElement;
		const tabCol = root.querySelector<HTMLElement>('[data-tab-scroll]');
		if (!tabCol || tabCol.contains(t)) return;
		// Anything under the mouse that can still scroll that way (accounts list, phone chat) keeps the wheel.
		for (let el: Element | null = t; el && el !== root; el = el.parentElement) {
			const h = el as HTMLElement;
			if (!/(auto|scroll)/.test(getComputedStyle(h).overflowY)) continue;
			if (e.deltaY < 0 ? h.scrollTop > 0 : h.scrollTop + h.clientHeight < h.scrollHeight - 1) return;
		}
		tabCol.scrollBy({ top: e.deltaY });
	}

	function back(e: MouseEvent) {
		if (e.metaKey || e.ctrlKey || e.shiftKey) return;
		e.preventDefault();
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		circleClose(close, r.left + r.width / 2, r.top + r.height / 2);
	}

	// Passwords load only when the eye is clicked (the endpoint answers this machine only).
	let shown = $state<Record<number, string | null>>({});
	// Balance blur: applies at once, saved through the same endpoint as the sidebar.
	let pending = $state<Record<number, boolean>>({});
	const isBlurred = (a: Account) => pending[a.id] ?? a.blurred;
	async function toggleBlur(a: Account) {
		const blurred = !isBlurred(a);
		pending[a.id] = blurred;
		await fetch(`/api/accounts/${a.id}/blur`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ blurred })
		});
	}

	async function togglePass(a: Account) {
		if (shown[a.id] != null) return void (shown[a.id] = null);
		const r = await fetch(`/api/accounts/${a.id}/password`);
		shown[a.id] = r.ok ? (await r.json()).password : '(local only)';
	}

	const broker = (server: string) =>
		server.toLowerCase().startsWith('fundednext') ? 'FundedNext' : server.toLowerCase().startsWith('fivepercent') ? 'The5ers' : server.split('-')[0] || '—';
	const brokerUrl = (server: string) =>
		server.toLowerCase().startsWith('fundednext') ? 'https://app.fundednext.com' : server.toLowerCase().startsWith('fivepercent') ? 'https://www.the5ers.com' : 'https://my.exness.com';
	const serverName = (server: string) => server.replace(/[-\s]/g, '');

	const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	// fundednext_combined_21-10-2026 -> FundedNext21Oct26
	function bookName(book: string | null) {
		if (!book) return '—';
		const m = book.match(/(\d{2})-(\d{2})-\d{2}(\d{2})$/);
		const head = book.toLowerCase().startsWith('fundednext') ? 'FundedNext' : book.toLowerCase().startsWith('the5ers') ? 'The5ers' : book.toLowerCase().includes('exness') ? 'Exness' : book.split('_')[0];
		return m ? `${head}${m[1]}${MONTHS[Number(m[2]) - 1]}${m[3]}` : book;
	}

	// Words for the Strategy tab header; the exact number shows on hover.
	// Correlation: mean pairwise correlation of sleeve daily returns.
	const corrWord = (c: number | null) => (c == null ? '—' : c < 0.1 ? 'Very Low' : c < 0.3 ? 'Low' : c < 0.6 ? 'Medium' : 'High');
	// Diversification against its ceiling for N fully independent equal sleeves, sqrt(N).
	function divWord(d: number | null, n: number) {
		if (d == null || !n) return '—';
		const r = d / Math.sqrt(n);
		return r >= 0.8 ? 'Very High' : r >= 0.6 ? 'High' : r >= 0.4 ? 'Medium' : 'Low';
	}

	function allTime(a: Account) {
		if (!a.initialBalance) return null;
		const pct = ((a.balance - a.initialBalance) / a.initialBalance) * 100;
		return { text: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% AT`, color: pct >= 0 ? 'text-blue-500' : 'text-red-400' };
	}

</script>

<svelte:window bind:innerWidth={winW} bind:innerHeight={winH} />

<section bind:this={pageEl} class="fixed inset-0 z-40 overflow-hidden bg-[#121212]" style="background-image: linear-gradient(180deg, #121212 0%, #161616 50%, #1f1f1f 100%)">
	<div class="absolute inset-0" onwheel={wheelToTab} style="transform: translateX({editing ? '-28%' : '0'}); transition: {ready ? PUSH : 'none'}" inert={editing}>
	<LiquidGlass as="a" source={pageEl} href="/chart" onclick={back} aria-label="Close" class="absolute top-[31px] left-[39px] z-20 flex size-[30px] items-center justify-center rounded-full text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
			<path d="M3 3l10 10M13 3L3 13" />
		</svg>
	</LiquidGlass>
	<!-- Add (plus): beside the X on the account list only (the detail page has the up arrow there). No action yet. -->
	{#if !detail}
	<LiquidGlass as="button" source={pageEl} type="button" aria-label="Add account" title="Add account" class="absolute top-[31px] left-[81px] z-20 flex size-[30px] items-center justify-center rounded-full text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
			<path d="M8 2.47v11.06M2.47 8h11.06" />
		</svg>
	</LiquidGlass>
	{/if}
	{#if detail}
		<div transition:fade={{ duration: 200 }} class="absolute top-[31px] left-[81px] z-20">
		<LiquidGlass as="a" source={pageEl} href="/account" onclick={(e: MouseEvent) => (e.preventDefault(), nav('/account'))} aria-label="Back to accounts"
			class="flex size-[30px] items-center justify-center rounded-full text-white">
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
				<path d="M8 13V3M3 8l5-5 5 5" />
			</svg>
		</LiquidGlass>
		</div>
		<!-- Tabs, on the X button's row. Every tab is blank for now. -->
		<nav transition:fade={{ duration: 200 }} class="absolute top-[29px] z-20 flex" style="left:{tabsL}px">
			{#each TABS as t, i (t)}
				<a href="/account?login={login}&tab={t}" onclick={(e) => (e.preventDefault(), nav(`/account?login=${login}&tab=${t}`))}
					class="flex w-[96px] items-center justify-center py-[10px] text-[14px] leading-none transition-colors duration-200 {i ? '-ml-[10px]' : ''}
						{tab === t ? 'bg-gradient-to-b from-[#a8a8a8] to-[#6e6e6e] font-medium text-white' : 'bg-[#262626] font-light text-neutral-300 hover:text-white'}"
					style="clip-path: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%)">
					{t[0].toUpperCase() + t.slice(1)}
				</a>
			{/each}
		</nav>
	{/if}


	{#if detail && sidePanel}
		<!-- SELECT chevron down the whole left edge (the reference mirrored): a strip SEL_TOP wide at the very top and
		     bottom that widens to its tip at mid-height, pointing right at the cards. Dotted grey, no edge line. Decoration only. -->
		<svg transition:fade={{ duration: 200 }} class="pointer-events-none absolute top-0 left-0 z-10 h-full" style="width:{selTip + 4}px"
			viewBox="0 0 {selTip + 4} {winH}" preserveAspectRatio="none" aria-hidden="true">
			<defs>
				<pattern id="sel-dots" width="4" height="4" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.9" fill="rgba(0,0,0,0.2)" /></pattern>
				<clipPath id="sel-clip"><polygon points="0,0 {SEL_TOP},0 {selTip},{winH / 2} {SEL_TOP},{winH} 0,{winH}" /></clipPath>
			</defs>
			<g clip-path="url(#sel-clip)">
				<rect width="100%" height="100%" fill="#7c8086" />
				<rect width="100%" height="100%" fill="url(#sel-dots)" />
			</g>
		</svg>
		<!-- SELECT badge, the reference mirrored: a chunky-outlined pentagon, flat on the left, pointed on the right
		     (towards the cards); inside the point an eject mark (bar + filled triangle) pointing right; the word
		     runs down the flat side, filling its height, letter tops facing the point, as in the reference. -->
		<svg transition:fade={{ duration: 200 }} class="pointer-events-none absolute z-10" style="left:16px; top:calc(50% - 90px)"
			width="66" height="180" viewBox="0 0 94 256" fill="none" aria-hidden="true">
			<!-- Traced from the reference (pixel map of the screenshot), mirrored to point right: flat left side, straight
			     right side at top and bottom with a shallow chevron point between them (apex mid-height), a tall filled
			     triangle just inside the point, big letters filling the body with their tops facing the point. -->
			<path d="M14 12H46Q56 12 56 22V43L88 130L56 217V238Q56 248 46 248H14Q4 248 4 238V22Q4 12 14 12Z" stroke="#111" stroke-width="6" stroke-linejoin="round" />
			<path d="M52 82L74 130L52 178Z" fill="#111" stroke="#111" stroke-width="2" stroke-linejoin="round" />
			<text x="28" y="18" transform="rotate(90 28 18)" fill="#111" font-family="'Arial Black', Arial, sans-serif" font-size="42" font-weight="900"
				textLength="220" lengthAdjust="spacingAndGlyphs" dominant-baseline="central">SELECT</text>
		</svg>
	{/if}
	<!-- Panel: wide card list on /account, a narrow column beside the tabs on a detail page. -->
	<div class="absolute" inert={detail && !sidePanel}
		style="top:{detail ? 29 : 90}px; left:{detail ? LIST_L : 39}px; width:{detail ? `${listW}px` : 'calc(100% - 78px)'}; opacity:{detail && !sidePanel ? 0 : 1}; visibility:{detail && !sidePanel ? 'hidden' : 'visible'};
			transition:none">
		<!-- Detail: 340px, grows with the cards up to 600px (never past the window), then scrolls instead of cropping the cards. -->
		<div class="{detail ? 'overflow-y-auto overflow-x-hidden' : 'overflow-hidden'}"
			style="height:{detail ? `min(${Math.max(340, smallListH)}px, 600px, calc(100vh - {detail ? 69 : 130}px))` : `${Math.max(Math.max(0, ...Object.values(bigH)) + 92, 392)}px`};
				transition:none">
				<div class="flex flex-wrap" style="gap:{detail ? GAP : 24}px">
				{#each accounts as a, ai (a.id)}
					{@const at = allTime(a)}
					{@const cs = a.candles}
					{@const lo = Math.min(...cs.map((k) => k.l), 0)}
					{@const hi = Math.max(...cs.map((k) => k.h), 0)}
					{@const y = (v: number) => 47 - ((v - lo) / (hi - lo || 1)) * 46}
					{@const sel = detail && a.login === login}
					<!-- Thick dark-grey frame, a little darker at the bottom, lit from the top-left; the card sits recessed in it. -->
					<!-- Detail list: the open account's frame is white/grey; any other card opens on click and its frame turns a little whiter on hover. -->
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<div class="group/card relative {detail && !sel ? 'cursor-pointer openable' : ''} {sel ? 'sel-frame' : 'card-frame'}"
						onclick={(e) => { if (detail && !sel && !(e.target as HTMLElement).closest('a,button')) nav(`/account?login=${a.login}&tab=${tab}`); }}
							style="border-radius:{detail ? 9 : 38}px; padding:{detail ? `${FRAME}px ${FRAME}px ${BAND}px` : '16px'};
								transform:{detail ? `translateX(${cardX[ai]}px) skewX(-${SLANT}deg)` : 'none'};
								transition:none">
						<!-- Light rim only around the top-left corner, fading out along the top and left edges. -->
						<div class:hidden={sel || detail} class="pointer-events-none absolute inset-0 p-[1.5px]" style:border-radius="{detail ? 9 : 38}px"
							style="background: linear-gradient(135deg, rgba(255,255,255,0.16) 0%, rgba(255,255,255,0.05) 18%, rgba(255,255,255,0) 32%);
								-webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor;
								mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0)"></div>
					<article class="relative overflow-hidden bg-gradient-to-b from-[#262626] to-[#1c1c1c] text-white"
						style="border-radius:{detail ? 5 : 22}px; width:{detail ? CARD_W : 300}px; height:{detail ? CARD_H : (bigH[a.id] ?? 0)}px;
							transition:none">
						{#if detail}
														<!-- Login and broker at the top, profit / drawdown / state on the dark band at the bottom. Every line
							     follows the card's slant (its box is skewed with the card) while its letters stay upright. -->
							<div class="absolute inset-0">
								<!-- Edit (pencil) in the card's top-right corner, its top and bottom on the Login label's cap line and baseline. No action yet. -->
								<button type="button" aria-label="Edit {a.login}" title="Edit account" class="up absolute top-[20px] right-[18px] cursor-pointer text-neutral-300 hover:text-white">
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" class="block h-[13px] w-[13px]">
										<path d="M11.2 2.6l2.2 2.2-8 8-2.9.7.7-2.9z" /><path d="M9.8 4l2.2 2.2" />
									</svg>
								</button>
								<dl class="flex flex-col gap-[10px] px-[26px] pt-[18px]">
									<div><dt class="up text-[12px] font-light">Login</dt><dd class="up mt-0.5 text-[14px] font-medium">{a.login}</dd></div>
									<div>
										<dt class="up text-[12px] font-light">Broker</dt>
										<dd class="up mt-0.5 text-[14px] font-medium">
											<a href={brokerUrl(a.server)} target="_blank" rel="noreferrer" class="inline-flex items-center gap-1 whitespace-nowrap hover:underline">
												{broker(a.server)}
												<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]"><path d="M5 4.5h6.5V11.1" /><path d="M4.25 11.75 11.5 4.5" /></svg>
											</a>
										</dd>
									</div>
									<div class="min-w-0"><dt class="up text-[12px] font-light">Server</dt><dd class="up mt-0.5 max-w-[130px] truncate text-[14px] font-medium" title={serverName(a.server)}>{serverName(a.server)}</dd></div>
								</dl>
							</div>
						{:else}
							<div bind:offsetHeight={() => bigH[a.id], (v) => { if (v) bigH[a.id] = v; }} class="absolute top-0 left-0 w-[300px] p-[18px]">
						<!-- Candles -->
						<svg class="mt-[64px] mb-4 block h-[48px]" style="width:{Math.max(cs.length, 1) * 10}px" viewBox="0 0 {Math.max(cs.length, 1) * 10} 48" stroke-width="1">
							{#each cs as k, i (i)}
								<line x1={i * 10 + 4} x2={i * 10 + 4} y1={y(k.h)} y2={y(Math.max(k.o, k.c))} stroke="rgba(255,255,255,0.3)" />
								<line x1={i * 10 + 4} x2={i * 10 + 4} y1={y(Math.min(k.o, k.c))} y2={y(k.l)} stroke="rgba(255,255,255,0.3)" />
								<rect x={i * 10 + 1} width="6" y={y(Math.max(k.o, k.c))} height={Math.max(Math.abs(y(k.o) - y(k.c)), 1)}
									fill="rgba(255,255,255,0.18)" stroke="rgba(255,255,255,0.4)" />
							{/each}
						</svg>
						<!-- Checker strip -->
						<svg class="absolute top-[18px] right-[18px] h-[112px] w-[48px]" viewBox="0 0 3 7" shape-rendering="crispEdges" fill="#e5e5e5">
							<rect x="0" y="0" width="1" height="1" /><rect x="1" y="1" width="1" height="1" />
							<rect x="0" y="2" width="1" height="1" /><rect x="2" y="2" width="1" height="1" />
							<rect x="1" y="3" width="1" height="1" /><rect x="0" y="4" width="1" height="1" />
							<rect x="2" y="4" width="1" height="1" /><rect x="1" y="5" width="1" height="1" />
							<rect x="2" y="6" width="1" height="1" />
						</svg>

						<dl class="grid grid-cols-[120px_1fr] gap-y-[14px] text-[10px]">
							<div><dt class="text-[12px] font-light text-white">Login</dt><dd class="mt-1 text-[14px] font-medium">{a.login}</dd></div>
							<div>
								<dt class="text-[12px] font-light text-white">Pass</dt>
								<dd class="mt-1 flex items-center gap-4 text-[14px] font-medium">
									<span class="min-w-[48px]">{shown[a.id] ?? '•••••••'}</span>
									<button type="button" onclick={() => togglePass(a)} aria-label="Show password" class="cursor-pointer">
										<img src={shown[a.id] != null ? eyeSlashIcon : eyeIcon} alt="" class="block h-3 w-3" />
									</button>
								</dd>
							</div>
							<div>
								<dt class="text-[12px] font-light text-white">Broker</dt>
								<dd class="mt-1 text-[14px] font-medium">
									<a href={brokerUrl(a.server)} target="_blank" rel="noreferrer" class="inline-flex items-center gap-1.5 whitespace-nowrap hover:underline">
										{broker(a.server)}
										<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]"><path d="M5 4.5h6.5V11.1" /><path d="M4.25 11.75 11.5 4.5" /></svg>
									</a>
								</dd>
							</div>
							<div><dt class="text-[12px] font-light text-white">Server</dt><dd class="mt-1 text-[14px] font-medium">{serverName(a.server)}</dd></div>
							<div class="col-span-2 flex items-end">
								<div class="flex-1">
									<dt class="text-[12px] font-light text-white">Balance</dt>
									<dd class="mt-1 flex items-center gap-2 text-[14px] font-medium">
										<span class="flex items-baseline gap-2 {isBlurred(a) ? 'blur-[6px] select-none' : ''}">
											${a.balance.toFixed(2)}
											{#if at}<span class="text-[10px] {at.color}">{at.text}</span>{/if}
										</span>
										<button type="button" onclick={() => toggleBlur(a)} aria-label={isBlurred(a) ? 'Show balance' : 'Hide balance'} class="cursor-pointer">
											<img src={isBlurred(a) ? eyeSlashIcon : eyeIcon} alt="" class="block h-3 w-3" />
										</button>
									</dd>
								</div>
								<div class="w-[44px] text-right"><dt class="text-[12px] font-light text-white">VaR</dt><dd class="mt-1 text-[14px] font-medium">{a.var95 == null ? "—" : `${a.var95.toFixed(1)}%`}</dd></div>
								<div class="w-[56px] text-right"><dt class="text-[12px] font-light text-white">Sharpe</dt><dd class="mt-1 text-[14px] font-medium">{a.sharpe == null ? "—" : a.sharpe.toFixed(2)}</dd></div>
							</div>
							<div class="col-span-2">
								<dt class="text-[12px] font-light text-white">Strategy</dt>
								<dd class="mt-1 text-[14px] font-medium">{bookName(a.book)} [{a.sleeves}]</dd>
							</div>
						</dl>

							</div>
						{/if}
						<!-- Recess: the frame lip shades the card's top edge, a faint catch-light on the bottom. -->
						<div class="pointer-events-none absolute inset-0" style="border-radius:{detail ? 5 : 22}px; box-shadow: inset 3px 3px 6px rgba(0,0,0,0.6), inset -1px -1px 0 rgba(255,255,255,0.06)"></div>
					</article>
					<!-- Detail: profit, drawdown and state sit on the frame's thick bottom band, like the list card's Detail row. -->
					{#if detail}
							<div class="absolute inset-x-0 bottom-0 flex items-center gap-3 px-[16px]" style="height:{BAND}px">
								<span class="up text-[14px] font-black italic {pnlOf(a) == null ? 'text-neutral-400' : pnlOf(a)! < 0 ? (sel ? 'text-red-600' : 'text-red-400') : sel ? 'text-blue-700' : 'text-blue-500'}">{pnlOf(a) == null ? '—' : `${pnlOf(a)! >= 0 ? '+' : ''}${pnlOf(a)!.toFixed(2)}%`}</span>
								<span class="up text-[11px] font-light {sel ? 'text-neutral-700' : 'text-neutral-300'}">DD <b class="text-[12px] font-black italic {sel ? 'text-black' : 'text-white'}">{a.mdd == null ? '—' : `${a.mdd.toFixed(1)}%`}</b></span>
								<!-- State: just the square, as on /account (green = active). -->
								<span class="up ml-auto size-[7px] shrink-0 rounded-[2.5px] {a.active ? 'bg-[#34c759] shadow-[0_0_8px_#34c759]' : 'bg-neutral-600'}" title={a.active ? 'Active' : 'Inactive'}></span>
							</div>
					{/if}
					<!-- Detail sits on the frame's thick bottom band, under the card. -->
					{#if !detail}
						<div class="mt-[12px] flex h-12 items-center gap-4">
							<button type="button" onclick={() => nav(`/account?login=${a.login}&tab=history`)}
								class="h-12 cursor-pointer rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] px-8 text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0]">
								Detail
							</button>
							<span class="flex h-[12px] items-center gap-2 text-[12px] leading-none font-normal {a.active ? 'text-white' : 'text-neutral-500'}">
								<span class="size-[7px] shrink-0 rounded-[2.5px] {a.active ? 'bg-[#34c759] shadow-[0_0_8px_#34c759]' : 'bg-neutral-600'}"></span>
								{a.active ? 'Active' : 'Inactive'}
							</span>
						</div>
					{/if}
					</div>
				{/each}
			</div>
		</div>
	</div>
	{#if detail && tab === 'history' && selected}
		<!-- Equity curve, closed positions under it; the column scrolls. It runs to the right edge, under the
		     floating phone, so the wheel scrolls the page anywhere outside the phone; the padding keeps content clear of it. -->
		<div data-tab-scroll transition:fade={{ duration: 200 }} class="absolute top-[74px] right-0 bottom-0 overflow-y-auto pt-[20px] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" style="left:{padL}px; padding-right:46px">
			<!-- One 14px line at top 94 like General and Strategy; the taller range pill hangs centred on it. -->
			<h2 class="flex h-[14px] items-center gap-3 whitespace-nowrap text-[14px] leading-none font-medium text-white">
				Equity Curve
				{#if equityChange != null}
					<span class="text-sm font-normal {Math.abs(equityChange) < 0.005 ? 'text-white' : equityChange > 0 ? 'text-blue-400' : 'text-red-400'}">
						{equityChange >= 0 ? '+' : ''}{equityChange.toFixed(2)}%
					</span>
				{/if}
				<RangePicker bind:value={equityRange} source={pageEl} />
			</h2>
			<div class="mt-7 h-[392px] w-[min(920px,100%)]">
				<EquityChart accountId={selected.id} bind:change={equityChange} bind:range={equityRange} />
			</div>
			<div class="mt-8">
				<ClosedPositions accountId={selected.id} />
			</div>
			<div class="mt-2">
				<PnlCalendar accountId={selected.id} />
			</div>
		</div>
	{/if}
	{#if detail && tab === 'strategy' && selected}
		<div data-tab-scroll transition:fade={{ duration: 200 }} style="left:{padL}px; padding-right:46px" class="absolute top-[94px] right-0 bottom-0 overflow-y-auto pb-10 text-white [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
			<!-- Same width as the History tab's content. -->
			<div class="w-[min(920px,100%)]">
			<div class="flex items-start gap-6">
			<button type="button" onclick={() => nav(`/account/edit?login=${login}&tab=strategy`)} class="order-last ml-auto flex h-12 w-[88px] shrink-0 cursor-pointer items-center justify-center rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0]">
				Edit
			</button>
			<dl class="flex flex-wrap gap-x-[32px] gap-y-3 whitespace-nowrap">
				<div><dt class="text-[12px] font-light">Strategy Name</dt><dd class="mt-1 text-[14px] font-medium">{bookName(selected.book)}</dd></div>
				<div><dt class="text-[12px] font-light">Total</dt><dd class="mt-1 text-[14px] font-medium">{selected.sleeves} Strategies</dd></div>
				<div><dt class="text-[12px] font-light">Risk</dt><dd class="mt-1 text-[14px] font-medium">{selected.risk ?? '—'}</dd></div>
				<div><dt class="text-[12px] font-light">Correlation</dt><dd class="mt-1 text-[14px] font-medium" title={selected.correlation?.toFixed(3)}>{corrWord(selected.correlation)}</dd></div>
				<div><dt class="text-[12px] font-light">Diversification</dt><dd class="mt-1 text-[14px] font-medium" title={selected.diversification == null ? undefined : `${selected.diversification.toFixed(2)}x`}>{divWord(selected.diversification, selected.sleeves)}</dd></div>
			</dl>
			</div>
			<h2 class="mt-[38px] mb-5 flex items-center gap-3 text-[14px] font-light text-neutral-300">
				Strategies
				<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-4 w-4"><path d="M2.5 8h11M9 3.5 13.5 8 9 12.5" /></svg>
			</h2>
			<StrategyList strategies={selected.strategies} />
			<div class="mt-[38px]">
				<BookBacktest accountId={selected.id} />
			</div>
			</div>
		</div>
	{/if}
	</div>
	<!-- Dims the account as the edit page slides over it. -->
	<div class="pointer-events-none absolute inset-0 z-40 bg-black transition-opacity duration-[480ms]" style="opacity: {editing ? 0.4 : 0}"></div>
	<section class="absolute inset-0 z-50 bg-[#121212]" style="transform: translateX({editing ? '0' : '104%'}); transition: {ready ? PUSH : 'none'}" inert={!editing}>
		<a href="/account?login={login}&tab=strategy" onclick={(e) => (e.preventDefault(), nav(`/account?login=${login}&tab=strategy`))} aria-label="Back"
			class="absolute top-[30px] left-[38px] flex cursor-pointer p-2 text-neutral-300 hover:text-white">
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
				<path d="M13.5 8h-11M7 3.5 2.5 8 7 12.5" />
			</svg>
		</a>
		<div style="right:{padR}px" class="absolute top-[100px] bottom-0 left-[84px] overflow-y-auto pb-10 text-white [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
			{#if selected}<StrategyList strategies={editStrategies} label={`Strategies [${editStrategies.length}]`} full ondelete={deleteStrategy} pool={available} onadd={addStrategy} />{/if}
			<button type="button" class="mt-8 flex h-12 w-[88px] cursor-pointer items-center justify-center rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0]" onclick={() => (runParams = { book: currentBook, removed: [...currentRemoved], risk: displayRisk })}>Run</button>
			{#if runParams && selected}
				{#key runParams}
					<div class="mt-[38px]"><BookBacktest accountId={selected.id} run={runParams} /></div>
				{/key}
			{/if}
		</div>
		<div class="absolute top-[38px] left-[84px] flex items-center gap-6 text-white">
			<div>
			<div class="text-[12px] leading-4 font-light">Strategy Name</div>
			<div class="mt-1 flex h-[22px] items-center gap-2">
				{#if renaming}
					<span contenteditable="plaintext-only" bind:textContent={draft} onkeydown={(e) => (e.key === 'Enter' || e.key === 'Escape') && (e.preventDefault(), nameKey(e))} onblur={() => (renaming = false)} use:focusSelect
						role="textbox" tabindex="0" class="text-[14px] font-medium whitespace-nowrap outline-none"></span>
				{:else}
					<span class="text-[14px] font-medium">{displayName}</span>
				{/if}
				<!-- Stays in place while editing (hidden), so nothing beside it moves. -->
				<button type="button" aria-label="Rename" onclick={() => ((draft = displayName), (renaming = true), (bookOpen = false))} class="cursor-pointer p-0.5 text-neutral-400 hover:text-white {renaming ? 'invisible' : ''}">
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3.5 w-3.5"><path d="M11 2.5 13.5 5 5.5 13H3v-2.5z M9.5 4l2.5 2.5" /></svg>
				</button>
			</div>
			</div>
			<div>
			<div class="text-[12px] leading-4 font-light">Risk</div>
			<div class="mt-1 flex h-[22px] items-center gap-2">
				{#if riskEditing}
					<span contenteditable="plaintext-only" bind:textContent={riskDraft} onkeydown={(e) => (e.key === 'Enter' || e.key === 'Escape') && (e.preventDefault(), riskKey(e))} onblur={() => (riskEditing = false)} use:focusSelect
						role="textbox" tabindex="0" inputmode="decimal" class="text-[14px] font-medium whitespace-nowrap outline-none"></span>
				{:else}
					<span class="text-[14px] font-medium">{displayRisk ?? '—'}</span>
				{/if}
				<button type="button" aria-label="Edit risk" onclick={() => ((riskDraft = displayRisk == null ? '' : String(displayRisk)), (riskEditing = true))} class="cursor-pointer p-0.5 text-neutral-400 hover:text-white {riskEditing ? 'invisible' : ''}">
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3.5 w-3.5"><path d="M11 2.5 13.5 5 5.5 13H3v-2.5z M9.5 4l2.5 2.5" /></svg>
				</button>
			</div>
			</div>
			{#if !renaming}
				<div class="relative">
					<button type="button" onclick={() => (bookOpen = !bookOpen)} class="cursor-pointer text-[14px] font-medium text-white underline underline-offset-4 hover:text-neutral-300">Preset</button>
					{#if bookOpen}
						<!-- Click-away layer -->
						<div class="fixed inset-0 z-10" onclick={() => (bookOpen = false)} role="presentation"></div>
						<div transition:fade={{ duration: 120 }}
							class="absolute -top-[14px] -left-[18px] z-20 min-w-[220px] overflow-hidden rounded-2xl border border-white/20 bg-white/10 p-1.5 shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.25)] backdrop-blur-xl backdrop-saturate-150">
							{#each books as b (b)}
								<button type="button" onclick={() => pickBook(b)}
									class="flex w-full cursor-pointer items-center justify-between gap-4 rounded-xl px-3 py-2 text-left text-[14px] hover:bg-white/15 {b === currentBook ? 'font-medium text-white' : 'font-light text-neutral-200'}">
									{bookName(b)}
									{#if b === currentBook}<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" class="block h-3.5 w-3.5"><path d="M3 8.5 6.5 12 13 4.5" /></svg>{/if}
								</button>
							{:else}
								<div class="px-3 py-2 text-[13px] font-light text-neutral-400">No books</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
		</div>
		{#if dirty}
			<button type="button" onclick={update} disabled={saving} transition:fade={{ duration: 150 }}
				style="right:{updateR}px" class="absolute top-[30px] flex h-12 w-[100px] cursor-pointer items-center justify-center rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0] disabled:opacity-60">
				Update
			</button>
		{/if}
	</section>
	<!-- Agent chat iPhone at the top right (list and every account tab); the closed icon tops-aligns with the tabs. -->
	<AgentPhone login={login ?? accounts[0]?.login ?? ''} phoneClass="absolute top-[30px] right-[46px]" iconClass="absolute top-[29px] right-[46px] h-[44.25px] w-[28.3px]" />
</section>

<style>
	/* Inside a slanted card: undo the slant on each line so letters stand upright, the lines keep the slant. */
	.up {
		transform: skewX(12deg);
		white-space: nowrap;
	}
	.card-frame {
		background: linear-gradient(180deg, #1a1a1a 55%, #141414 100%);
	}
	.card-frame.openable:hover {
		background: linear-gradient(180deg, #2e2e2e 55%, #262626 100%);
	}
	/* The open account's frame: flat white-to-grey (no bloom) that slowly pulses between whiter and greyer. */
	@property --lit {
		syntax: '<percentage>';
		inherits: false;
		initial-value: 90%;
	}
	.sel-frame {
		background: linear-gradient(180deg, hsl(0 0% var(--lit)) 0%, hsl(0 0% calc(var(--lit) - 22%)) 100%);
		animation: frame-pulse 4s ease-in-out infinite alternate;
	}
	@keyframes frame-pulse {
		from {
			--lit: 90%;
		}
		to {
			--lit: 62%;
		}
	}
</style>
