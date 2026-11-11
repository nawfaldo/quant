import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { error, json } from '@sveltejs/kit';
import { db } from '#lib/server/db.ts';
import { REPO } from '#lib/server/agentTools.ts';
import type { RequestHandler } from './$types';

const run = promisify(execFile);
const PYTHON = process.env.PYTHON ?? 'py';

// Close one open position from the Open positions table. The hand (mt5/hand.py)
// clicks Close in the account's MT5 window, under the same lock the bridge
// holds for its own hand orders; the live runtime then reconciles the
// strategy to flat as an external close.
export const POST: RequestHandler = async ({ params }) => {
	const ticket = Number(params.ticket);
	if (!Number.isInteger(ticket) || ticket <= 0) error(400, 'bad ticket');
	const row = db
		.prepare(
			`SELECT a.login FROM mt5_bridge_positions p JOIN mt5_accounts a ON a.id = p.account_id WHERE p.ticket = ?`
		)
		.get(ticket) as { login: unknown } | undefined;
	if (!row) error(404, `position ${ticket} is not open`);
	try {
		await run(PYTHON, ['mt5/hand.py', 'close', '--login', String(row.login), '--ticket', String(ticket), '--yes'], {
			cwd: REPO,
			timeout: 150_000,
			windowsHide: true
		});
	} catch (e) {
		const err = e as { stderr?: string; message: string };
		const last = (err.stderr ?? '').trim().split('\n').at(-1) || err.message;
		error(502, `close failed: ${last}`);
	}
	return json({ ticket, closed: true });
};
