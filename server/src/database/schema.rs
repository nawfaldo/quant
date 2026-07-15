pub(super) const SCHEMA: &str = r#"
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE,
  is_mt5 INTEGER NOT NULL DEFAULT 0,
  server TEXT NOT NULL DEFAULT '', login TEXT NOT NULL DEFAULT '',
  password TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS environments_name_unique ON environments(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS environment_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  environment_id INTEGER NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
  rule_type TEXT NOT NULL CHECK(rule_type IN ('spread','slippage','commission')),
  value REAL NOT NULL CHECK(value >= 0), created_at TEXT NOT NULL DEFAULT '',
  UNIQUE(environment_id, rule_type)
);
CREATE INDEX IF NOT EXISTS idx_environment_rules_environment ON environment_rules(environment_id);
CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT '', run_at TEXT NOT NULL DEFAULT '',
  first_ts TEXT NOT NULL DEFAULT '', last_ts TEXT NOT NULL DEFAULT '',
  total_days INTEGER NOT NULL DEFAULT 0, initial_bal REAL NOT NULL DEFAULT 0,
  final_bal REAL NOT NULL DEFAULT 0, net_growth REAL NOT NULL DEFAULT 0,
  max_drawdown REAL NOT NULL DEFAULT 0, num_trades INTEGER NOT NULL DEFAULT 0,
  symbol TEXT NOT NULL DEFAULT '', avg_drawdown REAL NOT NULL DEFAULT 0,
  sharpe REAL NOT NULL DEFAULT 0, total_win REAL NOT NULL DEFAULT 0,
  total_loss REAL NOT NULL DEFAULT 0, win_rate REAL NOT NULL DEFAULT 0,
  win_count INTEGER NOT NULL DEFAULT 0, profit_factor REAL NOT NULL DEFAULT 0,
  expectancy REAL NOT NULL DEFAULT 0, max_lose_streak INTEGER NOT NULL DEFAULT 0,
  avg_size REAL NOT NULL DEFAULT 0, min_size REAL NOT NULL DEFAULT 0,
  max_size REAL NOT NULL DEFAULT 0, avg_weekly REAL NOT NULL DEFAULT 0,
  avg_monthly REAL NOT NULL DEFAULT 0, avg_weekly_pct REAL NOT NULL DEFAULT 0,
  avg_monthly_pct REAL NOT NULL DEFAULT 0, instrument TEXT NOT NULL DEFAULT '',
  max_drawdown_dollars REAL NOT NULL DEFAULT 0,
  max_drawdown_peak_date TEXT NOT NULL DEFAULT '',
  max_drawdown_trough_date TEXT NOT NULL DEFAULT '',
  avg_drawdown_dollars REAL NOT NULL DEFAULT 0,
  max_intraday_drawdown REAL NOT NULL DEFAULT 0,
  max_intraday_drawdown_dollars REAL NOT NULL DEFAULT 0,
  max_intraday_drawdown_date TEXT NOT NULL DEFAULT '',
  avg_intraday_drawdown REAL NOT NULL DEFAULT 0,
  avg_intraday_drawdown_dollars REAL NOT NULL DEFAULT 0,
  max_daily_loss REAL NOT NULL DEFAULT 0,
  max_daily_loss_date TEXT NOT NULL DEFAULT '',
  avg_daily_loss REAL NOT NULL DEFAULT 0,
  environment_id INTEGER REFERENCES environments(id)
);
CREATE INDEX IF NOT EXISTS idx_backtests_environment ON backtests(environment_id);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, backtest_id INTEGER NOT NULL,
  side TEXT NOT NULL DEFAULT 'long', entry_ts TEXT NOT NULL DEFAULT '',
  exit_ts TEXT NOT NULL DEFAULT '', entry_price REAL NOT NULL DEFAULT 0,
  exit_price REAL NOT NULL DEFAULT 0, entry_raw REAL NOT NULL DEFAULT 0,
  exit_raw REAL NOT NULL DEFAULT 0, pnl REAL NOT NULL DEFAULT 0,
  contracts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trades_bt ON trades(backtest_id);
