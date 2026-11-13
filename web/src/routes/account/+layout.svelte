<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import AccountView from '#lib/components/AccountView.svelte';
	import type { LayoutProps } from './$types';

	// One AccountView for /account and /account?login=&tab=, so moving between them animates instead of remounting.
	let { data, children }: LayoutProps = $props();
</script>

<AccountView accounts={data.accounts} books={data.books} path={page.url.pathname + page.url.search} nav={(p: string) => goto(p)} close={() => goto('/chart')} />
{@render children()}
