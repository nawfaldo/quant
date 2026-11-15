import { repo } from './paths.ts';
import { db } from './db.ts';
import { accountEdits, blurredAccounts, type AccountEdits } from './webdb.ts';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { bookBacktest, bookSleeveStats, sleeveKey, type SleeveStats } from './backtest.ts';

export type Account = {
	id: number;
	name: string;
	login: string;
	server: string;
	balance: number;
	initialBalance: number | null;
	equity: number | null;
	blurred: boolean;
	// false when every strategy is switched off (Deactivate); still listed so it can be reactivated.
	active: boolean;
	book: string | null;
	sleeves: number;
	strategies: { strategy: string; symbol: string; oos?: SleeveStats | null }[];
	risk: number | null;
	correlation: number | null;
	diversification: number | null;
	candles: Candle[];
	sharpe: number | null;
	var95: number | null;
	// Max drawdown %, peak to trough on the closed-trade equity candles (not mark-to-market).
	mdd: number | null;
	edits: AccountEdits;
};

// Accounts with at least one strategy (active or deactivated), with the latest balance and
// equity the MT5 bridge reported.
export function activeAccounts(): Account[] {
	const blurred = blurredAccounts();
	return db
		.prepare(
			`SELECT a.id, a.login, a.server, h.balance, h.equity, h.initial_balance,
			        (SELECT s.book FROM mt5_account_strategies s WHERE s.account_id = a.id
			         GROUP BY s.book ORDER BY MAX(s.active) DESC, COUNT(*) DESC LIMIT 1) AS book,
			        EXISTS (SELECT 1 FROM mt5_account_strategies s WHERE s.account_id = a.id AND s.active = 1) AS active,
			        (SELECT COUNT(*) FROM mt5_account_strategies s WHERE s.account_id = a.id AND s.active = 1) AS sleeves
			 FROM mt5_accounts a
			 LEFT JOIN mt5_bridge_heartbeats h ON h.account_id = a.id
			 WHERE EXISTS (SELECT 1 FROM mt5_account_strategies s WHERE s.account_id = a.id)
			 ORDER BY a.id`
		)
		.all()
		.map((r: Record<string, unknown>) => ({
			id: Number(r.id),
			name: String(r.login),
			login: String(r.login),
			server: String(r.server ?? ''),
			balance: Number(r.balance ?? 0),
			initialBalance: r.initial_balance == null ? null : Number(r.initial_balance),
			equity: r.equity == null ? null : Number(r.equity),
			blurred: blurred.has(Number(r.id)),
			active: Number(r.active) === 1,
			edits: accountEdits(Number(r.id)),
			book: r.book == null ? null : String(r.book),
			sleeves: Number(r.sleeves ?? 0),
			strategies: activeStrategies(Number(r.id), r.book == null ? null : String(r.book)),
			risk: bookRisk(r.book == null ? null : String(r.book)),
			correlation: bookBacktest(r.book == null ? null : String(r.book))?.correlation ?? null,
			diversification: bookBacktest(r.book == null ? null : String(r.book))?.diversification ?? null,
			candles: equityCandles(Number(r.id)),
			...riskStats(Number(r.id), r.initial_balance == null ? null : Number(r.initial_balance))
		}));
}

// Only what moves tick to tick: the latest balance and equity per account in activeAccounts().
export type AccountTick = { id: number; balance: number; equity: number | null };
export function accountTicks(): AccountTick[] {
	return db
		.prepare(
			`SELECT a.id, h.balance, h.equity FROM mt5_accounts a
			 LEFT JOIN mt5_bridge_heartbeats h ON h.account_id = a.id
			 WHERE EXISTS (SELECT 1 FROM mt5_account_strategies s WHERE s.account_id = a.id)`
		)
		.all()
		.map((r: Record<string, unknown>) => ({
			id: Number(r.id),
			balance: Number(r.balance ?? 0),
			equity: r.equity == null ? null : Number(r.equity)
		}));
}

function activeStrategies(accountId: number, book: string | null) {
	const stats = bookSleeveStats(book);
	return db
		.prepare('SELECT strategy, symbol FROM mt5_account_strategies WHERE account_id = ? AND active = 1 ORDER BY symbol, strategy')
		.all(accountId)
		.map((r: Record<string, unknown>) => ({ strategy: String(r.strategy), symbol: String(r.symbol), oos: stats[sleeveKey(String(r.strategy))] ?? null }));
}

