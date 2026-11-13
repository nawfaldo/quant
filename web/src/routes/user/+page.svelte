<script lang="ts">
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import UserView from '#lib/components/UserView.svelte';
	import { me } from '#lib/me.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	me.name = untrack(() => data.user);
	me.avatar = untrack(() => data.avatar);
</script>

<UserView close={() => goto('/chart')} />
