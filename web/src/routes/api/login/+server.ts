import { json } from '@sveltejs/kit';
import { checkLogin, createSession, SESSION_COOKIE } from '#lib/server/auth.ts';
import type { RequestHandler } from './$types';

// Login from the shop's search box: { name, pass }. Answers only { ok }, never why it failed.
// Each attempt runs a 64 MiB argon2 hash, so attempts are capped per client address.
const WINDOW_MS = 10 * 60_000;
const MAX_TRIES = 10;
const tries = new Map<string, number[]>();

export const POST: RequestHandler = async ({ request, cookies, getClientAddress }) => {
	const ip = request.headers.get('cf-connecting-ip') ?? getClientAddress();
	const now = Date.now();
	const recent = (tries.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
	if (recent.length >= MAX_TRIES) return json({ ok: false });
	recent.push(now);
	tries.set(ip, recent);

	const body = await request.json().catch(() => ({}));
	const name = String(body.name ?? '').slice(0, 100);
	const pass = String(body.pass ?? '').slice(0, 200);
	const user = name && pass ? checkLogin(name, pass) : null;
	if (!user) return json({ ok: false });

	tries.delete(ip);
	const { token, expires } = createSession(user.id);
	cookies.set(SESSION_COOKIE, token, { path: '/', httpOnly: true, sameSite: 'lax', expires });
	return json({ ok: true });
};