// The risk dial the live runtime applies to the book: Book::risk_scale in
// live_trade/src/strategies/cfd/mod.rs names the const, e.g.
// `Self::FundedNext => crate::strategies::fundednext::oct_21_2026::RISK_SCALE`.
const STRATEGIES = pathToFileURL(repo('live_trade', 'src', 'strategies') + '/');
function bookRisk(book: string | null): number | null {
	if (!book) return null;
	const lower = book.toLowerCase();
	const variant = lower.startsWith('fundednext') ? 'FundedNext' : lower.startsWith('the5ers') ? 'The5ers' : 'Exness';
	try {
		const src = readFileSync(fileURLToPath(new URL('cfd/mod.rs', STRATEGIES)), 'utf8');
		const ref = src.match(new RegExp(`Self::${variant} => crate::strategies::(\\w+)::(\\w+)::(\\w+)`));
		if (!ref) return null;
		const mod = readFileSync(fileURLToPath(new URL(`${ref[1]}/${ref[2].replaceAll('_', '-')}/mod.rs`, STRATEGIES)), 'utf8');
		const v = mod.match(new RegExp(`const ${ref[3]}: f64 = ([\\d.]+)`));
		return v ? Number(v[1]) : null;
	} catch {
		return null;
	}
}

// The stored MT5 password, for the card's reveal button.
export function accountPassword(id: number): string | null {
	const r = db.prepare('SELECT password FROM mt5_accounts WHERE id = ?').get(id) as { password?: unknown } | undefined;
	return r?.password == null ? null : String(r.password);
}

export type Candle = { o: number; h: number; l: number; c: number };

// Daily candles of the account's closed-trade equity (cumulative realised P&L),
// last `days` trading days. Open = previous close; high/low follow the trade-by-trade path.
export function equityCandles(accountId: number, days = 12): Candle[] {
	const rows = db
		.prepare(
			`SELECT substr(t.mt5_exit_time, 1, 10) AS day, t.mt5pnl AS pnl
			 FROM account_trades t
			 WHERE t.mt5pnl IS NOT NULL AND t.mt5_exit_time <> ''
			   AND t.id IN (SELECT account_trade_id FROM mt5_execution_commands WHERE account_id = ?)
			 ORDER BY t.mt5_exit_time, t.id`
		)
		.all(accountId) as { day: string; pnl: number }[];
	const out: Candle[] = [];
	let eq = 0;
	let day = '';
	for (const r of rows) {
		if (r.day !== day) {
			day = r.day;
			out.push({ o: eq, h: eq, l: eq, c: eq });
		}
		eq += Number(r.pnl);
		const k = out[out.length - 1];
		k.c = eq;
		k.h = Math.max(k.h, eq);
		k.l = Math.min(k.l, eq);
	}
	return out.slice(-days);
}

// Sharpe (annualised, 252 days) and 1-day 95% historical VaR (% of balance)
// from daily closed-trade returns over all live days.
export function riskStats(accountId: number, startBalance: number | null): { sharpe: number | null; var95: number | null; mdd: number | null } {
	const ks = equityCandles(accountId, 10_000);
	if (ks.length < 2 || !startBalance) return { sharpe: null, var95: null, mdd: null };
	const r = ks.map((k) => (k.c - k.o) / (startBalance + k.o));
	const mean = r.reduce((s, x) => s + x, 0) / r.length;
	const sd = Math.sqrt(r.reduce((s, x) => s + (x - mean) ** 2, 0) / (r.length - 1));
	const sorted = [...r].sort((a, b) => a - b);
	const q = sorted[Math.floor(0.05 * (sorted.length - 1))];
	let peak = -Infinity, mdd = 0;
	for (const k of ks) {
		peak = Math.max(peak, startBalance + k.o);
		mdd = Math.max(mdd, (peak - (startBalance + k.l)) / peak);
		peak = Math.max(peak, startBalance + k.h);
	}
	return { mdd: mdd * 100, sharpe: sd > 0 ? (mean / sd) * Math.sqrt(252) : null, var95: Math.max(-q, 0) * 100 };
}

export const EQUITY_RANGES = ['day', 'week', 'month', 'ytd', '1y', '3y', '5y', 'all'] as const;
export type EquityRange = (typeof EQUITY_RANGES)[number];

