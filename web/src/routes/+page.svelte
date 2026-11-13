<script lang="ts">
	import { login } from '#lib/slide.ts';
	import { preloadCode } from '$app/navigation';
	import { onMount } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	let showIp = $state(false);

	// Blank white page with a hidden login: type the name, Enter, type the password, Enter.
	// Nothing is shown. Delete starts over, Backspace removes a letter; a wrong pair also starts over.
	let buf = '';
	let name: string | null = null;
	let busy = false;

	const reset = () => {
		buf = '';
		name = null;
	};

	const submit = async () => {
		const text = buf;
		buf = '';
		if (!text || busy) return;
		if (name === null) {
			name = text;
			return;
		}
		const n = name;
		name = null;
		busy = true;
		try {
			const res = await fetch('/api/login', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ name: n, pass: text })
			});
			if ((await res.json()).ok) await login();
		} catch {
			// offline: nothing to show
		} finally {
			busy = false;
		}
	};

	const onkey = (e: KeyboardEvent) => {
		if (e.ctrlKey || e.metaKey || e.altKey) return;
		if (e.key === 'Enter') submit();
		else if (e.key === 'Delete') reset();
		else if (e.key === 'Backspace') buf = buf.slice(0, -1);
		else if (e.key.length === 1) buf += e.key;
		else return;
		e.preventDefault();
	};

	// /chart is ~120 modules (three.js, charts); fetch its code early so the login only waits for data.
	onMount(() => {
		const t = setTimeout(() => preloadCode('/chart').catch(() => {}), 500);
		return () => clearTimeout(t);
	});
</script>

<svelte:window onkeydown={onkey} />

