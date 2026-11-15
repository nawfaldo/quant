import { error, json } from '@sveltejs/kit';
import { listSymbols } from '#lib/server/bars.ts';
import { desktopState, setDesktopSymbolFilter } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request }) => {
 const { desktopId, symbols } = await request.json();
 if (!Number.isInteger(desktopId) || !desktopState().desktops.some((d) => d.id === desktopId)) error(404, 'Desktop not found');
 const valid = new Set(listSymbols());
 if (!Array.isArray(symbols) || symbols.some((s) => typeof s !== 'string' || !valid.has(s))) error(400, 'Invalid symbols');
 setDesktopSymbolFilter(desktopId, [...new Set<string>(symbols)]);
 return json({ ok: true });
};
