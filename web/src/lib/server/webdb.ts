import { DatabaseSync } from 'node:sqlite';
import { symbolTable } from '#lib/server/bars.ts';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// The web app's own database, for UI preferences. Separate from the live
// server's app.db, which the web app only ever reads.
const DIR = fileURLToPath(new URL('../../../data/', import.meta.url));
mkdirSync(DIR, { recursive: true });

export const webdb = new DatabaseSync(DIR + 'web.db');
webdb.exec(`CREATE TABLE IF NOT EXISTS account_prefs (
	account_id INTEGER PRIMARY KEY,
	blurred INTEGER NOT NULL DEFAULT 0
)`);

export function blurredAccounts(): Set<number> {
	const rows = webdb.prepare('SELECT account_id FROM account_prefs WHERE blurred = 1').all();
	return new Set(rows.map((r) => Number(r.account_id)));
}

export function setBlurred(accountId: number, blurred: boolean) {
	webdb
		.prepare(
			`INSERT INTO account_prefs (account_id, blurred) VALUES (?, ?)
			 ON CONFLICT(account_id) DO UPDATE SET blurred = excluded.blurred`
		)
		.run(accountId, blurred ? 1 : 0);
}

// The chart layout (index into the page's LAYOUTS) and each of the six chart
// slots' symbol and timeframe, so a refresh restores them.
webdb.exec(`CREATE TABLE IF NOT EXISTS chart_layout (
	id INTEGER PRIMARY KEY CHECK (id = 1),
	layout INTEGER NOT NULL DEFAULT 0
)`);
webdb.exec(`CREATE TABLE IF NOT EXISTS chart_slots (
	slot INTEGER PRIMARY KEY,
	symbol TEXT NOT NULL,
	minutes INTEGER NOT NULL
)`);

export type ChartSlot = { symbol: string; minutes: number };
export const CHART_SLOTS = 6;

export function chartLayout(): { layout: number; slots: ChartSlot[] } {
	const row = webdb.prepare('SELECT layout FROM chart_layout WHERE id = 1').get();
	const saved = new Map(
		webdb.prepare('SELECT slot, symbol, minutes FROM chart_slots').all().map((r) => [
			Number(r.slot),
			{ symbol: String(r.symbol), minutes: Number(r.minutes) }
		])
	);
	return {
		layout: row ? Number(row.layout) : 0,
		// A saved symbol whose table is gone (e.g. an Exness-only one) shows USTEC.
		slots: Array.from({ length: CHART_SLOTS }, (_, i) => {
			const slot = saved.get(i);
			return slot && symbolTable(slot.symbol) ? slot : { symbol: 'USTEC', minutes: slot?.minutes ?? 1 };
		})
	};
}

export function setChartLayout(layout: number) {
	webdb
		.prepare('INSERT INTO chart_layout (id, layout) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET layout = excluded.layout')
		.run(layout);
}

export function setChartSlot(slot: number, symbol: string, minutes: number) {
	webdb
		.prepare(
			`INSERT INTO chart_slots (slot, symbol, minutes) VALUES (?, ?, ?)
			 ON CONFLICT(slot) DO UPDATE SET symbol = excluded.symbol, minutes = excluded.minutes`
		)
		.run(slot, symbol, minutes);
}
