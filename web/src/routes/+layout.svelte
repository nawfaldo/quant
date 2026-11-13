<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
	// "/" poses as a Cloudflare 522 error page.
	const front = $derived(page.url.pathname === '/');
</script>

<svelte:head>
	{#if front}
		<title>ariyanto.cloud | 522: Connection timed out</title>
	{:else}
		<title>ariyanto</title>
		<link rel="icon" type="image/svg+xml" href={favicon} />
	{/if}
</svelte:head>

{@render children()}
