use super::{
    prepare::PreparedRun,
    request::RunRequest,
    tuning::report::{Drawdowns, montecarlo, report},
    types::{Action, Bar, Instrument, Side, Strategy, Trade},
};
use crate::{
    error::ApiError,
    sizing::{VolTarget, VolTargetConfig},
    strategies::idk::{night_drift::NightDrift, noise_momentum::NoiseMomentum},
};
use serde_json::Value;

#[cfg(test)]
use super::data::{format_day, parse_iso_days};

pub fn execute(prepared: &PreparedRun, request: &RunRequest) -> Result<RunResult, ApiError> {
    execute_with_sizing(prepared, request, None)
}

pub fn execute_tuned(prepared: &PreparedRun, request: &RunRequest) -> Result<RunResult, ApiError> {
    // Zig's current Night Drift implementation owns its calibrated,
    // equity-compounded sizing internally. Although the tuning API still
    // accepts the legacy sizing grid, those values do not override the
    // strategy's emitted quantity. Preserve that benchmark behavior here.
    let _ = request.position_sizing()?;
    execute_with_sizing(prepared, request, None)
}

fn execute_with_sizing(
    prepared: &PreparedRun,
    request: &RunRequest,
    sizing: Option<PositionSizing>,
) -> Result<RunResult, ApiError> {
    let mut engine = prepared.engine.clone();
    engine.sizing = sizing;
    let result = match request.strategy.as_str() {
        "Night Drift" => run_engine(&prepared.bars, NightDrift::default(), engine),
        "Noise Momentum" => run_engine(&prepared.bars, NoiseMomentum::default(), engine),
        _ => return Err(ApiError::BadRequest("unknown strategy".into())),
    };
    Ok(result)
}

#[derive(Clone)]
pub(crate) struct EngineConfig {
    pub(crate) initial: f64,
    pub(crate) instrument: Instrument,
    pub(crate) symbol: String,
    pub(crate) spread: f64,
    pub(crate) slippage: f64,
    pub(crate) commission: f64,
    pub(crate) start_day: i64,
    pub(crate) sizing: Option<PositionSizing>,
}

#[derive(Clone, Copy)]
pub(crate) struct PositionSizing {
    pub(crate) base_lot: f64,
    pub(crate) leverage: f64,
    pub(crate) volatility: Option<VolTargetConfig>,
}
struct Position {
    side: Side,
    entry: f64,
    raw: f64,
    pub(crate) ts: i64,
    quantity: f64,
}
pub struct RunResult {
    pub body: Value,
    pub trades: Vec<Trade>,
}

