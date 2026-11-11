import { error, json } from '@sveltejs/kit';
import { EQUITY_RANGES, equityHistory, type EquityRange } from '#lib/server/accounts.ts';
import type { RequestHandler } from './$types';

// Equity candles for the History tab: ?range=day|week|month|ytd|1y|3y|5y|all.
export const GET: RequestHandler = ({ params, url }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const range = (url.searchParams.get('range') ?? 'all') as EquityRange;
	if (!EQUITY_RANGES.includes(range)) error(400, 'bad range');
	return json(equityHistory(id, range));
};
