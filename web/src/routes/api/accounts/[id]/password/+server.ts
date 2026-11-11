import { error, json } from '@sveltejs/kit';
import { accountPassword } from '#lib/server/accounts.ts';
import type { RequestHandler } from './$types';

// The account's MT5 password. Served to this machine only, never through a tunnel.
export const GET: RequestHandler = ({ params, request, getClientAddress }) => {
	const addr = getClientAddress();
	const local = addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
	if (!local || request.headers.has('cf-connecting-ip') || request.headers.has('x-forwarded-for')) error(403, 'local only');
	const id = Number(params.id);
	if (!Number.isInteger(id)) error(400, 'bad account id');
	return json({ password: accountPassword(id) ?? '' });
};
