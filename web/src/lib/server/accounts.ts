import { db } from './db.ts';
import { blurredAccounts } from './webdb.ts';

export type Account = { id: number; name: string; balance: number; equity: number | null; blurred: boolean };

// Accounts with at least one active strategy, with the latest balance and
// equity the MT5 bridge reported.
export function activeAccounts(): Account[] {
	const blurred = blurredAccounts();
	return db
		.prepare(
			`SELECT a.id, a.login AS name, h.balance, h.equity
			 FROM mt5_accounts a
			 LEFT JOIN mt5_bridge_heartbeats h ON h.account_id = a.id
			 WHERE EXISTS (SELECT 1 FROM mt5_account_strategies s WHERE s.account_id = a.id AND s.active = 1)
			 ORDER BY a.id`
		)
		.all()
		.map((r: Record<string, unknown>) => ({
			id: Number(r.id),
			name: String(r.name),
			balance: Number(r.balance ?? 0),
			equity: r.equity == null ? null : Number(r.equity),
			blurred: blurred.has(Number(r.id))
		}));
}
