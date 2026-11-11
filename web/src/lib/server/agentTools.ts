// Read-only tools the agent (Qwen on llama-server) can call to answer questions about the project:
// live account data from app.db, the repo's files, and the research notes. Nothing here writes.
import { execFile } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { db } from './db.ts';
import { activeAccounts, closedPositions } from './accounts.ts';
import { openPositions } from './positions.ts';
import { chartSymbol } from '#lib/symbols.ts';
import { strategyName, strategyWindow, titleCase } from '#lib/strategyLabel.ts';

const run = promisify(execFile);

export const REPO = resolve(fileURLToPath(new URL('../../../../', import.meta.url)));
/** Research notes (one finding per file, MEMORY.md is the index). */
export const NOTES = join(process.env.USERPROFILE ?? '', '.claude', 'projects', 'C--Users-JawirGaming66-quant', 'memory');

/** Each tool result is cut to this so a few calls fit in the 64k context. */
const MAX_CHARS = 12_000;
const clip = (s: string) => (s.length > MAX_CHARS ? s.slice(0, MAX_CHARS) + `\n…(cut, ${s.length - MAX_CHARS} more chars)` : s);
const out = (v: unknown) => clip(typeof v === 'string' ? v : JSON.stringify(v));

type Args = Record<string, any>;
type Ctx = { login: string | null };

function accountId(login: unknown, ctx: Ctx): number {
	const l = String(login ?? ctx.login ?? '');
	const r = db.prepare('SELECT id FROM mt5_accounts WHERE login = ?').get(l) as { id?: number } | undefined;
	if (r?.id == null) throw new Error(`no account with login ${l || '(none given)'}`);
	return Number(r.id);
}

function inRepo(path: unknown): string {
	const p = resolve(REPO, String(path ?? '.'));
	if (p !== REPO && !p.startsWith(REPO + sep)) throw new Error('path must be inside the repo');
	return p;
}

const loginParam = { login: { type: 'string', description: 'MT5 login. Defaults to the account open on screen.' } };

