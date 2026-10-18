<script lang="ts">
	// A chart layout drawn as outlined boxes, e.g. rows [3, 2] is three charts
	// on top and two below. Matches the style of the grid icon.
	let { rows, size = 16 }: { rows: number[]; size?: number } = $props();
</script>

<span class="flex shrink-0 flex-col gap-[2px]" style="width:{size}px; height:{size}px">
	{#each rows as count, r (r)}
		<span class="flex min-h-0 flex-1 gap-[2px]">
			{#each Array(count) as _, c (c)}
				<span class="block min-w-0 flex-1 rounded-[1px] border border-white"></span>
			{/each}
		</span>
	{/each}
</span>
