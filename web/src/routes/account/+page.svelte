<script lang="ts">
	import { goto } from '$app/navigation';
	import AccountView from '#lib/components/AccountView.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<!-- Opened directly by URL: closing goes to the chart page. -->
<AccountView accounts={data.accounts} close={() => goto('/')} />
