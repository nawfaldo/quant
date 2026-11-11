<script lang="ts">
	// iPhone-15-shaped Messages app for the agent (Qwen on the local llama-server, with project tools).
	// Home is the chat list (compose a new chat, tap one to open it); a chat pushes in from the right like iOS.
	// Chats are per account login, stored by /api/agent.
	import { onMount, tick } from 'svelte';
	import { backOut } from 'svelte/easing';
	import { fade, slide, type TransitionConfig } from 'svelte/transition';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import agentPic from '#lib/icons/agent.jpg';
	import { playIncoming, unlockSound } from '#lib/sound.ts';

	let { login, indicator = true }: { login: string; indicator?: boolean } = $props();

	type Message = {
		role: 'user' | 'assistant';
		text: string;
		createdAt: Date;
		error?: boolean;
		fresh?: boolean;
		typing?: boolean;
		status?: 'delivered' | 'read';
		readAt?: Date;
	};
	type Chat = { id: number; title: string; preview: string; updatedAt: number; unread: number; typing: boolean };

	let view = $state<'list' | 'chat'>('list');
	let chats = $state<Chat[]>([]);
	let chatId = $state<number | null>(null);
	let messages = $state<Message[]>([]);
	let input = $state('');
	let busy = $state(false);
	let scroller = $state<HTMLElement>();
	let listScroller = $state<HTMLElement>();
	let field = $state<HTMLTextAreaElement>();
	/** Under the picture: the chat's title (Qwen sums up the first message), or New Message before it exists. */
	const title = $derived(chats.find((c) => c.id === chatId)?.title ?? 'New Message');
	/** Unread replies in other chats: the count beside the back button, like iOS. */
	const unreadElsewhere = $derived(chats.reduce((n, c) => n + (c.id === chatId && view === 'chat' ? 0 : c.unread), 0));

	onMount(unlockSound);

	const api = (q: string) => `/api/agent?login=${encodeURIComponent(login)}${q}`;

	async function loadChats() {
		const l = login;
		const r = await fetch(api('')).catch(() => null);
		if (r?.ok && l === login) chats = await r.json();
	}

	// While a reply is being written somewhere, keep the list fresh (typing -> preview, unread dots).
	$effect(() => {
		if (!chats.some((c) => c.typing)) return;
		const t = setInterval(loadChats, 2000);
		return () => clearInterval(t);
	});

	// --- Swipe to delete (iOS): drag a row left to reveal Delete; past 60% of the width it deletes. ---
	const REVEAL = 80;
	const SNAP = 'all 300ms cubic-bezier(0.32, 0.72, 0, 1)';
	let offset = $state<Record<number, number>>({});
	let dragging = $state<number | null>(null);
	let drag: { id: number; x: number; y: number; base: number; width: number; moved: boolean; locked: boolean } | null = null;
	let justSwiped = false;

	function closeSwipes(except?: number) {
		for (const k of Object.keys(offset)) if (Number(k) !== except) offset[Number(k)] = 0;
	}

	function swipeStart(e: PointerEvent, id: number) {
		if (e.button !== 0) return;
		const width = (e.currentTarget as HTMLElement).offsetWidth;
		drag = { id, x: e.clientX, y: e.clientY, base: offset[id] ?? 0, width, moved: false, locked: false };
	}

	function swipeMove(e: PointerEvent, id: number) {
		if (!drag || drag.id !== id) return;
		const dx = e.clientX - drag.x;
		const dy = e.clientY - drag.y;
		if (!drag.moved) {
			if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
			if (Math.abs(dy) > Math.abs(dx)) return void (drag = null); // a scroll, not a swipe
			drag.moved = true;
			dragging = id;
			closeSwipes(id);
			(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		}
		let next = drag.base + dx;
		// Rubber band past the left edge and to the right of closed.
		if (next > 0) next = next / 4;
		if (next < -drag.width) next = -drag.width + (next + drag.width) / 4;
		offset[id] = next;
	}

	function swipeEnd(_: PointerEvent, id: number) {
		if (!drag || drag.id !== id) return;
		const { moved, width } = drag;
		drag = null;
		dragging = null;
		if (!moved) return;
		justSwiped = true;
		setTimeout(() => (justSwiped = false), 0);
		const x = offset[id] ?? 0;
		if (x < -width * 0.6) {
			offset[id] = -width;
			setTimeout(() => removeChat(id), 200);
		} else offset[id] = x < -REVEAL / 2 ? -REVEAL : 0;
	}

	function tapRow(id: number) {
		if (justSwiped) return;
		// A tap on an open row (or any row while one is open) just closes it, like iOS.
		if (Object.values(offset).some((v) => v !== 0)) return closeSwipes();
		openChat(id);
	}

	async function removeChat(id: number) {
		chats = chats.filter((c) => c.id !== id);
		delete offset[id];
		await fetch(api(`&chat=${id}`), { method: 'DELETE' });
	}

	// A different account: back to its chat list.
	$effect(() => {
		void login;
		view = 'list';
		chatId = null;
		messages = [];
		loadChats();
	});

	async function openChat(id: number) {
		chatId = id;
		messages = [];
		view = 'chat';
		const r = await fetch(api(`&chat=${id}`)).catch(() => null);
		if (!r?.ok || chatId !== id) return;
		const rows: { role: Message['role']; text: string; error: boolean; createdAt: number }[] = await r.json();
		messages = rows.map((m) => ({ ...m, createdAt: new Date(m.createdAt) }));
		const c = chats.find((x) => x.id === id);
		if (c) c.unread = 0;
		// Its reply is still being written: show the typing bubble.
		if (c?.typing) messages.push({ role: 'assistant', text: '', createdAt: new Date(), typing: true });
		if (messages.at(-1)?.role === 'user') messages.at(-1)!.status = 'delivered';
		scrollToBottom(false);
	}

	function newChat() {
		chatId = null;
		messages = [];
		input = '';
		view = 'chat';
		setTimeout(() => field?.focus(), 350);
	}

	function back() {
		view = 'list';
		field?.blur();
		loadChats();
	}

	async function scrollToBottom(smooth = true) {
		await tick();
		scroller?.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'instant' });
	}

	// Like iMessage: Delivered, then Read, then the typing bubble, then the reply streaming in.
	async function send(e: SubmitEvent) {
		e.preventDefault();
		const text = input.trim();
		if (!text || busy) return;
		input = '';
		busy = true;
		let sendChat = chatId;
		messages.push({ role: 'user', text, createdAt: new Date(), fresh: true });
		messages.push({ role: 'assistant', text: '', createdAt: new Date(), fresh: true });
		const mine = messages[messages.length - 2];
		const reply = messages[messages.length - 1];
		scrollToBottom();

		const markRead = () => {
			if (mine.status === 'read') return;
			mine.status = 'read';
			mine.readAt = new Date();
		};
		let readTimer: ReturnType<typeof setTimeout> | undefined;
		try {
			const res = await fetch('/api/agent', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ login, chat: chatId, message: text })
			});
			if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
			const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
			let buffer = '';
			while (true) {
				const { value, done } = await reader.read();
				if (done) break;
				buffer += value;
				const lines = buffer.split('\n');
				buffer = lines.pop()!;
				for (const line of lines.filter(Boolean)) {
					const evt = JSON.parse(line);
					if (evt.type === 'start') {
						mine.status = 'delivered';
						if (sendChat == null) {
							// A new chat now exists: give it its row (titled once Qwen names it).
							sendChat = evt.chat;
							if (chatId == null) chatId = evt.chat;
							chats = [{ id: evt.chat, title: 'New Message', preview: text, updatedAt: Date.now(), unread: 0, typing: true }, ...chats];
						} else {
							const c = chats.find((x) => x.id === sendChat);
							if (c) Object.assign(c, { typing: true, preview: text, updatedAt: Date.now() });
						}
						readTimer = setTimeout(() => {
							markRead();
							reply.typing = true;
							scrollToBottom();
						}, 600);
					} else if (evt.type === 'thinking' || evt.type === 'tool') {
						clearTimeout(readTimer);
						markRead();
						reply.typing = true;
					} else if (evt.type === 'text') {
						clearTimeout(readTimer);
						if (!reply.text) playIncoming();
						markRead();
						reply.text += evt.text;
					} else if (evt.type === 'title') {
						const c = chats.find((x) => x.id === sendChat);
						if (c) c.title = evt.title;
					} else if (evt.type === 'done') {
						reply.text = evt.reply || reply.text;
					} else if (evt.type === 'error') Object.assign(reply, { text: evt.message, error: true });
				}
				scrollToBottom();
			}
		} catch (err) {
			Object.assign(reply, { text: String(err), error: true });
		} finally {
			clearTimeout(readTimer);
			busy = false;
			const c = chats.find((x) => x.id === sendChat);
			if (c) c.typing = false;
			if (view === 'chat' && chatId === sendChat && sendChat != null) {
				// Watched it arrive: read. If the chat was reopened mid-reply, reload to show the reply.
				if (messages.includes(reply)) await fetch(api(`&chat=${sendChat}`), { method: 'PATCH' });
				else await openChat(sendChat);
			}
			loadChats();
		}
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			(e.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
		}
	}

	const HOUR = 3_600_000;
	const startOfDay = (d: Date) => new Date(d).setHours(0, 0, 0, 0);
	const daysAgo = (d: Date) => Math.round((startOfDay(new Date()) - startOfDay(d)) / (24 * HOUR));
	const time = (d: Date) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
	function dayLabel(d: Date) {
		const n = daysAgo(d);
		if (n === 0) return 'Today';
		if (n === 1) return 'Yesterday';
		if (n < 7) return d.toLocaleDateString([], { weekday: 'long' });
		return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
	}
	/** List dates as in Messages: time today, Yesterday, weekday this week, else d/m/yy. */
	function listDate(ms: number) {
		const d = new Date(ms);
		const n = daysAgo(d);
		if (n === 0) return time(d);
		if (n === 1) return 'Yesterday';
		if (n < 7) return d.toLocaleDateString([], { weekday: 'long' });
		return d.toLocaleDateString([], { day: 'numeric', month: 'numeric', year: '2-digit' });
	}

	const stamp = (i: number) => i === 0 || +messages[i].createdAt - +messages[i - 1].createdAt > HOUR;
	const tail = (i: number) => i === messages.length - 1 || messages[i + 1].role !== messages[i].role || stamp(i + 1);
	const receiptAt = $derived.by(() => {
		const i = messages.findLastIndex((m) => m.role === 'user');
		const answered = messages.slice(i + 1).some((m) => m.text || m.error);
		return i >= 0 && !answered && messages[i].status ? i : -1;
	});

	// Clock in the status bar.
	let now = $state(new Date());
	onMount(() => {
		const t = setInterval(() => (now = new Date()), 10_000);
		return () => clearInterval(t);
	});

	// --- Animations (skipped for history and reduced motion) ---
	const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
	function rise(_: Element, fresh?: boolean): TransitionConfig {
		if (!fresh || still()) return { duration: 0 };
		return {
			duration: 420,
			easing: backOut,
			css: (t, u) => `transform-origin: bottom right; transform: translateY(${u * 56}px) scale(${0.92 + 0.08 * t}); opacity: ${Math.min(1, t * 2)}`
		};
	}
	function pop(_: Element, fresh?: boolean): TransitionConfig {
		if (!fresh || still()) return { duration: 0 };
		return {
			duration: 380,
			easing: backOut,
			css: (t) => `transform-origin: bottom left; transform: scale(${0.55 + 0.45 * t}); opacity: ${Math.min(1, t * 3)}`
		};
	}
	const appear = (node: Element, { fresh, mine }: { fresh?: boolean; mine: boolean }) => (mine ? rise(node, fresh) : pop(node, fresh));
	function pulse(node: HTMLElement, delay: number) {
		if (still()) return;
		const a = node.animate(
			[
				{ opacity: 0.35, transform: 'scale(0.85)' },
				{ opacity: 1, transform: 'scale(1)' },
				{ opacity: 0.35, transform: 'scale(0.85)' }
			],
			{ duration: 1300, delay, iterations: Infinity, easing: 'ease-in-out' }
		);
		return { destroy: () => a.cancel() };
	}

	// iOS navigation push: the chat slides over from the right while the list drifts left and dims.
	const PUSH = 'transform 480ms cubic-bezier(0.32, 0.72, 0, 1)';

	// Messages dark mode: systemBlue #0A84FF sent, #3B3B3D received.
	const sentColor = 'bg-[#0a84ff] text-white';
	const receivedColor = 'bg-[#3b3b3d] text-white';
	const BG = 'bg-[#262626]';
