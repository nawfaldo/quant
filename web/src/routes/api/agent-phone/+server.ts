import { json } from '@sveltejs/kit';
import { setUiPref, uiPref } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

// Whether the agent iPhone is shown (true) or closed to its icon (false); default shown.
export const GET: RequestHandler = () => json({ open: uiPref('agent_phone_open') !== '0' });

export const POST: RequestHandler = async ({ request }) => {
	const open = Boolean((await request.json()).open);
	setUiPref('agent_phone_open', open ? '1' : '0');
	return json({ open });
};
