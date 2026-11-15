import { repo } from './paths.ts';
import { DatabaseSync } from 'node:sqlite';
import { argon2Sync, createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { mkdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Users and their sessions. Its own file (data/auth.db, gitignored, outside Vite's served
// paths), apart from the UI-preferences web.db. Passwords are stored only as argon2id hashes
// (PHC string, random 16-byte salt); session cookies are stored only as SHA-256 hashes, so a
// copy of this file gives neither a password nor a usable session.
const DIR = repo('web', 'data') + '/';
mkdirSync(DIR, { recursive: true });

export const authdb = new DatabaseSync(DIR + 'auth.db');
authdb.exec(`PRAGMA foreign_keys = ON`);
authdb.exec(`CREATE TABLE IF NOT EXISTS users (
	id INTEGER PRIMARY KEY,
	name TEXT NOT NULL UNIQUE COLLATE NOCASE,
	pass_hash TEXT NOT NULL,
	created_at INTEGER NOT NULL
)`);
authdb.exec(`CREATE TABLE IF NOT EXISTS sessions (
	token_hash TEXT PRIMARY KEY,
	user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	expires_at INTEGER NOT NULL
)`);

// argon2id, well above OWASP's minimum (19 MiB, t=2): 64 MiB, 3 passes, 4 lanes.
const ARGON = { memory: 65536, passes: 3, parallelism: 4, tagLength: 32 };

function argon(password: string, salt: Buffer, p = ARGON): Buffer {
	return argon2Sync('argon2id', { message: Buffer.from(password, 'utf8'), nonce: salt, ...p });
}

export function hashPassword(password: string): string {
	const salt = randomBytes(16);
	const tag = argon(password, salt);
	const b64 = (b: Buffer) => b.toString('base64').replace(/=+$/, '');
	return `$argon2id$v=19$m=${ARGON.memory},t=${ARGON.passes},p=${ARGON.parallelism}$${b64(salt)}$${b64(tag)}`;
}

export function verifyPassword(password: string, stored: string): boolean {
	const m = /^\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$/.exec(stored);
	if (!m) return false;
	const want = Buffer.from(m[5], 'base64');
	const got = argon(password, Buffer.from(m[4], 'base64'), {
		memory: Number(m[1]),
		passes: Number(m[2]),
		parallelism: Number(m[3]),
		tagLength: want.length
	});
	return got.length === want.length && timingSafeEqual(got, want);
}

export function createUser(name: string, password: string): number {
	const r = authdb
		.prepare('INSERT INTO users (name, pass_hash, created_at) VALUES (?, ?, ?)')
		.run(name, hashPassword(password), Date.now());
	return Number(r.lastInsertRowid);
}

export type User = { id: number; name: string };

// Checks a name + password; runs a full hash even for an unknown name so timing doesn't
// reveal which names exist. (Used by the login, which doesn't exist yet.)
const DUMMY = hashPassword(randomBytes(16).toString('hex'));
export function checkLogin(name: string, password: string): User | null {
	const row = authdb.prepare('SELECT id, name, pass_hash FROM users WHERE name = ?').get(name);
	const ok = verifyPassword(password, row ? String(row.pass_hash) : DUMMY);
	return row && ok ? { id: Number(row.id), name: String(row.name) } : null;
}

// A new password for a user, stored as a fresh argon2id hash like createUser.
export function setPassword(userId: number, password: string) {
	authdb.prepare('UPDATE users SET pass_hash = ? WHERE id = ?').run(hashPassword(password), userId);
}

// A new name for a user. False when another user already has it (names are unique, any case).
export function setName(userId: number, name: string): boolean {
	try {
		authdb.prepare('UPDATE users SET name = ? WHERE id = ?').run(name, userId);
		return true;
	} catch {
		return false;
	}
}

// Each user's own profile picture (a square webp made in the browser), in data/avatars/.
export const AVATAR_DIR = DIR + 'avatars/';
mkdirSync(AVATAR_DIR, { recursive: true });
export const avatarFile = (userId: number) => `${AVATAR_DIR}${userId}.webp`;
// The picture's URL with its save time, so a new picture is never served from cache;
// null when the user has none yet (the page then shows the default Arisu).
export function avatarUrl(userId: number): string | null {
	try {
		return `/api/user/avatar?v=${Math.round(statSync(avatarFile(userId)).mtimeMs)}`;
	} catch {
		return null;
	}
}

const tokenHash = (token: string) => createHash('sha256').update(token).digest('hex');
export const SESSION_COOKIE = 'session';
export const SESSION_DAYS = 30;

// A new 256-bit random session token; only its hash is stored.
export function createSession(userId: number): { token: string; expires: Date } {
	const token = randomBytes(32).toString('base64url');
	const expires = new Date(Date.now() + SESSION_DAYS * 86400_000);
	authdb
		.prepare('INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)')
		.run(tokenHash(token), userId, expires.getTime());
	return { token, expires };
}

export function sessionUser(token: string | undefined): User | null {
	if (!token || token.length > 100) return null;
	authdb.prepare('DELETE FROM sessions WHERE expires_at <= ?').run(Date.now());
	const row = authdb
		.prepare(
			`SELECT u.id, u.name FROM sessions s JOIN users u ON u.id = s.user_id
			 WHERE s.token_hash = ? AND s.expires_at > ?`
		)
		.get(tokenHash(token), Date.now());
	return row ? { id: Number(row.id), name: String(row.name) } : null;
}

export function deleteSession(token: string) {
	authdb.prepare('DELETE FROM sessions WHERE token_hash = ?').run(tokenHash(token));
}
