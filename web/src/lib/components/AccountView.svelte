<script lang="ts">
	import { circleClose } from '#lib/circleNav.ts';
	import userIcon from '#lib/icons/user.svg';
	import type { Account } from '#lib/server/accounts.ts';

	// Shows the account view over a blank page; `close` hides it. The X shrinks the circle back into the Settings button.
	let { accounts, close }: { accounts: Account[]; close: () => unknown } = $props();
	// A single account is the active one: its row sits on the table header gray.
	const single = $derived(accounts.length === 1);

	function back(e: MouseEvent) {
		if (e.metaKey || e.ctrlKey || e.shiftKey) return;
		e.preventDefault();
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		circleClose(close, r.left + r.width / 2, r.top + r.height / 2);
	}
</script>

<section class="fixed inset-0 z-40 bg-[#121212]">
	<a href="/" onclick={back} class="absolute top-3 left-3 flex cursor-pointer items-center gap-2 p-2 text-neutral-300 hover:text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-6 w-6">
			<path d="M3 3l10 10M13 3L3 13" />
		</svg>
		<span class="pl-2 text-sm font-medium text-white">Accounts</span>
	</a>
	<!-- pl-12 lines the list up with the "Accounts" label: the link's padding, icon, gap and label padding add to 60px from the edge. -->
	<ul class="absolute top-16 left-3 space-y-3 pl-12 text-white">
		{#each accounts as a (a.id)}
			<li class="flex items-center gap-2 text-sm font-normal {single ? '-ml-4 rounded-2xl bg-[#181818] px-4 py-2.5' : ''}">
				<img src={userIcon} alt="" class="block h-3.5 w-3.5" />
				{a.name}
			</li>
		{/each}
	</ul>
</section>
