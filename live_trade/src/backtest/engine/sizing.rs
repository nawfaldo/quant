//! Turning a strategy's request into a quantity the account can actually take.
//!
//! The engine sizes from a risk fraction and a stop distance, then clamps
//! against margin and against a strategy's own drawdown ceiling. Marking is here
//! too because a drawdown clamp has to see open risk, not just realised P&L.

use super::costs::*;
use super::*;
use crate::backtest::types::risk_lot_size;

pub(super) fn marked_equity(
    equity: f64,
    position: Option<&Position>,
    action: Action,
    bar: Bar,
) -> f64 {
    let Some(position) = position else {
        return equity;
    };
    match action {
        Action::Close { price, fraction } if fraction >= 1.0 => equity + net_pnl(position, price),
        Action::ClosePosition { id, price } if position.id == Some(id) => {
            equity + net_pnl(position, price)
        }
        _ => {
            let movement = if position.side == Side::Long {
                bar.close - position.entry
            } else {
                position.entry - bar.close
            };
            equity + movement * position.quantity * position.point_value
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn risk_limited_quantity(
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
    // SIZING, NOT FILLING. The distance is measured from the RAW entry with no
    // cost folded in: an entry charge subtracted from a risk distance would
    // shrink every position by the charge rather than bill it. The charge is
    // taken where it is incurred, on the real fill.
    let distance = match side {
        Side::Long => raw_entry - raw_stop,
        Side::Short => raw_stop - raw_entry,
    };
    let loss_per_unit = distance * market_point_value(&cfg.symbol);
    if !loss_per_unit.is_finite() || loss_per_unit <= 0.0 {
        return None;
    }
    risk_lot_size(budget / loss_per_unit)
}

pub(super) fn exit_raw_for_equity(position: &Position, equity: f64, target_equity: f64) -> f64 {
    let point_value = position.point_value;
    let desired_pnl = target_equity - equity;
    let movement = desired_pnl / (position.quantity * point_value);
    // The exit is charged nothing: the whole round trip was taken at entry, so
    // inverting the P&L is the whole inversion.
    if position.side == Side::Long {
        position.entry + movement
    } else {
        position.entry - movement
    }
}

pub(super) fn closed_size(open_quantity: f64, fraction: f64) -> f64 {
    if fraction >= 1.0 {
        return open_quantity;
    }
    open_quantity * fraction.clamp(0.0, 1.0)
}