fn run_engine<S: Strategy>(bars: &[Bar], mut strategy: S, cfg: EngineConfig) -> RunResult {
    let mut trades = Vec::new();
    let mut position: Option<Position> = None;
    let mut equity = cfg.initial;
    let mut peak = equity;
    let mut peak_day = cfg.start_day;
    let mut max_dd = 0.0f64;
    let mut max_dd_dollars = 0.0f64;
    let mut max_dd_peak = cfg.start_day;
    let mut max_dd_trough = cfg.start_day;
    let mut dd_sum = 0.0;
    let mut dd_dollars_sum = 0.0;
    let mut dd_count = 0usize;
    let mut current_day = cfg.start_day;
    let mut day_peak = equity;
    let mut day_max = 0.0f64;
    let mut day_max_dollars = 0.0f64;
    let mut max_idd = 0.0f64;
    let mut max_idd_dollars = 0.0f64;
    let mut max_idd_day = cfg.start_day;
    let mut idd_sum = 0.0;
    let mut idd_dollars_sum = 0.0;
    let mut idd_days = 0usize;
    let mut sizing_day = None;
    let mut volatility_target = cfg
        .sizing
        .and_then(|sizing| sizing.volatility)
        .map(VolTarget::new);
    for (bar_index, bar) in bars.iter().enumerate() {
        let day = bar.ts.div_euclid(86400);
        let fill_timestamp = bars
            .get(bar_index + 1)
            .map(|next_bar| next_bar.ts)
            .unwrap_or(bar.ts);
        let day_changed = sizing_day != Some(day);
        sizing_day = Some(day);
        if let Some(target) = &mut volatility_target {
            target.on_bar(bar.close, day_changed);
        }
        let action = strategy.update(*bar, equity);
        if day < cfg.start_day {
            strategy.discard(action);
            continue;
        }
        let mut mtm = equity;
        if let Some(p) = &position {
            mtm = match action {
                Action::Close { price, fraction } if fraction >= 1.0 => {
                    let exit = fill(price, p.side == Side::Short, &cfg);
                    equity + net_pnl(p, exit, cfg.commission, cfg.instrument, &cfg.symbol)
                }
                _ => {
                    equity
                        + pnl(
                            p.side,
                            p.entry,
                            bar.close,
                            p.quantity,
                            cfg.instrument,
                            &cfg.symbol,
                        )
                        - cfg.commission * p.quantity
                }
            };
        }
        if mtm > peak {
            peak = mtm;
            peak_day = day;
        }
        let dd_dollars = peak - mtm;
        let dd = if peak > 0.0 {
            dd_dollars / peak * 100.0
        } else {
            0.0
        };
        if dd > max_dd {
            max_dd = dd;
            max_dd_dollars = dd_dollars;
            max_dd_peak = peak_day;
            max_dd_trough = day;
        }
        dd_sum += dd;
        dd_dollars_sum += dd_dollars;
        dd_count += 1;
        if day != current_day {
            idd_sum += day_max;
            idd_dollars_sum += day_max_dollars;
            idd_days += 1;
            current_day = day;
            day_peak = mtm;
            day_max = 0.0;
            day_max_dollars = 0.0;
        }
        if mtm > day_peak {
            day_peak = mtm;
        }
        let dollars = day_peak - mtm;
        let pct = if day_peak > 0.0 {
            dollars / day_peak * 100.0
        } else {
            0.0
        };
        if pct > day_max {
            day_max = pct;
            day_max_dollars = dollars;
        }
        if pct > max_idd {
            max_idd = pct;
            max_idd_dollars = dollars;
            max_idd_day = day;
        }
        match action {
            Action::Hold => {}
            Action::Enter {
                side,
                price,
                quantity,
            } => {
                if position.as_ref().is_some_and(|p| p.side == side) {
                    continue;
                }
                if let Some(old) = position.take() {
                    let exit = fill(price, old.side == Side::Short, &cfg);
                    let gain = net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
                    equity += gain;
                    trades.push(to_trade(old, fill_timestamp, exit, price, gain));
                }
                let raw_qty = cfg
                    .sizing
                    .map(|sizing| {
                        let volatility_multiplier = volatility_target
                            .as_ref()
                            .map(VolTarget::multiplier)
                            .unwrap_or(1.0);
                        sizing.base_lot * sizing.leverage * volatility_multiplier
                    })
                    .unwrap_or(quantity);
                let quantity = cfg.instrument.size(raw_qty);
                position = Some(Position {
                    side,
                    entry: fill(price, side == Side::Long, &cfg),
                    raw: price,
                    ts: fill_timestamp,
                    quantity,
                });
            }
            Action::Close { price, fraction } => {
                if let Some(mut old) = position.take() {
                    let close_qty = closed_size(old.quantity, fraction, cfg.instrument);
                    let exit = fill(price, old.side == Side::Short, &cfg);
                    let partial = Position {
                        side: old.side,
                        entry: old.entry,
                        raw: old.raw,
                        ts: old.ts,
                        quantity: close_qty,
                    };
                    let gain = net_pnl(&partial, exit, cfg.commission, cfg.instrument, &cfg.symbol);
                    equity += gain;
                    trades.push(to_trade(partial, fill_timestamp, exit, price, gain));
                    old.quantity -= close_qty;
                    if old.quantity > 1e-8 {
                        position = Some(old);
                    }
                }
            }
        }
    }
    if let (Some(old), Some(last)) = (position.take(), bars.last()) {
        let exit = fill(last.close, old.side == Side::Short, &cfg);
        let gain = net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
        equity += gain;
        trades.push(to_trade(old, last.ts, exit, last.close, gain));
    }
    idd_sum += day_max;
    idd_dollars_sum += day_max_dollars;
    idd_days += 1;
    let first = bars
        .iter()
        .find(|b| b.ts.div_euclid(86400) >= cfg.start_day)
        .map(|b| b.ts)
        .unwrap_or(bars[0].ts);
    let last = bars.last().unwrap().ts;
    let mut body = report(
        &trades,
        &cfg,
        first,
        last,
        equity,
        Drawdowns {
            max_dd,
            max_dd_dollars,
            max_dd_peak,
            max_dd_trough,
            avg_dd: dd_sum / dd_count.max(1) as f64,
            avg_dd_dollars: dd_dollars_sum / dd_count.max(1) as f64,
            max_idd,
            max_idd_dollars,
            max_idd_day,
            avg_idd: idd_sum / idd_days.max(1) as f64,
            avg_idd_dollars: idd_dollars_sum / idd_days.max(1) as f64,
        },
    );
    body["montecarlo"] = montecarlo(&trades, cfg.initial);
    RunResult { body, trades }
}

fn closed_size(open_quantity: f64, fraction: f64, instrument: Instrument) -> f64 {
    if fraction >= 1.0 {
        return open_quantity;
    }
    let requested = open_quantity * fraction.clamp(0.0, 1.0);
    match instrument {
        Instrument::Forex => requested,
        Instrument::Mini | Instrument::Micro => instrument.size(requested).min(open_quantity),
    }
}

fn fill(raw: f64, buying: bool, cfg: &EngineConfig) -> f64 {
    let cost = cfg.spread / 2.0 + cfg.slippage;
    if buying { raw + cost } else { raw - cost }
}
fn pnl(
    side: Side,
    entry: f64,
    exit: f64,
    quantity: f64,
    instrument: Instrument,
    symbol: &str,
) -> f64 {
    let movement = if side == Side::Long {
        exit - entry
    } else {
        entry - exit
    };
    movement * quantity * instrument.point_value(symbol)
}
fn net_pnl(
    pos: &Position,
    exit: f64,
    commission: f64,
    instrument: Instrument,
    symbol: &str,
) -> f64 {
    pnl(pos.side, pos.entry, exit, pos.quantity, instrument, symbol)
        - 2.0 * commission * pos.quantity
}
fn to_trade(
    pos: Position,
    exit_timestamp: i64,
    exit_price: f64,
    exit_raw: f64,
    gain: f64,
) -> Trade {
    Trade {
        side: pos.side,
        entry_timestamp: pos.ts,
        exit_timestamp,
        entry_price: pos.entry,
        exit_price,
        pnl: gain,
        quantity: pos.quantity,
        entry_raw: pos.raw,
        exit_raw,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn civil_dates_round_trip() {
        let d = parse_iso_days("2026-07-15").unwrap();
        assert_eq!(format_day(d), "2026-07-15");
    }
    #[test]
    fn fill_cost_is_adverse() {
        let c = EngineConfig {
            initial: 1.0,
            instrument: Instrument::Forex,
            symbol: "nq".into(),
            spread: 0.2,
            slippage: 0.1,
            commission: 0.0,
            start_day: 0,
            sizing: None,
        };
        assert_eq!(fill(100.0, true, &c), 100.2);
        assert_eq!(fill(100.0, false, &c), 99.8);
    }
}
