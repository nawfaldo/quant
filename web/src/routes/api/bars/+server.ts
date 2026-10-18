import { error, json } from '@sveltejs/kit';
import { chartBars, symbolTable, TIMEFRAMES } from '#lib/server/bars.ts';
import type { RequestHandler } from './$types';

// Candles for one symbol and timeframe: /api/bars?symbol=USTEC&tf=15 (minutes).
export const GET: RequestHandler = async ({ url }) => {
	const tf = Number(url.searchParams.get('tf') ?? 1);
	if (!(TIMEFRAMES as readonly number[]).includes(tf)) error(400, 'unknown timeframe');
	const table = symbolTable(url.searchParams.get('symbol') ?? 'USTEC');
	if (!table) error(404, 'unknown symbol');
	return json(await chartBars(table, 3000, tf));
};