export const TOOLS = [
	{
		name: 'list_accounts',
		description: 'All live MT5 accounts with balance, equity, starting balance, strategy book, sleeve count, Sharpe and VaR.',
		parameters: { type: 'object', properties: {} },
		run: () => activeAccounts().map(({ candles, blurred, strategies, ...a }) => a)
	},
	{
		name: 'account_strategies',
		description:
			"The account's strategy book as the Strategy tab shows it: book name, risk dial, active sleeve count, and every sleeve with its id, " +
			'symbol, display name (SYMBOL:Family), session window, timeframe and when it went live. Pass all=true to include inactive sleeves.',
		parameters: { type: 'object', properties: { ...loginParam, all: { type: 'boolean', description: 'Include inactive sleeves too.' } } },
		run: (a: Args, ctx: Ctx) => {
			const id = accountId(a.login, ctx);
			const acct = activeAccounts().find((x) => x.id === id);
			const rows = db
				.prepare('SELECT strategy, symbol, book, active, live_started_at FROM mt5_account_strategies WHERE account_id = ? ORDER BY active DESC, symbol, strategy')
				.all(id) as { strategy: string; symbol: string; book: string; active: number; live_started_at: number | null }[];
			return {
				book: acct?.book ?? null,
				risk: acct?.risk ?? null,
				active: rows.filter((r) => r.active).length,
				sleeves: rows
					.filter((r) => a.all || r.active)
					.map((r) => ({
						strategy: r.strategy,
						symbol: r.symbol,
						name: `${chartSymbol(r.symbol)}:${titleCase(strategyName(r.strategy, r.symbol))}`,
						window: titleCase(strategyWindow(r.strategy) || 'all day'),
						timeframe: r.strategy.split('-')[0].match(/_(\d+[mh])$/)?.[1] ?? null,
						active: !!r.active,
						live_since: r.live_started_at ? new Date(r.live_started_at * 1000).toISOString().slice(0, 10) : null
					}))
			};
		}
	},
	{
		name: 'open_positions',
		description: 'Positions open right now (all accounts, or one login): strategy, symbol, side, lot, entry price, floating P&L.',
		parameters: { type: 'object', properties: loginParam },
		run: (a: Args) => openPositions().filter((p) => !a.login || p.account === String(a.login))
	},
	{
		name: 'closed_trades',
		description: 'Closed trades of an account, newest first. Times are the broker server clock.',
		parameters: {
			type: 'object',
			properties: {
				...loginParam,
				limit: { type: 'integer', description: 'Max rows (default 30, max 200).' },
				symbol: { type: 'string', description: 'Only this broker symbol, e.g. XAUUSD.' },
				strategy: { type: 'string', description: 'Only strategies whose id contains this text.' },
				since: { type: 'string', description: 'Only trades closed on/after this date, YYYY-MM-DD.' }
			}
		},
		run: (a: Args, ctx: Ctx) =>
			closedPositions(accountId(a.login, ctx))
				.filter((t) => !a.symbol || t.symbol.toLowerCase() === String(a.symbol).toLowerCase())
				.filter((t) => !a.strategy || t.strategy.toLowerCase().includes(String(a.strategy).toLowerCase()))
				.filter((t) => !a.since || t.closeTime.slice(0, 10).replace(/\./g, '-') >= String(a.since))
				.slice(0, Math.min(Number(a.limit) || 30, 200))
	},
	{
		name: 'pnl_breakdown',
		description: 'Realised P&L of an account summed by strategy, symbol, day or month: total, trade count, wins.',
		parameters: {
			type: 'object',
			properties: {
				...loginParam,
				by: { type: 'string', enum: ['strategy', 'symbol', 'day', 'month'] },
				since: { type: 'string', description: 'Only trades closed on/after this date, YYYY-MM-DD.' }
			},
			required: ['by']
		},
		run: (a: Args, ctx: Ctx) => {
			const groups = new Map<string, { pnl: number; trades: number; wins: number }>();
			for (const t of closedPositions(accountId(a.login, ctx))) {
				const day = t.closeTime.slice(0, 10).replace(/\./g, '-');
				if (a.since && day < String(a.since)) continue;
				const k = a.by === 'symbol' ? t.symbol : a.by === 'day' ? day : a.by === 'month' ? day.slice(0, 7) : t.strategy;
				const g = groups.get(k) ?? { pnl: 0, trades: 0, wins: 0 };
				g.pnl += t.pnl;
				g.trades++;
				if (t.pnl > 0) g.wins++;
				groups.set(k, g);
			}
			return [...groups].map(([key, g]) => ({ key, pnl: Math.round(g.pnl * 100) / 100, trades: g.trades, wins: g.wins }))
				.sort((x, y) => (a.by === 'day' || a.by === 'month' ? y.key.localeCompare(x.key) : y.pnl - x.pnl));
		}
	},
	{
		name: 'sql',
		description:
			'Run one read-only SQLite SELECT on the live database (live_trade/app.db). Tables: mt5_accounts, ' +
			'mt5_account_strategies, mt5_bridge_heartbeats, mt5_bridge_positions, mt5_execution_commands, ' +
			'mt5_strategy_positions, account_trades, live_trades, live_events, backtests, montecarlo, strategies. ' +
			'Use `PRAGMA table_info(name)` style questions via the sql tool to see columns. At most 100 rows come back.',
		parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
		run: (a: Args) => {
			const q = String(a.query ?? '').trim().replace(/;\s*$/, '');
			if (!/^(select|with|pragma\s+table_info)\b/i.test(q) || q.includes(';')) throw new Error('only a single SELECT / WITH / PRAGMA table_info');
			if (/password/i.test(q) || /select\s+\*[\s\S]*mt5_accounts/i.test(q)) throw new Error('account passwords are off limits');
			return db.prepare(q).all().slice(0, 100);
		}
	},
	{
		name: 'search_code',
		description: 'Search the repo (code, configs, research .md results) for a regex, like grep. Returns file:line: text.',
		parameters: {
			type: 'object',
			properties: {
				pattern: { type: 'string', description: 'Case-insensitive regex.' },
				path: { type: 'string', description: 'Folder or file to search in, relative to the repo root, e.g. live_trade/src.' }
			},
			required: ['pattern']
		},
		run: async (a: Args) => {
			const where = relative(REPO, inRepo(a.path)) || '.';
			try {
				const { stdout } = await run('git', ['grep', '-n', '-I', '-i', '-E', '--untracked', '--max-count=5', '-e', String(a.pattern), '--', where], {
					cwd: REPO,
					maxBuffer: 8 << 20
				});
				const lines = stdout.split('\n').filter(Boolean).map((l) => (l.length > 240 ? l.slice(0, 240) + '…' : l));
				return lines.slice(0, 80).join('\n') + (lines.length > 80 ? `\n…${lines.length - 80} more matches; narrow the path or pattern` : '');
			} catch (e: any) {
				if (e.code === 1) return 'no matches';
				throw e;
			}
		}
	},
	{
		name: 'list_dir',
		description: 'List a folder of the repo (relative to the repo root).',
		parameters: { type: 'object', properties: { path: { type: 'string' } } },
		run: (a: Args) => {
			const dir = inRepo(a.path);
			return readdirSync(dir, { withFileTypes: true })
				.filter((d) => !['node_modules', '.git', 'target', '.svelte-kit'].includes(d.name))
				.map((d) => (d.isDirectory() ? d.name + '/' : `${d.name} (${statSync(join(dir, d.name)).size} B)`))
				.join('\n');
		}
	},
	{
		name: 'read_file',
		description: 'Read a text file of the repo, with line numbers. Use offset/limit for long files.',
		parameters: {
			type: 'object',
			properties: {
				path: { type: 'string', description: 'Relative to the repo root.' },
				offset: { type: 'integer', description: 'First line, 1-based (default 1).' },
				limit: { type: 'integer', description: 'Lines to read (default 200, max 400).' }
			},
			required: ['path']
		},
		run: (a: Args) => {
			const p = inRepo(a.path);
			if (/\.(db|parquet|exe|dll|png|jpg|zip|pyc)$/i.test(p) || /\.env/i.test(p)) throw new Error('not a readable text file');
			const lines = readFileSync(p, 'utf8').split('\n');
			const from = Math.max(1, Number(a.offset) || 1);
			const n = Math.min(Number(a.limit) || 200, 400);
			return lines.slice(from - 1, from - 1 + n).map((l, i) => `${from + i}\t${l}`).join('\n') +
				(from - 1 + n < lines.length ? `\n…(${lines.length} lines total)` : '');
		}
	},
	{
		name: 'search_notes',
		description:
			'Search the research notes: one finding per note (what worked, what failed and why, live setup, broker rules). ' +
			'Returns matching note names with their one-line summary. Read one with read_note.',
		parameters: { type: 'object', properties: { query: { type: 'string', description: 'Words or regex, case-insensitive.' } }, required: ['query'] },
		run: (a: Args) => {
			const words = String(a.query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
			const hits = readdirSync(NOTES)
				.filter((f) => f.endsWith('.md') && f !== 'MEMORY.md')
				.map((f) => {
					const text = readFileSync(join(NOTES, f), 'utf8');
					const lower = (f + ' ' + text).toLowerCase();
					const score = words.reduce((s, w) => s + (lower.includes(w) ? 1 : 0), 0);
					const desc = text.match(/^description:\s*"?(.*?)"?\s*$/m)?.[1] ?? '';
					return { note: f.replace(/\.md$/, ''), desc, score };
				})
				.filter((h) => h.score > 0)
				.sort((x, y) => y.score - x.score)
				.slice(0, 15);
			return hits.length ? hits.map((h) => `${h.note}: ${h.desc}`).join('\n') : 'no notes match';
		}
	},
	{
		name: 'read_note',
		description: 'Read one research note by name (from search_notes).',
		parameters: { type: 'object', properties: { name: { type: 'string' } }, required: ['name'] },
		run: (a: Args) => {
			const name = String(a.name ?? '').replace(/\.md$/, '');
			if (!/^[\w.-]+$/.test(name)) throw new Error('bad note name');
			return readFileSync(join(NOTES, name + '.md'), 'utf8');
		}
	}
] as const;

/** The tool list in OpenAI format, for llama-server's /v1/chat/completions. */
export const TOOL_SPECS = TOOLS.map((t) => ({
	type: 'function' as const,
	function: { name: t.name, description: t.description, parameters: t.parameters }
}));

/** Run one tool call; errors come back as text so the model can correct itself. */
export async function callTool(name: string, rawArgs: string, ctx: Ctx): Promise<string> {
	const tool = TOOLS.find((t) => t.name === name);
	if (!tool) return `error: unknown tool ${name}`;
	let args: Args = {};
	try {
		args = rawArgs?.trim() ? JSON.parse(rawArgs) : {};
	} catch {
		return 'error: arguments were not valid JSON';
	}
	try {
		return out(await (tool.run as (a: Args, c: Ctx) => unknown)(args, ctx));
	} catch (e) {
		return `error: ${e instanceof Error ? e.message : String(e)}`;
	}
}

/** Short status for the chat while a tool runs. */
export const TOOL_LABELS: Record<string, string> = {
	list_accounts: 'Checking accounts…',
	account_strategies: 'Looking at strategies…',
	open_positions: 'Checking open positions…',
	closed_trades: 'Reading trade history…',
	pnl_breakdown: 'Adding up P&L…',
	sql: 'Querying the database…',
	search_code: 'Searching the code…',
	list_dir: 'Browsing files…',
	read_file: 'Reading a file…',
	search_notes: 'Searching notes…',
	read_note: 'Reading a note…'
};
