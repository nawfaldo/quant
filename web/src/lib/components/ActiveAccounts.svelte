<script lang="ts">
	import eyeIcon from '#lib/icons/eye.png';
	import eyeSlashIcon from '#lib/icons/eye-slash.png';
	import userIcon from '#lib/icons/user.svg';
	import type { Account } from '#lib/server/accounts.ts';
	import type { Position } from '#lib/server/positions.ts';
	import { circleOpen } from '#lib/circleNav.ts';
	import { pushState } from '$app/navigation';

	let { accounts, positions = [] }: { accounts: Account[]; positions?: Position[] } = $props();

	// The Settings link opens /account through the black circle, which grows out of the link.
	function openAccount(e: MouseEvent) {
		if (e.metaKey || e.ctrlKey || e.shiftKey) return;
		e.preventDefault();
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		circleOpen(() => pushState('/account', { account: true }), r.left + r.width / 2, r.top + r.height / 2);
	}

	// Clicks apply at once; the saved value then arrives with the live stream.
	let pending = $state<Record<number, boolean>>({});
	const isBlurred = (a: Account) => pending[a.id] ?? a.blurred;

	async function toggleBlur(a: Account) {
		const blurred = !isBlurred(a);
		pending[a.id] = blurred;
		await fetch(`/api/accounts/${a.id}/blur`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ blurred })
		});
	}

	// Drop a pending click once the stream agrees with it.
	$effect(() => {
		for (const a of accounts) if (pending[a.id] === a.blurred) delete pending[a.id];
	});

	const money = (v: number) => `$${v.toFixed(2)}`;

	const hasOpenPositions = (a: Account) =>
		positions.some((p) => p.account.toLowerCase() === a.name.toLowerCase() || p.account === String(a.id));

	function change(balance: number, equity: number) {
		const pct = ((equity - balance) / balance) * 100;
		if (Math.abs(pct) < 0.005) return '+0.00%';
		return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
	}

	function changeColor(balance: number, equity: number) {
		const pct = ((equity - balance) / balance) * 100;
		if (Math.abs(pct) < 0.005) return 'text-white';
		return pct > 0 ? 'text-blue-400' : 'text-red-400';
	}
</script>

<div class="flex h-full max-w-64 flex-col p-4 text-white">
	<header class="mb-4 flex items-center justify-between gap-4 whitespace-nowrap">
		<h2 class="text-sm font-medium">Active Accounts</h2>
		<a href="/account" onclick={openAccount} class="flex shrink-0 items-center text-[13px] leading-none font-light text-neutral-300 hover:text-white">
			Settings
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1" class="block h-[18px] w-[18px]">
				<path d="M5 4.5h6.5V11.1" />
				<path d="M4.25 11.75 11.5 4.5" />
			</svg>
		</a>
	</header>

	<ul class="space-y-3">
		{#each accounts as a (a.id)}
			<li>
				<div class="flex items-center gap-2 text-sm font-medium">
					<img src={userIcon} alt="" class="block h-4 w-4" />
					{a.name}
				</div>
				<div class="group flex items-center gap-2 text-[13px] text-neutral-300">
					<span class="flex w-4"><span class="ml-[7.5px] block h-2 w-2 -translate-y-0.5 border-b border-l border-white"></span></span>
					<div class="flex items-center gap-3 font-light {isBlurred(a) ? 'blur-[8px] select-none' : ''}">
						<span>{money(a.balance)}</span>
						{#if hasOpenPositions(a) && a.equity !== null && a.balance > 0}
							<span>{money(a.equity)}</span>
							<span class={changeColor(a.balance, a.equity)}>{change(a.balance, a.equity)}</span>
						{/if}
					</div>
					<!-- Right edge lines up with the visible tip of the Settings arrow.
					     Hidden until the row is hovered. -->
					<button
						type="button"
						onclick={() => toggleBlur(a)}
						aria-label={isBlurred(a) ? 'Show balance' : 'Hide balance'}
						class="mr-[4.5px] ml-auto flex shrink-0 cursor-pointer opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
					>
						<img src={isBlurred(a) ? eyeSlashIcon : eyeIcon} alt="" class="block h-4 w-4" />
					</button>
				</div>
			</li>
		{/each}
	</ul>
</div>
