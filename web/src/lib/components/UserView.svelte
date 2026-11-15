<script lang="ts">
	import { circleClose } from '#lib/circleNav.ts';
	import LiquidGlass from '#lib/components/LiquidGlass.svelte';
	import { me, defaultAvatar } from '#lib/me.svelte.ts';

	// The /user page, on the same background as Settings, with the same X: the profile picture,
	// the name, and the password. The X shrinks the circle back into the profile picture it grew out of.
	let { close }: { close: () => unknown } = $props();
	let pageEl = $state<HTMLElement>();

	function back(e: MouseEvent) {
		if (e.metaKey || e.ctrlKey || e.shiftKey) return;
		e.preventDefault();
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		circleClose(close, r.left + r.width / 2, r.top + r.height / 2);
	}

	// Name and password: the pencil turns the value into a text field; Enter saves, Escape or
	// clicking away cancels. The saved password is only a one-way hash, so it shows as dots.
	type Field = 'name' | 'pass';
	let editing = $state<Field | null>(null);
	let draft = $state('');
	let status = $state<{ field: Field; ok: boolean } | null>(null);
	let statusTimer: ReturnType<typeof setTimeout>;

	function edit(f: Field) {
		draft = f === 'name' ? me.name : '';
		editing = f;
	}

	function flash(field: Field, ok: boolean) {
		status = { field, ok };
		clearTimeout(statusTimer);
		statusTimer = setTimeout(() => (status = null), 2000);
	}

	async function save() {
		const f = editing;
		const value = f === 'name' ? draft.trim() : draft;
		editing = null;
		if (!f || !value || (f === 'name' && value === me.name)) return;
		const r = await fetch(f === 'name' ? '/api/user/name' : '/api/user/password', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(f === 'name' ? { name: value } : { pass: value })
		}).catch(() => null);
		const ok = !!r?.ok && (await r.json()).ok;
		if (ok && f === 'name') me.name = value;
		flash(f, ok);
	}

	function key(e: KeyboardEvent) {
		if (e.key === 'Enter') save();
		else if (e.key === 'Escape') editing = null;
	}

	// Picture: the camera opens a file picker; the chosen image is centre-cropped to a square,
	// scaled to 512 px and saved as webp.
	let fileInput: HTMLInputElement;
	let uploadFailed = $state(false);

	async function pick() {
		const file = fileInput.files?.[0];
		fileInput.value = '';
		if (!file) return;
		uploadFailed = false;
		try {
			const img = await createImageBitmap(file);
			const side = Math.min(img.width, img.height);
			const canvas = document.createElement('canvas');
			canvas.width = canvas.height = Math.min(512, side);
			canvas.getContext('2d')!.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, canvas.width, canvas.height);
			const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, 'image/webp', 0.9));
			if (!blob) throw new Error('encode');
			const r = await fetch('/api/user/avatar', { method: 'POST', headers: { 'Content-Type': 'image/webp' }, body: blob });
			const j = await r.json();
			if (!j.ok) throw new Error('save');
			me.avatar = j.url;
		} catch {
			uploadFailed = true;
		}
	}

	const focus = (el: HTMLInputElement) => el.focus();
</script>

<section bind:this={pageEl} class="fixed inset-0 z-40 overflow-hidden bg-[#121212]" style="background-image: linear-gradient(180deg, #121212 0%, #161616 50%, #1f1f1f 100%)">
	<LiquidGlass as="a" source={pageEl} href="/chart" onclick={back} aria-label="Close" class="absolute top-[31px] left-[39px] z-20 flex size-[30px] items-center justify-center rounded-full text-white">
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" class="block h-4 w-4">
			<path d="M3 3l10 10M13 3L3 13" />
		</svg>
	</LiquidGlass>

	<div class="absolute top-[94px] left-[39px] flex items-start gap-8 text-white">
		<div class="flex flex-col items-center gap-2">
			<button type="button" aria-label="Change picture" onclick={() => fileInput.click()} class="group relative h-[180px] w-[180px] cursor-pointer overflow-hidden rounded-[40px]">
				<img src={me.avatar ?? defaultAvatar} alt="" class="h-full w-full object-cover" />
				<!-- camera on hover -->
				<span class="absolute inset-0 flex items-center justify-center bg-black/45 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" class="block h-10 w-10 text-white">
						<path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h2.6l1.5-2h6.8l1.5 2h2.6A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" />
						<circle cx="12" cy="13" r="3.5" />
					</svg>
				</span>
			</button>
			<input bind:this={fileInput} type="file" accept="image/*" onchange={pick} class="hidden" />
			{#if uploadFailed}<span class="text-[12px] font-light text-red-400">Not saved</span>{/if}
		</div>

		<dl class="grid gap-y-[14px] pt-1">
			{#each [['name', 'Name'], ['pass', 'Pass']] as const as [f, label] (f)}
				<div>
					<dt class="text-[12px] font-light">{label}</dt>
					<dd class="mt-1 flex h-[22px] items-center gap-2 text-[14px] font-medium">
						{#if editing === f}
							<input
								type={f === 'pass' ? 'password' : 'text'}
								bind:value={draft}
								use:focus
								autocomplete={f === 'pass' ? 'new-password' : 'off'}
								placeholder={f === 'pass' ? 'New password' : ''}
								onkeydown={key}
								onblur={() => (editing = null)}
								class="w-[180px] border-0 bg-transparent p-0 outline-none placeholder:font-light placeholder:text-neutral-500"
							/>
						{:else}
							<span class="whitespace-nowrap">{f === 'name' ? me.name : '•••••••'}</span>
							<button type="button" aria-label="Edit {label}" onclick={() => edit(f)} class="cursor-pointer p-0.5 text-neutral-400 hover:text-white">
								<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" class="block h-3.5 w-3.5"><path d="M11 2.5 13.5 5 5.5 13H3v-2.5z M9.5 4l2.5 2.5" /></svg>
							</button>
						{/if}
						{#if status?.field === f}
							<span class="text-[12px] font-light {status.ok ? 'text-neutral-300' : 'text-red-400'}">{status.ok ? 'Saved' : f === 'name' ? 'Not saved (taken?)' : 'Not saved'}</span>
						{/if}
					</dd>
				</div>
			{/each}
		</dl>
	</div>
</section>
