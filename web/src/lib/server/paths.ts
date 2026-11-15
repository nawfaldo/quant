import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// The repo root (the folder holding live_trade/ and web/), found by walking up from this
// file. Not a fixed '../../..': in the production build this code lives in
// web/build/server/chunks/, at a different depth than src/lib/server/.
function findRoot(): string {
	let dir = dirname(fileURLToPath(import.meta.url));
	for (let i = 0; i < 10; i++) {
		if (existsSync(join(dir, 'live_trade')) && existsSync(join(dir, 'web'))) return dir;
		dir = dirname(dir);
	}
	throw new Error('repo root (with live_trade/ and web/) not found above ' + fileURLToPath(import.meta.url));
}

export const REPO = findRoot();
/** A path inside the repo, e.g. repo('data', 'parquet'), with a trailing separator for folders. */
export const repo = (...parts: string[]) => join(REPO, ...parts);
