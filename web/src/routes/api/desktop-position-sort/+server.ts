import { error, json } from '@sveltejs/kit';
import { desktopState, setDesktopPositionSort } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request }) => {
 const { desktopId, sorting } = await request.json();
 if (!Number.isInteger(desktopId) || !desktopState().desktops.some((d) => d.id === desktopId)) error(404, 'Desktop not found');
 const keys = ['account', 'strategy', 'symbol', 'side', 'openTime', 'price', 'lot', 'pnl'];
 if (sorting !== null && (!sorting || !keys.includes(sorting.key) || !['asc', 'desc'].includes(sorting.direction))) error(400, 'Invalid sorting');
 setDesktopPositionSort(desktopId, sorting);
 return json({ ok: true });
};
