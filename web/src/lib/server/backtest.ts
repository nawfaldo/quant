import { repo } from './paths.ts';
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Realised (closed-trade) backtest of a live book, from the canon replay the
// research side writes: `py -m sandbox.research._fundednext_parity canon`
// -> sandbox/results/fundednext_canon_trades.json (seal, risk, $6,000 start,
// every settled trade per window). Only the FundedNext book has one today.
const RESULTS = pathToFileURL(repo('sandbox', 'results') + '/');
const FILES: Record<string, string> = { fundednext: 'fundednext_canon_trades.json', the5ers: 'the5ers_canon_trades.json' };

type Trade = { s: string; e: number; x: number; pnl: number };
type RawWindow = { from: number; to: number; summary: { final: number; return_pct: number; max_dd_pct: number; mtm_dd_pct: number; trades: number }; trades: Trade[] };
type Raw = { seal: string; risk_scale: number; initial_balance: number; members: string[]; windows: Record<string, RawWindow> };

export type BacktestWindow = {
	label: string;
	from: number;
	to: number;
	initial: number;
	final: number;
	returnPct: number;
	mtmDdPct: number;
	sharpe: number;
	profitFactor: number;
	trades: number;
	bars: { time: number; open: number; high: number; low: number; close: number }[];
};
export type Backtest = { seal: string; risk: number; sleeves: number; correlation: number | null; diversification: number | null; windows: BacktestWindow[] };

const cache = new Map<string, { mtime: number; value: Backtest }>();

export function bookBacktest(book: string | null): Backtest | null {
	const key = book?.toLowerCase().split('_')[0] ?? '';
	const file = FILES[key];
	if (!file) return null;
	const path = fileURLToPath(new URL(file, RESULTS));
	let mtime: number;
	try {
		mtime = statSync(path).mtimeMs;
	} catch {
		return null;
	}
	const hit = cache.get(path);
	if (hit && hit.mtime === mtime) return hit.value;

	const raw = JSON.parse(readFileSync(path, 'utf8')) as Raw;
	const pick = (name: string, label: string) => {
		const w = raw.windows[name];
		return w ? summarise(label, w, raw.initial_balance) : null;
	};
	const value: Backtest = {
		seal: raw.seal,
		risk: raw.risk_scale,
		sleeves: raw.members.length,
		...mix(raw),
		windows: [pick('2024-2025 IS', 'In-Sample'), pick('2026 OOS', 'Out-of-Sample')].filter((w) => w != null)
	};
	cache.set(path, { mtime, value });
	return value;
}

const WEEK = 7 * 86400;
// Weeks start Monday: the epoch was a Thursday.
const weekOf = (t: number) => Math.floor((t - 4 * 86400) / WEEK) * WEEK + 4 * 86400;

function summarise(label: string, w: RawWindow, initial: number): BacktestWindow {
	const trades = [...w.trades].sort((a, b) => a.x - b.x);
	let eq = initial;
	const bars: BacktestWindow['bars'] = [];
	let gross = 0;
	let loss = 0;
	// Daily realised returns on the closed balance, for an annualised (x sqrt 252) Sharpe.
	const daily = new Map<number, { start: number; pnl: number }>();
	for (const t of trades) {
		const before = eq;
		eq += t.pnl;
		if (t.pnl > 0) gross += t.pnl;
		else loss -= t.pnl;
		const d = Math.floor(t.x / 86400);
		const day = daily.get(d) ?? { start: before, pnl: 0 };
		day.pnl += t.pnl;
		daily.set(d, day);
		const time = weekOf(t.x);
		let k = bars[bars.length - 1];
		if (!k || k.time !== time) bars.push((k = { time, open: before, high: before, low: before, close: before }));
		k.close = eq;
		k.high = Math.max(k.high, eq);
		k.low = Math.min(k.low, eq);
	}
	const rets = [...daily.values()].map((d) => d.pnl / d.start);
	const mean = rets.reduce((a, b) => a + b, 0) / (rets.length || 1);
	const sd = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(rets.length - 1, 1));
	const r2 = (v: number) => Math.round(v * 100) / 100;
	return {
		label,
		from: w.from,
		to: w.to,
		initial,
		final: w.summary.final,
		returnPct: w.summary.return_pct,
		mtmDdPct: w.summary.mtm_dd_pct,
		sharpe: sd > 0 ? (mean / sd) * Math.sqrt(252) : 0,
		profitFactor: loss > 0 ? gross / loss : 0,
		trades: w.summary.trades,
		bars: bars.map((k) => ({ time: k.time, open: r2(k.open), high: r2(k.high), low: r2(k.low), close: r2(k.close) }))
	};
}


