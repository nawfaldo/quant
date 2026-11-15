import { json } from '@sveltejs/kit';
import { deleteSession, SESSION_COOKIE } from '#lib/server/auth.ts';
import type { RequestHandler } from './$types';

// Logout (the door on /chart): drops this browser's session.
export const POST: RequestHandler = async ({ cookies }) => {
	const token = cookies.get(SESSION_COOKIE);
	if (token) deleteSession(token);
	cookies.delete(SESSION_COOKIE, { path: '/' });
	return json({ ok: true });
};
