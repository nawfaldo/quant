import { json } from '@sveltejs/kit';
import { setName } from '#lib/server/auth.ts';
import type { RequestHandler } from './$types';

// The /user page's new name: { name }. Only reached logged in; renames that user.
// { ok: false } when it is empty, too long, or another user has it.
export const POST: RequestHandler = async ({ request, locals }) => {
	const body = await request.json().catch(() => ({}));
	const name = String(body.name ?? '').trim();
	if (!name || name.length > 100) return json({ ok: false });
	return json({ ok: setName(locals.user.id, name) });
};