// Equity candles over a range, from closed trades: equity after each trade is
// today's balance minus the P&L booked since. Candle size grows with the range.
// Times are the trade exit stamps as stored (broker wall clock), read as UTC.
export function equityHistory(accountId: number, range: EquityRange) {
	const bal = db.prepare('SELECT balance FROM mt5_bridge_heartbeats WHERE account_id = ?').get(accountId) as { balance?: number } | undefined;
	const rows = db
		.prepare(
			`SELECT t.mt5_exit_time AS at, t.mt5pnl AS pnl
			 FROM account_trades t
			 WHERE t.mt5pnl IS NOT NULL AND t.mt5_exit_time <> ''
			   AND t.id IN (SELECT account_trade_id FROM mt5_execution_commands WHERE account_id = ?)
			 ORDER BY t.mt5_exit_time, t.id`
		)
		.all(accountId) as { at: string; pnl: number }[];
	if (rows.length === 0) return [];
	const pts = rows.map((r) => ({ t: Date.parse(r.at.replace(' ', 'T') + 'Z') / 1000, pnl: Number(r.pnl) }));
	let eq = Number(bal?.balance ?? 0) - pts.reduce((s, p) => s + p.pnl, 0);

	const end = pts[pts.length - 1].t;
	const D = 86400;
	const endDate = new Date(end * 1000);
	const start = {
		day: end - D,
		week: end - 7 * D,
		month: end - 30 * D,
		ytd: Date.UTC(endDate.getUTCFullYear(), 0, 1) / 1000,
		'1y': end - 365 * D,
		'3y': end - 3 * 365 * D,
		'5y': end - 5 * 365 * D,
		all: -Infinity
	}[range];
	const step = { day: 900, week: 3600, month: 4 * 3600, ytd: D, '1y': D, '3y': 7 * D, '5y': 7 * D, all: D }[range];

	const out: { time: number; open: number; high: number; low: number; close: number }[] = [];
	for (const p of pts) {
		const before = eq;
		eq += p.pnl;
		if (p.t < start) continue;
		const time = Math.floor(p.t / step) * step;
		let k = out[out.length - 1];
		if (!k || k.time !== time) out.push((k = { time, open: before, high: before, low: before, close: before }));
		k.close = eq;
		k.high = Math.max(k.high, eq);
		k.low = Math.min(k.low, eq);
	}
	const r2 = (v: number) => Math.round(v * 100) / 100;
	return out.map((k) => ({ time: k.time, open: r2(k.open), high: r2(k.high), low: r2(k.low), close: r2(k.close) }));
}

export type ClosedPosition = {
	id: number;
	strategy: string;
	symbol: string;
	side: 'long' | 'short';
	lot: number;
	openTime: string;
	closeTime: string;
	openPrice: number;
	closePrice: number;
	pnl: number;
};

// The account's closed trades, newest first. Symbol, side and lot come from the order that opened it.
export function closedPositions(accountId: number): ClosedPosition[] {
	return db
		.prepare(
			`SELECT t.id, t.strategy_name AS strategy, c.symbol, c.action AS side, c.volume AS lot,
			        t.mt5_entry_time AS open_time, t.mt5_exit_time AS close_time,
			        t.mt5_entry_price AS open_price, t.mt5_exit_price AS close_price, t.mt5pnl AS pnl
			 FROM account_trades t
			 JOIN mt5_execution_commands c ON c.account_trade_id = t.id AND c.account_id = ? AND c.action IN ('long', 'short')
			 WHERE t.mt5pnl IS NOT NULL AND t.mt5_exit_time <> ''
			 GROUP BY t.id
			 ORDER BY t.mt5_exit_time DESC, t.id DESC`
		)
		.all(accountId)
		.map((r: Record<string, unknown>) => ({
			id: Number(r.id),
			strategy: String(r.strategy ?? ''),
			symbol: String(r.symbol ?? ''),
			side: r.side === 'short' ? 'short' : 'long',
			lot: Number(r.lot ?? 0),
			openTime: String(r.open_time ?? ''),
			closeTime: String(r.close_time ?? ''),
			openPrice: Number(r.open_price ?? 0),
			closePrice: Number(r.close_price ?? 0),
			pnl: Number(r.pnl ?? 0)
		}));
}

// Every book any account has run, newest name last; the edit page's Change dropdown lists them.
export function allBooks(): string[] {
	return db
		.prepare('SELECT DISTINCT book FROM mt5_account_strategies WHERE book IS NOT NULL ORDER BY book')
		.all()
		.map((r: Record<string, unknown>) => String(r.book));
}
