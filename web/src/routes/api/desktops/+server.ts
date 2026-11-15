import { error, json } from '@sveltejs/kit';
import { addDesktop, deleteDesktop, selectDesktop, desktopState, chartLayout } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = () => json(addDesktop());

// Read an intermediate desktop without activating it.
export const GET: RequestHandler = ({ url }) => {
	const id = Number(url.searchParams.get('id'));
	if (!Number.isInteger(id) || !desktopState().desktops.some((d) => d.id === id)) error(404, 'Desktop not found');
	return json({ id, ...chartLayout(id) });
};

export const PATCH: RequestHandler = async ({ request }) => {
	const { id } = await request.json();
	if (!selectDesktop(id)) error(404, 'Desktop not found');
	return json({ ...desktopState(), ...chartLayout(id) });
};

export const DELETE: RequestHandler = async ({ request }) => {
	const { id } = await request.json();
	const state = deleteDesktop(id);
	if (state === null) error(400, 'Invalid desktop or active desktop');
	return json(state);
};
