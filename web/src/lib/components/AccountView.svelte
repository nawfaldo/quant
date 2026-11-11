<script lang="ts">
	import { circleClose } from '#lib/circleNav.ts';
	import { phoneOpen } from '#lib/agentPhone.svelte.ts';
	import AgentPhone from '#lib/components/AgentPhone.svelte';
	import eyeIcon from '#lib/icons/eye.png';
	import eyeSlashIcon from '#lib/icons/eye-slash.png';
	import type { Account } from '#lib/server/accounts.ts';
	import { fade } from 'svelte/transition';
	import { invalidateAll } from '$app/navigation';
	import EquityChart from '#lib/components/EquityChart.svelte';
	import ClosedPositions from '#lib/components/ClosedPositions.svelte';
	import PnlCalendar from '#lib/components/PnlCalendar.svelte';
	import StrategyList from '#lib/components/StrategyList.svelte';
	import BookBacktest from '#lib/components/BookBacktest.svelte';

	// Shows the account view over a blank page; `close` hides it. The X shrinks the circle back into the Settings button.
	// path: /account (card list) or /account?login=<login>&tab=<tab> (detail). nav moves between them; close leaves to /.
	let { accounts, books = [], path, nav, close }: { accounts: Account[]; books?: string[]; path: string; nav: (p: string) => unknown; close: () => unknown } = $props();

	const TABS = ['general', 'history', 'strategy'] as const;
	const query = $derived(new URL(path, 'http://x').searchParams);
	const login = $derived(query.get('login'));
	const selected = $derived(accounts.find((a) => a.login === login));
	const tab = $derived(query.get('tab') ?? 'general');
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
			nav(`/account?login=${e.login ?? a.login}&tab=general`);
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
	let tabW = $state(0), tabH = $state(0);
	let bigH = $state<Record<number, number>>({});
	let smallH = $state<Record<number, number>>({});
	// Small-card height before it is measured (two label+value rows, 18px padding; same for every account).
	const SMALL_H = 130;
	// Detail list height from the cards' target sizes, not a live measurement: measuring while the cards
	// shrank kept moving the box's height target, so it went thinner first and only then shorter.
	const smallListH = $derived(accounts.reduce((s, a) => s + (smallH[a.id] ?? SMALL_H), 0) + 12 * Math.max(accounts.length - 1, 0));
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
	const phoneDocked = $derived(phoneOpen.loaded && phoneOpen.v && winW >= 1000);
	const padR = $derived(phoneDocked ? 410 : 46);
	const sidePanel = $derived(winW - 397 - padR >= 700);
	const padL = $derived(sidePanel ? 397 : 46);
	const tabsL = $derived(sidePanel ? 397 : 180);
	const updateR = $derived(phoneDocked ? 410 : 90);

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

<svelte:window bind:innerWidth={winW} />