// Monte Carlo of the realised book: each window's daily returns (closed P&L over
// that morning's balance, 0 on days with no exits) resampled in 14-day blocks
// with replacement and chained in SEQUENCE, so a repeated block lands at a new
// point of the path instead of on top of itself. Drawdown is on the closed balance.
const PATHS = 10_000;
const BLOCK = 14;
const PCTS = [5, 50, 75, 95] as const;
export type Percentiles = Record<(typeof PCTS)[number], number>;
export type MonteCarloWindow = { label: string; from: number; to: number; ret: Percentiles; dd: Percentiles };
export type MonteCarlo = { paths: number; block: number; windows: MonteCarloWindow[] };

const mcCache = new Map<string, MonteCarlo>();

export function bookMonteCarlo(book: string | null): MonteCarlo | null {
	const key = book?.toLowerCase().split('_')[0] ?? '';
	const file = FILES[key];
	if (!file) return null;
	const path = fileURLToPath(new URL(file, RESULTS));
	let mtime: number;
	try {
		mtime = statSync(path).mtimeMs;
	} catch {
		return null;
	}
	const ck = path + mtime;
	const hit = mcCache.get(ck);
	if (hit) return hit;
	const raw = JSON.parse(readFileSync(path, 'utf8')) as Raw;
	const win = (name: string, label: string) => {
		const w = raw.windows[name];
		return w ? simulate(label, w, raw.initial_balance) : null;
	};
	const value: MonteCarlo = {
		paths: PATHS,
		block: BLOCK,
		windows: [win('2024-2025 IS', 'In-Sample'), win('2026 OOS', 'Out-of-Sample')].filter((w) => w != null)
	};
	mcCache.set(ck, value);
	return value;
}

function dailyReturns(w: RawWindow, initial: number): number[] {
	const first = Math.floor(w.from / 86400);
	const days = Math.max(1, Math.ceil(w.to / 86400) - first);
	const pnl = new Float64Array(days);
	for (const t of w.trades) {
		const d = Math.floor(t.x / 86400) - first;
		if (d >= 0 && d < days) pnl[d] += t.pnl;
	}
	const out: number[] = [];
	let bal = initial;
	for (const p of pnl) {
		out.push(p / bal);
		bal += p;
	}
	return out;
}

