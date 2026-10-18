// Internet download speed, measured by downloading a ~10 MB test file every
// 5 minutes while a dashboard is open (about 120 MB an hour, and it briefly
// loads the connection). Cloudflare first, OVH if Cloudflare refuses: it
// answers 429 with a 1-byte body when tested too often, which once read as
// "0.0 Mbps", so a result only counts if the whole file arrived.

const SOURCES = ['https://speed.cloudflare.com/__down?bytes=10000000', 'https://proof.ovh.net/files/10Mb.dat'];
const EVERY_MS = 5 * 60 * 1000;
const BACKOFF_MS = 15 * 60 * 1000; // after every source failed
const MIN_BYTES = 1_000_000;

// Kept on globalThis so a dev-server hot reload reuses one schedule instead
// of starting a second set of tests (which is what got us rate-limited).
type State = {
	mbps: number | null;
	viewers: number;
	timer?: ReturnType<typeof setTimeout>;
	running: boolean;
};
const g = globalThis as typeof globalThis & { __speed?: State };
const state: State = (g.__speed ??= { mbps: null, viewers: 0, running: false });

async function measure(url: string): Promise<number | null> {
	const started = performance.now();
	const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(60_000) });
	if (!res.ok || !res.body) return null;
	const reader = res.body.getReader();
	let bytes = 0;
	for (;;) {
		const { done, value } = await reader.read();
		if (done) break;
		bytes += value.byteLength;
	}
	if (bytes < MIN_BYTES) return null;
	return (bytes * 8) / ((performance.now() - started) / 1000) / 1e6;
}

async function test() {
	if (state.running) return;
	state.running = true;
	let ok = false;
	try {
		for (const url of SOURCES) {
			const mbps = await measure(url).catch(() => null);
			if (mbps !== null) {
				state.mbps = mbps;
				ok = true;
				break;
			}
		}
	} finally {
		state.running = false;
	}
	// Next test, unless every dashboard has closed meanwhile.
	clearTimeout(state.timer);
	if (state.viewers > 0) state.timer = setTimeout(test, ok ? EVERY_MS : BACKOFF_MS);
}

// The latest good result, in Mbps, or null before the first one.
export const internetMbps = () => state.mbps;

// Test while at least one dashboard is open; the returned function stops it.
export function measureWhileOpen(): () => void {
	if (state.viewers++ === 0 && !state.timer && !state.running) test();
	return () => {
		if (--state.viewers === 0) {
			clearTimeout(state.timer);
			state.timer = undefined;
		}
	};
}
