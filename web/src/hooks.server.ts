import type { Handle, RequestEvent, ResolveOptions } from '@sveltejs/kit';
import { gzipSync } from 'node:zlib';
import { SESSION_COOKIE, sessionUser } from '#lib/server/auth.ts';

// Logged out: "/" (the shop) is the only page; every other page sends you back to "/", and
// APIs / page data answer a blank 401. Logged in: "/" sends you to /chart.
// The login is hidden in the shop's search box ("/" posts to /api/login).
const BLANK = '<!doctype html><html><head><meta charset="utf-8"><title>ariyanto</title></head><body></body></html>';

// Gzip what the server generates (pages, page data, JSON APIs): the dashboard is served
// from this PC through the Cloudflare tunnel, and Cloudflare only compresses AFTER the PC has
// uploaded the response. Candle history is ~240 KB of JSON per chart uncompressed, so six
// charts were 1.2 MB of upload per load. Static files are precompressed by adapter-node.
// Never the agent chat (/api/agent streams its reply).
async function compressed(event: RequestEvent, res: Response): Promise<Response> {
	const type = res.headers.get('content-type') ?? '';
	if (
		!/json|html/.test(type) ||
		res.headers.has('content-encoding') ||
		event.url.pathname.startsWith('/api/agent') ||
		!/\bgzip\b/.test(event.request.headers.get('accept-encoding') ?? '')
	)
		return res;
	const body = Buffer.from(await res.arrayBuffer());
	const headers = new Headers(res.headers);
	headers.append('vary', 'Accept-Encoding');
	if (body.length < 1024) return new Response(body, { status: res.status, statusText: res.statusText, headers });
	headers.set('content-encoding', 'gzip');
	headers.delete('content-length');
	return new Response(gzipSync(body, { level: 6 }), { status: res.status, statusText: res.statusText, headers });
}

const go = (to: string) => new Response(null, { status: 303, headers: { location: to, 'cache-control': 'no-store' } });

export const handle: Handle = async ({ event, resolve }) => compressed(event, await guard(event, resolve));

async function guard(event: RequestEvent, resolve: (e: RequestEvent, o?: ResolveOptions) => Response | Promise<Response>): Promise<Response> {
	const user = sessionUser(event.cookies.get(SESSION_COOKIE));
	const p = event.url.pathname;
	const method = event.request.method;
	if (p === '/api/login' && method === 'POST') return resolve(event);
	if (user) {
		if (p === '/') return go('/chart');
		event.locals.user = user;
		return resolve(event);
	}
	if (p === '/' || p === '/__data.json') return resolve(event); // the 522 page's ray/time/ip
	// a page visit goes back to the shop (SvelteKit turns this into a redirect for its data
	// fetches too, so they carry no data); API calls get the blank 401
	if (method === 'GET' && !p.startsWith('/api/')) return go('/');
	return new Response(BLANK, {
		status: 401,
		headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }
	});
};