// Small seeded generator so the page shows the same numbers on every load.
function rng(seed: number) {
	return () => {
		seed = (seed + 0x6d2b79f5) | 0;
		let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

function simulate(label: string, w: RawWindow, initial: number): MonteCarloWindow {
	const r = dailyReturns(w, initial);
	const n = r.length;
	const rand = rng(12345);
	const rets = new Float64Array(PATHS);
	const dds = new Float64Array(PATHS);
	for (let p = 0; p < PATHS; p++) {
		let eq = 1;
		let peak = 1;
		let dd = 0;
		for (let filled = 0; filled < n; ) {
			const start = Math.floor(rand() * Math.max(1, n - BLOCK + 1));
			for (let k = 0; k < BLOCK && filled < n; k++, filled++) {
				eq *= 1 + r[Math.min(start + k, n - 1)];
				if (eq > peak) peak = eq;
				else dd = Math.max(dd, 1 - eq / peak);
			}
		}
		rets[p] = (eq - 1) * 100;
		dds[p] = dd * 100;
	}
	rets.sort();
	dds.sort();
	const pick = (a: Float64Array) => Object.fromEntries(PCTS.map((q) => [q, a[Math.min(PATHS - 1, Math.floor((q / 100) * PATHS))]])) as Percentiles;
	return { label, from: w.from, to: w.to, ret: pick(rets), dd: pick(dds) };
}

// How independent the sleeves are, over the full 2024-2026 replay. Each sleeve's
// daily return is its closed P&L over the book's balance that morning (0 on days
// it closed nothing). correlation = mean pairwise Pearson correlation;
// diversification = sum of sleeve volatilities / the book's volatility (1 = no benefit).
function mix(raw: Raw): { correlation: number | null; diversification: number | null } {
	const w = raw.windows['2024-2026'];
	if (!w) return { correlation: null, diversification: null };
	const first = Math.floor(w.from / 86400);
	const days = Math.max(1, Math.ceil(w.to / 86400) - first);
	const idx = new Map(raw.members.map((m, i) => [m, i]));
	const pnl = raw.members.map(() => new Float64Array(days));
	const book = new Float64Array(days);
	for (const t of w.trades) {
		const d = Math.floor(t.x / 86400) - first;
		const i = idx.get(t.s);
		if (d < 0 || d >= days || i == null) continue;
		pnl[i][d] += t.pnl;
		book[d] += t.pnl;
	}
	// Trading days only (any sleeve closed something), each scaled by that morning's balance.
	const live: number[] = [];
	const start = new Float64Array(days);
	let bal = raw.initial_balance;
	for (let d = 0; d < days; d++) {
		start[d] = bal;
		bal += book[d];
		if (book[d] !== 0) live.push(d);
	}
	const series = pnl.map((p) => live.map((d) => p[d] / start[d]));
	const total = live.map((d) => book[d] / start[d]);
	const sd = (x: number[]) => {
		const m = x.reduce((a, b) => a + b, 0) / x.length;
		return { m, s: Math.sqrt(x.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(x.length - 1, 1)) };
	};
	const stats = series.map(sd);
	let sum = 0;
	let pairs = 0;
	for (let i = 0; i < series.length; i++)
		for (let j = i + 1; j < series.length; j++) {
			if (!stats[i].s || !stats[j].s) continue;
			let c = 0;
			for (let k = 0; k < live.length; k++) c += (series[i][k] - stats[i].m) * (series[j][k] - stats[j].m);
			sum += c / Math.max(live.length - 1, 1) / (stats[i].s * stats[j].s);
			pairs++;
		}
	const bookSd = sd(total).s;
	return {
		correlation: pairs ? sum / pairs : null,
		diversification: bookSd ? stats.reduce((a, x) => a + x.s, 0) / bookSd : null
	};
}

// Per-sleeve out-of-sample stats from the same canon replay: return and closed-trade drawdown as % of the
// book's starting balance (each sleeve alone, so the drawdowns don't add up to the book's), and profit factor.
export type SleeveStats = { ret: number; dd: number; pf: number | null };
const sleeveCache = new Map<string, { mtime: number; value: Record<string, SleeveStats> }>();

// fn_audjpy_efficiency_30m-barlb -> audjpy:efficiency@30m-barlb (the replay's member key)
export const sleeveKey = (strategy: string) => strategy.replace(/^(?:fn|t5)_([a-z0-9]+)_(.+)_(\d+[mhd])(?:-(.+))?$/, (_, s, n, tf, w) => `${s}:${n}@${tf}${w ? '-' + w : ''}`);

export function bookSleeveStats(book: string | null): Record<string, SleeveStats> {
	const file = FILES[book?.toLowerCase().split('_')[0] ?? ''];
	if (!file) return {};
	const path = fileURLToPath(new URL(file, RESULTS));
	let mtime: number;
	try {
		mtime = statSync(path).mtimeMs;
	} catch {
		return {};
	}
	const hit = sleeveCache.get(path);
	if (hit && hit.mtime === mtime) return hit.value;
	const raw = JSON.parse(readFileSync(path, 'utf8')) as Raw;
	const w = raw.windows['2026 OOS'];
	const value: Record<string, SleeveStats> = {};
	if (w) {
		const by = new Map<string, Trade[]>();
		for (const t of w.trades) (by.get(t.s) ?? by.set(t.s, []).get(t.s)!).push(t);
		for (const [s, ts] of by) {
			ts.sort((a, b) => a.x - b.x);
			let cum = 0, peak = 0, dd = 0, win = 0, loss = 0;
			for (const t of ts) {
				cum += t.pnl;
				peak = Math.max(peak, cum);
				dd = Math.max(dd, peak - cum);
				if (t.pnl > 0) win += t.pnl; else loss -= t.pnl;
			}
			value[s] = { ret: (cum / raw.initial_balance) * 100, dd: (dd / raw.initial_balance) * 100, pf: loss > 0 ? win / loss : null };
		}
	}
	sleeveCache.set(path, { mtime, value });
	return value;
}

// The edit page's Run: the same canon replay with the deleted sleeves' trades dropped and every
// P&L scaled by risk / the replay's risk. An approximation, not a re-run: sizing in the replay
// compounded on the full book's balance, min-lot sleeves don't scale linearly, and drawdown is
// on the closed balance (the replay's mark-to-market drawdown can't be rebuilt from trades).
export function bookBacktestVariant(book: string | null, removed: string[], risk: number | null): (Backtest & { montecarlo: MonteCarlo | null }) | null {
	const file = FILES[book?.toLowerCase().split('_')[0] ?? ''];
	if (!file) return null;
	let raw: Raw;
	try {
		raw = JSON.parse(readFileSync(fileURLToPath(new URL(file, RESULTS)), 'utf8')) as Raw;
	} catch {
		return null;
	}
	const drop = new Set(removed.map(sleeveKey));
	const scale = risk != null && raw.risk_scale > 0 ? risk / raw.risk_scale : 1;
	for (const w of Object.values(raw.windows)) {
		const trades = w.trades.filter((t) => !drop.has(t.s)).map((t) => ({ ...t, pnl: t.pnl * scale }));
		trades.sort((a, b) => a.x - b.x);
		let eq = raw.initial_balance, peak = eq, dd = 0;
		for (const t of trades) {
			eq += t.pnl;
			peak = Math.max(peak, eq);
			dd = Math.max(dd, (peak - eq) / peak);
		}
		w.trades = trades;
		w.summary = { final: eq, return_pct: ((eq - raw.initial_balance) / raw.initial_balance) * 100, max_dd_pct: dd * 100, mtm_dd_pct: dd * 100, trades: trades.length };
	}
	raw.members = raw.members.filter((m) => !drop.has(m));
	const pick = (name: string, label: string) => (raw.windows[name] ? summarise(label, raw.windows[name], raw.initial_balance) : null);
	const win = (name: string, label: string) => (raw.windows[name] ? simulate(label, raw.windows[name], raw.initial_balance) : null);
	return {
		seal: raw.seal,
		risk: risk ?? raw.risk_scale,
		sleeves: raw.members.length,
		...mix(raw),
		windows: [pick('2024-2025 IS', 'In-Sample'), pick('2026 OOS', 'Out-of-Sample')].filter((w) => w != null),
		montecarlo: {
			paths: PATHS,
			block: BLOCK,
			windows: [win('2024-2025 IS', 'In-Sample'), win('2026 OOS', 'Out-of-Sample')].filter((w) => w != null)
		}
	};
}

// Strategies that can be added to a book: every sleeve the live runtime has compiled in
// (live_trade/src/strategies/<broker>/<date>/sleeves/*.rs), with its 2026 OOS stats from its
// book's canon replay where one exists (FundedNext today; others show none).
import { readdirSync } from 'node:fs';
const STRATEGIES = pathToFileURL(repo('live_trade', 'src', 'strategies') + '/');
type PoolEntry = { strategy: string; symbol: string; oos: SleeveStats | null };
let poolCache: { at: number; value: PoolEntry[] } | null = null;

export function strategyPool(): PoolEntry[] {
	if (poolCache && Date.now() - poolCache.at < 60_000) return poolCache.value;
	const root = fileURLToPath(STRATEGIES);
	const value: PoolEntry[] = [];
	const stats: Record<string, Record<string, SleeveStats>> = {};
	for (const broker of readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory())) {
		for (const date of readdirSync(root + broker.name, { withFileTypes: true }).filter((d) => d.isDirectory())) {
			const dir = `${root}${broker.name}/${date.name}/sleeves/`;
			let files: string[];
			try {
				files = readdirSync(dir).filter((f) => f.endsWith('.rs') && f !== 'mod.rs');
			} catch {
				continue;
			}
			stats[broker.name] ??= bookSleeveStats(broker.name);
			for (const f of files) {
				const src = readFileSync(dir + f, 'utf8');
				const id = src.match(/\bid: "([^"]+)"/)?.[1];
				const market = src.match(/\bmarket: "([^"]+)"/)?.[1];
				const key = src.match(/\bpython_key: "([^"]+)"/)?.[1];
				if (!id || !market) continue;
				value.push({ strategy: id, symbol: market.toUpperCase(), oos: (key && (stats[broker.name][key] ?? survivorStats(broker.name, key))) || null });
			}
		}
	}
	value.sort((a, b) => a.symbol.localeCompare(b.symbol) || a.strategy.localeCompare(b.strategy));
	poolCache = { at: Date.now(), value };
	return value;
}

