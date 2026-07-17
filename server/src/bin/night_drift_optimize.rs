use backend_rust::questdb::QuestDb;
use serde::Serialize;
use std::{collections::BTreeMap, sync::Arc, thread};

const DAY: i64 = 86_400;
const INITIAL: f64 = 6_000.0;
const SPREAD: f64 = 0.2;

#[derive(Clone, Copy)]
struct Bar {
    minute: u16,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

struct MarketDay {
    day: i64,
    bars: Vec<Bar>,
    vix: f64,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Params {
    lookback: usize,
    preclose_minute: u16,
    delta_start: u16,
    delta_end: u16,
    entry_minute: u16,
    exit_minute: u16,
    min_selloff: f64,
    max_selloff: f64,
    max_delta: f64,
    min_vix: f64,
    max_vix: f64,
    weekday_mask: u8,
    target_sigma: f64,
    stop_sigma: f64,
}

#[derive(Clone, Copy)]
struct RawTrade {
    exit_day: i64,
    points: f64,
    boost: f64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Metrics {
    trades: usize,
    growth: f64,
    max_drawdown: f64,
    profit_factor: f64,
    worst_year_growth: f64,
    positive_years: usize,
    years: usize,
    yearly_growth: BTreeMap<i32, f64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ResultRow {
    score: f64,
    size_scale: f64,
    params: Params,
    #[serde(rename = "inSample")]
    is_metrics: Metrics,
    #[serde(rename = "outOfSample")]
    oos_metrics: Metrics,
    full: Metrics,
}

fn year(day: i64) -> i32 {
    // Civil date conversion, Howard Hinnant's algorithm.
    let z = day + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let mut y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    if mp >= 10 {
        y += 1;
    }
    y as i32
}

fn sigma(closes: &[f64], index: usize, lookback: usize) -> Option<f64> {
    if index <= lookback {
        return None;
    }
    let start = index - lookback;
    let mut sum = 0.0;
    let mut sum2 = 0.0;
    for pair in closes[start..=index].windows(2) {
        let d = pair[1] - pair[0];
        sum += d;
        sum2 += d * d;
    }
    let n = lookback as f64;
    let variance = (sum2 - sum * sum / n) / (n - 1.0);
    (variance > 0.0).then(|| variance.sqrt())
}

fn bar_before(day: &MarketDay, minute: u16) -> Option<Bar> {
    day.bars
        .iter()
        .rev()
        .find(|bar| bar.minute < minute)
        .copied()
}

fn bar_at_or_after(day: &MarketDay, minute: u16) -> Option<(usize, Bar)> {
    day.bars
        .iter()
        .enumerate()
        .find(|(_, bar)| bar.minute >= minute)
        .map(|(index, bar)| (index, *bar))
}

fn raw_trades(days: &[MarketDay], params: Params) -> Vec<RawTrade> {
    let closes = days
        .iter()
        .map(|day| day.bars.last().map(|bar| bar.close).unwrap_or(0.0))
        .collect::<Vec<_>>();
    let mut output = Vec::new();
    for index in 1..days.len().saturating_sub(1) {
        let day = &days[index];
        let next = &days[index + 1];
        if next.day != day.day + 1 {
            continue;
        }
        let Some(sigma) = sigma(&closes, index - 1, params.lookback) else {
            continue;
        };
        let weekday = (day.day + 3).rem_euclid(7) as u8;
        if weekday > 4 || params.weekday_mask & (1 << weekday) == 0 {
            continue;
        }
        let Some(first) = day.bars.first().copied() else {
            continue;
        };
        let Some(preclose) = bar_before(day, params.preclose_minute) else {
            continue;
        };
        let selloff = (first.open - preclose.close) / sigma;
        if selloff <= params.min_selloff || selloff >= params.max_selloff {
            continue;
        }
        let mut delta = 0.0;
        let mut volume = 0.0;
        for bar in day
            .bars
            .iter()
            .filter(|bar| bar.minute >= params.delta_start && bar.minute < params.delta_end)
        {
            let range = bar.high - bar.low;
            if range > 0.0 {
                delta += ((2.0 * bar.close - bar.high - bar.low) / range) * bar.volume;
            }
            volume += bar.volume;
        }
        let relative_delta = if volume > 0.0 { delta / volume } else { 0.0 };
        if relative_delta >= params.max_delta
            || day.vix < params.min_vix
            || day.vix >= params.max_vix
        {
            continue;
        }
        let Some((entry_index, entry_bar)) = bar_at_or_after(day, params.entry_minute) else {
            continue;
        };
        let entry = entry_bar.open;
        let target = entry + params.target_sigma * sigma;
        let stop = entry - params.stop_sigma * sigma;
        let mut exit = None;
        for bar in day.bars.iter().skip(entry_index + 1) {
            if bar.low <= stop {
                exit = Some(bar.open.min(stop));
                break;
            }
            if bar.high >= target {
                exit = Some(bar.open.max(target));
                break;
            }
        }
        for bar in next
            .bars
            .iter()
            .take_while(|bar| bar.minute < params.exit_minute)
        {
            if exit.is_some() {
                break;
            }
            if bar.low <= stop {
                exit = Some(bar.open.min(stop));
                break;
            }
            if bar.high >= target {
                exit = Some(bar.open.max(target));
                break;
            }
        }
        let exit =
            exit.or_else(|| bar_at_or_after(next, params.exit_minute).map(|(_, bar)| bar.open));
        if let Some(exit) = exit {
            output.push(RawTrade {
                exit_day: next.day,
                points: exit - entry - SPREAD,
                boost: if selloff >= 0.6 { 1.3 } else { 1.0 },
            });
        }
    }
    output
}

fn metrics(trades: &[RawTrade], scale: f64, from_year: i32, to_year: i32) -> Metrics {
    let mut equity = INITIAL;
    let mut peak = equity;
    let mut max_dd = 0.0_f64;
    let mut gross_win = 0.0;
    let mut gross_loss = 0.0;
    let mut yearly = BTreeMap::<i32, (f64, f64)>::new();
    let mut count = 0;
    for trade in trades {
        let y = year(trade.exit_day);
        if y < from_year || y > to_year {
            continue;
        }
        let start = yearly.entry(y).or_insert((equity, equity)).0;
        let quantity = 1.1 * scale * (equity / INITIAL).max(0.0) * trade.boost;
        let pnl = trade.points * quantity;
        equity += pnl;
        yearly.insert(y, (start, equity));
        peak = peak.max(equity);
        if peak > 0.0 {
            max_dd = max_dd.max((peak - equity) / peak * 100.0);
        }
        if pnl >= 0.0 {
            gross_win += pnl
        } else {
            gross_loss -= pnl
        }
        count += 1;
    }
    let yearly_growth = yearly
        .into_iter()
        .map(|(y, (start, end))| (y, (end / start - 1.0) * 100.0))
        .collect::<BTreeMap<_, _>>();
    let worst = yearly_growth
        .values()
        .copied()
        .reduce(f64::min)
        .unwrap_or(0.0);
    let positive = yearly_growth
        .values()
        .filter(|growth| **growth > 0.0)
        .count();
    Metrics {
        trades: count,
        growth: (equity / INITIAL - 1.0) * 100.0,
        max_drawdown: max_dd,
        profit_factor: if gross_loss > 0.0 {
            gross_win / gross_loss
        } else {
            0.0
        },
        worst_year_growth: worst,
        positive_years: positive,
        years: yearly_growth.len(),
        yearly_growth,
    }
}

fn optimize_scale(trades: &[RawTrade]) -> (f64, Metrics) {
    let mut best = None::<(f64, Metrics, f64)>;
    for step in 4..=60 {
        let scale = step as f64 * 0.05;
        let m = metrics(trades, scale, 2018, 2024);
        if m.trades < 380 || m.trades > 520 || m.max_drawdown > 20.0 {
            continue;
        }
        let target_penalty = (m.growth - 450.0).abs();
        let consistency_penalty = (-m.worst_year_growth).max(0.0) * 8.0
            + (m.years.saturating_sub(m.positive_years) as f64) * 80.0;
        let dd_penalty = (m.max_drawdown - 17.5).max(0.0) * 20.0;
        let value = target_penalty + consistency_penalty + dd_penalty;
        if best.as_ref().is_none_or(|(_, _, current)| value < *current) {
            best = Some((scale, m, value));
        }
    }
    best.map(|(scale, metrics, _)| (scale, metrics))
        .unwrap_or_else(|| (0.5, metrics(trades, 0.5, 2018, 2024)))
}

fn rng(state: &mut u64) -> u64 {
    *state ^= *state << 13;
    *state ^= *state >> 7;
    *state ^= *state << 17;
    *state
}

fn choose<T: Copy>(state: &mut u64, values: &[T]) -> T {
    values[(rng(state) as usize) % values.len()]
}

fn random_params(state: &mut u64) -> Params {
    let (delta_start, delta_end) = choose(
        state,
        &[
            (900, 960),
            (930, 990),
            (960, 1020),
            (990, 1050),
            (1020, 1080),
        ],
    );
    Params {
        lookback: choose(state, &[10, 14, 20, 30, 40, 60]),
        preclose_minute: choose(state, &[960, 990, 1020, 1050]),
        delta_start,
        delta_end,
        entry_minute: choose(state, &[1110, 1140, 1170, 1200, 1230, 1260, 1290]),
        exit_minute: choose(state, &[240, 270, 300, 330, 360, 390, 420, 450, 480]),
        min_selloff: choose(state, &[-0.75, -0.5, -0.25, 0.0, 0.25, 0.5]),
        max_selloff: choose(state, &[1.0, 1.5, 2.0, 3.0, 99.0]),
        max_delta: choose(state, &[-0.3, 0.0, 0.3, 0.65]),
        min_vix: choose(state, &[0.0, 12.0, 15.0, 18.0]),
        max_vix: choose(state, &[18.0, 22.0, 26.0, 32.0, 99.0]),
        weekday_mask: choose(
            state,
            &[0b11111, 0b11110, 0b01111, 0b01110, 0b10111, 0b11101],
        ),
        target_sigma: choose(state, &[0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.1, 2.4]),
        stop_sigma: choose(state, &[0.35, 0.45, 0.55, 0.7, 0.85, 1.0, 1.2]),
    }
}

fn evaluate(days: &[MarketDay], params: Params) -> Option<ResultRow> {
    if params.max_vix <= params.min_vix || params.max_selloff <= params.min_selloff {
        return None;
    }
    let trades = raw_trades(days, params);
    let (scale, is_metrics) = optimize_scale(&trades);
    if is_metrics.trades < 380 || is_metrics.trades > 520 || is_metrics.max_drawdown > 20.0 {
        return None;
    }
    let oos_metrics = metrics(&trades, scale, 2025, 2026);
    let full = metrics(&trades, scale, 2018, 2026);
    let score = -(is_metrics.growth - 450.0).abs() - is_metrics.max_drawdown.max(15.0) * 4.0
        + is_metrics.worst_year_growth.min(20.0) * 5.0
        + is_metrics.profit_factor * 50.0
        - (is_metrics.years.saturating_sub(is_metrics.positive_years) as f64) * 100.0;
    Some(ResultRow {
        score,
        size_scale: scale,
        params,
        is_metrics,
        oos_metrics,
        full,
    })
}

async fn load_days() -> anyhow::Result<Vec<MarketDay>> {
    let db = QuestDb::from_env()?;
    let mut days = Vec::new();
    for y in 2017..=2026 {
        let to = y + 1;
        let month_day = if y == 2017 { "09-01" } else { "01-01" };
        let sql = format!(
            concat!(
                "SELECT cast(n.timestamp as long) ts,n.timestamp,n.open,n.high,n.low,n.close,n.volume,v.close ",
                "FROM nq_1m n ASOF JOIN vix_1d v WHERE n.timestamp >= '{}-{}' ",
                "AND n.timestamp < '{}-01-01' ORDER BY n.timestamp"
            ),
            y, month_day, to
        );
        let rows = db.csv(&sql).await?;
        for row in rows {
            let ts = row[0].parse::<i64>()? / 1_000_000;
            let day = ts.div_euclid(DAY);
            if days.last().is_none_or(|d: &MarketDay| d.day != day) {
                days.push(MarketDay {
                    day,
                    bars: Vec::new(),
                    vix: row[7].parse()?,
                });
            }
            days.last_mut().unwrap().bars.push(Bar {
                minute: (ts.rem_euclid(DAY) / 60) as u16,
                open: row[2].parse()?,
                high: row[3].parse()?,
                low: row[4].parse()?,
                close: row[5].parse()?,
                volume: row[6].parse()?,
            });
        }
    }
    Ok(days)
}

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let days = Arc::new(load_days().await?);
    eprintln!("loaded {} market days", days.len());
    let workers = thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(4)
        .min(8);
    let per_worker = 1_500;
    let mut handles = Vec::new();
    for worker in 0..workers {
        let days = Arc::clone(&days);
        handles.push(thread::spawn(move || {
            let mut state = 0x9e3779b97f4a7c15_u64 ^ (worker as u64 + 1) * 0x100000001b3;
            let mut best = Vec::new();
            for _ in 0..per_worker {
                if let Some(row) = evaluate(&days, random_params(&mut state)) {
                    best.push(row);
                    best.sort_by(|a, b| b.score.total_cmp(&a.score));
                    best.truncate(30);
                }
            }
            best
        }));
    }
    let mut results = Vec::new();
    for handle in handles {
        results.extend(handle.join().unwrap());
    }
    results.sort_by(|a, b| b.score.total_cmp(&a.score));
    results.truncate(50);
    println!("{}", serde_json::to_string_pretty(&results)?);
    Ok(())
}
