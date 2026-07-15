use crate::backtest::Trade;
use serde::Serialize;

#[derive(Serialize)]
pub struct Environment {
    pub id: i64,
    pub name: String,
    #[serde(rename = "isMt5")]
    pub is_mt5: bool,
    pub server: String,
    pub login: String,
}

pub struct CreateEnvironment {
    pub name: String,
    pub is_mt5: bool,
    pub server: String,
    pub login: String,
    pub password: String,
}

#[derive(Clone, Copy, Default)]
pub struct EnvironmentCosts {
    pub spread: f64,
    pub slippage: f64,
    pub commission: f64,
}

#[derive(Serialize)]
pub struct EnvironmentRule {
    pub id: i64,
    #[serde(rename = "type")]
    pub rule_type: String,
    pub value: f64,
}

#[derive(Serialize)]
pub struct MarchStrategy {
    pub name: String,
    pub active: bool,
}

#[derive(Serialize)]
pub struct LiveTrade {
    pub id: i64,
    pub strategy_name: String,
    pub side: String,
    pub contract: f64,
    pub zig_entry_price: f64,
    pub zig_close_price: f64,
    pub mt5_entry_price: f64,
    pub mt5_close_price: f64,
    pub zig_open_time: String,
    pub zig_close_time: String,
    pub mt5_open_time: String,
    pub mt5_close_time: String,
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
}

pub struct ExecutionTarget {
    pub account_id: i64,
    pub symbol: String,
}

pub struct Mt5Command {
    pub id: i64,
    pub action: String,
    pub symbol: String,
    pub volume: f64,
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
    pub zig_entry_price: f64,
    pub zig_entry_time: i64,
}

pub struct Mt5PositionInput {
    pub ticket: i64,
    pub position_type: String,
    pub symbol: String,
    pub volume: f64,
    pub profit: f64,
    pub open_price: f64,
    pub open_time: i64,
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
