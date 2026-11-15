import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// The live server owns app.db writes, so the delete goes through its API.
const LIVE = `http://127.0.0.1:${process.env.LIVE_PORT ?? 4000}`;

// Delete the account and all its strategies. Open positions in MT5 are not closed.
export const POST: RequestHandler = async ({ params }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const r = await fetch(`${LIVE}/api/march/mt5/accounts/${id}`, { method: 'DELETE' }).catch(() => null);
	if (!r?.ok) error(502, `live server refused the delete (${r?.status ?? 'unreachable'})`);
	return json({ id, deleted: true });
};
