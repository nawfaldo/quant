<script lang="ts">
	import Chart from '#lib/components/Chart.svelte';
	import type { Backtest, MonteCarlo } from '#lib/server/backtest.ts';

	// The book's realised backtest, In-Sample and Out-of-Sample side by side, each from the same start balance.
	// run: the edit page's Run, a what-if of the edited book instead of the live one.
	let { accountId, run }: { accountId: number; run?: { book: string | null; removed: string[]; risk: number | null } } = $props();

	let data = $state<(Backtest & { montecarlo: MonteCarlo | null }) | null>(null);
	let loaded = $state(false);
	$effect(() => {
		const id = accountId;
		let stale = false;
		fetch(`/api/accounts/${id}/backtest`, run ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(run) } : undefined)
			.then((r) => r.json())
			.then((b: typeof data) => {
				if (stale) return;
				data = b;
				loaded = true;
			});
		return () => (stale = true);
	});

	const day = (t: number) => {
		const d = new Date(t * 1000);
		return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}/${d.getUTCFullYear()}`;
	};
	const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
	const tone = (v: number) => (v >= 0 ? 'text-blue-500' : 'text-red-400');
</script>

<h2 class="mb-5 text-[14px] font-light text-neutral-300">Backtest: <span class="font-medium text-white">Realised</span></h2>

{#if data && data.windows.length}
	<div class="grid grid-cols-[repeat(auto-fit,minmax(380px,1fr))] gap-[24px]">
		{#each data.windows as w (w.label)}
			<article class="text-white">
				<header class="flex flex-wrap items-baseline gap-x-3">
					<span class="text-[14px] font-medium whitespace-nowrap">{w.label}</span>
					<span class="flex items-center gap-1.5 text-[12px] font-light text-white">{day(w.from)} <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3 w-3"><path d="M2.5 8h11M9 3.5 13.5 8 9 12.5" /></svg> {day(w.to - 86400)}</span>
				</header>

				<div class="mt-[14px] flex flex-wrap items-center justify-between gap-[16px] whitespace-nowrap">
					<div>
					<div class="text-[22px] font-medium {tone(w.returnPct)}">{pct(w.returnPct)}</div>
					</div>
					<dl class="flex gap-[20px]">
						<div><dt class="text-[12px] font-light">DD</dt><dd class="mt-0.5 text-[14px] font-medium">{w.mtmDdPct.toFixed(2)}%</dd></div>
						<div><dt class="text-[12px] font-light">Trades</dt><dd class="mt-0.5 text-[14px] font-medium">{w.trades.toLocaleString('en-US')}</dd></div>
						<div><dt class="text-[12px] font-light">Sharpe</dt><dd class="mt-0.5 text-[14px] font-medium">{w.sharpe.toFixed(2)}</dd></div>
						<div><dt class="text-[12px] font-light">PF</dt><dd class="mt-0.5 text-[14px] font-medium">{w.profitFactor.toFixed(2)}</dd></div>
					</dl>
				</div>

				<div class="mt-[12px] h-[150px]">
					<Chart bars={w.bars} minutes={10080} fit />
				</div>

			</article>
		{/each}
	</div>
{:else if loaded}
	<div class="text-[12px] font-light text-neutral-500">No realised backtest for this book</div>
{/if}

{#if data?.montecarlo?.windows.length}
	{@const mc = data.montecarlo}
	<!-- Daily realised returns resampled in 14-day blocks, chained in sequence; percentiles across paths. -->
	<h2 class="mt-[38px] mb-5 text-[14px] font-light text-neutral-300">
		Backtest: <span class="font-medium text-white">Monte Carlo {mc.paths / 1000}k Path</span>
	</h2>
	<div class="grid grid-cols-[repeat(auto-fit,minmax(380px,1fr))] gap-[24px]">
		{#each mc.windows as w (w.label)}
			<article class="text-white">
				<header class="flex flex-wrap items-baseline gap-x-3">
					<span class="text-[14px] font-medium whitespace-nowrap">{w.label}</span>
					<span class="flex items-center gap-1.5 text-[12px] font-light">{day(w.from)} <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3 w-3"><path d="M2.5 8h11M9 3.5 13.5 8 9 12.5" /></svg> {day(w.to - 86400)}</span>
				</header>
				<table class="mt-[14px] w-full">
					<thead>
						<tr class="text-[12px]">
							<th class="pb-2 text-left font-light"></th>
							{#each [5, 50, 75, 95] as q (q)}<th class="pb-2 text-right font-light">P{q}</th>{/each}
						</tr>
					</thead>
					<tbody class="text-[14px] font-medium">
						<tr>
							<td class="py-1.5 text-[12px] font-light">Return</td>
							{#each [5, 50, 75, 95] as const as q (q)}<td class="py-1.5 text-right">{pct(w.ret[q])}</td>{/each}
						</tr>
						<tr>
							<td class="py-1.5 text-[12px] font-light">DD</td>
							{#each [5, 50, 75, 95] as const as q (q)}<td class="py-1.5 text-right">{w.dd[q].toFixed(2)}%</td>{/each}
						</tr>
					</tbody>
				</table>
			</article>
		{/each}
	</div>
{/if}
