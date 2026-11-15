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

// Desktop IDs own their chart layout and six chart slots.
webdb.exec(`PRAGMA foreign_keys = ON`);
webdb.exec(`CREATE TABLE IF NOT EXISTS ui_prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);
webdb.exec(`CREATE TABLE IF NOT EXISTS desktops (id INTEGER PRIMARY KEY AUTOINCREMENT)`);
const legacyLayout = webdb.prepare("PRAGMA table_info(chart_layout)").all();
webdb.exec('BEGIN IMMEDIATE');
try {
	if (!webdb.prepare('SELECT id FROM desktops LIMIT 1').get()) {
		const oldCount = Number(uiPref('desktop_count') ?? 1);
		const count = Number.isInteger(oldCount) && oldCount >= 1 && oldCount <= 4 ? oldCount : 1;
		for (let i = 0; i < count; i++) webdb.prepare('INSERT INTO desktops DEFAULT VALUES').run();
	}
	const firstId = Number(webdb.prepare('SELECT id FROM desktops ORDER BY id LIMIT 1').get()!.id);
	if (legacyLayout.length && !legacyLayout.some((c) => c.name === 'desktop_id')) {
		webdb.exec('ALTER TABLE chart_layout RENAME TO legacy_chart_layout');
		webdb.exec('ALTER TABLE chart_slots RENAME TO legacy_chart_slots');
	}
	webdb.exec(`CREATE TABLE IF NOT EXISTS chart_layout (
		desktop_id INTEGER PRIMARY KEY REFERENCES desktops(id) ON DELETE CASCADE,
		layout INTEGER NOT NULL DEFAULT 0
	)`);
	webdb.exec(`CREATE TABLE IF NOT EXISTS chart_slots (
		desktop_id INTEGER NOT NULL REFERENCES desktops(id) ON DELETE CASCADE,
		slot INTEGER NOT NULL,
		symbol TEXT NOT NULL,
		minutes INTEGER NOT NULL,
		PRIMARY KEY (desktop_id, slot)
	)`);
	if (legacyLayout.length && !legacyLayout.some((c) => c.name === 'desktop_id')) {
		webdb.prepare('INSERT INTO chart_layout (desktop_id, layout) SELECT ?, layout FROM legacy_chart_layout WHERE id = 1').run(firstId);
		webdb.prepare('INSERT INTO chart_slots (desktop_id, slot, symbol, minutes) SELECT ?, slot, symbol, minutes FROM legacy_chart_slots').run(firstId);
		webdb.exec('DROP TABLE legacy_chart_layout; DROP TABLE legacy_chart_slots');
	}
	webdb.prepare('INSERT OR IGNORE INTO chart_layout (desktop_id) SELECT id FROM desktops').run();
	webdb.exec('COMMIT');
} catch (e) {
	webdb.exec('ROLLBACK');
	throw e;
}

export type ChartSlot = { symbol: string; minutes: number };
export const CHART_SLOTS = 6;

export function chartLayout(desktopId: number): { layout: number; slots: ChartSlot[] } {
	const row = webdb.prepare('SELECT layout FROM chart_layout WHERE desktop_id = ?').get(desktopId);
	const saved = new Map(
		webdb.prepare('SELECT slot, symbol, minutes FROM chart_slots WHERE desktop_id = ?').all(desktopId).map((r) => [
			Number(r.slot),
			{ symbol: String(r.symbol), minutes: Number(r.minutes) }
		])
	);
	return {
		layout: row ? Number(row.layout) : 0,
		slots: Array.from({ length: CHART_SLOTS }, (_, i) => {
			const slot = saved.get(i);
			return slot && symbolTable(slot.symbol) ? slot : { symbol: 'USTEC', minutes: slot?.minutes ?? 1 };
		})
	};
}

export function setChartLayout(desktopId: number, layout: number) {
	webdb.prepare('INSERT INTO chart_layout (desktop_id, layout) VALUES (?, ?) ON CONFLICT(desktop_id) DO UPDATE SET layout = excluded.layout').run(desktopId, layout);
}

export function setChartSlot(desktopId: number, slot: number, symbol: string, minutes: number) {
	webdb.prepare(`INSERT INTO chart_slots (desktop_id, slot, symbol, minutes) VALUES (?, ?, ?, ?)
		ON CONFLICT(desktop_id, slot) DO UPDATE SET symbol = excluded.symbol, minutes = excluded.minutes`).run(desktopId, slot, symbol, minutes);
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

export function desktopState(): { desktops: { id: number }[]; activeDesktopId: number } {
	const desktops = webdb.prepare('SELECT id FROM desktops ORDER BY id').all().map((r) => ({ id: Number(r.id) }));
	const savedId = Number(uiPref('active_desktop_id'));
	return { desktops, activeDesktopId: desktops.some((d) => d.id === savedId) ? savedId : desktops[0].id };
}

export function selectDesktop(id: number): boolean {
	if (!Number.isInteger(id) || !webdb.prepare('SELECT id FROM desktops WHERE id = ?').get(id)) return false;
	setUiPref('active_desktop_id', String(id));
	return true;
}

export function addDesktop() {
	if (desktopState().desktops.length < 4) {
		webdb.exec('BEGIN IMMEDIATE');
		try {
			const id = Number(webdb.prepare('INSERT INTO desktops DEFAULT VALUES').run().lastInsertRowid);
			setChartLayout(id, 0);
			webdb.exec('COMMIT');
		} catch (e) { webdb.exec('ROLLBACK'); throw e; }
	}
	return desktopState();
}

export function deleteDesktop(id: number) {
	const state = desktopState();
	if (!Number.isInteger(id) || id === state.activeDesktopId || !state.desktops.some((d) => d.id === id)) return null;
	webdb.prepare('DELETE FROM desktops WHERE id = ?').run(id);
	return desktopState();
}

// Account filter belongs to the stable desktop ID, and is removed with it.
webdb.exec(`CREATE TABLE IF NOT EXISTS desktop_account_filters (
 desktop_id INTEGER PRIMARY KEY REFERENCES desktops(id) ON DELETE CASCADE,
 account_id INTEGER
)`);

// Upgrade existing single-account preferences without losing saved selections.
if (!webdb.prepare('PRAGMA table_info(desktop_account_filters)').all().some((c) => c.name === 'account_ids')) {
 webdb.exec("ALTER TABLE desktop_account_filters ADD COLUMN account_ids TEXT NOT NULL DEFAULT '[]'");
 webdb.exec("UPDATE desktop_account_filters SET account_ids = '[' || account_id || ']' WHERE account_id IS NOT NULL");
}

export function desktopAccountFilters(): Record<number, number[]> {
 return Object.fromEntries(webdb.prepare('SELECT desktop_id, account_ids FROM desktop_account_filters').all()
  .map((r) => [Number(r.desktop_id), JSON.parse(String(r.account_ids)) as number[]]));
}

export function setDesktopAccountFilter(desktopId: number, accountIds: number[]) {
 webdb.prepare(`INSERT INTO desktop_account_filters (desktop_id, account_ids) VALUES (?, ?)
  ON CONFLICT(desktop_id) DO UPDATE SET account_ids = excluded.account_ids`).run(desktopId, JSON.stringify(accountIds));
}

webdb.exec(`CREATE TABLE IF NOT EXISTS desktop_symbol_filters (
 desktop_id INTEGER PRIMARY KEY REFERENCES desktops(id) ON DELETE CASCADE,
 symbols TEXT NOT NULL DEFAULT '[]'
)`);
export function desktopSymbolFilters(): Record<number, string[]> {
 return Object.fromEntries(webdb.prepare('SELECT desktop_id, symbols FROM desktop_symbol_filters').all()
  .map((r) => [Number(r.desktop_id), JSON.parse(String(r.symbols))]));
}
export function setDesktopSymbolFilter(desktopId: number, symbols: string[]) {
 webdb.prepare(`INSERT INTO desktop_symbol_filters (desktop_id, symbols) VALUES (?, ?)
 ON CONFLICT(desktop_id) DO UPDATE SET symbols = excluded.symbols`).run(desktopId, JSON.stringify(symbols));
}

export type PositionSort = { key: 'account' | 'strategy' | 'symbol' | 'side' | 'openTime' | 'price' | 'lot' | 'pnl'; direction: 'asc' | 'desc' } | null;
webdb.exec(`CREATE TABLE IF NOT EXISTS desktop_position_sorts (
 desktop_id INTEGER PRIMARY KEY REFERENCES desktops(id) ON DELETE CASCADE,
 sorting TEXT NOT NULL DEFAULT 'null'
)`);
export function desktopPositionSorts(): Record<number, PositionSort> {
 return Object.fromEntries(webdb.prepare('SELECT desktop_id, sorting FROM desktop_position_sorts').all()
  .map((r) => [Number(r.desktop_id), JSON.parse(String(r.sorting))]));
}
export function setDesktopPositionSort(desktopId: number, sorting: PositionSort) {
 webdb.prepare(`INSERT INTO desktop_position_sorts (desktop_id, sorting) VALUES (?, ?)
 ON CONFLICT(desktop_id) DO UPDATE SET sorting = excluded.sorting`).run(desktopId, JSON.stringify(sorting));
}
