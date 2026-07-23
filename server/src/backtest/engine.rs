use super::{
    drawdown::DrawdownTracker,
    prepare::PreparedRun,
    request::RunRequest,
    tuning::report::{montecarlo, report},
    types::{Action, Bar, Instrument, Side, Strategy, Trade},
};
use crate::{
    error::ApiError,
    sizing::{VolTarget, VolTargetConfig},
    strategies::{
        StrategyEnvironment,
        idk::{
            hourly_delta_reversal_es::HourlyDeltaReversalEs,
            hourly_delta_reversal_nq::HourlyDeltaReversalNq, night_drift::NightDrift,
            night_drift_2::NightDrift2, noise_momentum::NoiseMomentum,
            noise_momentum_2::NoiseMomentum2,
        },
    },
};
use serde_json::Value;

#[cfg(test)]
use super::data::{format_day, parse_iso_days};

pub fn execute(prepared: &PreparedRun, request: &RunRequest) -> Result<RunResult, ApiError> {
    let sizing = request.position_sizing()?;
    execute_with_sizing(prepared, request, sizing)
}

pub fn execute_tuned(prepared: &PreparedRun, request: &RunRequest) -> Result<RunResult, ApiError> {
    let sizing = request.position_sizing()?;
    execute_with_sizing(prepared, request, sizing)
}

