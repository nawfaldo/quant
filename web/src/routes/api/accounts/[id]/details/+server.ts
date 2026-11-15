import { error, json } from '@sveltejs/kit';
import { DatabaseSync } from 'node:sqlite';
import { DB_PATH } from '#lib/server/db.ts';
import type { RequestHandler } from './$types';

// The live server has no endpoint for these fields and the MT5 bridge reads them straight
// from app.db, so this route writes them itself. Refused while the account is active, so a
// running bridge never sees its login change underneath it.

// { login?, password?, server? }. The password is only accepted from this machine.
export const POST: RequestHandler = async ({ params, request, getClientAddress }) => {
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	const body = (await request.json()) as { login?: string; password?: string; server?: string };
	const db = new DatabaseSync(DB_PATH);
	try {
		db.exec('PRAGMA busy_timeout = 5000');
		const active = db.prepare('SELECT 1 FROM mt5_account_strategies WHERE account_id = ? AND active = 1 LIMIT 1').get(id);
		if (active) error(409, 'deactivate the account before editing it');
		if (!db.prepare('SELECT 1 FROM mt5_accounts WHERE id = ?').get(id)) error(404, 'account not found');
		if (body.password != null) {
			const addr = getClientAddress();
			const local = addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
			if (!local || request.headers.has('cf-connecting-ip') || request.headers.has('x-forwarded-for')) error(403, 'password can only be changed from this machine');
		}
		const set: [string, string][] = [];
		if (body.login != null && body.login.trim()) set.push(['login', body.login.trim()]);
		if (body.server != null && body.server.trim()) set.push(['server', body.server.trim()]);
		if (body.password != null && body.password !== '') set.push(['password', body.password]);
		if (!set.length) error(400, 'nothing to update');
		db.prepare(`UPDATE mt5_accounts SET ${set.map(([k]) => `${k} = ?`).join(', ')} WHERE id = ?`).run(...set.map(([, v]) => v), id);
		return json({ id, updated: set.map(([k]) => k) });
	} finally {
		db.close();
	}
};
