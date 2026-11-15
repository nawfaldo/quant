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
	import { onDestroy, tick, untrack } from 'svelte';
	import { page } from '$app/state';
	import { pushState } from '$app/navigation';
	import { scale } from 'svelte/transition';
	import { pauseGlassWithin } from '#lib/liquidGlass.ts';
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
	type DesktopScene = { id: number; layout: number; slots: { symbol: string; minutes: number }[]; bars: (Bar[] | undefined)[] };
	let sceneCache = $state<Record<number, DesktopScene>>(untrack(() => ({
		[data.activeDesktopId]: { id: data.activeDesktopId, layout: data.layout, slots: data.slots, bars: [data.bars] }
	})));
	let layoutOpen = $state(false);
	let desktops = $state(untrack(() => data.desktops));
	let activeDesktopId = $state(untrack(() => data.activeDesktopId));
	const activeScene = $derived(sceneCache[activeDesktopId]);
	const layout = $derived(activeScene.layout);
	const desktopCount = $derived(desktops.length);
	const chartLayers = new Map<number, HTMLDivElement>();
	let bottomSection: HTMLElement;
	let desktopSurface = $state<HTMLDivElement>();
	let desktopTrack = $state<HTMLDivElement>();
	let slidingDesktop = $state(false);
	function desktopStride(width: number) {
		// Spaces-style black gutter, scaled to the viewport without shrinking desktops.
		return width + Math.round(Math.min(88, Math.max(24, width * 0.045)));
	}

	function copyCanvases(source: HTMLElement, copy: HTMLElement) {
		const canvases = copy.querySelectorAll('canvas');
		source.querySelectorAll('canvas').forEach((canvas, index) => {
			canvases[index].getContext('2d')?.drawImage(canvas, 0, 0);
		});
	}
	function registerChartLayer(node: HTMLDivElement, id: number) {
		chartLayers.set(id, node);
		return { destroy() { chartLayers.delete(id); } };
	}

	// The animation strip contains only frozen views. Live charts stay mounted
	// at their normal size behind it and never move or rebuild during the slide.
	function snapshotDesktop(id: number, offset: number): HTMLElement {
		const source = chartLayers.get(id);
		if (!source || !desktopTrack || !chartsEl) throw new Error('Desktop not prepared');
		const snapshot = document.createElement('div');
		snapshot.className = 'absolute inset-0 flex min-h-0 flex-col bg-[#121212]';
		const chartSection = document.createElement('section');
		chartSection.className = 'relative min-h-0 flex-[2] bg-[#121212]';
		const charts = source.cloneNode(true) as HTMLElement;
		copyCanvases(source, charts);
		charts.style.visibility = 'visible';
		charts.removeAttribute('aria-hidden');
		chartSection.appendChild(charts);
		const bottom = bottomSection.cloneNode(true) as HTMLElement;
		copyCanvases(bottomSection, bottom);
		snapshot.append(chartSection, bottom);
		snapshot.inert = true;
		snapshot.setAttribute('aria-hidden', 'true');
		Object.assign(snapshot.style, {
			pointerEvents: 'none', contain: 'strict', isolation: 'isolate',
			willChange: 'transform', transform: `translate3d(${offset * desktopStride(desktopTrack.clientWidth)}px, 0, 0)`
		});
		desktopTrack.appendChild(snapshot);
		for (const animation of snapshot.getAnimations({ subtree: true })) animation.pause();
		return snapshot;
	}

	async function slideDesktop(snapshots: HTMLElement[], direction: number, distance: number) {
		const track = desktopTrack;
		const surface = desktopSurface;
		if (!track || !surface) return;
		let animation: Animation | undefined;
		let resumeGlass: (() => void) | undefined;
		let pausedAnimations: Animation[] = [];
		try {
			pausedAnimations = surface.getAnimations({ subtree: true }).filter((a) => a.playState === 'running');
			for (const animation of pausedAnimations) animation.pause();
			resumeGlass = pauseGlassWithin(surface);
			track.style.willChange = 'transform, filter';
			await paintDesktop();
			// Hide the live workspace only after the opaque overlay is painted.
			surface.style.opacity = '0';
			animation = track.animate([
				{ offset: 0, transform: 'translate3d(0, 0, 0)', filter: 'blur(0px)' },
				{ offset: 0.18, filter: 'blur(0.7px)' },
				{ offset: 0.65, filter: 'blur(0.35px)' },
				{ offset: 1, transform: `translate3d(${-direction * distance * desktopStride(track.clientWidth)}px, 0, 0)`, filter: 'blur(0px)' }
			], {
				duration: 420 + 140 * (distance - 1),
				easing: 'cubic-bezier(0.22, 0.61, 0.36, 1)', fill: 'both'
			});
			await animation.finished;
		} finally {
			surface.style.opacity = '';
			animation?.cancel();
			track.style.willChange = '';
			for (const snapshot of snapshots) snapshot.remove();
			for (const animation of pausedAnimations) animation.play();
			resumeGlass?.();
		}
	}
	const MAX_DESKTOPS = 4;
	let savingDesktop = $state(false);
	let desktopError = $state('');
	let desktopMenu = $state<number | null>(null);
	let desktopChanger: HTMLDivElement;
	function focusDesktopMenu(node: HTMLButtonElement) {
		node.focus();
	}
	async function deleteDesktop(id: number, number: number) {
		desktopMenu = null;
		if (savingDesktop || !window.confirm(`Delete desktop ${number}?`)) return;
		savingDesktop = true;
		desktopError = '';
		try {
			const response = await fetch('/api/desktops', {
				method: 'DELETE',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ id })
			});
			if (!response.ok) throw new Error('Desktop delete failed');
			const saved: { desktops: { id: number }[] } = await response.json();
			desktops = saved.desktops;
			delete sceneCache[id];
		} catch {
			desktopError = 'Could not delete desktop. Please try again.';
		} finally {
			savingDesktop = false;
		}
	}
	async function addDesktop() {
		if (savingDesktop || desktopCount >= MAX_DESKTOPS) return;
		savingDesktop = true;
		desktopError = '';
		try {
			const response = await fetch('/api/desktops', { method: 'POST' });
			if (!response.ok) throw new Error('Desktop save failed');
			const saved: { desktops: { id: number }[] } = await response.json();
			desktops = saved.desktops;
		} catch {
			desktopError = 'Could not save desktop. Please try again.';
		} finally {
			savingDesktop = false;
		}
	}
	// Bottom panel height: the PC case shrinks on short screens so the speed line, avatar and door below it still fit.
	let infoH = $state(360);
	// The speed line, avatar and door need ~140px; the case gets what is left (full size at 310px+), and is
	// hidden when less than 60px is left (a very short window) rather than overlapping them.
	const caseRoom = $derived(infoH - 140);
	const caseK = $derived(caseRoom < 60 ? 0 : Math.min(1, caseRoom / 170));
	const caseBottom = $derived(48 + 170 * caseK);
	let layoutPicker: HTMLElement;
	let chartsEl = $state<HTMLElement>(); // all charts, which the layout picker's glass refracts

	const symbols = $derived(activeScene.slots.map((slot) => slot.symbol));

	// Capture the desktop ID when saving; finish writes before switching desktops.
	let pendingSaves: Promise<unknown> = Promise.resolve();
	function save(body: object, desktopId = activeDesktopId) {
		pendingSaves = pendingSaves.then(async () => {
			const response = await fetch('/api/chart-layout', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ ...body, desktopId })
			});
			if (!response.ok) throw new Error('Chart save failed');
		}).catch(() => { desktopError = 'Could not save chart settings. Please try again.'; });
		return pendingSaves;
	}
	const sceneLoads = new Map<number, Promise<DesktopScene>>();
	const deferredScenes = new Map<number, DesktopScene>();
	let disposed = false;
	onDestroy(() => { disposed = true; });
	async function ensureScene(id: number): Promise<DesktopScene> {
		if (sceneCache[id]) return sceneCache[id];
		const pending = sceneLoads.get(id);
		if (pending) return pending;
		const loading = (async () => {
			const response = await fetch(`/api/desktops?id=${id}`);
			if (!response.ok) throw new Error('Desktop load failed');
			const saved: Omit<DesktopScene, 'bars'> = await response.json();
			const count = LAYOUTS[saved.layout].reduce((n, c) => n + c, 0);
			const candles = new Map<string, Promise<Bar[] | undefined>>();
			const bars = await Promise.all(saved.slots.slice(0, count).map((slot) => {
				const key = `${slot.symbol}:${slot.minutes}`;
				if (!candles.has(key)) candles.set(key, (async () => {
					try {
						const response = await fetch(`/api/bars?symbol=${encodeURIComponent(slot.symbol)}&tf=${slot.minutes}`);
						return response.ok ? await response.json() as Bar[] : undefined;
					} catch { return undefined; }
				})());
				return candles.get(key)!;
			}));
			const scene = { ...saved, bars };
			if (!disposed && desktops.some((d) => d.id === id)) {
				if (slidingDesktop) deferredScenes.set(id, scene);
				else sceneCache[id] = scene;
			}
			return scene;
		})();
		sceneLoads.set(id, loading);
		try { return await loading; } finally { sceneLoads.delete(id); }
	}
	async function paintDesktop() {
		await tick();
		await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
	}
	// Prewarm inactive desktops while idle, not when the user starts a slide.
	$effect(() => {
		const ids = desktops.map((d) => d.id);
		if (savingDesktop) return;
		let cancelled = false;
		const warm = async () => {
			for (const id of ids) {
				if (cancelled || disposed) return;
				if (sceneCache[id]) continue;
				try { await ensureScene(id); await paintDesktop(); } catch { /* retry on selection */ }
			}
		};
		const timer = window.setTimeout(() => {
			if ('requestIdleCallback' in window) {
				idle = window.requestIdleCallback(() => { void warm(); }, { timeout: 1000 });
			} else { void warm(); }
		}, 120);
		let idle: number | undefined;
		return () => {
			cancelled = true;
			clearTimeout(timer);
			if (idle !== undefined) window.cancelIdleCallback(idle);
		};
	});
	async function switchDesktop(id: number) {
		if (savingDesktop || id === activeDesktopId) return;
		const from = desktops.findIndex((d) => d.id === activeDesktopId);
		const to = desktops.findIndex((d) => d.id === id);
		if (from < 0 || to < 0) return;
		const direction = to > from ? 1 : -1;
		const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		const route = Array.from({ length: Math.abs(to - from) }, (_, step) => desktops[from + direction * (step + 1)].id);
		const snapshots: HTMLElement[] = [];
		savingDesktop = true;
		desktopMenu = null;
		layoutOpen = false;
		desktopError = '';
		try {
			await pendingSaves;
			await Promise.all((reducedMotion ? [id] : route).map(ensureScene));
			await paintDesktop();
			const response = await fetch('/api/desktops', {
				method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id })
			});
			if (!response.ok) throw new Error('Desktop switch failed');
			const saved: { desktops: { id: number }[]; activeDesktopId: number } = await response.json();
			if (!reducedMotion) snapshots.push(snapshotDesktop(activeDesktopId, 0));
			slidingDesktop = !reducedMotion;
			desktops = saved.desktops;
			activeDesktopId = saved.activeDesktopId;
			await paintDesktop();
			if (!reducedMotion) {
				for (let step = 0; step < route.length; step++) snapshots.push(snapshotDesktop(route[step], direction * (step + 1)));
				await slideDesktop(snapshots, direction, route.length);
			}
		} catch {
			desktopError = 'Could not switch desktop. Please try again.';
		} finally {
			for (const snapshot of snapshots) snapshot.remove();
			if (desktopSurface) desktopSurface.style.opacity = '';
			slidingDesktop = false;
			savingDesktop = false;
			for (const [id, scene] of deferredScenes) {
				if (desktops.some((d) => d.id === id)) sceneCache[id] = scene;
			}
			deferredScenes.clear();
		}
	}
	const shownCount = $derived(LAYOUTS[layout].reduce((n, c) => n + c, 0));

	// Poll for every symbol on screen every POLL_MS (the next poll starts when the last one
	// answers, so a slow tunnel never stacks requests); restarts when that set changes.
	// Polled because the Cloudflare tunnel does not pass server-sent events.
	const POLL_MS = 400;
	const FULL_EVERY = 25; // whole accounts about every 10 s
	const streamKey = $derived([...new Set(symbols.slice(0, shownCount))].sort().join(','));
	$effect(() => {
		// Avoid live table/canvas repaints competing with the compositor slide.
		if (slidingDesktop) return;
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
	onpointerdown={(e) => {
		if (layoutOpen && !layoutPicker.contains(e.target as Node)) layoutOpen = false;
		if (desktopMenu !== null && !desktopChanger.contains(e.target as Node)) desktopMenu = null;
	}}
	onkeydown={(e) => {
		if (e.key === 'Escape') {
			layoutOpen = false;
			desktopMenu = null;
		}
	}}
/>

<main class="relative isolate flex h-screen flex-col bg-black {slidingDesktop ? 'overflow-hidden' : ''}">
	<div bind:this={desktopSurface} class="relative isolate flex min-h-0 flex-1 flex-col bg-[#121212]">
	<!-- The charts, laid out in rows, touching each other. -->
	<section bind:this={chartsEl} class="relative flex min-h-0 flex-[2] flex-col {slidingDesktop ? 'overflow-hidden' : ''}">
		{#each Object.values(sceneCache) as scene (scene.id)}
			<div
				use:registerChartLayer={scene.id}
				class="absolute inset-0 flex min-h-0 flex-col bg-[#121212]"
				style:visibility={scene.id === activeDesktopId ? 'visible' : 'hidden'}
				inert={scene.id !== activeDesktopId || slidingDesktop}
				aria-hidden={scene.id !== activeDesktopId}
			>
				{#each LAYOUTS[scene.layout] as count, r (r)}
					<div class="flex min-h-0 flex-1">
						{#each Array(count) as _, c (c)}
							{@const slot = LAYOUTS[scene.layout].slice(0, r).reduce((n, k) => n + k, 0) + c}
							<div class="min-w-0 flex-1">
								<ChartPane
									bind:symbol={scene.slots[slot].symbol}
									corner={slot !== 0}
									initialMinutes={scene.slots[slot].minutes}
									initialBars={scene.bars[slot]}
									active={scene.id === activeDesktopId && !slidingDesktop}
									onchange={(symbol, minutes) => {
										scene.slots[slot] = { symbol, minutes };
										scene.bars[slot] = undefined;
										save({ slot, symbol, minutes }, scene.id);
									}}
									live={scene.id === activeDesktopId && !slidingDesktop ? live?.candles[scene.slots[slot].symbol] ?? [] : []}
									positions={scene.id === activeDesktopId ? positions : []}
								/>
							</div>
						{/each}
					</div>
				{/each}
			</div>
		{/each}
	</section>
	<section bind:this={bottomSection} bind:clientHeight={infoH} class="relative flex min-h-0 flex-[1] bg-[#181818]">
		<!-- Display only for now: none of these buttons does anything yet. -->
		<!-- Proportions from the design: text, arrow and grid are about 10, 11 and
		     16px tall, 22px apart (16px gap + the arrow image's 6px margin). -->
		<div class="absolute top-[15px] right-4 z-10 flex items-center gap-6">
			<!-- Desktop Changer: up to four desktops. -->
			<div bind:this={desktopChanger} class="flex items-center gap-2 border border-[#262626] bg-[#262626] px-1.5 py-1">
				<div class="flex items-center gap-2" aria-live="polite" aria-label="Desktop Changer: {desktopCount} desktops">
					{#each desktops as desktop, i (desktop.id)}
						<div class="relative flex">
							<button
								type="button"
								aria-label="Desktop {i + 1}"
								aria-pressed={desktop.id === activeDesktopId}
								onclick={() => switchDesktop(desktop.id)}
								aria-haspopup={desktop.id !== activeDesktopId ? 'menu' : undefined}
								aria-expanded={desktop.id !== activeDesktopId ? desktopMenu === desktop.id : undefined}
								oncontextmenu={(e) => {
									if (desktop.id === activeDesktopId) return;
									e.preventDefault();
									if (savingDesktop) return;
									layoutOpen = false;
									desktopMenu = desktop.id;
								}}
								class="group box-border flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded-[2px] text-[9px] leading-none font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white {desktop.id === activeDesktopId ? 'cursor-default bg-white text-black' : 'cursor-pointer text-white ring-1 ring-white ring-inset'}"
							>
								<span aria-hidden="true" class={desktop.id === activeDesktopId ? '' : 'opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100'}>{i + 1}</span>
							</button>
							{#if desktopMenu === desktop.id}
								<div transition:scale={{ start: 1.15, duration: 70 }} class="absolute bottom-full left-0 z-30 mb-3 origin-bottom-left">
									<LiquidGlass source={chartsEl} hoverable={false} class="w-max rounded-[18px] p-2">
										<div role="menu" aria-label="Desktop {i + 1}">
											<button type="button" role="menuitem" use:focusDesktopMenu onclick={() => deleteDesktop(desktop.id, i + 1)} class="flex h-9 min-w-24 cursor-pointer items-center rounded-lg px-3 text-sm text-white hover:bg-white/10 focus-visible:bg-white/10 focus-visible:outline-none">Delete</button>
										</div>
									</LiquidGlass>
								</div>
							{/if}
						</div>
					{/each}
				</div>
				<button
					type="button"
					aria-label="Add desktop"
					title={desktopError || (desktopCount === MAX_DESKTOPS ? 'Maximum 4 desktops' : 'Add desktop')}
					disabled={savingDesktop || desktopCount >= MAX_DESKTOPS}
					onclick={addDesktop}
					class="flex h-3 w-3 cursor-pointer items-center justify-center rounded-sm text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white disabled:cursor-not-allowed {desktopCount >= MAX_DESKTOPS ? 'opacity-40' : ''}"
				>
					<svg class="h-3 w-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M8 2.5v11M2.5 8h11" /></svg>
				</button>
				<span class="sr-only" role="status">{desktopError}</span>
			</div>
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
					disabled={savingDesktop}
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
									onclick={() => ((activeScene.layout = i), (layoutOpen = false), save({ layout: i }))}
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
			<OpenPositions {positions} {accounts} desktopId={activeDesktopId} initialAccountSelections={data.accountFilters} symbols={data.filterSymbols} initialSymbolSelections={data.symbolFilters} initialSorts={data.positionSorts} />
		</div>
	</section>
	</div>
	<div bind:this={desktopTrack} aria-hidden="true" class="pointer-events-none absolute inset-0 z-20"></div>
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
