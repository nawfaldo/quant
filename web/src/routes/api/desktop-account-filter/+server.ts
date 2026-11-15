import { error, json } from '@sveltejs/kit';
import { activeAccounts } from '#lib/server/accounts.ts';
import { desktopState, setDesktopAccountFilter } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request }) => {
 const { desktopId, accountIds } = await request.json();
 if (!Number.isInteger(desktopId) || !desktopState().desktops.some((d) => d.id === desktopId)) error(404, 'Desktop not found');
 const validIds = new Set(activeAccounts().map((a) => a.id));
 if (!Array.isArray(accountIds) || accountIds.some((id) => !Number.isInteger(id) || !validIds.has(id))) error(400, 'Invalid accounts');
 setDesktopAccountFilter(desktopId, [...new Set<number>(accountIds)]);
 return json({ ok: true });
};
