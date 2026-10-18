import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

// The live server's database, located relative to this file so it does not
// depend on which folder the dev server was started from. Opened read-only:
// the Rust server owns all writes.
const DB_PATH = fileURLToPath(new URL('../../../../live_trade/app.db', import.meta.url));

export const db = new DatabaseSync(DB_PATH, { readOnly: true });
