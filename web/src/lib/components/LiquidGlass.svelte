<script lang="ts">
	// Apple-style liquid glass around any content. `source` is the element
	// behind the glass (e.g. the chart) whose canvases and images it refracts.
	//
	//   <LiquidGlass source={chartEl} as="button" class="h-10 rounded-full px-5">…</LiquidGlass>
	//
	// The shape comes from the element's border-radius: rounded-full gives a
	// capsule, anything smaller gives Apple's squircle corners.
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';
	import { liquidGlass, type LiquidGlassOptions } from '#lib/liquidGlass.ts';

	let {
		source,
		as = 'div',
		blur,
		bezel,
		refraction,
		profile,
		edgeBlur,
		hoverable,
		hoverBlur,
		hoverBrightness,
		class: className = '',
		children,
		...rest
	}: Omit<LiquidGlassOptions, 'source'> &
		HTMLAttributes<HTMLElement> & {
			source: HTMLElement | undefined;
			as?: string;
			type?: string;
			children?: Snippet;
		} = $props();

	// The glass canvas needs a positioned parent. Add `relative` only when the
	// caller has not already positioned it: Tailwind's `relative` would
	// otherwise override an `absolute` or `fixed` passed in `class`.
	const positioned = $derived(/(^|\s)(absolute|fixed|sticky)(\s|$)/.test(String(className)));
</script>

<svelte:element
	this={as}
	use:liquidGlass={{ source, blur, bezel, refraction, profile, edgeBlur, hoverable, hoverBlur, hoverBrightness }}
	class="{positioned ? '' : 'relative'} isolate {as === 'button' || as === 'a' ? 'cursor-pointer' : ''} {className}"
	{...rest}
>
	{@render children?.()}
</svelte:element>
