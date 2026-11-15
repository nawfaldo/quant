import { json } from '@sveltejs/kit';
import { readFileSync, writeFileSync } from 'node:fs';
import { avatarFile, avatarUrl } from '#lib/server/auth.ts';
import type { RequestHandler } from './$types';

// The logged-in user's profile picture. GET serves it; POST saves a new one: the raw bytes of a
// square webp the /user page made from the chosen image (at most 2 MB), answering its new URL.
export const GET: RequestHandler = ({ locals }) => {
	try {
		return new Response(readFileSync(avatarFile(locals.user.id)), {
			headers: { 'content-type': 'image/webp', 'cache-control': 'private, max-age=31536000, immutable' }
		});
	} catch {
		return new Response(null, { status: 404 });
	}
};

export const POST: RequestHandler = async ({ request, locals }) => {
	const bytes = Buffer.from(await request.arrayBuffer());
	// RIFF....WEBP header
	const webp = bytes.length > 12 && bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP';
	if (!webp || bytes.length > 2_000_000) return json({ ok: false });
	writeFileSync(avatarFile(locals.user.id), bytes);
	return json({ ok: true, url: avatarUrl(locals.user.id) });
};
