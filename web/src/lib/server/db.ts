import { repo } from './paths.ts';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

// The live server's database, located relative to this file so it does not
// depend on which folder the dev server was started from. Opened read-only:
// the Rust server owns all writes.
export const DB_PATH = repo('live_trade', 'app.db');

export const db = new DatabaseSync(DB_PATH, { readOnly: true });
