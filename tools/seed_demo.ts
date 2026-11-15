/** Local dashboard fixtures. Run: bun tools/seed_demo.ts */
import { DatabaseSync } from 'node:sqlite';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { authdb, hashPassword, verifyPassword } from '../web/src/lib/server/auth.ts';

const root = fileURLToPath(new URL('../', import.meta.url));
const appPath = join(root, 'live_trade/app.db');
const webPath = join(root, 'web/data/web.db');
const authPath = join(root, 'web/data/auth.db');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const quote = (s: string) => `'${s.replaceAll("'", "''")}'`;
authdb.close();
for (const path of [appPath, authPath, webPath]) {
  if (!existsSync(path)) continue;
  const copy = new DatabaseSync(path);
  try {
    copy.exec('PRAGMA busy_timeout=5000');
    copy.exec(`VACUUM INTO ${quote(`${path}.bak-demo-${stamp}`)}`);
  } finally { copy.close(); }
}
mkdirSync(dirname(appPath), { recursive: true });
const db = new DatabaseSync(appPath);
db.exec('PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON');
db.exec(`ATTACH DATABASE ${quote(authPath)} AS auth; ATTACH DATABASE ${quote(webPath)} AS prefs`);
const password = randomBytes(15).toString('base64url');
let createdUser = false;
const accountIds: number[] = [];
try {
  db.exec('BEGIN IMMEDIATE');
  // Reuse the backend schema rather than maintaining a second set of trading tables.
  const schema = readFileSync(join(root, 'live_trade/src/database/schema.rs'), 'utf8').match(/r#"([\s\S]*?)"#/);
  if (!schema) throw new Error('Rust database schema not found');
  db.exec(schema[1]);
  const strategyCols = db.prepare('PRAGMA table_info(mt5_account_strategies)').all() as {name: string}[];
  if (!strategyCols.some(c => c.name === 'book')) {
    db.exec("ALTER TABLE mt5_account_strategies ADD COLUMN book TEXT NOT NULL DEFAULT ''");
  }
  db.exec(`CREATE TABLE IF NOT EXISTS prefs.account_edits (
    account_id INTEGER PRIMARY KEY, name TEXT, book TEXT, risk REAL,
    removed TEXT NOT NULL DEFAULT '[]', added TEXT NOT NULL DEFAULT '[]'
  )`);
  if (!db.prepare('SELECT id FROM auth.users WHERE name=?').get('demo')) {
    const hash = hashPassword(password);
    if (!verifyPassword(password, hash)) throw new Error('Password verification failed');
    db.prepare('INSERT INTO auth.users(name,pass_hash,created_at) VALUES(?,?,?)').run('demo', hash, Date.now());
    createdUser = true;
  }
  const now = new Date();
  const sqlTime = (d: Date) => d.toISOString().slice(0, 19).replace('T', ' ');
  const created = sqlTime(now);
  for (const [index, initial] of [10000, 25000].entries()) {
    const login = `DUMMY-${index + 1}`;
    const found = db.prepare("SELECT id FROM mt5_accounts WHERE login=? AND server='LOCAL-DUMMY'").get(login) as {id: number} | undefined;
    if (found) { accountIds.push(Number(found.id)); continue; }
    const id = Number(db.prepare(`INSERT INTO mt5_accounts(name,login,password,server,created_at,account_type)
      VALUES(?,?,?,'LOCAL-DUMMY',?,'mt5')`).run(`Dummy Account ${index + 1}`, login, 'dummy-not-a-broker-password', created).lastInsertRowid);
    accountIds.push(id);
    const strategy = 'Demo EURUSD';
    const sid = Number(db.prepare(`INSERT INTO mt5_account_strategies(account_id,strategy,symbol,active,created_at,book)
      VALUES(?,?,'EURUSD',0,?,'demo')`).run(id, strategy, created).lastInsertRowid);
    db.prepare('INSERT INTO prefs.account_edits(account_id,name) VALUES(?,?)').run(id, `Dummy Account ${index + 1}`);
    let total = 0;
    let latestDayPnl = 0;
    // Twenty trading days, three closed trades a day; newest day is the last weekday.
    const days: Date[] = [];
    const cursor = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1));
    while (days.length < 20) {
      if (cursor.getUTCDay() !== 0 && cursor.getUTCDay() !== 6) days.unshift(new Date(cursor));
      cursor.setUTCDate(cursor.getUTCDate() - 1);
    }
    for (const [dayIndex, day] of days.entries()) {
      let dayPnl = 0;
      for (let j = 0; j < 3; j++) {
        const k = dayIndex * 3 + j;
        const side = k % 2 ? 'short' : 'long';
        const lot = index ? 0.2 : 0.1;
        const pips = [18, -12, 26, 9, -15, 32, -8, 21][(k + index) % 8];
        const open = Number((1.095 + k * 0.00007).toFixed(5));
        const close = Number((open + (side === 'long' ? 1 : -1) * pips * 0.0001).toFixed(5));
        const pnl = Math.round(pips * lot * 10 * 100) / 100;
        const entry = new Date(day); entry.setUTCHours(9 + j * 3, 15);
        const exit = new Date(entry.getTime() + (45 + k % 60) * 60000);
        const entryAt = sqlTime(entry), exitAt = sqlTime(exit);
        const tid = Number(db.prepare(`INSERT INTO account_trades(
          strategy_name,mt5pnl,bookmap_entry_time,bookmap_exit_time,bookmap_entry_price,bookmap_exit_price,
          mt5_entry_time,mt5_exit_time,mt5_entry_price,mt5_exit_price,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)`).run(strategy,pnl,entryAt.slice(0,16),exitAt.slice(0,16),open,close,entryAt,exitAt,open,close,entryAt).lastInsertRowid);
        db.prepare(`INSERT INTO mt5_execution_commands(account_id,strategy,action,symbol,volume,
          account_trade_id,account_strategy_id,position_key,status,ticket,created_at,completed_at)
          VALUES(?,?,?,'EURUSD',?,?,?,?,'completed',?,?,?)`).run(id,strategy,side,lot,tid,sid,`demo:${id}:${k}`,900000 + id * 1000 + k,entryAt,entryAt);
        db.prepare(`INSERT INTO mt5_execution_commands(account_id,strategy,action,symbol,volume,
          closed_account_trade_id,account_strategy_id,position_key,status,ticket,created_at,completed_at)
          VALUES(?,?,'close','EURUSD',?,?,?,?,'completed',?,?,?)`).run(id,strategy,lot,tid,sid,`demo:${id}:${k}`,900000 + id * 1000 + k,exitAt,exitAt);
        total += pnl; dayPnl += pnl;
      }
      latestDayPnl = dayPnl;
    }
    db.prepare(`INSERT INTO mt5_bridge_heartbeats(account_id,server,balance,equity,currency,detail,
      seen_at,initial_balance,day_start_balance) VALUES(?,'LOCAL-DUMMY',?,?,'USD',?,?,?,?)`)
      .run(id,initial + total,initial + total,'Synthetic dashboard fixture; no terminal connected',created,initial,initial + total - latestDayPnl);
  }
  db.exec('COMMIT');
  console.log('Dummy data ready. Accounts:', accountIds.join(', '));
  console.log(createdUser ? `Login: demo\nPassword: ${password}` : 'User demo already exists; existing password preserved.');
  console.log('Each new account has 60 closed trades, inactive strategies, and no pending commands.');
  console.log('SQLite backups use suffix:', `.bak-demo-${stamp}`);
} catch (error) {
  try { db.exec('ROLLBACK'); } catch {}
  throw error;
} finally { db.close(); }
