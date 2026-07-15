use super::types::{Bar, Strategy};
use crate::{error::ApiError, questdb::QuestDb, strategies::idk::noise_momentum::NoiseMomentum};
use serde::Serialize;

#[derive(Serialize)]
pub struct NoiseAreaPoint {
    time: i64,
    ub: f64,
    lb: f64,
}

pub async fn noise_area(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<NoiseAreaPoint>, ApiError> {
    let symbol = symbol.to_ascii_lowercase();
    if !["nq", "es"].contains(&symbol.as_str()) {
        return Err(ApiError::BadRequest("unknown symbol for march".into()));
    }

    let from_filter = if from.is_empty() {
        String::new()
    } else if valid_date(from) {
        format!(" WHERE timestamp >= dateadd('d',-90,'{from}')")
    } else {
        return Err(ApiError::BadRequest("invalid from date".into()));
    };
    let to_filter = if to.is_empty() {
        String::new()
    } else if valid_date(to) {
        let conjunction = if from_filter.is_empty() {
            " WHERE"
        } else {
            " AND"
        };
        format!("{conjunction} timestamp < '{to}'")
    } else {
        return Err(ApiError::BadRequest("invalid to date".into()));
    };
    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,open,high,low,close,volume,0.0 ",
            "FROM {symbol}_1m{from_filter}{to_filter} ORDER BY timestamp",
        ),
        symbol = symbol,
        from_filter = from_filter,
        to_filter = to_filter,
    );
    let rows = questdb.csv(&sql).await?;
    let start_day = if from.is_empty() {
        i64::MIN
    } else {
        parse_iso_days(from).ok_or_else(|| ApiError::BadRequest("invalid from date".into()))?
    };
    let mut strategy = NoiseMomentum::default();
    let mut output = Vec::new();

    for row in rows {
        let bar = bar_from_csv(&row)?;
        strategy.update(bar, 10_000.0);
        if bar.ts.div_euclid(86_400) >= start_day
            && (strategy.upper_bound > 0.0 || strategy.lower_bound > 0.0)
        {
            output.push(NoiseAreaPoint {
                time: bar.ts,
                ub: round4(strategy.upper_bound),
                lb: round4(strategy.lower_bound),
            });
        }
    }

    // Match Zig's PGWire-backed dataset boundary: the final available bar is
    // not emitted by the noise overlay for a bounded request.
    if !to.is_empty() {
        output.pop();
    }

    Ok(output)
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn bar_from_csv(row: &csv::StringRecord) -> Result<Bar, ApiError> {
    let field = |index: usize| {
        row.get(index)
            .ok_or_else(|| ApiError::QuestDb(format!("missing bar CSV column {index}")))
    };
    let number = |index: usize| -> Result<f64, ApiError> {
        field(index)?
            .parse()
            .map_err(|_| ApiError::QuestDb(format!("invalid bar CSV column {index}")))
    };

    Ok(Bar {
        ts: field(0)?
            .parse::<i64>()
            .map_err(|_| ApiError::QuestDb("invalid bar timestamp".into()))?
            / 1_000_000,
        open: number(1)?,
        high: number(2)?,
        low: number(3)?,
        close: number(4)?,
        volume: number(5)?,
        vix: number(6)?,
    })
}

pub(crate) fn valid_date(s: &str) -> bool {
    s.len() == 10
        && s.bytes().enumerate().all(|(i, b)| {
            if i == 4 || i == 7 {
                b == b'-'
            } else {
                b.is_ascii_digit()
            }
        })
}
pub fn iso_day(s: &str) -> Option<i64> {
    parse_iso_days(s)
}
pub(crate) fn parse_iso_days(s: &str) -> Option<i64> {
    if !valid_date(s) {
        return None;
    }
    let y = s[0..4].parse().ok()?;
    let m = s[5..7].parse().ok()?;
    let d = s[8..10].parse().ok()?;
    Some(days_from_civil(y, m, d))
}
fn days_from_civil(y0: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y0 - 1 } else { y0 };
    let era = if y >= 0 { y } else { y - 399 }.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}
fn civil_from_days(z0: i64) -> (i64, i64, i64) {
    let z = z0 + 719468;
    let era = if z >= 0 { z } else { z - 146096 }.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (y + if m <= 2 { 1 } else { 0 }, m, d)
}
pub(crate) fn format_day(day: i64) -> String {
    let (y, m, d) = civil_from_days(day);
    format!("{y:04}-{m:02}-{d:02}")
}
pub fn format_ts(ts: i64) -> String {
    format!(
        "{} {:02}:{:02}",
        format_day(ts.div_euclid(86400)),
        ts.rem_euclid(86400) / 3600,
        ts.rem_euclid(3600) / 60
    )
}
