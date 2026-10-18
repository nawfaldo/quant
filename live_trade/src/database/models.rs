use crate::backtest::Trade;
use serde::Serialize;

#[derive(Serialize)]
/// An MT5 account with its latest bridge balance, as the accounts list shows it.
pub struct AccountOverview {
    pub id: i64,
    pub server: String,
    pub login: String,
    pub balance: Option<f64>,
    pub equity: Option<f64>,
    pub initial_balance: Option<f64>,
    pub all_time_change_percent: Option<f64>,
    pub day_change_percent: Option<f64>,
    pub active_strategies: i64,
    pub equity_chart_range: String,
    pub history_range: String,
    pub account_type: String,
}

pub struct NewMt5Account {
    pub server: String,
    pub login: String,
    pub password: String,
    pub account_type: String,
}

/// One row of the account trade ledger: the engine's entry and exit beside
/// MT5's fills.
pub struct AccountTrade {
    pub id: i64,
    pub strategy_name: String,
    pub mt5pnl: f64,
    pub bookmap_entry_time: String,
    pub bookmap_exit_time: String,
    pub bookmap_entry_price: f64,
    pub bookmap_exit_price: f64,
    pub mt5_entry_time: String,
    pub mt5_exit_time: String,
    pub mt5_entry_price: f64,
    pub mt5_exit_price: f64,
}

#[derive(Serialize)]
pub struct AccountTradeSummary {
    pub id: i64,
    pub strategy_name: String,
    pub pnl: f64,
    pub entry_time: String,
    pub exit_time: String,
    pub entry_price: f64,
    pub exit_price: f64,
}

#[derive(Serialize)]
pub struct MarchStrategy {
    pub name: String,
    pub active: bool,
}

/// One decision the live runtime made that is not a trade — a block, a refused
/// entry, a retried exit, a flatten. Kept durable because these are exactly the
/// events that explain a strategy's absence from the trade log.
#[derive(Serialize)]
pub struct LiveEvent {
    pub at: String,
    pub bar_time: String,
    pub account_strategy_id: i64,
    pub strategy: String,
    pub kind: String,
    pub position_key: String,
    pub detail: String,
}

#[derive(Serialize)]
pub struct LiveTrade {
    pub id: i64,
    pub account_id: Option<i64>,
    pub account_login: String,
    pub account_name: String,
    pub strategy_name: String,
    pub side: String,
    pub contract: f64,
    pub engine_entry_price: f64,
    pub engine_close_price: f64,
    pub mt5_entry_price: f64,
    pub mt5_close_price: f64,
    pub engine_open_time: String,
    pub engine_close_time: String,
    pub mt5_open_time: String,
    pub mt5_close_time: String,
    pub position_key: String,
    pub exit_reason: String,
    pub closed_at: String,
}

#[derive(Serialize)]
pub struct Mt5Account {
    pub id: i64,
    pub name: String,
    pub login: String,
    pub server: String,
}

pub struct Mt5AccountInput {
    pub name: String,
    pub login: String,
    pub server: String,
}

#[derive(Serialize)]
pub struct AccountStrategy {
    pub id: i64,
    pub strategy: String,
    pub symbol: String,
    pub active: bool,
    /// The sealed book this row belongs to, or empty for a row written before
    /// books were recorded. The account panel groups on it, so twenty sleeves
    /// read as one selection rather than twenty loose strategies.
    pub book: String,
}

pub struct ExecutionTarget {
    pub account_id: i64,
    pub symbol: String,
}

#[derive(Clone)]
pub struct LiveStrategyTarget {
    pub account_strategy_id: i64,
    pub account_id: i64,
    pub strategy: String,
    pub symbol: String,
    pub live_started_at: i64,
    pub equity: f64,
    pub connected: bool,
}

#[derive(Clone)]
pub struct StrategyPosition {
    pub position_key: String,
    pub side: String,
    pub remaining_volume: f64,
    pub ticket: i64,
    pub status: String,
}

pub struct Mt5Command {
    pub id: i64,
    pub action: String,
    pub symbol: String,
    pub volume: f64,
    pub target_ticket: i64,
    pub magic: i64,
    /// `created_at` as a Unix epoch. The bridge recovers a fill by scanning MT5
    /// deal history for the command's comment, and needs a lower bound so a
    /// stale deal carrying the same comment can never be mistaken for this one.
    pub created_at: i64,
}

#[derive(Serialize)]
pub struct Mt5AccountStatus {
    pub account_id: i64,
    pub login: String,
    pub status: String,
    pub detail: String,
    pub balance: Option<f64>,
    pub equity: Option<f64>,
    pub currency: Option<String>,
}

#[derive(Serialize)]
pub struct Mt5Position {
    pub account: String,
    pub account_name: String,
    pub ticket: i64,
    #[serde(rename = "type")]
    pub position_type: String,
    pub symbol: String,
    pub volume: f64,
    pub profit: f64,
    pub open_price: f64,
    pub strategy: String,
    pub engine_entry_price: f64,
    pub engine_entry_time: i64,
}

pub struct Mt5PositionInput {
    pub ticket: i64,
    pub position_type: String,
    pub symbol: String,
    pub volume: f64,
    pub profit: f64,
    pub open_price: f64,
    pub open_time: i64,
    pub magic: i64,
}

pub struct AccountStrategyInput {
    pub strategy: String,
    pub symbol: String,
}

pub struct CombineSource {
    pub id: i64,
    pub strategy: String,
    pub symbol: String,
    pub instrument: String,
    pub initial_balance: f64,
    pub trades: Vec<Trade>,
}
