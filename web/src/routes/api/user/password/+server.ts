import { json } from '@sveltejs/kit';
import { setPassword } from '#lib/server/auth.ts';
import type { RequestHandler } from './$types';

// The /user page's new password: { pass }. Only reached logged in (hooks.server.ts sets
// locals.user); changes that user's password.
export const POST: RequestHandler = async ({ request, locals }) => {
	const body = await request.json().catch(() => ({}));
	const pass = String(body.pass ?? '');
	if (!pass || pass.length > 200) return json({ ok: false });
	setPassword(locals.user.id, pass);
	return json({ ok: true });
};
