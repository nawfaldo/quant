use crate::backtest::Trade;
use serde::Serialize;

#[derive(Serialize)]
pub struct Environment {
    pub id: i64,
    pub name: String,
}

pub struct CreateEnvironment {
    pub name: String,
}

/// One environment's stored backtest rule, as the rules screen renders it.
///
/// A MATERIALISED VIEW OF `backtest::cost_model`, NOT AN INDEPENDENT RECORD.
/// The engine charges from compiled tables, so a rule the database could
/// disagree with is the defect the old editable `environment_rules` table had:
/// what the screen showed and what the backtest charged were unrelated numbers.
/// `sync_environment_cost_rules` rewrites these rows from the cost model on
/// every boot, and the read path re-syncs when it finds a registered
/// environment with none -- so the stored copy can only ever be the engine's
/// own figures.
#[derive(Serialize)]
pub struct EnvironmentCostRule {
    pub id: String,
    pub name: String,
    pub detail: String,
    pub markets: Vec<EnvironmentCostRuleMarket>,
}

/// One market's line of a stored rule. Field names match
/// `backtest::MarketCost`, because the same TypeScript type reads both.
#[derive(Serialize)]
pub struct EnvironmentCostRuleMarket {
    pub market: String,
    /// Entry charge in basis points of the fill price, slippage allowance
    /// included.
    pub spread_bp: f64,
    /// Smallest order the broker accepts, in MT5 lots.
    pub minimum_lots: f64,
}

#[derive(Serialize)]
pub struct EnvironmentMt5Account {
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

pub struct EnvironmentMt5AccountInput {
    pub server: String,
    pub login: String,
    pub password: String,
    pub account_type: String,
}

pub struct EnvironmentTrade {
    pub id: i64,
    pub environment_id: i64,
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
pub struct EnvironmentAccountTrade {
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
    pub environment_id: Option<i64>,
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
    pub environment_id: Option<i64>,
}

#[derive(Clone)]
pub struct LiveStrategyTarget {
    pub account_strategy_id: i64,
    pub account_id: i64,
    pub environment_id: i64,
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
    pub environment_id: Option<i64>,
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
