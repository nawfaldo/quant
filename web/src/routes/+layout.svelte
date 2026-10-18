<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<title>quant</title>
	<link rel="icon" type="image/svg+xml" href={favicon} />
</svelte:head>

{@render children()}
