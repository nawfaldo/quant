import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Which symbols an open chart is showing, for tools/chart_live_candle.py:
// it publishes the live candle of every symbol listed here. Written whenever
// the set changes and every 5 seconds as a heartbeat (the script ignores the
// file once it is 30 seconds old, e.g. after the web server stops).
const DIR = fileURLToPath(new URL('../../../../data/live/', import.meta.url));
const PATH = DIR + 'watch.json';
// Kept on globalThis so a dev-server hot reload reuses one registry and one
// heartbeat instead of starting a second, empty one that overwrites the file.
const shared = globalThis as typeof globalThis & {
	__watching?: Map<string, number>; // symbol -> open charts
	__watchTimer?: ReturnType<typeof setInterval>;
};
const watching = (shared.__watching ??= new Map());

function write() {
	mkdirSync(DIR, { recursive: true });
	const tmp = PATH + '.tmp';
	writeFileSync(tmp, JSON.stringify({ updated: Date.now() / 1000, symbols: [...watching.keys()] }));
	renameSync(tmp, PATH);
}

if (shared.__watchTimer) clearInterval(shared.__watchTimer);
shared.__watchTimer = setInterval(write, 5000);
shared.__watchTimer.unref();

// Mark a symbol as shown; the returned function un-marks it.
export function watch(symbol: string): () => void {
	watching.set(symbol, (watching.get(symbol) ?? 0) + 1);
	write();
	return () => {
		const n = (watching.get(symbol) ?? 1) - 1;
		if (n > 0) watching.set(symbol, n);
		else watching.delete(symbol);
		write();
	};
}
