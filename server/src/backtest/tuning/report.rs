use super::super::{
    data::{format_day, format_ts},
    engine::EngineConfig,
    types::Trade,
};
use serde_json::{Value, json};

pub(crate) struct Drawdowns {
    pub(crate) max_dd: f64,
    pub(crate) max_dd_dollars: f64,
    pub(crate) max_dd_peak: i64,
    pub(crate) max_dd_trough: i64,
    pub(crate) avg_dd: f64,
    pub(crate) avg_dd_dollars: f64,
    pub(crate) max_idd: f64,
    pub(crate) max_idd_dollars: f64,
    pub(crate) max_idd_day: i64,
    pub(crate) avg_idd: f64,
    pub(crate) avg_idd_dollars: f64,
}
pub(crate) fn report(
    trades: &[Trade],
    cfg: &EngineConfig,
    first: i64,
    last: i64,
    final_balance: f64,
    dd: Drawdowns,
) -> Value {
    let total_win: f64 = trades.iter().filter(|t| t.pnl >= 0.0).map(|t| t.pnl).sum();
    let total_loss: f64 = trades.iter().filter(|t| t.pnl < 0.0).map(|t| t.pnl).sum();
    let wins = trades.iter().filter(|t| t.pnl >= 0.0).count();
    let losses = trades.len() - wins;
    let win_rate = if trades.is_empty() {
        0.0
    } else {
        wins as f64 / trades.len() as f64 * 100.0
    };
    let avg_win = if wins > 0 {
        total_win / wins as f64
    } else {
        0.0
    };
    let avg_loss = if losses > 0 {
        total_loss / losses as f64
    } else {
        0.0
    };
    let expectancy = win_rate / 100.0 * avg_win + (1.0 - win_rate / 100.0) * avg_loss;
    let mut streak = 0;
    let mut max_streak = 0;
    for t in trades {
        if t.pnl < 0.0 {
            streak += 1;
            max_streak = max_streak.max(streak)
        } else {
            streak = 0
        }
    }
    let quantities: Vec<f64> = trades.iter().map(|t| t.quantity).collect();
    let avg_size = if quantities.is_empty() {
        0.0
    } else {
        quantities.iter().sum::<f64>() / quantities.len() as f64
    };
    let min_size = quantities.iter().copied().reduce(f64::min).unwrap_or(0.0);
    let max_size = quantities.iter().copied().reduce(f64::max).unwrap_or(0.0);
    let total_days = (last.div_euclid(86400) - first.div_euclid(86400)).max(0);
    let net = final_balance - cfg.initial;
    let avg_weekly = if total_days > 0 {
        net / (total_days as f64 / 7.0)
    } else {
        0.0
    };
    let avg_monthly = if total_days > 0 {
        net / (total_days as f64 / 30.4375)
    } else {
        0.0
    };
    let mut daily = std::collections::BTreeMap::new();
    for t in trades {
        *daily
            .entry(t.exit_timestamp.div_euclid(86400))
            .or_insert(0.0) += t.pnl;
    }
    let losing: Vec<_> = daily.iter().filter(|(_, v)| **v < 0.0).collect();
    let (max_daily_day, max_daily_loss) = losing
        .iter()
        .min_by(|a, b| a.1.total_cmp(b.1))
        .map(|(d, v)| (**d, **v))
        .unwrap_or((0, 0.0));
    let avg_daily_loss = if losing.is_empty() {
        0.0
    } else {
        losing.iter().map(|(_, v)| **v).sum::<f64>() / losing.len() as f64
    };
    let mut day_equity = cfg.initial;
    let returns = daily
        .values()
        .map(|pnl| {
            let daily_return = if day_equity > 0.0 {
                pnl / day_equity
            } else {
                0.0
            };
            day_equity += pnl;
            daily_return
        })
        .collect::<Vec<_>>();
    let mean = if returns.is_empty() {
        0.0
    } else {
        returns.iter().sum::<f64>() / returns.len() as f64
    };
    let variance = if returns.len() > 1 {
        let squared_deviations = returns
            .iter()
            .map(|value| (value - mean).powi(2))
            .sum::<f64>();

        squared_deviations / (returns.len() - 1) as f64
    } else {
        0.0
    };
    let sharpe = if variance > 0.0 {
        mean / variance.sqrt() * 252f64.sqrt()
    } else {
        0.0
    };
    let net_growth = percentage_of(net, cfg.initial);
    let profit_factor = if total_loss < 0.0 {
        total_win / -total_loss
    } else {
        0.0
    };
    let max_daily_loss_date = match max_daily_day {
        0 => "          ".to_owned(),
        day => format_day(day),
    };

    json!({
        "symbol": cfg.symbol,
        "instrument": cfg.instrument.name(&cfg.symbol),
        "first_ts": format_ts(first),
        "last_ts": format_ts(last),
        "total_days": total_days,
        "num_trades": trades.len(),
        "initial_bal": round2(cfg.initial),
        "final_bal": round2(final_balance),
        "net_growth": round4(net_growth),
        "sharpe": round4(sharpe),
        "total_win": round2(total_win),
        "total_loss": round2(total_loss),
        "win_rate": round4(win_rate),
        "win_count": wins,
        "profit_factor": round4(profit_factor),
        "expectancy": round4(expectancy),
        "max_lose_streak": max_streak,
        "avg_size": round4(avg_size),
        "min_size": round4(min_size),
        "max_size": round4(max_size),
        "avg_weekly": round2(avg_weekly),
        "avg_monthly": round2(avg_monthly),
        "avg_weekly_pct": round4(percentage_of(avg_weekly, cfg.initial)),
        "avg_monthly_pct": round4(percentage_of(avg_monthly, cfg.initial)),
        "max_drawdown": round4(dd.max_dd),
        "max_drawdown_dollars": round2(dd.max_dd_dollars),
        "max_drawdown_peak_date": format_day(dd.max_dd_peak),
        "max_drawdown_trough_date": format_day(dd.max_dd_trough),
        "avg_drawdown": round4(dd.avg_dd),
        "avg_drawdown_dollars": round2(dd.avg_dd_dollars),
        "max_intraday_drawdown": round4(dd.max_idd),
        "max_intraday_drawdown_dollars": round2(dd.max_idd_dollars),
        "max_intraday_drawdown_date": format_day(dd.max_idd_day),
        "avg_intraday_drawdown": round4(dd.avg_idd),
        "avg_intraday_drawdown_dollars": round2(dd.avg_idd_dollars),
        "max_daily_loss": round2(max_daily_loss),
        "max_daily_loss_date": max_daily_loss_date,
        "avg_daily_loss": round2(avg_daily_loss),
        "trades": trades,
        "montecarlo": Value::Null,
        "fx": Value::Null,
    })
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn percentage_of(value: f64, base: f64) -> f64 {
    if base == 0.0 {
        0.0
    } else {
        value / base * 100.0
    }
}

pub(crate) fn montecarlo(trades: &[Trade], initial: f64) -> Value {
    let pnls = trades.iter().map(|t| t.pnl).collect::<Vec<_>>();
    crate::monte_carlo::run(&pnls, initial)
}