</script>


{#snippet avatar(_name: string, size: string)}
	<!-- The agent's picture; scaled up inside the circle so the photo's white corners never show. -->
	<span class={['block shrink-0 overflow-hidden rounded-full bg-[#3b3b3d]', size]}>
		<img src={agentPic} alt="" draggable="false" class="block size-full scale-[1.12] object-cover" />
	</span>
{/snippet}

<!-- iPhone 15 screen shape, Dynamic Island; no frame, just the gray background. -->
<div class="relative isolate h-full w-full overflow-hidden rounded-[55px] {BG} text-white/85 [clip-path:inset(0_round_55px)]">
	<!-- Status bar + Dynamic Island, above both screens -->
	<div class="pointer-events-none absolute inset-x-0 top-0 z-30 flex h-[46px] items-center justify-between px-[30px] text-[14px] font-semibold text-white">
		<span class="w-[60px] text-center">{now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: false })}</span>
		<span class="absolute top-[10px] left-1/2 h-[26px] w-[80px] -translate-x-1/2 rounded-full bg-black"></span>
		<span class="flex items-center gap-1.5">
			<svg viewBox="0 0 18 12" class="h-[10px] w-[16px]" fill="currentColor">
				<rect x="0" y="8" width="3" height="4" rx="1" /><rect x="5" y="5.5" width="3" height="6.5" rx="1" />
				<rect x="10" y="3" width="3" height="9" rx="1" /><rect x="15" y="0" width="3" height="12" rx="1" />
			</svg>
			<svg viewBox="0 0 27 13" class="h-[11px] w-[24px]">
				<rect x="0.5" y="0.5" width="23" height="12" rx="3.5" fill="none" stroke="currentColor" opacity="0.4" />
				<rect x="2" y="2" width="16" height="9" rx="2" fill="currentColor" />
				<path d="M25 4.5v4c.8-.3 1.3-1.1 1.3-2s-.5-1.7-1.3-2z" fill="currentColor" opacity="0.4" />
			</svg>
		</span>
	</div>

	<!-- ===== Home: chat list ===== -->
	<section class="absolute inset-0 flex flex-col {BG}" style="transform: translateX({view === 'chat' ? '-28%' : '0'}); transition: {PUSH}" inert={view === 'chat'}>
		<!-- iOS scroll-edge effect: content blurs and fades out under the status bar and header. -->
		<div class="edge-top pointer-events-none absolute inset-x-0 top-0 z-[5] h-[100px]"></div>
		<!-- Toolbar: title on the left, compose on glass on the right -->
		<div class="absolute inset-x-0 top-[50px] z-10 flex items-center justify-between pr-[14px] pl-[18px]">
			<h1 class="text-[22px] leading-none font-bold text-white">Agent</h1>
			<LiquidGlass as="button" source={listScroller} type="button" onclick={newChat} aria-label="New message"
				class="flex size-[34px] items-center justify-center rounded-full text-white">
				<!-- square.and.pencil -->
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="size-[18px]">
					<path d="M11 4.5H6.5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V13" />
					<path d="M17.6 3.6a1.9 1.9 0 0 1 2.8 2.8L12 14.8l-3.6.8.8-3.6z" />
				</svg>
			</LiquidGlass>
		</div>

		<div bind:this={listScroller} class="flex-1 overflow-y-auto {BG} pt-[92px] pb-10 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">

			{#if !chats.length}
				<p class="pointer-events-none absolute inset-0 flex items-center justify-center text-[15px] text-white/45">No Messages</p>
			{/if}
			{#each chats as c, i (c.id)}
				{@const x = offset[c.id] ?? 0}
				<!-- Swipe left like iOS: a red Delete sits under the row; a long swipe deletes straight away. -->
				<div class="relative overflow-hidden" out:slide={{ duration: 260 }}>
					<button type="button" onclick={() => removeChat(c.id)} aria-label="Delete {c.title}" tabindex={x < 0 ? 0 : -1}
						class="absolute inset-y-0 right-0 flex cursor-pointer items-center justify-end bg-[#ff453a] text-[15px] font-medium text-white"
						style="width: {Math.max(-x, 0)}px; transition: {dragging === c.id ? 'none' : SNAP}">
						<span class="flex w-[80px] shrink-0 items-center justify-center">
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="size-[22px]">
								<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6" />
							</svg>
						</span>
					</button>
					<button type="button" onclick={() => tapRow(c.id)}
						onpointerdown={(e) => swipeStart(e, c.id)} onpointermove={(e) => swipeMove(e, c.id)}
						onpointerup={(e) => swipeEnd(e, c.id)} onpointercancel={(e) => swipeEnd(e, c.id)}
						class="relative flex w-full min-w-0 cursor-pointer touch-pan-y items-center gap-3 {BG} py-[9px] pl-[14px] text-left select-none active:bg-[#2c2c2c]"
						style="transform: translateX({x}px); transition: {dragging === c.id ? 'none' : SNAP}">
						{#if c.unread > 0}
							<!-- Unread: blue dot in the margin left of the picture, as in Messages. -->
							<span aria-label="Unread" class="absolute top-1/2 left-[3px] size-[9px] -translate-y-1/2 rounded-full bg-[#0a84ff]"></span>
						{/if}
						{@render avatar(c.title, 'size-[44px] text-[18px]')}
						<div class={['min-w-0 flex-1 self-stretch py-0.5 pr-[14px]', i < chats.length - 1 && 'border-b border-white/10']}>
							<div class="flex items-baseline gap-2">
								<span class="flex-1 truncate text-[15px] font-semibold text-white">{c.title}</span>
								<span class="shrink-0 text-[13px] text-white/45">{listDate(c.updatedAt)}</span>
								<svg viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="1.6" class="h-[11px] w-[6px] shrink-0 self-center text-white/30">
									<path d="m1 1 6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
								</svg>
							</div>
							{#if c.typing}
								<!-- Still writing: the typing bubble instead of the preview. -->
								<span class="relative mt-1 mb-2 ml-1 inline-flex items-center gap-[3px] rounded-full bg-[#3b3b3d] px-2.5 py-[7px]" aria-label="Typing">
									{#each [0, 180, 360] as delay (delay)}
										<span use:pulse={delay} class="size-[6px] rounded-full bg-[#98989d]"></span>
									{/each}
									<span class="absolute -bottom-px -left-px size-2 rounded-full bg-[#3b3b3d]"></span>
									<span class="absolute -bottom-1.5 -left-1.5 size-1 rounded-full bg-[#3b3b3d]"></span>
								</span>
							{:else}
								<p class={['line-clamp-2 pb-1.5 text-[14px] leading-snug', c.unread ? 'text-white/70' : 'text-white/45']}>{c.preview}</p>
							{/if}
						</div>
					</button>
				</div>
			{/each}
		</div>
		<!-- Dims under the chat as it slides over -->
		<div class="pointer-events-none absolute inset-0 bg-black transition-opacity duration-[480ms]" style="opacity: {view === 'chat' ? 0.35 : 0}"></div>
	</section>

	<!-- ===== Chat ===== -->
	<section class="absolute inset-0 flex flex-col {BG} shadow-[-10px_0_30px_rgba(0,0,0,0.35)]"
		style="transform: translateX({view === 'chat' ? '0' : '104%'}); transition: {PUSH}" inert={view !== 'chat'}>
		<!-- iOS scroll-edge effect: content blurs and fades out under the status bar and header. -->
		<div class="edge-top pointer-events-none absolute inset-x-0 top-0 z-[5] h-[165px]"></div>
		<!-- Header: back, and the avatar over a glass name pill -->
		<header class="pointer-events-none absolute inset-x-0 top-[48px] z-10 flex justify-center px-14">
			<LiquidGlass as="button" source={scroller} type="button" aria-label="Back" onclick={back}
				class="pointer-events-auto absolute top-[6px] left-[14px] flex size-[38px] items-center justify-center rounded-full text-white">
				<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" class="size-[18px]">
					<path d="m12.5 4-6 6 6 6" stroke-linecap="round" stroke-linejoin="round" />
				</svg>
				{#if unreadElsewhere}
					<span class="absolute -top-1 -right-2 min-w-[18px] rounded-full bg-[#0a84ff] px-1 text-center text-[11px] leading-[18px] font-semibold text-white">{unreadElsewhere}</span>
				{/if}
			</LiquidGlass>
			<div class="flex max-w-full flex-col items-center">
				{@render avatar('Agent', 'relative z-10 size-12 text-xl')}
				<LiquidGlass source={scroller} hoverable={false} class="pointer-events-auto -mt-1 max-w-full truncate rounded-full px-3 py-1 text-[12px] font-medium">
					{title}
				</LiquidGlass>
			</div>
		</header>

		<main bind:this={scroller} class="flex-1 overflow-x-hidden overflow-y-auto {BG} [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
			<div class="flex flex-col px-4 pt-[132px] pb-20">
				{#each messages as m, i (i)}
					{@const mine = m.role === 'user'}
					{#if stamp(i)}
						<p class="mt-4 mb-1.5 text-center text-[11px] text-white/55">
							<span class="font-semibold">{dayLabel(m.createdAt)}</span>
							{time(m.createdAt)}
						</p>
					{/if}
					{#if !mine && !m.text && !m.error}
						{#if m.typing}
							<div in:pop|global={m.fresh} class="mt-2.5 flex flex-col items-start">
								<div class={['relative flex items-center gap-1 rounded-[18px] px-3.5 py-3', receivedColor]} aria-label="Typing">
									{#each [0, 180, 360] as delay (delay)}
										<span use:pulse={delay} class="size-2 rounded-full bg-[#98989d]"></span>
									{/each}
									<span class={['absolute -bottom-0.5 -left-0.5 size-3 rounded-full', receivedColor]}></span>
									<span class={['absolute -bottom-2 -left-2 size-1.5 rounded-full', receivedColor]}></span>
								</div>
							</div>
						{/if}
					{:else}
						<div
							in:appear|global={{ fresh: m.fresh, mine }}
							class={['flex', mine ? 'justify-end' : 'justify-start', i > 0 && !stamp(i) && (messages[i - 1].role === m.role ? 'mt-0.5' : 'mt-2.5')]}
						>
							<div class={['relative max-w-[75%] rounded-[18px] px-3.5 py-1.5 text-[15px] leading-snug break-words whitespace-pre-wrap', mine ? sentColor : receivedColor, m.error && 'text-[#ff453a]']}>
								{m.text}
								{#if tail(i)}
									<svg viewBox="0 0 16 18" aria-hidden="true"
										class={['absolute bottom-0 h-[18px] w-4', mine ? '-right-[5px] fill-[#0a84ff]' : '-left-[5px] -scale-x-100 fill-[#3b3b3d]']}>
										<path d="M4 0H11V8C11 13 13 16.3 16 18C12 18.6 7.5 17.4 4 14.6Z" />
									</svg>
								{/if}
							</div>
						</div>
					{/if}
					{#if i === receiptAt}
						{#key m.status}
							<p in:fade={{ duration: 250 }} class="mt-1 mr-1 text-right text-[11px] text-white/55">
								{#if m.status === 'read' && m.readAt}
									<span class="font-semibold">Read</span> {time(m.readAt)}
								{:else}
									Delivered
								{/if}
							</p>
						{/key}
					{/if}
				{/each}
			</div>
		</main>

		<!-- ...and under the text field and home bar. -->
		<div class="edge-bottom pointer-events-none absolute inset-x-0 bottom-0 z-[5] h-[110px]"></div>
		<form onsubmit={send} class="absolute inset-x-6 bottom-[26px] z-10">
			<LiquidGlass source={scroller} hoverable={false} class="flex items-end gap-2 rounded-full pr-[3px] pl-4">
				<textarea
					bind:this={field}
					bind:value={input}
					onkeydown={onKeydown}
					rows="1"
					placeholder="Message"
					class="field-sizing-content max-h-32 flex-1 resize-none bg-transparent py-2 text-[15px] leading-5 text-white outline-none placeholder:text-white/40"
				></textarea>
				{#if input.trim()}
					<button
						type="submit"
						disabled={busy}
						aria-label="Send"
						in:fade={{ duration: 120 }}
						class="my-[5px] mr-0.5 flex size-[26px] shrink-0 cursor-pointer items-center justify-center rounded-full bg-gradient-to-b from-[#4fa8ff] to-[#0a84ff] text-white disabled:opacity-40"
					>
						<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" class="size-3.5">
							<path d="M10 16V4m0 0-5 5m5-5 5 5" stroke-linecap="round" stroke-linejoin="round" />
						</svg>
					</button>
				{/if}
			</LiquidGlass>
		</form>
	</section>

	<!-- Home indicator -->
	{#if indicator}	<span class="pointer-events-none absolute bottom-[8px] left-1/2 z-30 h-[5px] w-[80px] -translate-x-1/2 rounded-full bg-white/80"></span>{/if}
</div>

<style>
	/* iOS 26 scroll-edge effect: a blur that fades in toward the edge, under a matching color fade. */
	.edge-top,
	.edge-bottom {
		backdrop-filter: blur(6px);
		-webkit-backdrop-filter: blur(6px);
	}
	.edge-top {
		background: linear-gradient(to bottom, #262626 0%, #262626 45%, rgb(38 38 38 / 0.8) 70%, rgb(38 38 38 / 0) 100%);
		mask-image: linear-gradient(to bottom, #000 65%, transparent 100%);
	}
	.edge-bottom {
		background: linear-gradient(to top, #262626 0%, #262626 25%, rgb(38 38 38 / 0.75) 55%, rgb(38 38 38 / 0) 100%);
		mask-image: linear-gradient(to top, #000 50%, transparent 100%);
	}
</style>