<section class="fixed inset-0 z-40 overflow-hidden bg-[#121212]">
	<div class="absolute inset-0" style="transform: translateX({editing ? '-28%' : '0'}); transition: {ready ? PUSH : 'none'}" inert={editing}>
	<a href="/" onclick={back} aria-label="Close" class="absolute top-[30px] left-[38px] z-20 flex cursor-pointer p-2 text-neutral-300 hover:text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
			<path d="M3 3l10 10M13 3L3 13" />
		</svg>
	</a>
	<!-- Add (plus): between the X and the up arrow, evenly spaced, on every page. No action yet. -->
	<button type="button" aria-label="Add account" title="Add account" class="absolute top-[30px] left-[80px] z-20 flex cursor-pointer p-2 text-neutral-300 hover:text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
			<path d="M8 2.47v11.06M2.47 8h11.06" />
		</svg>
	</button>
	{#if detail}
		<a href="/account" onclick={(e) => (e.preventDefault(), nav('/account'))} aria-label="Back to accounts" transition:fade={{ duration: 200 }}
			class="absolute top-[30px] left-[122px] z-20 flex cursor-pointer p-2 text-neutral-300 hover:text-white">
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
				<path d="M8 13V3M3 8l5-5 5 5" />
			</svg>
		</a>
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
		{#if tab === 'general' && selected}
			<!-- Login, pass, broker, server on one line above the balance row (same fixed-width slots).
			     Pencils edit in place like the strategy edit page; locked while the account is active. -->
			<dl transition:fade={{ duration: 200 }} class="absolute top-[94px] z-20 grid grid-cols-[repeat(3,minmax(0,170px))] gap-y-[14px] text-white" style="left:{padL}px; right:{padR}px">
				{#each [['login', 'Login', ''], ['password', 'Pass', ''], ['broker', 'Broker', 'col-start-1'], ['server', 'Server', '']] as const as [f, label, w] (f)}
					<div class={w}>
						<dt class="text-[12px] font-light">{label}</dt>
						<dd class="mt-1 flex h-[22px] items-center gap-2 text-[14px] font-medium">
							{#if fieldEditing === f}
								<span contenteditable="plaintext-only" bind:textContent={fieldDraft} use:focusSelect role="textbox" tabindex="0"
									onkeydown={(e) => (e.key === 'Enter' || e.key === 'Escape') && (e.preventDefault(), fieldKey(e))} onblur={() => (fieldEditing = null)}
									class="whitespace-nowrap outline-none"></span>
							{:else if f === 'password'}
								<span class="min-w-[48px]">{fieldEdits[selected.id]?.password ?? shown[selected.id] ?? '•••••••'}</span>
								<button type="button" onclick={() => togglePass(selected)} aria-label="Show password" class="cursor-pointer">
									<img src={shown[selected.id] != null ? eyeSlashIcon : eyeIcon} alt="" class="block h-4 w-4" />
								</button>
							{:else if f === 'broker' && fieldEdits[selected.id]?.broker == null}
								<a href={brokerUrl(selected.server)} target="_blank" rel="noreferrer" class="inline-flex items-center gap-1 whitespace-nowrap hover:underline">
									{broker(selected.server)}
									<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]"><path d="M5 4.5h6.5V11.1" /><path d="M4.25 11.75 11.5 4.5" /></svg>
								</a>
							{:else}
								<span class="whitespace-nowrap">{f === 'server' ? serverName(fieldValue(selected, 'broker') + fieldValue(selected, 'server')) : fieldValue(selected, f)}</span>
							{/if}
							<button type="button" aria-label="Edit {label}" disabled={selected.active} title={selected.active ? 'Deactivate the account to edit' : undefined}
								onclick={() => ((fieldDraft = f === 'password' ? (fieldEdits[selected.id]?.password ?? shown[selected.id] ?? '') : fieldValue(selected, f)), (fieldEditing = f))}
								class="cursor-pointer p-0.5 text-neutral-400 hover:text-white disabled:cursor-not-allowed disabled:hover:text-neutral-400 {fieldEditing === f ? 'invisible' : ''}">
								<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3.5 w-3.5"><path d="M11 2.5 13.5 5 5.5 13H3v-2.5z M9.5 4l2.5 2.5" /></svg>
							</button>
						</dd>
					</div>
				{/each}
			</dl>
			{#if fieldsDirty}
				<button type="button" onclick={updateFields} disabled={saving} transition:fade={{ duration: 150 }}
					style="right:{updateR}px" class="absolute top-[30px] z-20 flex h-12 w-[100px] cursor-pointer items-center justify-center rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0] disabled:opacity-60">
					Update
				</button>
			{/if}
			<!-- Balance, equity, Sharpe, VaR; balance/equity follow the card's hide toggle. -->
			<dl transition:fade={{ duration: 200 }} class="absolute top-[210px] z-20 grid grid-cols-[repeat(4,minmax(0,170px))] gap-y-[14px] text-white" style="left:{padL}px; right:{padR}px">
				{#each [['Balance', `$${selected.balance.toFixed(2)}`, true], ['Equity', selected.equity == null ? '—' : `$${selected.equity.toFixed(2)}`, true], ['Sharpe', selected.sharpe == null ? '—' : selected.sharpe.toFixed(2), false], ['VaR', selected.var95 == null ? '—' : `${selected.var95.toFixed(1)}%`, false], ['MDD', selected.mdd == null ? '—' : `${selected.mdd.toFixed(1)}%`, false], ['PnL%', !selected.initialBalance ? '—' : `${selected.balance >= selected.initialBalance ? '+' : ''}${(((selected.balance - selected.initialBalance) / selected.initialBalance) * 100).toFixed(2)}%`, false]] as const as [k, v, money] (k)}
					<div class={k === 'Sharpe' ? 'col-start-1' : ''}><dt class="text-[12px] font-light">{k}</dt><dd class="mt-1 flex items-center gap-3 text-[14px] font-medium tabular-nums">
						<span class="{money && isBlurred(selected) ? 'blur-[6px] select-none' : ''} {k === 'MDD' && v !== '—' ? 'text-red-400' : k === 'PnL%' && v !== '—' ? (v.startsWith('-') ? 'text-red-400' : 'text-blue-500') : ''}">{v}</span>
						{#if k === 'Equity'}
							<button type="button" onclick={() => toggleBlur(selected)} aria-label={isBlurred(selected) ? 'Show balance and equity' : 'Hide balance and equity'} class="cursor-pointer">
								<img src={isBlurred(selected) ? eyeSlashIcon : eyeIcon} alt="" class="block h-4 w-4" />
							</button>
						{/if}
					</dd></div>
				{/each}
			</dl>
			<!-- Below the tab list: switch the whole account on or off. -->
			<div transition:fade={{ duration: 200 }} class="absolute top-[332px] z-20 flex items-center gap-6" style="left:{padL}px">
			<button type="button" onclick={() => toggleActive(selected)} disabled={toggling}
				class="flex h-12 cursor-pointer items-center justify-center rounded-full bg-gradient-to-b px-7 text-[14px] font-medium disabled:cursor-wait disabled:opacity-60
					{selected.active ? 'from-[#ff8a8a] to-[#a33a3a] text-white hover:from-[#ffa0a0] hover:to-[#b84848]' : 'from-[#f2f2f2] to-[#9a9a9a] text-black hover:from-white hover:to-[#b0b0b0]'}">
				{selected.active ? 'Deactivate' : 'Activate'}
			</button>
			<button type="button" onclick={() => deleteAccount(selected)} disabled={toggling} aria-label="Delete account" title="Delete account"
				class="flex cursor-pointer p-1 text-[#ff6b6b] hover:text-[#ff8a8a] disabled:cursor-wait disabled:opacity-60">
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" class="block h-[18px] w-[18px]">
					<path d="M2.5 4h11M6 4V2.5h4V4M3.75 4l.75 9.5h7l.75-9.5M6.5 6.5v4.5M9.5 6.5v4.5" />
				</svg>
			</button>
			</div>
		{/if}
	{/if}


	<!-- Panel: wide card list on /account, a narrow column beside the tabs on a detail page. -->
	<div class="absolute" inert={detail && !sidePanel}
		style="top:70px; left:46px; width:{detail ? '320px' : 'calc(100% - 92px)'}; opacity:{detail && !sidePanel ? 0 : 1}; visibility:{detail && !sidePanel ? 'hidden' : 'visible'};
			transition:{ready ? `top 280ms ${EASE}, left 280ms ${EASE}, width 280ms ${EASE}, opacity 200ms, visibility 200ms` : 'none'}">
		<!-- Folder tab -->
		<!-- Folder tab drawn in real pixels so both slants are the same 8px whatever the label width. -->
		<div class="relative z-10 inline-block" bind:clientWidth={tabW} bind:clientHeight={tabH}>
			<svg class="absolute inset-0 h-full w-full" fill="#121212" stroke="#3a3a3a">
				<path d="M0.5 {tabH} L8.5 0.5 H{tabW - 8.5} L{tabW - 0.5} {tabH}" />
			</svg>
			<span class="relative block px-6 pt-1.5 pb-1 text-[14px] font-medium whitespace-nowrap text-white">Accounts</span>
		</div>
		<!-- Detail: 340px, grows with the cards up to 600px (never past the window), then scrolls instead of cropping the cards. -->
		<div class="border border-[#3a3a3a] {detail ? 'overflow-y-auto overflow-x-hidden' : 'overflow-hidden'}"
			style="height:{detail ? `min(${Math.max(340, smallListH + 42)}px, 600px, calc(100vh - 110px))` : `${Math.max(Math.max(0, ...Object.values(bigH)) + 66, 392)}px`}; padding:{detail ? 20 : 32}px;
				transition:{ready ? `height 280ms ${EASE}, padding 280ms ${EASE}` : 'none'}">
			<div class="flex flex-wrap" style="gap:{detail ? 12 : 24}px">
				{#each accounts as a (a.id)}
					{@const at = allTime(a)}
					{@const cs = a.candles}
					{@const lo = Math.min(...cs.map((k) => k.l), 0)}
					{@const hi = Math.max(...cs.map((k) => k.h), 0)}
					{@const y = (v: number) => 47 - ((v - lo) / (hi - lo || 1)) * 46}
					{@const sel = detail && a.login === login}
					<article class="relative overflow-hidden rounded-[22px] bg-gradient-to-b from-[#262626] to-[#1c1c1c] text-white"
						style="width:{detail ? 278 : 300}px; height:{detail ? (smallH[a.id] ?? SMALL_H) : (bigH[a.id] ?? 0)}px;
							transition:{ready ? `width 280ms ${EASE}, height 280ms ${EASE}` : 'none'}">
						{#if detail}
							<div bind:offsetHeight={smallH[a.id]} in:fade={{ duration: 200, delay: 150 }} out:fade={{ duration: 80 }} class="absolute top-0 left-0 w-[278px] p-[18px]">
								<dl class="grid grid-cols-[116px_minmax(0,1fr)] gap-y-[12px]">
									<div class="col-span-2"><dt class="text-[12px] font-light">Login</dt><dd class="mt-0.5 text-[14px] font-medium">{a.login}</dd></div>
									<div>
										<dt class="text-[12px] font-light">Broker</dt>
										<dd class="mt-0.5 text-[14px] font-medium">
											<a href={brokerUrl(a.server)} target="_blank" rel="noreferrer" class="inline-flex items-center gap-1 whitespace-nowrap hover:underline">
												{broker(a.server)}
												<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]"><path d="M5 4.5h6.5V11.1" /><path d="M4.25 11.75 11.5 4.5" /></svg>
											</a>
										</dd>
									</div>
									<div><dt class="text-[12px] font-light">Server</dt><dd class="mt-0.5 truncate text-[14px] font-medium" title={serverName(a.server)}>{serverName(a.server)}</dd></div>
								</dl>
							</div>
							<button type="button" onclick={() => nav(`/account?login=${a.login}&tab=${sel ? tab : 'general'}`)} aria-label="Open {a.login}" disabled={sel}
								in:fade={{ duration: 200, delay: 150 }} out:fade={{ duration: 80 }}
								class="absolute top-[8px] right-[8px] flex h-[44px] w-[86px] cursor-pointer items-center disabled:cursor-default justify-center rounded-full
									{sel ? 'bg-[#333333] text-neutral-500' : 'bg-gradient-to-b from-[#f2f2f2] to-[#c8c8c8] text-neutral-700 hover:from-white hover:to-[#d8d8d8]'}">
								<svg viewBox="0 0 30 12" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" class="block h-4 w-10">
									<path d="M1 6h27.5M6 1.5 10.5 6 6 10.5M15 1.5 19.5 6 15 10.5M24 1.5 28.5 6 24 10.5" />
								</svg>
							</button>
						{:else}
							<div bind:offsetHeight={() => bigH[a.id], (v) => { if (v) bigH[a.id] = v; }} in:fade={{ duration: 200, delay: 150 }} out:fade={{ duration: 80 }} class="absolute top-0 left-0 w-[300px] p-[18px]">
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

						<button type="button" onclick={() => nav(`/account?login=${a.login}&tab=general`)} class="mt-[14px] h-12 cursor-pointer rounded-full bg-gradient-to-b from-[#f2f2f2] to-[#9a9a9a] px-8 text-[14px] font-medium text-black hover:from-white hover:to-[#b0b0b0]">
							Detail
						</button>
							</div>
						{/if}
					</article>
				{/each}
			</div>
		</div>
	</div>
	{#if detail && tab === 'history' && selected}
		<!-- Equity curve, closed positions under it; the column scrolls. It runs to the right edge, under the
		     floating phone, so the wheel scrolls the page anywhere outside the phone; the padding keeps content clear of it. -->
		<div transition:fade={{ duration: 200 }} class="absolute top-[94px] right-0 bottom-0 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" style="left:{padL}px; padding-right:{phoneDocked ? 458 : 46}px">
			<h2 class="flex items-baseline gap-3 pt-1.5 pb-1 text-[14px] font-medium text-white">
				Equity Curve
				{#if equityChange != null}
					<span class="text-sm font-normal {Math.abs(equityChange) < 0.005 ? 'text-white' : equityChange > 0 ? 'text-blue-400' : 'text-red-400'}">
						{equityChange >= 0 ? '+' : ''}{equityChange.toFixed(2)}%
					</span>
				{/if}
			</h2>
			<div class="mt-4 h-[392px] w-[min(920px,100%)]">
				<EquityChart accountId={selected.id} bind:change={equityChange} />
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
		<div transition:fade={{ duration: 200 }} style="left:{padL}px; right:{padR}px" class="absolute top-[94px] bottom-0 overflow-y-auto pb-10 text-white [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
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
