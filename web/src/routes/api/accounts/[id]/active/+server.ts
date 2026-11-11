import { error, json } from '@sveltejs/kit';
import { db } from '#lib/server/db.ts';
import { deactivatedStrategies, setDeactivatedStrategies } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

// The live server owns app.db writes, so strategies are switched through its API.
const LIVE = `http://127.0.0.1:${process.env.LIVE_PORT ?? 4000}`;

// Activate or deactivate a whole account: { active: boolean }. Deactivate switches every
// active strategy off (no new trades) and remembers which; Activate switches those back on.
export const POST: RequestHandler = async ({ params, request }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const active = Boolean((await request.json()).active);
	const rows = db.prepare('SELECT id, active FROM mt5_account_strategies WHERE account_id = ?').all(id);
	if (!rows.length) error(404, 'account has no strategies');
	let ids: number[];
	if (active) {
		const saved = deactivatedStrategies(id);
		ids = saved.length ? saved : rows.map((r) => Number(r.id));
	} else ids = rows.filter((r) => Number(r.active) === 1).map((r) => Number(r.id));
	const failed: number[] = [];
	for (const sid of ids) {
		const r = await fetch(`${LIVE}/api/march/mt5/accounts/${id}/strategies/${sid}/${active ? 'on' : 'off'}`, { method: 'PUT' }).catch(() => null);
		if (!r?.ok) failed.push(sid);
	}
	if (failed.length) error(502, `live server refused strategies ${failed.join(', ')}`);
	setDeactivatedStrategies(id, active ? [] : ids);
	return json({ id, active, strategies: ids.length });
};
