import { error, json } from '@sveltejs/kit';
import { db } from '#lib/server/db.ts';
import { bookBacktest, bookBacktestVariant, bookMonteCarlo } from '#lib/server/backtest.ts';
import type { RequestHandler } from './$types';

// The realised IS/OOS backtest of the book the account trades, for the Strategy tab. null when none exists.
export const GET: RequestHandler = ({ params }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const r = db
		.prepare('SELECT book FROM mt5_account_strategies WHERE account_id = ? AND active = 1 GROUP BY book ORDER BY COUNT(*) DESC LIMIT 1')
		.get(id) as { book?: string } | undefined;
	const book = r?.book ?? null;
	const backtest = bookBacktest(book);
	return json(backtest && { ...backtest, montecarlo: bookMonteCarlo(book) });
};

// The edit page's Run: { book, removed: strategy names, risk } -> the same shape, approximated from the replay.
export const POST: RequestHandler = async ({ params, request }) => {
	if (!Number.isInteger(Number(params.id))) error(400, 'bad account id');
	const b = await request.json();
	const risk = b.risk == null ? null : Number(b.risk);
	if (risk != null && !(Number.isFinite(risk) && risk > 0)) error(400, 'bad risk');
	return json(bookBacktestVariant(typeof b.book === 'string' ? b.book : null, Array.isArray(b.removed) ? b.removed.map(String) : [], risk));
};