<div class="cf">
	<header class="wrap hd">
		<h1>Error 522 <small>Ray ID: {data.ray} &bull; {data.time}</small></h1>
		<h2>Connection timed out</h2>
	</header>

	<section class="band">
		<div class="wrap trio">
			<div class="node">
				<span class="ico">
					<svg viewBox="0 0 100 80" aria-hidden="true"><rect x="4" y="6" width="92" height="68" rx="6" fill="#999" /><rect x="10" y="20" width="80" height="48" fill="#fff" /><circle cx="13" cy="13" r="2.5" fill="#fff" /><circle cx="21" cy="13" r="2.5" fill="#fff" /><circle cx="29" cy="13" r="2.5" fill="#fff" /></svg>
					<i class="ok"></i>
				</span>
				<span class="who">You</span>
				<span class="what">Browser</span>
				<span class="st ok">Working</span>
			</div>
			<div class="node">
				<span class="ico">
					<svg viewBox="0 0 120 80" aria-hidden="true"><path d="M30 72a22 22 0 0 1-2-44 30 30 0 0 1 57-6 24 24 0 0 1 7 50z" fill="#999" /></svg>
					<i class="ok"></i>
				</span>
				<span class="who">Jakarta</span>
				<span class="what">Cloudflare</span>
				<span class="st ok">Working</span>
			</div>
			<div class="node">
				<span class="ico">
					<svg viewBox="0 0 100 80" aria-hidden="true"><path d="M16 10h68l12 34v26a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V44z" fill="#999" /><rect x="10" y="50" width="80" height="16" rx="2" fill="#777" /><circle cx="78" cy="58" r="3" fill="#ccc" /><circle cx="68" cy="58" r="3" fill="#ccc" /></svg>
					<i class="err"></i>
				</span>
				<span class="who">ariyanto.cloud</span>
				<span class="what">Host</span>
				<span class="st err">Error</span>
			</div>
		</div>
		<div class="wrap pointer"><span></span></div>
	</section>

	<section class="wrap info">
		<div>
			<h2>What happened?</h2>
			<p>The initial connection between Cloudflare's network and the origin web server timed out. As a result, the web page can not be displayed.</p>
		</div>
		<div>
			<h2>What can I do?</h2>
			<h5>If you're a visitor of this website:</h5>
			<p>Please try again in a few minutes.</p>
			<h5>If you're the owner of this website:</h5>
			<p>Contact your hosting provider letting them know your web server is not completing requests. An Error 522 means that the request was able to connect to your web server, but that the request didn't finish. The most likely cause is that something on your server is hogging resources. <a href="/">Additional troubleshooting information here.</a></p>
		</div>
	</section>

	<footer class="wrap ft">
		<span>Cloudflare Ray ID: <b>{data.ray}</b></span>
		<span class="dot">&bull;</span>
		<span>Your IP: {#if showIp}{data.ip}{:else}<button type="button" onclick={() => (showIp = true)}>Click to reveal</button>{/if}</span>
		<span class="dot">&bull;</span>
		<span>Performance &amp; security by <a href="/">Cloudflare</a></span>
	</footer>
</div>

<style>
	.cf {
		min-height: 100vh;
		background: #fff;
		color: #404040;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
		-webkit-font-smoothing: antialiased;
	}
	.wrap {
		max-width: 960px;
		margin: 0 auto;
		padding: 0 20px;
		box-sizing: border-box;
	}
	.hd {
		padding-top: 32px;
		padding-bottom: 24px;
	}
	h1 {
		margin: 0;
		font-size: 60px;
		font-weight: 300;
		line-height: 1.1;
	}
	h1 small {
		font-size: 15px;
		font-weight: 400;
		font-family: Consolas, 'Courier New', monospace;
		color: #404040;
		vertical-align: middle;
	}
	.hd h2 {
		margin: 4px 0 0;
		font-size: 30px;
		font-weight: 300;
		color: #999;
	}
	.band {
		position: relative;
		background: linear-gradient(#dedede, #ebebeb 3%, #ebebeb 97%, #dedede);
		padding: 40px 0 0;
	}
	.trio {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		text-align: center;
	}
	.node {
		display: flex;
		flex-direction: column;
		align-items: center;
	}
	.ico {
		position: relative;
		width: 120px;
		height: 80px;
		margin-bottom: 20px;
	}
	.ico svg {
		width: 100%;
		height: 100%;
	}
	.ico i {
		position: absolute;
		left: 50%;
		bottom: -10px;
		width: 44px;
		height: 44px;
		margin-left: -22px;
		border-radius: 50%;
		border: 4px solid #ebebeb;
		box-sizing: border-box;
	}
	.ico i.ok {
		background: #9bca3e url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M6 12.5l4 4 8-9' fill='none' stroke='%23fff' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / 26px no-repeat;
	}
	.ico i.err {
		background: #bd2426 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M7 7l10 10M17 7L7 17' stroke='%23fff' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E") center / 22px no-repeat;
	}
	.who {
		font-size: 15px;
		color: #404040;
	}
	.what {
		font-size: 24px;
		font-weight: 300;
		color: #999;
		line-height: 1.3;
	}
	.st {
		font-size: 24px;
		font-weight: 300;
		line-height: 1.3;
	}
	.st.ok {
		color: #9bca3e;
	}
	.st.err {
		color: #bd2426;
	}
	.pointer {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		height: 30px;
		margin-top: 20px;
	}
	.pointer span {
		grid-column: 3;
		justify-self: center;
		align-self: end;
		width: 0;
		height: 0;
		border: 15px solid transparent;
		border-bottom-color: #fff;
		border-top: 0;
	}
	.info {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 40px;
		padding-top: 30px;
		padding-bottom: 40px;
		border-bottom: 1px solid #dedede;
	}
	.info h2 {
		margin: 0 0 12px;
		font-size: 30px;
		font-weight: 300;
	}
	.info h5 {
		margin: 12px 0 4px;
		font-size: 15px;
		font-weight: 600;
	}
	.info p {
		margin: 0;
		font-size: 15px;
		line-height: 1.5;
	}
	.info a,
	.ft a {
		color: #2f7bbf;
		text-decoration: none;
	}
	.info a:hover,
	.ft a:hover {
		color: #f68b1f;
	}
	.ft {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 6px;
		padding-top: 16px;
		padding-bottom: 24px;
		font-size: 13px;
		color: #404040;
	}
	.ft b {
		font-weight: 600;
	}
	.ft button {
		border: 0;
		padding: 0;
		background: none;
		font: inherit;
		color: #2f7bbf;
		cursor: pointer;
	}
	.dot {
		color: #999;
	}
	@media (max-width: 720px) {
		h1 {
			font-size: 40px;
		}
		h1 small {
			display: block;
			margin-top: 6px;
		}
		.trio,
		.pointer {
			gap: 10px;
		}
		.info {
			grid-template-columns: 1fr;
		}
	}
</style>
