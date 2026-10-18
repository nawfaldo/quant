import { error, json } from '@sveltejs/kit';
import { setBlurred } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

// Hide or show an account's balance, equity and change: { blurred: boolean }.
export const POST: RequestHandler = async ({ params, request }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const { blurred } = await request.json();
	setBlurred(id, Boolean(blurred));
	return json({ id, blurred: Boolean(blurred) });
};
