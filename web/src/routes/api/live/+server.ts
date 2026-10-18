import { json } from '@sveltejs/kit';
import { activeAccounts } from '#lib/server/accounts.ts';
import { liveTail, symbolTable } from '#lib/server/bars.ts';
import { openPositions } from '#lib/server/positions.ts';
import { internetMbps, measureWhileOpen } from '#lib/server/speed.ts';
import { watch } from '#lib/server/watch.ts';
import type { RequestHandler } from './$types';

// POLLED, NOT STREAMED. This used to be a server-sent event stream, but
// Cloudflare quick tunnels (trycloudflare.com) do not pass server-sent events:
// through the tunnel not one byte arrived in 40 seconds, so the dashboard never
// updated from outside. The page now asks about once a second.

// A symbol stays watched (the live candle script keeps publishing it), and the
// speed test keeps running, until no poll has asked for it for this long.
const LEASE_MS = 10_000;

type Lease = { release: () => void; until: number };
const shared = globalThis as typeof globalThis & {
	__liveLeases?: Map<string, Lease>; // symbol, or '' for the speed test
	__liveSweep?: ReturnType<typeof setInterval>;
};
const leases = (shared.__liveLeases ??= new Map());

function hold(key: string, start: () => () => void) {
	const until = Date.now() + LEASE_MS;
	const lease = leases.get(key);
	if (lease) lease.until = until;
	else leases.set(key, { release: start(), until });
}

if (shared.__liveSweep) clearInterval(shared.__liveSweep);
shared.__liveSweep = setInterval(() => {
	for (const [key, lease] of leases)
		if (lease.until < Date.now()) {
			lease.release();
			leases.delete(key);
		}
}, 1000);
shared.__liveSweep.unref();

// /api/live?symbols=USTEC,EURUSD: accounts, positions, and the previous and
// forming candle per symbol on screen (one per chart in the layout).
export const GET: RequestHandler = async ({ url }) => {
	const symbols = [
		...new Set(
			(url.searchParams.get('symbols') ?? 'USTEC')
				.split(',')
				.map((s) => s.trim().toUpperCase())
				.filter((s) => symbolTable(s))
		)
	];
	symbols.forEach((s) => hold(s, () => watch(s)));
	hold('', measureWhileOpen);
	const tails = await Promise.all(symbols.map((s) => liveTail(symbolTable(s)!)));
	return json(
		{
			accounts: activeAccounts(),
			positions: openPositions(),
			candles: Object.fromEntries(symbols.map((s, i) => [s, tails[i]])),
			mbps: internetMbps()
		},
		{ headers: { 'Cache-Control': 'no-store' } }
	);
};
