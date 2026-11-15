import { repo } from './paths.ts';
import { DatabaseSync } from 'node:sqlite';
import { symbolTable } from '#lib/server/bars.ts';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// The web app's own database, for UI preferences. Separate from the live
// server's app.db, which the web app only ever reads.
const DIR = repo('web', 'data') + '/';
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

// The edit page's saved changes per account (display name, book, risk, deleted sleeves). Only the
// web app reads these; the live runtime does not.
webdb.exec(`CREATE TABLE IF NOT EXISTS account_edits (
	account_id INTEGER PRIMARY KEY,
	name TEXT,
	book TEXT,
	risk REAL,
	removed TEXT NOT NULL DEFAULT '[]'
)`);
try {
	webdb.exec("ALTER TABLE account_edits ADD COLUMN added TEXT NOT NULL DEFAULT '[]'");
} catch {
	// already there
}

export type AccountEdits = { name: string | null; book: string | null; risk: number | null; removed: string[]; added: string[] };

export function accountEdits(accountId: number): AccountEdits {
	const r = webdb.prepare('SELECT name, book, risk, removed, added FROM account_edits WHERE account_id = ?').get(accountId);
	if (!r) return { name: null, book: null, risk: null, removed: [], added: [] };
	return {
		name: r.name == null ? null : String(r.name),
		book: r.book == null ? null : String(r.book),
		risk: r.risk == null ? null : Number(r.risk),
		removed: JSON.parse(String(r.removed ?? '[]')),
		added: JSON.parse(String(r.added ?? '[]'))
	};
}

export function setAccountEdits(accountId: number, e: AccountEdits) {
	webdb
		.prepare(
			`INSERT INTO account_edits (account_id, name, book, risk, removed, added) VALUES (?, ?, ?, ?, ?, ?)
			 ON CONFLICT(account_id) DO UPDATE SET name = excluded.name, book = excluded.book, risk = excluded.risk, removed = excluded.removed, added = excluded.added`
		)
		.run(accountId, e.name, e.book, e.risk, JSON.stringify(e.removed), JSON.stringify(e.added));
}

// Strategy ids Deactivate switched off, per account, so Activate turns back on
// only those (not strategies that were already off before).
webdb.exec(`CREATE TABLE IF NOT EXISTS deactivated_strategies (
	account_id INTEGER NOT NULL,
	strategy_id INTEGER NOT NULL,
	PRIMARY KEY (account_id, strategy_id)
)`);

export function deactivatedStrategies(accountId: number): number[] {
	return webdb
		.prepare('SELECT strategy_id FROM deactivated_strategies WHERE account_id = ?')
		.all(accountId)
		.map((r) => Number(r.strategy_id));
}

export function setDeactivatedStrategies(accountId: number, ids: number[]) {
	webdb.prepare('DELETE FROM deactivated_strategies WHERE account_id = ?').run(accountId);
	const ins = webdb.prepare('INSERT INTO deactivated_strategies (account_id, strategy_id) VALUES (?, ?)');
	for (const id of ids) ins.run(accountId, id);
}

// Small global UI switches (e.g. whether the agent iPhone is shown), key -> value.
webdb.exec(`CREATE TABLE IF NOT EXISTS ui_prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);

export function uiPref(key: string): string | null {
	const row = webdb.prepare('SELECT value FROM ui_prefs WHERE key = ?').get(key);
	return row ? String(row.value) : null;
}

export function setUiPref(key: string, value: string) {
	webdb.prepare('INSERT INTO ui_prefs (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(key, value);
}
