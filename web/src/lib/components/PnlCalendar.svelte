<script lang="ts">
	import type { ClosedPosition } from '#lib/server/accounts.ts';

	// Month calendar of closed-trade P&L: one square per day (by close date), Monday first.
	let { accountId }: { accountId: number } = $props();

	let rows = $state<ClosedPosition[]>([]);
	// Month shown, as [year, month 0-11]; starts on the newest trade's month.
	let ym = $state<[number, number] | null>(null);
	$effect(() => {
		let stale = false;
		fetch(`/api/accounts/${accountId}/closed`)
			.then((r) => r.json())
			.then((d: ClosedPosition[]) => {
				if (stale) return;
				rows = d;
				const last = d[0]?.closeTime;
				const now = new Date();
				ym = last ? [Number(last.slice(0, 4)), Number(last.slice(5, 7)) - 1] : [now.getFullYear(), now.getMonth()];
			});
		return () => (stale = true);
	});

	// "YYYY-MM-DD" -> { pnl, trades }
	const byDay = $derived.by(() => {
		const m = new Map<string, { pnl: number; trades: number }>();
		for (const r of rows) {
			const k = r.closeTime.slice(0, 10);
			const d = m.get(k) ?? { pnl: 0, trades: 0 };
			d.pnl += r.pnl;
			d.trades++;
			m.set(k, d);
		}
		return m;
	});

	const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
	const DAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
	const pad = (n: number) => String(n).padStart(2, '0');

	// Leading blanks so the 1st lands under its weekday, then one cell per day.
	const cells = $derived.by(() => {
		if (!ym) return [];
		const [y, m] = ym;
		const lead = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7;
		const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
		const out: ({ day: number; weekend: boolean; pnl: number; trades: number } | null)[] = Array(lead).fill(null);
		for (let d = 1; d <= days; d++) {
			const s = byDay.get(`${y}-${pad(m + 1)}-${pad(d)}`);
			out.push({ day: d, weekend: (lead + d - 1) % 7 >= 5, pnl: s?.pnl ?? 0, trades: s?.trades ?? 0 });
		}
		return out;
	});

	// Months you can visit: from the first trade's month to this month, nothing before live or in the future.
	const key = (y: number, m: number) => y * 12 + m;
	const first = $derived.by(() => {
		const t = rows.at(-1)?.closeTime;
		return t ? key(Number(t.slice(0, 4)), Number(t.slice(5, 7)) - 1) : null;
	});
	const now = new Date();
	const lastKey = key(now.getFullYear(), now.getMonth());
	const canPrev = $derived(!!ym && first != null && key(ym[0], ym[1]) > first);
	const canNext = $derived(!!ym && key(ym[0], ym[1]) < lastKey);

	function shift(n: number) {
		if (!ym || (n < 0 ? !canPrev : !canNext)) return;
		const d = new Date(Date.UTC(ym[0], ym[1] + n, 1));
		ym = [d.getUTCFullYear(), d.getUTCMonth()];
	}

	const money = (v: number) => (Math.abs(v) < 0.005 ? '$0.00' : `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`);
	const pnlColor = (v: number) => (Math.abs(v) < 0.005 ? 'text-white' : v > 0 ? 'text-blue-400' : 'text-red-400');
	function bg(c: { weekend: boolean; pnl: number; trades: number }) {
		if (c.trades > 0 && c.pnl > 0.005) return 'bg-[#1f5fbf]';
		if (c.trades > 0 && c.pnl < -0.005) return 'bg-[#b8323a]';
		return c.weekend ? 'bg-[#161616]' : 'bg-[#262626]';
	}
	const arrow = 'flex h-7 w-6 cursor-pointer items-center justify-center rounded-lg text-neutral-300 hover:bg-white/10 hover:text-white disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent';
</script>

{#if ym}
	<div class="w-[min(920px,100%)] pb-8 text-white">
		<header class="mb-3 flex items-center gap-2">
			<h2 class="text-[14px] font-medium">Calendar</h2>
			<div class="ml-auto flex items-center gap-1">
				<span class="text-[14px] font-medium">{MONTHS[ym[1]]} {ym[0]}</span>
				<span class="w-1"></span>
				<button type="button" aria-label="Previous month" onclick={() => shift(-1)} disabled={!canPrev} class={arrow}>
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M13 8H3M7 4 3 8l4 4" /></svg>
				</button>
				<button type="button" aria-label="Next month" onclick={() => shift(1)} disabled={!canNext} class={arrow}>
					<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="h-4 w-4"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
				</button>
			</div>
		</header>
		<div class="grid grid-cols-[repeat(7,minmax(0,1fr))] gap-1">
			{#each DAYS as d (d)}
				<div class="pb-1 text-center text-[12px] font-medium text-neutral-300">{d}</div>
			{/each}
			{#each cells as c, i (i)}
				{#if c}
					<div class="flex aspect-square flex-col justify-between rounded-b-lg p-2 {bg(c)}">
						<span class="text-[20px] font-medium">{c.day}</span>
						{#if c.trades > 0}
							<span class="flex flex-col leading-tight">
								<span class="text-[14px] font-medium">{money(c.pnl)}</span>
								<span class="text-[12px] font-light text-white/70">{c.trades} {c.trades === 1 ? 'trade' : 'trades'}</span>
							</span>
						{/if}
					</div>
				{:else}
					<div></div>
				{/if}
			{/each}
		</div>
	</div>
{/if}
