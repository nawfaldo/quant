import { asyncBufferFromFile, parquetMetadataAsync, parquetReadObjects } from 'hyparquet';
import { compressors } from 'hyparquet-compressors';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// The Parquet market-data store. A table is `<table>.parquet` (bulk history),
// a `<table>/` folder of daily shards written by a live collector, or both.
const STORE = fileURLToPath(new URL('../../../../data/parquet/', import.meta.url));

export type Bar = { time: number; open: number; high: number; low: number; close: number };

// Timestamps are stored as text in New York wall-clock time with a `Z` suffix.
// Keeping them as "UTC" makes the chart display New York time, which is the
// convention the rest of the store uses.
const toSeconds = (ts: string) => Date.parse(ts.slice(0, 19) + 'Z') / 1000;

const cache = new Map<string, { mtime: number; bars: Bar[] }>();

async function tailOfFile(path: string, n: number): Promise<Bar[]> {
	const mtime = statSync(path).mtimeMs;
	const key = `${path}:${n}`;
	const hit = cache.get(key);
	if (hit && hit.mtime === mtime) return hit.bars;

	const file = await asyncBufferFromFile(path);
	const metadata = await parquetMetadataAsync(file);
	const rows = Number(metadata.num_rows);
	const objs = await parquetReadObjects({
		file,
		metadata,
		compressors,
		columns: ['timestamp', 'open', 'high', 'low', 'close'],
		rowStart: Math.max(0, rows - n),
		rowEnd: rows
	});
	const bars = objs.map((r) => ({
		time: toSeconds(String(r.timestamp)),
		open: Number(r.open),
		high: Number(r.high),
		low: Number(r.low),
		close: Number(r.close)
	}));
	cache.set(key, { mtime, bars });
	return bars;
}

// The newest `n` bars of a table, oldest first, with shards overriding the
// bulk file where both hold the same minute.
export async function lastBars(table: string, n: number): Promise<Bar[]> {
	const byTime = new Map<number, Bar>();
	const add = (bars: Bar[]) => bars.forEach((b) => byTime.set(b.time, b));

	const bulk = join(STORE, `${table}.parquet`);
	if (existsSync(bulk)) add(await tailOfFile(bulk, n));

	const dir = join(STORE, table);
	if (existsSync(dir)) {
		const shards = readdirSync(dir).filter((f) => f.endsWith('.parquet')).sort();
		// Walk back from the newest shard until there are enough bars.
		const picked: string[] = [];
		let count = 0;
		for (let i = shards.length - 1; i >= 0 && count < n; i--) {
			picked.unshift(shards[i]);
			count += 1440;
		}
		for (const s of picked) add(await tailOfFile(join(dir, s), n));
	}

	return [...byTime.values()].sort((a, b) => a.time - b.time).slice(-n);
}

// Written by tools/chart_live_candle.py while a chart shows the symbol: the
// previous and the forming candle, from Dukascopy ticks or Binance.
const LIVE = fileURLToPath(new URL('../../../../data/live/', import.meta.url));
const LIVE_STALE_MS = 20_000;

function readLive(table: string): Bar[] {
	const path = join(LIVE, `${table}.live.json`);
	try {
		if (Date.now() - statSync(path).mtimeMs > LIVE_STALE_MS) return [];
		return JSON.parse(readFileSync(path, 'utf8')).bars as Bar[];
	} catch {
		return []; // script not running, or mid-replace
	}
}

// The previous and the forming candle, for streaming to the chart: the live
// script's while it is fresh, else the newest two bars the feed daemon wrote.
export async function liveTail(table: string): Promise<Bar[]> {
	const live = readLive(table);
	return live.length > 0 ? live : lastBars(table, 2);
}

// Timeframes the chart offers, in minutes.
export const TIMEFRAMES = [1, 5, 15, 30, 60, 240, 1440] as const;

// Start of the candle a time falls in. Times are New York wall clock, so a
// 1d candle runs midnight to midnight New York time.
export const bucket = (time: number, minutes: number) => Math.floor(time / (minutes * 60)) * minutes * 60;

// Merge 1-minute bars (oldest first) into `minutes`-long candles.
export function aggregate(bars: Bar[], minutes: number): Bar[] {
	if (minutes === 1) return bars;
	const out: Bar[] = [];
	for (const b of bars) {
		const t = bucket(b.time, minutes);
		const last = out.at(-1);
		if (last && last.time === t) {
			last.high = Math.max(last.high, b.high);
			last.low = Math.min(last.low, b.low);
			last.close = b.close;
		} else out.push({ ...b, time: t });
	}
	return out;
}

// The newest `n` candles of a timeframe: stored bars topped up with the live
// script's, then merged. Reads at most 400k minutes (about 15 months), so the
// longest timeframes may show fewer than `n`.
export async function chartBars(table: string, n: number, minutes = 1): Promise<Bar[]> {
	const need = Math.min(n * minutes, 400_000);
	const byTime = new Map<number, Bar>();
	for (const bars of [await lastBars(table, need), readLive(table)]) bars.forEach((b) => byTime.set(b.time, b));
	const minutesBars = [...byTime.values()].sort((a, b) => a.time - b.time).slice(-need);
	return aggregate(minutesBars, minutes).slice(-n);
}

// Chart symbols whose table is named differently: BTCUSD is Binance's btc_1m.
const TABLE_NAME: Record<string, string> = { BTCUSD: 'btc' };
// CME futures, neither Dukascopy nor Binance.
const EXCLUDED = new Set(['es', 'nq']);

// What the picker offers: currency, metal, commodity and crypto PAIRS, and the
// named index and oil CFDs. Allowed by SHAPE rather than by denying stocks, so
// a stock table imported later (aapl_1m, nvda_1m, ...) never shows up.
const ASSETS = 'AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD|XAU|XAG|XPT|XPD|XCU|XNG|BTC|ETH';
const QUOTES = 'AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD|BTC';
const PAIR = new RegExp(`^(${ASSETS})(${QUOTES})$`);
const MARKETS = new Set(['USTEC', 'US500', 'USOIL', 'UK100', 'UKOIL', 'DE40', 'FR40', 'JP225', 'HK50', 'AUS200', 'STOXX50', 'BTCUSD', 'ETHUSD']);

// Symbols with Dukascopy or Binance 1-minute data in the store (an unprefixed
// `<symbol>_1m` file or folder), upper case, sorted; markets only, no stocks.
export function listSymbols(): string[] {
	const byTable = Object.fromEntries(Object.entries(TABLE_NAME).map(([s, t]) => [t, s]));
	const names = new Set<string>();
	for (const entry of readdirSync(STORE)) {
		const m = /^([a-z0-9]+)_1m(.parquet)?$/.exec(entry);
		if (!m || EXCLUDED.has(m[1])) continue;
		const symbol = byTable[m[1]] ?? m[1].toUpperCase();
		if (MARKETS.has(symbol) || PAIR.test(symbol)) names.add(symbol);
	}
	return [...names].sort();
}

// The store table for a symbol, or undefined if there is none.
export function symbolTable(symbol: string): string | undefined {
	const upper = symbol.toUpperCase();
	return listSymbols().includes(upper) ? `${TABLE_NAME[upper] ?? upper.toLowerCase()}_1m` : undefined;
}