fn execute_with_sizing(
    prepared: &PreparedRun,
    request: &RunRequest,
    sizing: Option<PositionSizing>,
) -> Result<RunResult, ApiError> {
    let mut engine = prepared.engine.clone();
    engine.sizing = sizing;
    let result = match (request.strategy_environment, request.strategy.as_str()) {
        (StrategyEnvironment::Idk, "Hourly Delta Reversal NQ") => {
            run_engine(&prepared.bars, HourlyDeltaReversalNq::default(), engine)
        }
        (StrategyEnvironment::Idk, "Hourly Delta Reversal ES") => {
            run_engine(&prepared.bars, HourlyDeltaReversalEs::default(), engine)
        }
        (StrategyEnvironment::Idk, "Night Drift") => {
            run_engine(&prepared.bars, NightDrift::default(), engine)
        }
        (StrategyEnvironment::Idk, "Night Drift 2") => {
            run_engine(&prepared.bars, NightDrift2::default(), engine)
        }
        (StrategyEnvironment::Idk, "Noise Momentum") => {
            request.validate_noise_momentum()?;
            let point_value = engine.instrument.point_value(&engine.symbol);
            let quantity_step = engine.instrument.quantity_step();
            run_engine(
                &prepared.bars,
                NoiseMomentum::new(point_value, quantity_step),
                engine,
            )
        }
        (StrategyEnvironment::Idk, "Noise Momentum 2") => {
            request.validate_noise_momentum()?;
            run_engine(
                &prepared.bars,
                NoiseMomentum2::new(engine.instrument.quantity_step()),
                engine,
            )
        }
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
    let max_drawdown_dollars = strategy.max_drawdown_dollars();
    let mut equity_peak = equity;
    let mut drawdowns = DrawdownTracker::new(equity, cfg.start_day);
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
        let mut action = strategy.update(*bar, equity);
        if day < cfg.start_day {
            strategy.discard(action);
            continue;
        }
        let flatten_after_bar = strategy.session_end_minute().is_some_and(|session_end| {
            let minute = bar.ts.rem_euclid(86_400) as usize / 60;
            minute < session_end
                && bars.get(bar_index + 1).is_none_or(|next| {
                    next.ts.div_euclid(86_400) != day
                        || next.ts.rem_euclid(86_400) as usize / 60 >= session_end
                })
        });
        let mut mtm = marked_equity(equity, position.as_ref(), action, *bar, &cfg);
        if let (Some(limit), Some(open)) = (max_drawdown_dollars, position.as_ref()) {
            let floor = equity_peak - limit;
            if mtm < floor {
                action = Action::Close {
                    price: exit_raw_for_equity(open, equity, floor, &cfg),
                    fraction: 1.0,
                };
                mtm = floor;
            }
        }
        equity_peak = equity_peak.max(mtm);
        drawdowns.observe(mtm, day);
        match action {
            Action::Hold => {}
            Action::Enter {
                side,
                price,
                quantity,
            } => {
                if !position.as_ref().is_some_and(|p| p.side == side) {
                    if let Some(old) = position.take() {
                        let exit = fill(price, old.side == Side::Short, &cfg);
                        let gain = net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
                        equity += gain;
                        trades.push(to_trade(old, fill_timestamp, exit, price, gain));
                    }
                    equity_peak = equity_peak.max(equity);
                    let sized_quantity = if let (Some(risk_fraction), Some(stop)) =
                        (strategy.entry_risk_fraction(), strategy.entry_stop_price())
                    {
                        risk_limited_quantity(
                            side,
                            price,
                            stop,
                            equity,
                            equity_peak,
                            risk_fraction,
                            max_drawdown_dollars,
                            &cfg,
                        )
                    } else {
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
                        Some(cfg.instrument.size(raw_qty))
                    };
                    if let Some(quantity) = sized_quantity {
                        position = Some(Position {
                            side,
                            entry: fill(price, side == Side::Long, &cfg),
                            raw: price,
                            ts: fill_timestamp,
                            quantity,
                        });
                    }
                }
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
        if flatten_after_bar && let Some(old) = position.take() {
            let mut raw_exit = bar.close;
            let mut exit = fill(raw_exit, old.side == Side::Short, &cfg);
            if let Some(limit) = max_drawdown_dollars {
                let floor = equity_peak - limit;
                let closing_equity =
                    equity + net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
                if closing_equity < floor {
                    raw_exit = exit_raw_for_equity(&old, equity, floor, &cfg);
                    exit = fill(raw_exit, old.side == Side::Short, &cfg);
                }
            }
            let gain = net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
            equity += gain;
            trades.push(to_trade(old, bar.ts, exit, raw_exit, gain));
            equity_peak = equity_peak.max(equity);
            drawdowns.observe(equity, day);
        }
    }
    if let (Some(old), Some(last)) = (position.take(), bars.last()) {
        let mut raw_exit = last.close;
        let mut exit = fill(raw_exit, old.side == Side::Short, &cfg);
        if let Some(limit) = max_drawdown_dollars {
            let floor = equity_peak - limit;
            let closing_equity =
                equity + net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
            if closing_equity < floor {
                raw_exit = exit_raw_for_equity(&old, equity, floor, &cfg);
                exit = fill(raw_exit, old.side == Side::Short, &cfg);
            }
        }
        let gain = net_pnl(&old, exit, cfg.commission, cfg.instrument, &cfg.symbol);
        equity += gain;
        trades.push(to_trade(old, last.ts, exit, raw_exit, gain));
        drawdowns.observe(equity, last.ts.div_euclid(86_400));
    }
    let first = bars
        .iter()
        .find(|b| b.ts.div_euclid(86400) >= cfg.start_day)
        .map(|b| b.ts)
        .unwrap_or(bars[0].ts);
    let last = bars.last().unwrap().ts;
    let mut body = report(&trades, &cfg, first, last, equity, drawdowns.finish());
    body["montecarlo"] = if let Some(limit) = strategy.monte_carlo_drawdown_ruin_dollars() {
        let pnls = trades.iter().map(|trade| trade.pnl).collect::<Vec<_>>();
        crate::backtest::monte_carlo::run_with_drawdown_ruin(&pnls, cfg.initial, limit)
    } else {
        montecarlo(&trades, cfg.initial)
    };
    RunResult { body, trades }
}

fn marked_equity(
    equity: f64,
    position: Option<&Position>,
    action: Action,
    bar: Bar,
    cfg: &EngineConfig,
) -> f64 {
    let Some(position) = position else {
        return equity;
    };
    match action {
        Action::Close { price, fraction } if fraction >= 1.0 => {
            let exit = fill(price, position.side == Side::Short, cfg);
            equity + net_pnl(position, exit, cfg.commission, cfg.instrument, &cfg.symbol)
        }
        _ => {
            equity
                + pnl(
                    position.side,
                    position.entry,
                    bar.close,
                    position.quantity,
                    cfg.instrument,
                    &cfg.symbol,
                )
                - cfg.commission * position.quantity
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn risk_limited_quantity(
    side: Side,
    raw_entry: f64,
    raw_stop: f64,
    equity: f64,
    equity_peak: f64,
    risk_fraction: f64,
    max_drawdown_dollars: Option<f64>,
    cfg: &EngineConfig,
) -> Option<f64> {
    let remaining_drawdown = max_drawdown_dollars
        .map(|limit| (limit - (equity_peak - equity)).max(0.0))
        .unwrap_or(f64::INFINITY);
    let budget = (equity.max(0.0) * risk_fraction).min(remaining_drawdown);
    if budget <= 0.0 {
        return None;
    }
    let unit = Position {
        side,
        entry: fill(raw_entry, side == Side::Long, cfg),
        raw: raw_entry,
        ts: 0,
        quantity: 1.0,
    };
    let stop = fill(raw_stop, side == Side::Short, cfg);
    let loss_per_unit = -net_pnl(&unit, stop, cfg.commission, cfg.instrument, &cfg.symbol);
    if !loss_per_unit.is_finite() || loss_per_unit <= 0.0 {
        return None;
    }
    cfg.instrument.risk_size(budget / loss_per_unit)
}

fn exit_raw_for_equity(
    position: &Position,
    equity: f64,
    target_equity: f64,
    cfg: &EngineConfig,
) -> f64 {
    let point_value = cfg.instrument.point_value(&cfg.symbol);
    let desired_pnl = target_equity - equity;
    let movement = (desired_pnl + 2.0 * cfg.commission * position.quantity)
        / (position.quantity * point_value);
    let desired_fill = if position.side == Side::Long {
        position.entry + movement
    } else {
        position.entry - movement
    };
    let cost = cfg.spread / 2.0 + cfg.slippage;
    if position.side == Side::Long {
        desired_fill + cost
    } else {
        desired_fill - cost
    }
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

    struct IntradayOnce {
        bars_seen: usize,
    }

    struct RiskSizedOnce {
        bars_seen: usize,
    }

    struct DrawdownLimitedOnce {
        bars_seen: usize,
    }

    impl Strategy for IntradayOnce {
        fn update(&mut self, bar: Bar, _equity: f64) -> Action {
            self.bars_seen += 1;
            if self.bars_seen == 1 {
                Action::Enter {
                    side: Side::Long,
                    price: bar.close,
                    quantity: 1.0,
                }
            } else {
                Action::Hold
            }
        }

        fn session_end_minute(&self) -> Option<usize> {
            Some(930)
        }
    }

    impl Strategy for RiskSizedOnce {
        fn update(&mut self, _bar: Bar, _equity: f64) -> Action {
            self.bars_seen += 1;
            if self.bars_seen == 1 {
                Action::Enter {
                    side: Side::Long,
                    price: 100.0,
                    quantity: 999.0,
                }
            } else {
                Action::Close {
                    price: 90.0,
                    fraction: 1.0,
                }
            }
        }

        fn entry_risk_fraction(&self) -> Option<f64> {
            Some(0.03)
        }

        fn entry_stop_price(&self) -> Option<f64> {
            Some(90.0)
        }
    }

    impl Strategy for DrawdownLimitedOnce {
        fn update(&mut self, _bar: Bar, _equity: f64) -> Action {
            self.bars_seen += 1;
            if self.bars_seen == 1 {
                Action::Enter {
                    side: Side::Long,
                    price: 100.0,
                    quantity: 10.0,
                }
            } else {
                Action::Hold
            }
        }

        fn max_drawdown_dollars(&self) -> Option<f64> {
            Some(300.0)
        }
    }

    fn bar(ts: i64, close: f64) -> Bar {
        Bar {
            ts,
            open: close,
            high: close,
            low: close,
            close,
            volume: 1.0,
            volume_delta: 0.0,
            depth_events: 0,
            vix: 0.0,
        }
    }

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

    #[test]
    fn intraday_strategy_closes_before_exact_session_boundary() {
        let bars = [
            bar(600 * 60, 100.0),
            bar(601 * 60, 110.0),
            bar(930 * 60, 50.0),
        ];
        let cfg = EngineConfig {
            initial: 100.0,
            instrument: Instrument::Forex,
            symbol: "nq".into(),
            spread: 0.0,
            slippage: 0.0,
            commission: 0.0,
            start_day: 0,
            sizing: None,
        };

        let result = run_engine(&bars, IntradayOnce { bars_seen: 0 }, cfg);

        assert_eq!(result.trades.len(), 1);
        assert_eq!(result.trades[0].entry_timestamp, 601 * 60);
        assert_eq!(result.trades[0].exit_timestamp, 601 * 60);
        assert_eq!(result.trades[0].exit_raw, 110.0);
        assert_eq!(result.trades[0].pnl, 10.0);
        assert_eq!(result.body["final_bal"], 110.0);
    }

    #[test]
    fn risk_sizing_includes_costs_and_never_rounds_above_budget() {
        let bars = [bar(600 * 60, 100.0), bar(601 * 60, 90.0)];
        let cfg = EngineConfig {
            initial: 5_000.0,
            instrument: Instrument::Forex,
            symbol: "nq".into(),
            spread: 0.5,
            slippage: 0.0,
            commission: 0.0,
            start_day: 0,
            sizing: None,
        };

        let result = run_engine(&bars, RiskSizedOnce { bars_seen: 0 }, cfg);

        assert_eq!(result.trades.len(), 1);
        assert!((result.trades[0].quantity - 14.28).abs() < 1e-9);
        assert!(result.trades[0].pnl.abs() <= 150.0);
        assert!((result.trades[0].pnl + 149.94).abs() < 1e-9);
    }

    #[test]
    fn strategy_drawdown_limit_forces_an_account_level_exit() {
        let bars = [bar(600 * 60, 100.0), bar(601 * 60, 50.0)];
        let cfg = EngineConfig {
            initial: 1_000.0,
            instrument: Instrument::Forex,
            symbol: "nq".into(),
            spread: 0.0,
            slippage: 0.0,
            commission: 0.0,
            start_day: 0,
            sizing: None,
        };

        let result = run_engine(&bars, DrawdownLimitedOnce { bars_seen: 0 }, cfg);

        assert_eq!(result.trades.len(), 1);
        assert_eq!(result.trades[0].exit_raw, 70.0);
        assert_eq!(result.trades[0].pnl, -300.0);
        assert_eq!(result.body["max_drawdown_dollars"], 300.0);
        assert_eq!(result.body["final_bal"], 700.0);
    }
}
