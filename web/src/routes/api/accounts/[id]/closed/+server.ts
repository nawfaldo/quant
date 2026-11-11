import { error, json } from '@sveltejs/kit';
import { closedPositions } from '#lib/server/accounts.ts';
import type { RequestHandler } from './$types';

// The account's closed trades for the History tab, newest first.
export const GET: RequestHandler = ({ params }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	return json(closedPositions(id));
};