// Books without a canon replay (Exness): the sleeve's standalone OOS from the broker's survivor
// pool, matched on symbol, family and timeframe (no @tf = 30m).
const survivorIdx = new Map<string, Map<string, SleeveStats>>();
function survivorStats(broker: string, key: string): SleeveStats | null {
	let idx = survivorIdx.get(broker);
	if (!idx) {
		idx = new Map();
		try {
			const text = readFileSync(fileURLToPath(new URL(`${broker}/SURVIVORS.json`, RESULTS)), 'utf8').replace(/\bNaN\b/g, 'null');
			type S = { symbol: string; family: string; timeframe: string; oos_return_pct: number | null; oos_max_dd_pct: number | null; oos_pf: number | null };
			for (const s of (JSON.parse(text).survivors ?? []) as S[]) {
				const k = `${s.symbol}:${s.family}@${s.timeframe}`;
				if (!idx.has(k) && s.oos_return_pct != null) idx.set(k, { ret: s.oos_return_pct, dd: s.oos_max_dd_pct ?? 0, pf: s.oos_pf ?? null });
			}
		} catch {
			// no pool for this broker
		}
		survivorIdx.set(broker, idx);
	}
	const m = key.match(/^([^:]+):([^@-]+)(?:@(\d+[mhd]))?/);
	if (!m) return null;
	const tf = m[3] ?? '30m';
	return idx.get(`${m[1]}:${m[2]}@${tf === '60m' ? '1h' : tf}`) ?? idx.get(`${m[1]}:${m[2]}@${tf}`) ?? null;
}