CREATE TABLE IF NOT EXISTS fx_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, backtest_id INTEGER NOT NULL,
  side TEXT NOT NULL DEFAULT 'long', entry_ts TEXT NOT NULL DEFAULT '',
  exit_ts TEXT NOT NULL DEFAULT '', entry_price REAL NOT NULL DEFAULT 0,
  exit_price REAL NOT NULL DEFAULT 0, entry_raw REAL NOT NULL DEFAULT 0,
  exit_raw REAL NOT NULL DEFAULT 0, pnl REAL NOT NULL DEFAULT 0,
  contracts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fx_trades_bt ON fx_trades(backtest_id);
CREATE TABLE IF NOT EXISTS montecarlo (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL DEFAULT '',
  source_id INTEGER NOT NULL, initial_balance REAL NOT NULL DEFAULT 0,
  final_p5 REAL NOT NULL DEFAULT 0, final_p25 REAL NOT NULL DEFAULT 0,
  final_p50 REAL NOT NULL DEFAULT 0, final_p75 REAL NOT NULL DEFAULT 0,
  final_p95 REAL NOT NULL DEFAULT 0, p_profit REAL NOT NULL DEFAULT 0,
  p_ruin REAL NOT NULL DEFAULT 0, sims INTEGER NOT NULL DEFAULT 0,
  dd_p5 REAL NOT NULL DEFAULT 0, dd_p25 REAL NOT NULL DEFAULT 0,
  dd_p50 REAL NOT NULL DEFAULT 0, dd_p75 REAL NOT NULL DEFAULT 0,
  dd_p95 REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mc_src ON montecarlo(source_id);
CREATE TABLE IF NOT EXISTS montecarlo_paths (
  mc_id INTEGER NOT NULL, path_idx INTEGER NOT NULL,
  step INTEGER NOT NULL, equity REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mcp ON montecarlo_paths(mc_id,path_idx,step);
CREATE TABLE IF NOT EXISTS strategies (
  name TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mt5_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
  login TEXT NOT NULL DEFAULT '', password TEXT NOT NULL DEFAULT '',
  server TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mt5_account_strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
  strategy TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS live_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_name TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'long', contract REAL NOT NULL,
  zig_entry_price REAL NOT NULL DEFAULT 0, zig_close_price REAL NOT NULL DEFAULT 0,
  mt5_entry_price REAL NOT NULL DEFAULT 0,
  mt5_entry_price_spread REAL NOT NULL DEFAULT 0,
  mt5_close_price REAL NOT NULL DEFAULT 0,
  zig_open_time TEXT NOT NULL DEFAULT '', mt5_open_time TEXT NOT NULL DEFAULT '',
  zig_close_time TEXT NOT NULL DEFAULT '', mt5_close_time TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mt5_execution_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES mt5_accounts(id) ON DELETE CASCADE,
  strategy TEXT NOT NULL, action TEXT NOT NULL, symbol TEXT NOT NULL,
  volume REAL NOT NULL DEFAULT 0, trade_id INTEGER NOT NULL DEFAULT -1,
  closed_trade_id INTEGER NOT NULL DEFAULT -1,
  status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
  ticket INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT '',
  leased_at TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mt5_commands_account_status
  ON mt5_execution_commands(account_id,status,id);
CREATE TABLE IF NOT EXISTS mt5_bridge_heartbeats (
  account_id INTEGER PRIMARY KEY REFERENCES mt5_accounts(id) ON DELETE CASCADE,
  server TEXT NOT NULL DEFAULT '', balance REAL NOT NULL DEFAULT 0,
  equity REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mt5_bridge_positions (
  account_id INTEGER NOT NULL REFERENCES mt5_accounts(id) ON DELETE CASCADE,
  ticket INTEGER NOT NULL, position_type TEXT NOT NULL,
  symbol TEXT NOT NULL, volume REAL NOT NULL DEFAULT 0,
  profit REAL NOT NULL DEFAULT 0, open_price REAL NOT NULL DEFAULT 0,
  open_time INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(account_id,ticket)
);
"#;
