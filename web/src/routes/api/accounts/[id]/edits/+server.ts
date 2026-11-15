import { error, json } from '@sveltejs/kit';
import { setAccountEdits } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

// Save the edit page's changes: { name, book, risk, removed }.
export const POST: RequestHandler = async ({ params, request }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const b = await request.json();
	const risk = b.risk == null ? null : Number(b.risk);
	if (risk != null && !(Number.isFinite(risk) && risk > 0)) error(400, 'bad risk');
	const edits = {
		name: typeof b.name === 'string' && b.name.trim() ? b.name.trim() : null,
		book: typeof b.book === 'string' && b.book ? b.book : null,
		risk,
		removed: Array.isArray(b.removed) ? b.removed.map(String) : [],
		added: Array.isArray(b.added) ? b.added.map(String) : []
	};
	setAccountEdits(id, edits);
	return json(edits);
};
