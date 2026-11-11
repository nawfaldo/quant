<script lang="ts">
	// The agent iPhone: the Messages-style chat in a phone you drag by its status bar and close by swiping the
	// home strip up (it flies into the mini phone icon; tapping the icon grows it back out). Used on / and /account.
	// `iconClass` places and sizes the closed icon; `phoneClass` places the open phone.
	import { onMount } from 'svelte';
	import { cubicOut } from 'svelte/easing';
	import { fade, scale, type TransitionConfig } from 'svelte/transition';
	import AgentChat from '#lib/components/AgentChat.svelte';
	import { refreshGlass } from '#lib/liquidGlass.ts';
	import iphoneBack from '#lib/icons/iphone-back.png';
	import { loadPhoneState, phoneOpen, phonePos, setPhoneOpen } from '#lib/agentPhone.svelte.ts';

	let { login = '', iconClass, phoneClass }: { login?: string; iconClass: string; phoneClass: string } = $props();

	let lift = $state(0);
	let gesture = $state(false); // a finger is on the phone: follow it 1:1, no easing
	let anchor = $state<HTMLElement>(); // where the icon sits, open or closed: the fly target

	onMount(loadPhoneState);

	function open() {
		lift = 0;
		setPhoneOpen(true);
	}

	function dragPhone(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		const start = { x: phonePos.x, y: phonePos.y }, sx = e.clientX, sy = e.clientY;
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		gesture = true;
		// Keep the whole phone inside the window: clamp against where it sat at offset 0.
		const r = (el.parentElement as HTMLElement).getBoundingClientRect();
		const bx = r.left - start.x, by = r.top - start.y;
		const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
		const move = (m: PointerEvent) => {
			phonePos.x = clamp(start.x + m.clientX - sx, -bx, innerWidth - r.width - bx);
			phonePos.y = clamp(start.y + m.clientY - sy, -by, innerHeight - r.height - by);
		};
		const up = () => ((gesture = false), el.removeEventListener('pointermove', move), el.removeEventListener('pointerup', up), el.removeEventListener('pointercancel', up));
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
		el.addEventListener('pointercancel', up);
	}

	// Swipe the home strip up past 80px to close; the strip rides up with the finger, the phone stays put.
	function swipeClose(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		const el = e.currentTarget as HTMLElement, sy = e.clientY;
		el.setPointerCapture(e.pointerId);
		gesture = true;
		const move = (m: PointerEvent) => (lift = Math.max(0, sy - m.clientY));
		const up = () => {
			el.removeEventListener('pointermove', move), el.removeEventListener('pointerup', up), el.removeEventListener('pointercancel', up);
			gesture = false;
			if (lift > 80) setPhoneOpen(false);
			else lift = 0;
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
		el.addEventListener('pointercancel', up);
	}

	// iOS app close/open: the phone flies between where it is and the icon, rounding into its shape. Both ways.
	function appIcon(node: HTMLElement): TransitionConfig {
		const from = node.getBoundingClientRect();
		const a = anchor?.getBoundingClientRect();
		const to = a ? { left: a.left, top: a.top, w: a.width, h: a.height } : { left: from.right - 28, top: from.top, w: 28, h: 44 };
		const r = to.w * 0.16; // icon corner radius
		const w = node.offsetWidth, h = node.offsetHeight;
		const x0 = from.left - phonePos.x, y0 = from.top - phonePos.y; // untransformed corner
		const lerp = (p: number, q: number, k: number) => p + (q - p) * k;
		return {
			duration: phoneOpen.animate ? 420 : 0,
			easing: cubicOut,
			css: (t, u) => {
				// Uniform scale (never squash the corners); a rounded clip trims the tall phone to the icon's shape.
				const s = lerp(from.width, to.w, u) / w;
				const hv = lerp(h, (to.h * w) / to.w, u), cut = (h - hv) / 2;
				const x = lerp(from.left, to.left, u) - x0, y = lerp(from.top, to.top, u) - y0 - s * cut;
				return `transform-origin: 0 0; transform: translate(${x}px, ${y}px) scale(${s}); transition: none;
					opacity: ${Math.min(1, t * 3)}; clip-path: inset(${cut}px 0 round ${lerp(55, (r * w) / to.w, u)}px);`;
			}
		};
	}
</script>

<!-- Invisible twin of the icon, always present, so the fly animation knows where the icon is even while open. -->
<span bind:this={anchor} aria-hidden="true" class="pointer-events-none invisible {iconClass}"></span>

{#if !phoneOpen.loaded}
	<!-- nothing until the saved open/closed state is known, so a closed phone never flashes open on reload -->
{:else if phoneOpen.v}
	<div transition:appIcon onintroend={refreshGlass} class="z-[60] {phoneClass}"
		style="width:332px; height:min(560px, calc(100vh - 60px)); transform: translate({phonePos.x}px, {phonePos.y}px)">
		<AgentChat {login} indicator={false} />
		<!-- Drag handle: the status bar strip at the top of the phone. -->
		<div onpointerdown={dragPhone} role="presentation" class="absolute inset-x-[20px] top-0 z-40 h-[44px] cursor-grab touch-none active:cursor-grabbing"></div>
		<!-- Home indicator: swipe up to close. -->
		<div onpointerdown={swipeClose} role="presentation" class="absolute bottom-0 left-1/2 z-40 flex h-[24px] w-[120px] -translate-x-1/2 cursor-pointer touch-none items-end justify-center pb-[8px]">
			<span class="pointer-events-none block h-[5px] w-[80px] rounded-full bg-white/80" style="transform: translateY({-Math.min(lift, 120)}px); transition: {gesture ? 'none' : 'transform 300ms cubic-bezier(0.32, 0.72, 0, 1)'}"></span>
		</div>
	</div>
{:else}
	<!-- Closed: the back of a silver iPhone (the reference photo, text painted out) with a one-colour logo. -->
	<button type="button" onclick={open} aria-label="Open agent" in:scale={{ start: 0.6, duration: 250, delay: phoneOpen.animate ? 250 : 0 }} out:fade={{ duration: 80 }}
		class="z-[60] block cursor-pointer hover:brightness-110 {iconClass}">
		<img src={iphoneBack} alt="" draggable="false" class="block size-full" />
		<svg viewBox="0 0 384 512" class="absolute top-[24.4%] left-[52%] h-[16.9%] w-[19.8%] -translate-x-1/2 fill-[#434247]">
			<path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.9 48.6-.7 90.4-82.5 102.6-119.3-65.2-30.7-61.7-90-61.7-91.9zm-56.6-164.2c27.3-32.4 24.8-61.9 24-72.5-24.1 1.4-52 16.4-67.9 34.9-17.5 19.8-27.8 44.3-25.6 71.9 26.1 2 49.9-11.4 69.5-34.3z" />
		</svg>
	</button>
{/if}
